"""Deterministic field extraction from raw SMS text.

Nothing in this module calls an LLM. Everything is a pure function of
``(text, now)`` so it is fast, testable, and safe to run on-device or on the
filter extension's network edge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

from .models import EntityKind, SenderInfo
from .normalize import normalize_text
from .registry import ENTITIES_BY_CODE, SHORTENERS, SUSPICIOUS_TLDS

# --------------------------------------------------------------------------
# Text normalisation
# --------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def normalize(body: str) -> str:
    """Collapse whitespace, convert regional digits/currency, make text matchable.

    Regional numerals must be translated *before* anything else: a UPI RRN
    written in Devanagari has to deduplicate against the ASCII version that
    PhonePe sent, or the ledger counts the same payment twice.
    """
    text = normalize_text(body.replace("\xa0", " ")).text
    return _WS.sub(" ", text).strip()


# --------------------------------------------------------------------------
# Sender parsing (TRAI DLT)
# --------------------------------------------------------------------------

DLT_SENDER_RE = re.compile(r"^(?:([A-Z]{2})-)?([A-Z0-9]{2,9})(?:-([PSTG]))?$")
MOBILE_RE = re.compile(r"^(?:\+?91)?([6-9]\d{9})$")
SHORTCODE_RE = re.compile(r"^\d{3,6}$")


def parse_sender(raw: str) -> SenderInfo:
    """Decode an Indian sender ID.

    Formats seen in the wild:
      * ``AD-HDFCBK-T``  operator prefix, entity code, DLT category suffix
      * ``VM-HDFCBK``    no suffix
      * ``HDFCBK``       bare entity code
      * ``+919876543210`` / ``9876543210``  personal mobile
      * ``1909`` / ``56161``                short code
    """
    raw = (raw or "").strip()
    info = SenderInfo(raw=raw, normalized=raw.upper())

    compact = re.sub(r"[\s\-()]", "", raw)
    mobile = MOBILE_RE.match(compact)
    if mobile:
        info.entity_kind = EntityKind.PERSONAL_MOBILE
        info.display_name = "personal mobile"
        info.notes.append("10-digit personal number: no DLT registration, treat claims of being a bank with suspicion")
        return info

    if SHORTCODE_RE.match(compact):
        info.entity_kind = EntityKind.SHORTCODE
        info.display_name = raw
        info.notes.append("short code")
        return info

    match = DLT_SENDER_RE.match(raw.upper())
    if not match:
        info.notes.append("sender ID not recognised")
        return info

    operator, code, suffix = match.group(1), match.group(2), match.group(3)
    info.operator = operator
    info.entity_code = code
    info.dlt_suffix = suffix

    entity = ENTITIES_BY_CODE.get(code)
    if entity:
        info.entity_kind = entity.kind
        info.display_name = entity.name
        info.is_known_brand = True
    else:
        info.notes.append(f"unknown DLT entity code {code}")

    if suffix == "P":
        info.notes.append("DLT suffix -P: registered promotional header")
    elif suffix == "S":
        info.notes.append("DLT suffix -S: service header (banks also send marketing on this)")
    elif suffix == "T":
        info.notes.append("DLT suffix -T: transactional header")
    elif suffix == "G":
        info.notes.append("DLT suffix -G: government header")

    return info


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------

_AMT = r"((?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d{1,2})?)"
_PREFIX_RE = re.compile(r"(?:\brs\.?|\binr|\b₹)\s*" + _AMT, re.IGNORECASE)
_SUFFIX_RE = re.compile(_AMT + r"\s*(?:rs\.?|inr|₹|/-)", re.IGNORECASE)
_FX_RE = re.compile(r"(?:\busd|\$)\s*" + _AMT, re.IGNORECASE)

_BALANCE_RE = re.compile(
    r"\b(?:avl\.?\s*bal(?:ance)?|available\s+bal(?:ance)?|closing\s+bal(?:ance)?|bal(?:ance)?)\b"
    r"\s*(?:is|:)?\s*(?:rs\.?|inr|₹)?\s*" + _AMT,
    re.IGNORECASE,
)
_CREDIT_LIMIT_RE = re.compile(
    r"\b(?:avl\.?\s+(?:cr(?:edit)?\s+)?limit|available\s+credit\s+limit|credit\s+limit)\b"
    r"\s*(?:is|:)?\s*(?:rs\.?|inr|₹)?\s*" + _AMT,
    re.IGNORECASE,
)
_MIN_DUE_RE = re.compile(
    r"\b(?:min(?:imum)?\.?\s+(?:amt|amount|due))\b\s*(?:is|:)?\s*(?:rs\.?|inr|₹)?\s*" + _AMT,
    re.IGNORECASE,
)
_TOTAL_DUE_RE = re.compile(
    r"\b(?:total\s+(?:amt\.?|amount)?\s*(?:due|payable)|total\s+due)\b"
    r"\s*(?:is|:)?\s*(?:rs\.?|inr|₹)?\s*" + _AMT,
    re.IGNORECASE,
)

DEBIT_WORDS = re.compile(
    r"\b(?:debited|debit|dr|withdrawn|withdrawal|spent|paid|payment\s+of|purchase[d]?|"
    r"txn\s+of|transferred|sent|deducted|charged|billed)\b",
    re.IGNORECASE,
)
CREDIT_WORDS = re.compile(
    r"\b(?:credited|credit|cr|received|refunded|reversed|deposited|salary|cashback)\b",
    re.IGNORECASE,
)


@dataclass
class AmountHit:
    value: float
    currency: str
    raw: str
    start: int
    end: int
    direction: str = "none"


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def find_balance(text: str) -> Optional[tuple[float, tuple[int, int]]]:
    match = _BALANCE_RE.search(text)
    if not match:
        return None
    return _to_float(match.group(1)), match.span()


def find_credit_limit(text: str) -> Optional[float]:
    match = _CREDIT_LIMIT_RE.search(text)
    return _to_float(match.group(1)) if match else None


def find_due_amounts(text: str) -> tuple[Optional[float], Optional[float]]:
    total = _TOTAL_DUE_RE.search(text)
    minimum = _MIN_DUE_RE.search(text)
    return (
        _to_float(total.group(1)) if total else None,
        _to_float(minimum.group(1)) if minimum else None,
    )


def _direction(text: str, start: int, end: int) -> str:
    window = text[max(0, start - 45) : end + 45]
    debit = DEBIT_WORDS.search(window)
    credit = CREDIT_WORDS.search(window)
    if debit and not credit:
        return "debit"
    if credit and not debit:
        return "credit"
    return "none"


def find_amounts(text: str, skip_spans: tuple[tuple[int, int], ...] = ()) -> list[AmountHit]:
    """Find monetary values, ignoring balances/limits that were already claimed."""
    hits: list[AmountHit] = []
    seen: set[tuple[int, int]] = set()

    def _blocked(start: int, end: int) -> bool:
        for s, e in skip_spans:
            if start >= s and end <= e:
                return True
        return False

    for regex, currency in ((_PREFIX_RE, "INR"), (_SUFFIX_RE, "INR"), (_FX_RE, "USD")):
        for match in regex.finditer(text):
            start, end = match.span()
            if (start, end) in seen or _blocked(start, end):
                continue
            seen.add((start, end))
            hits.append(
                AmountHit(
                    value=_to_float(match.group(1)),
                    currency=currency,
                    raw=match.group(0).strip(),
                    start=start,
                    end=end,
                    direction=_direction(text, start, end),
                )
            )
    hits.sort(key=lambda h: h.start)
    return hits


# --------------------------------------------------------------------------
# Account / card identifiers and references
# --------------------------------------------------------------------------

_LAST4_PATTERNS = (
    re.compile(
        r"\b(?:a/?c|acct|account|card|खाता|खाते|கணக்கு|ఖాతా|ಖಾತೆ|ખાતું|অ্যাকাউন্ট)\b(?:\s*(?:no\.?|number|#))?"
        r"[^0-9A-Za-z\n]{0,25}?[Xx*]{1,}[^\d\n]{0,3}(\d{4})\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:a/?c|acct|account|card)\b[^.\n]{0,30}?ending(?:\s+with|\s+in)?[^\d\n]{0,6}(\d{4})\b", re.IGNORECASE),
    re.compile(r"\b(?:a/?c|acct|account|card)\s*(?:no\.?|number)?\s*[:#]?\s*(\d{4})\b", re.IGNORECASE),
)


def find_last4(text: str) -> Optional[str]:
    for pattern in _LAST4_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


_REF_RE = re.compile(
    r"\b(?:upi\s*(?:ref(?:erence)?(?:\s*(?:no\.?|id|number))?)|rrn|utr|ref(?:erence)?"
    r"(?:\s*(?:no\.?|id|number))?|txn\s*(?:id|no\.?)|transaction\s*id)\b"
    r"\s*[:#\-]?\s*([A-Za-z0-9]{6,24})",
    re.IGNORECASE,
)
_UPI_RRN_RE = re.compile(r"\b(\d{12})\b")


def find_reference(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return ``(reference, upi_rrn)``.

    A 12-digit reference is treated as a UPI RRN, which is the strongest
    available key for collapsing "your app confirmation" + "your bank SMS"
    into a single transaction.
    """
    match = _REF_RE.search(text)
    best = match.group(1) if match else None
    if best is None:
        rrn = _UPI_RRN_RE.search(text)
        if rrn:
            return rrn.group(1), rrn.group(1)
        return None, None
    return best, best if best.isdigit() and len(best) == 12 else None


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b")
_NAMED_DATE_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-/.]*([A-Za-z]{3,9})\.?[\s\-,/]*(\d{2,4})?\b"
)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


@dataclass
class DateHit:
    value: datetime
    raw: str
    start: int
    end: int


def _resolve_year(year: Optional[int], now: datetime, month: int, day: int) -> int:
    if year is not None:
        return 2000 + year if year < 100 else year
    # No year in the SMS: assume the next occurrence of that date.
    candidate = datetime(now.year, month, day)
    # A bill or journey date more than a month in the past is almost certainly
    # next year's occurrence (e.g. "due on 5 Jan" read in March).
    if candidate.date() < (now - timedelta(days=30)).date():
        candidate = datetime(now.year + 1, month, day)
    return candidate.year


def find_dates(text: str, now: datetime) -> list[DateHit]:
    hits: list[DateHit] = []

    for match in _ISO_DATE_RE.finditer(text):
        year, month, day = (int(g) for g in match.groups())
        try:
            hits.append(DateHit(datetime(year, month, day), match.group(0), match.start(), match.end()))
        except ValueError:
            continue

    claimed = [(h.start, h.end) for h in hits]

    for match in _NUMERIC_DATE_RE.finditer(text):
        if any(s <= match.start() < e for s, e in claimed):
            continue
        day, month, year = (int(g) for g in match.groups())
        if month > 12 and day <= 12:  # written as mm-dd-yy
            day, month = month, day
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        resolved = _resolve_year(year, now, month, day)
        try:
            hits.append(DateHit(datetime(resolved, month, day), match.group(0), match.start(), match.end()))
        except ValueError:
            continue

    claimed = [(h.start, h.end) for h in hits]

    for match in _NAMED_DATE_RE.finditer(text):
        if any(s <= match.start() < e for s, e in claimed):
            continue
        day = int(match.group(1))
        month = _MONTHS.get(match.group(2).lower())
        if not month or not 1 <= day <= 31:
            continue
        year = int(match.group(3)) if match.group(3) else None
        resolved = _resolve_year(year, now, month, day)
        try:
            hits.append(DateHit(datetime(resolved, month, day), match.group(0), match.start(), match.end()))
        except ValueError:
            continue

    hits.sort(key=lambda h: h.start)
    return hits


# --------------------------------------------------------------------------
# OTP, vouchers, PINs
# --------------------------------------------------------------------------

_OTP_KEYWORD_RE = re.compile(
    r"(?:\botp\b|\bo\.t\.p\.?\b|one[\s\-]?time\s+(?:password|code|pin)|verification\s+code|"
    r"secure\s+code|authentication\s+code|login\s+code)",
    re.IGNORECASE,
)
_DIGIT_GROUP_RE = re.compile(r"\b(\d{4,8})\b")


def find_otp(text: str) -> Optional[tuple[str, str]]:
    """Return ``(code, context)`` for the most likely OTP.

    The code must be a standalone 4-8 digit group, not an amount decimal, not a
    year, and as close as possible to an OTP keyword.
    """
    keyword = _OTP_KEYWORD_RE.search(text)
    if not keyword:
        return None

    best: Optional[tuple[int, str]] = None
    for match in _DIGIT_GROUP_RE.finditer(text):
        code = match.group(1)
        before = text[max(0, match.start() - 6) : match.start()]
        after = text[match.end() : match.end() + 4]
        if re.search(r"(?:rs\.?|inr|₹|\d,)\s*$", before, re.IGNORECASE):
            continue
        if after.startswith(".00") or after.startswith("0"):
            continue
        if len(code) == 4 and code.startswith(("19", "20")) and 1900 <= int(code) <= 2099:
            continue
        distance = abs(match.start() - keyword.end())
        if best is None or distance < best[0]:
            best = (distance, code)

    if best is None:
        return None
    return best[1], keyword.group(0)


_VOUCHER_CODE_RE = re.compile(
    r"\b(?:voucher|coupon|gift\s*card|promo(?:tion)?)\s*code\b[^A-Za-z0-9]{0,12}([A-Z0-9][A-Z0-9\-]{4,23})",
    re.IGNORECASE,
)
_BARE_CODE_RE = re.compile(r"\bcode\s*(?:is|:)?\s*([A-Z0-9]{5,16}[-][A-Z0-9]{2,8}(?:[-][A-Z0-9]{2,8})?)\b")
_PIN_RE = re.compile(r"\bpin(?:\s*code)?\s*(?:is|:)?\s*(\d{4,6})\b", re.IGNORECASE)


def find_voucher_code(text: str) -> Optional[str]:
    for regex in (_VOUCHER_CODE_RE, _BARE_CODE_RE):
        match = regex.search(text)
        if match:
            candidate = match.group(1).strip("-")
            if any(ch.isdigit() for ch in candidate) and len(candidate) >= 6:
                return candidate
    return None


def find_pin(text: str) -> Optional[str]:
    match = _PIN_RE.search(text)
    return match.group(1) if match else None


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------

_URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>\"')]+", re.IGNORECASE)
_BARE_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+(?:com|in|net|org|co|io|gov|info|biz|xyz|top|link|click|shop|site|online|live|icu|buzz|app|dev|me|cc|tk|ml|ga|gq|work|rest|fun|club|store|tech|space|website|press|ly|gd|re|so|to|ws|ai|gg|id|ae|ru|fr|at|is|do|ee)\b(?:/[^\s<>\"')]*)?",
    re.IGNORECASE,
)
_TRAILING_PUNCT = ".,;:!?)]}\"'"


def find_urls(text: str) -> list[str]:
    found: list[str] = []
    for match in _URL_RE.finditer(text):
        found.append(match.group(0).rstrip(_TRAILING_PUNCT))
    for match in _BARE_DOMAIN_RE.finditer(text):
        candidate = match.group(0).rstrip(_TRAILING_PUNCT)
        if not any(candidate in seen for seen in found):
            found.append(candidate)
    # de-duplicate, keep order
    seen: set[str] = set()
    unique: list[str] = []
    for url in found:
        key = url.lower().rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(url)
    return unique


def url_domain(url: str) -> str:
    candidate = url if "://" in url else f"http://{url}"
    host = (urlsplit(candidate).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_shortener(domain: str) -> bool:
    return domain in SHORTENERS


def is_suspicious_tld(domain: str) -> bool:
    return "." in domain and domain.rsplit(".", 1)[-1] in SUSPICIOUS_TLDS


# --------------------------------------------------------------------------
# Travel / logistics
# --------------------------------------------------------------------------

_PNR_RE = re.compile(r"\bpnr\b[^A-Za-z0-9]{0,6}([A-Z0-9]{6}|[0-9]{10})\b", re.IGNORECASE)
_AWB_RE = re.compile(
    r"\b(?:awb|tracking|consignment|docket)\b\s*(?:id|no\.?|number)?\s*[:#]?\s*([A-Za-z0-9]{8,16})\b",
    re.IGNORECASE,
)
_VEHICLE_RE = re.compile(r"\b([A-Z]{2}[\s\-]?\d{1,2}[\s\-]?[A-Z]{1,3}[\s\-]?\d{4})\b", re.IGNORECASE)
_JOURNEY_RE = re.compile(
    r"\b(?:train|flight|bus)\s*(?:no\.?|number)?\s*[:#\-]?\s*([A-Z0-9]{1,3}[\s\-]?\d{2,5})\b",
    re.IGNORECASE,
)
_COACH_RE = re.compile(r"\bcoach\s*[:#]?\s*([A-Z]\d{1,2})\b", re.IGNORECASE)
_BERTH_RE = re.compile(r"\b(?:berth|seat)\s*(?:no\.?)?\s*[:#]?\s*(\d{1,3})\s*(?:\(([A-Z]{2,3})\))?", re.IGNORECASE)
_HANDOVER_RE = re.compile(
    r"\b(?:delivery|dac|handover|otp)\s*(?:code|otp|pin)?\s*(?:is|:)?\s*(\d{4,8})\b", re.IGNORECASE
)


def find_pnr(text: str) -> Optional[str]:
    match = _PNR_RE.search(text)
    return match.group(1) if match else None


def find_tracking(text: str) -> Optional[str]:
    match = _AWB_RE.search(text)
    return match.group(1) if match else None


def find_vehicle(text: str) -> Optional[str]:
    match = _VEHICLE_RE.search(text)
    return re.sub(r"[\s\-]", "", match.group(1)).upper() if match else None


def find_journey(text: str) -> Optional[str]:
    match = _JOURNEY_RE.search(text)
    return re.sub(r"\s+", " ", match.group(1)).upper().strip() if match else None


def find_seat(text: str) -> Optional[str]:
    coach = _COACH_RE.search(text)
    berth = _BERTH_RE.search(text)
    parts: list[str] = []
    if coach:
        parts.append(coach.group(1).upper())
    if berth:
        parts.append(berth.group(1))
        if berth.group(2):
            parts.append(f"({berth.group(2).upper()})")
    return " / ".join(parts) if parts else None


def find_handover_code(text: str) -> Optional[str]:
    match = _HANDOVER_RE.search(text)
    return match.group(1) if match else None


# --------------------------------------------------------------------------
# Merchant
# --------------------------------------------------------------------------

#: Each pattern captures a merchant-ish run of characters. The trailing
#: ``\b`` before the stop words matters: without it "Amazon" is truncated to
#: "Amaz" because the stop word "on" matches inside the brand name.
_TAIL = r"\s*\b(?:on|for|via|at|towards|using|by|dated|dt|ref|avl|balance|\.|,|;|$)"
_MERCHANT_PATTERNS = (
    re.compile(rf"\bat\s+([A-Z0-9][A-Z0-9&*\.\'\- ]{{2,40}}?){_TAIL}", re.IGNORECASE),
    re.compile(r"\bto\s+VPA\s+([A-Za-z0-9.\-_]+)", re.IGNORECASE),
    re.compile(r"\bup[iI]\s*(?:to|:)\s*([A-Za-z0-9.\-_]+@[A-Za-z0-9.\-_]+)"),
    re.compile(rf"\btowards\s+([A-Za-z0-9][A-Za-z0-9&*\.\'\- ]{{2,40}}?){_TAIL}", re.IGNORECASE),
    re.compile(rf"\b(?:paid to|payment to|sent to)\s+([A-Z0-9][A-Z0-9&*\.\'\- ]{{2,40}}?){_TAIL}", re.IGNORECASE),
    re.compile(rf"\bfrom\s+([A-Z0-9][A-Z0-9&*\.\'\- ]{{2,40}}?){_TAIL}", re.IGNORECASE),
    re.compile(
        r"\bfor\s+(?:your\s+)?([A-Z0-9][A-Z0-9&*\.\'\- ]{2,40}?)\s+(?:order|purchase|booking|refund|payment)",
        re.IGNORECASE,
    ),
    re.compile(rf"\b(?:vpa|merchant)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9&*\.\'\-]{{2,40}}?){_TAIL}", re.IGNORECASE),
    # Indian scripts put the "to" *after* the name: "SWIGGY को डेबिट",
    # "SWIGGY க்கு பற்று", "SWIGGY కు డెబిట్". English patterns cannot see this,
    # so the postposition is matched here rather than invented in normalize.py.
    re.compile(
        r"([A-Z0-9][A-Z0-9&*\.\'\- ]{2,40}?)\s*(?:को|க்கு|కు|ಗೆ|ത്തിലേക്ക്|ને)\s*"
        r"(?:डेबिट|debited|debit|பற்று|డెబిట్|ಡೆಬಿಟ್)",
        re.IGNORECASE,
    ),
)

_MERCHANT_STOPWORDS = {
    "YOUR", "THE", "A/C", "AC", "ACCOUNT", "CARD", "BANK", "UPI", "VPA", "DEAR", "CUSTOMER",
    "AVAILABLE", "AVL", "BAL", "BALANCE", "RS", "INR", "TXN", "TRANSACTION", "REF", "ON",
    "DT", "DATED", "AND", "WITH", "YOURS", "SIR", "MADAM",
}


def find_merchant_raw(text: str) -> Optional[str]:
    for pattern in _MERCHANT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = re.sub(r"\s+", " ", match.group(1)).strip(" .*-—")
        words = [w for w in candidate.split() if w.upper() not in _MERCHANT_STOPWORDS]
        candidate = " ".join(words).strip()
        if len(candidate) < 3:
            continue
        if candidate.upper().startswith("VPA") or "@" in candidate:
            return candidate
        if any(ch.isalpha() for ch in candidate):
            return candidate
    return None
