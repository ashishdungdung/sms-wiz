"""Rule-first classification and enrichment.

The LLM is an *optional* second opinion (see ``llm.py``). Everything here is
deterministic: same input, same output, fully explainable, and safe to run
inside an iOS filter extension where there is no network and very little RAM.

Rule order matters. Phishing beats everything; a delivery handover code beats
generic OTP; a FASTag toll beats a generic debit; a bank's standalone loan
pitch is only considered after every real-money rule has declined.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from . import catalog, extract, forensics, templates
from .merchants import MerchantDictionary
from .normalize import normalize_text
from .models import (
    Amount,
    Confidence,
    DedupInfo,
    EntityKind,
    GovInfo,
    Institution,
    Intent,
    Ledger,
    LedgerTreatment,
    LogisticsInfo,
    Merchant,
    OtpInfo,
    ReminderInfo,
    Routing,
    SenderInfo,
    SmsEvent,
    TradeInfo,
    VoucherInfo,
    utcnow,
)
from .redact import redact
from .registry import ALIAS_INDEX, ROUTING, EntityKind, effective_subaction

# --------------------------------------------------------------------------
# Pattern banks
# --------------------------------------------------------------------------

CC_BILL_PATTERNS = (
    r"\bpayment\s+received\b.{0,60}\b(?:card|credit\s+card)\b",
    r"\bthank\s+you\s+for\s+(?:your\s+)?payment\b",
    r"\b(?:credit\s+card|cc)\s+bill\s+(?:payment|paid|received|successful)",
    r"\bpayment\s+(?:of|towards)\b.{0,50}\b(?:credit\s+card|card\s+(?:ending|no|xx)|cc\b)",
    r"\bbill\s+(?:payment|pay)\b.{0,40}\b(?:card|cc)\b",
    r"\bcredited\s+to\s+your\s+(?:credit\s+)?card\b",
    r"\bcard\s+payment\s+(?:received|successful|of|done)\b",
    r"\bpayment\s+(?:successful|done|completed)\b.{0,60}\bcredit\s+card\b",
    # Paying the card *from* the bank account: "Rs 18,450 debited from A/c
    # XX4092 to Credit Card XX4433". The debit is real, the spend already
    # happened on the card, so counting both would double it.
    r"\b(?:to|towards)\s+(?:your\s+)?(?:credit\s+card|card\s+(?:ending|no\.?|xx)|cc\s*(?:a/c|ac|payment))\b",
    r"\b(?:cc|card)\s+payment\b",
)

REFUND_INITIATED_PATTERNS = (
    r"\brefund\b.{0,80}\b(?:initiated|being\s+process|processed|approved|will\s+be\s+credit|shall\s+be\s+credit|under\s+process)",
    r"\b(?:refund|reversal)\b.{0,60}\b(?:5|7|10)\s*(?:-\s*\d+\s*)?(?:working\s+)?days?\b",
    r"\brefund\s+(?:request|claim)\b",
)

REFUND_CREDITED_PATTERNS = (
    r"\brefund\b.{0,60}\b(?:has\s+been\s+)?(?:credited|received|completed|deposited)\b",
    r"\b(?:credited|received|deposited)\b.{0,80}\brefund\b",
    r"\b(?:refund|reversal)\b.{0,40}\bof\s+rs\.?\s*[\d,.]+\s+(?:is\s+)?credited\b",
    r"\breversal\s+credited\b",
)

DELIVERY_CONTEXT = re.compile(
    r"\b(?:delivery|deliver|parcel|consignment|courier|order|shipment|instamart|blinkit|zepto|amazon|flipkart|myntra|indane|gas\s+booking)\b",
    re.IGNORECASE,
)
AUTH_CONTEXT = re.compile(
    r"\b(?:login|log\s*in|netbanking|net\s+banking|transaction|payment|txn|mandate|beneficiary|"
    r"payee|register|registration|card|upi|transfer|withdraw)\b",
    re.IGNORECASE,
)
LOGISTICS_PATTERNS = (
    r"\bout\s+for\s+delivery\b",
    r"\bdelivered\b",
    r"\b(?:shipped|dispatched|in\s+transit|arriving|reached\s+(?:your\s+)?(?:city|hub))\b",
    r"\bready\s+for\s+(?:pickup|collection)\b",
    r"\b(?:courier|parcel|consignment|shipment)\b",
    r"\bchart\s+prepared\b",
    r"\b(?:pnr|coach|berth|boarding|train\s+no|flight\s+no)\b",
    r"\b(?:awb|docket)\b",
)
#: Travel is a *booking record*. The money arrives as its own bank debit, so a
#: ticket must never be counted a second time — it carries an itinerary instead.
TRAVEL_PATTERNS = (
    r"\b(?:pnr|coach|berth|boarding\s+pass|e-?ticket|web\s+ticket)\b",
    r"\b(?:train|railway|irctc)\b.{0,40}\b(?:booked|confirmed|ticket|journey)\b",
    r"\b(?:bus|flight|airlines?|airways)\b.{0,40}\b(?:booked|confirmed|ticket|departure)\b",
    r"\b(?:hotel|resort)\b.{0,40}\b(?:booking|reservation|check-?in)\b",
    r"\b(?:departure|arrival)\s*(?:time|at)?\b.{0,20}\d{1,2}[:.]\d{2}",
)
TRAVEL_MODE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("train", r"\b(?:train|irctc|railway|coach|berth|sleeper|chair\s+car)\b"),
    ("bus", r"\b(?:bus|volvo|sleeper\s+bus|redbus|abhibus|bus\s+ticket)\b"),
    ("flight", r"\b(?:flight|airline|airways|boarding|terminal|gate|indigo|air\s+india|vistara|spicejet|akasa)\b"),
    ("hotel", r"\b(?:hotel|resort|stay|check-?in|check-?out|room)\b"),
)
TRAVEL_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("IRCTC", r"\birctc\b|indian\s+railway"),
    ("RedBus", r"\bredbus\b"),
    ("AbhiBus", r"\babhibus\b"),
    ("ixigo", r"\bixigo\b"),
    ("MakeMyTrip", r"\bmakemytrip\b|\bmmt\b"),
    ("Goibibo", r"\bgoibibo\b"),
    ("Cleartrip", r"\bcleartrip\b"),
    ("IndiGo", r"\bindigo\b|\b6e\b"),
    ("Air India", r"\bair\s+india\b"),
    ("Vistara", r"\bvistara\b"),
    ("SpiceJet", r"\bspicejet\b"),
    ("Akasa Air", r"\bakasa\b"),
    ("OYO", r"\boyo\b"),
)
TRADE_PATTERNS = (
    r"\b(?:bought|sold|purchased)\b.{0,60}\b(?:shares?|units?|qty)\b",
    r"\border\b.{0,40}\b(?:executed|placed|confirmed)\b",
    r"\b(?:trade|contract)\s+(?:confirmation|note)\b",
    r"\bdelivery\s+(?:instruction|obligation)\b",
    r"\b(?:units?|shares?)\b.{0,40}\ballotted\b",
    r"\ballotted\s+(?:to\s+)?(?:your\s+)?folio\b",
    r"\bfolio\b.{0,40}\b(?:units?|nav)\b",
)
DIVIDEND_PATTERNS = (
    r"\bdividend\b",
    r"\binterest\s+(?:payout|credited)\b.{0,40}\b(?:debenture|bond|ncd)\b",
)
IPO_PATTERNS = (
    r"\b(?:asba|ipo)\b.{0,60}\b(?:allot|allotted|allotment|blocked|applied|application|refund|unblock)\b",
    r"\b(?:allotment|allotted)\b.{0,40}\b(?:shares?|ipo)\b",
)
CORPORATE_ACTION_PATTERNS = (
    r"\bbonus\s+(?:shares?|issue)\b",
    r"\b(?:stock|share)\s+split\b|\bsub-?division\b",
    r"\brights\s+(?:issue|entitlement)\b",
    r"\bbuy-?back\b",
    r"\bface\s+value\s+split\b",
)
UPCOMING_DEBIT_PATTERNS = (
    r"\bwill\s+be\s+(?:presented|debited|deducted|charged)\b",
    r"\b(?:e-?mandate|mandate|nach|auto\s*pay|autopay|standing\s+instruction)\b",
    r"\b(?:is|are)\s+(?:scheduled|due)\s+to\s+be\s+debited\b",
    r"\bpre-?debit\s+(?:notice|alert|intimation)\b",
)
BILL_DUE_PATTERNS = (
    r"\b(?:bill|statement)\b.{0,60}\b(?:due|generated|outstanding|payable)\b",
    r"\b(?:total|minimum)\s+(?:amount\s+)?due\b",
    r"\bdue\s+date\b",
    r"\bpayment\s+due\b",
    # LIC / insurance renewals: "Premium of Rs 15,340 is due on 28-03-25".
    # The `due` requirement keeps "premium debited from a/c" in the ledger.
    r"\bpremium\b.{0,50}\b(?:due|payable)\b",
)

#: Words that prove money actually moved. An offer can mention "credit" or
#: "limit" without any of these; a real transaction almost always has one.
STRONG_MOVEMENT = re.compile(
    r"\b(?:debited|credited|withdrawn|withdrawal|spent|received|deposited|"
    r"transferred|refunded|reversed)\b",
    re.IGNORECASE,
)

#: Telecom recharge confirmations have no debit verb: "Recharge of Rs 349
#: successful". FASTag/wallet/UPI-Lite loads are matched earlier as loads.
_RECHARGE_DONE = re.compile(r"\b(?:successful|success|completed|done|recharged)\b", re.IGNORECASE)
VOUCHER_PATTERNS = (
    r"\b(?:voucher|gift\s*card|e-?gift|coupon)\b",
)
VOUCHER_REJECT = re.compile(
    r"\b(?:win|winner|shop\s+now|buy\s+now|book\s+now|upto|up\s+to|chance\s+to|stand\s+a\s+chance|"
    r"grab|hurry|limited\s+period|apply\s+now)\b",
    re.IGNORECASE,
)
#: "to A/c XX7788 (self transfer)", "to your own account", "self transfer to".
#: The intent existed from day one but nothing produced it, so an own-account
#: move was counted as spending.
SELF_TRANSFER_PATTERNS = (
    r"\bself\s+transfer\b",
    r"\bto\s+(?:your\s+)?own\s+(?:a/c|account|ac)\b",
    r"\btransfer\s+within\s+(?:your\s+)?(?:own\s+)?accounts?\b",
    r"\bbetween\s+your\s+(?:own\s+)?accounts?\b",
)

INVESTMENT_PATTERNS = (
    r"\b(?:sip|systematic\s+investment|mutual\s+fund)\b",
    r"\b(?:units?\s+(?:allotted|purchased|credited)|nav\s+of|folio)\b",
    r"\b(?:ipo|asba)\b",
    r"\b(?:demat|cdsl|nsdl)\b",
    r"\b(?:dividend|bonus\s+shares|stock\s+split)\b",
)
#: An AWB or courier docket makes it a parcel, whatever else the text says.
TRACKING_ONLY = re.compile(r"\b(?:awb|docket|consignment|tracking\s+(?:id|no))\b", re.IGNORECASE)

GOVT_PATTERNS = (
    r"\b(?:e-?challan|challan)\b",
    r"\b(?:income\s+tax|itr|tds|form\s+16)\b",
    r"\b(?:epfo|pf\s+(?:credit|contribution)|uan)\b",
    r"\b(?:passport|police\s+verification)\b",
    r"\b(?:vahan|parivahan|rto)\b",
    r"\b(?:fastag\s+(?:blacklist|low\s+balance))\b",
    r"\b(?:pucc|pollution\s+certificate)\b",
)
UPSELL_PATTERNS = (
    r"\bpre-?approved\b",
    r"\b(?:personal|home|business|gold)\s+loan\b",
    r"\b(?:instant\s+)?loan\s+(?:of|up\s+to|offer)\b",
    r"\bconvert\b.{0,40}\bemi\b",
    r"\beasy\s*emi\b",
    r"\bemi\s+(?:option|available|on\s+your)\b",
    r"\bcredit\s+limit\s+(?:increase|enhancement|upgrade)\b",
    r"\bupgrade\s+your\s+(?:card|credit\s+card)\b",
    r"\b(?:insurance|term)\s+plan\b",
    r"\bapply\s+now\b",
    r"\b(?:avail|get)\s+(?:a\s+)?(?:loan|limit\s+increase)\b",
)
PROMO_PATTERNS = (
    r"\b(?:offer|discount|deal|sale|cashback|coupon|voucher)\b",
    r"\b\d{1,3}\s*%\s*off\b",
    r"\bunsubscribe\b",
    r"\bt\s*&\s*c\b",
    r"\b(?:win|won|free|gift)\b",
    r"\b(?:shop|buy|book|order)\s+now\b",
    r"\bclick\s+here\b",
)

DEBIT_VERBS = re.compile(
    r"\b(?:debited|debit|withdrawn|withdrawal|spent|paid|purchase[d]?|txn\s+of|transferred|"
    r"sent|deducted|charged|billed|dr\b)\b",
    re.IGNORECASE,
)
#: Card programmes write a spend as "your card has been used for Rs X at SHOP"
#: (Citibank, Amex, HSBC). Kept separate from :data:`DEBIT_VERBS` and gated on
#: the word "card", because "Rs 5,000 has been used for a package tour" in a
#: marketing message is not a movement this ledger can vouch for.
_CARD_USE = re.compile(r"\bused\s+(?:for|at)\b", re.IGNORECASE)
_CARD_WORD = re.compile(r"\bcard\b", re.IGNORECASE)


def _debit_signal(text: str) -> Optional[re.Match[str]]:
    """A debit verb, or a card-use sentence that is about a card."""
    match = DEBIT_VERBS.search(text)
    if match is not None:
        return match
    if _CARD_WORD.search(text):
        return _CARD_USE.search(text)
    return None
CREDIT_VERBS = re.compile(
    r"\b(?:credited|credit|received|refunded|reversed|deposited|salary|cashback|cr\b)\b",
    re.IGNORECASE,
)
#: Something moved *and is finished*. "Rs 5,000 credited to A/c" is money;
#: "OTP for a transfer of Rs 5,000" is not, even though both name an amount.
COMPLETED_MOVEMENT = re.compile(
    r"\b(?:has\s+been|have\s+been|was|were|been)?\s*"
    r"(?:debited|credited|withdrawn|deposited|deducted|transferred|paid|received|reversed|refunded)\b",
    re.IGNORECASE,
)
_FUTURE_MOVEMENT = re.compile(
    r"\b(?:will|would|shall|is\s+going\s+to)\s+be\s+"
    r"(?:debited|deducted|charged|presented|paid)\b",
    re.IGNORECASE,
)
_ACCOUNT_MARKER = re.compile(r"\b(?:a/c|acct|account|xx\d{4}|\*{2,}\d{4}|card\s+\S*\d{4})\b", re.IGNORECASE)
_DECLINED = re.compile(
    r"\b(?:declined|failed|unsuccessful|not\s+processed|could\s+not\s+be\s+processed|rejected)\b",
    re.IGNORECASE,
)


_BOOKING_LANGUAGE = re.compile(r"\b(?:booked|booking\s+confirmed|ticket|reservation)\b", re.IGNORECASE)


def _money_already_moved(lower: str, amount: object, pnr: Optional[str] = None) -> bool:
    """True when the message reports a *finished* movement of money.

    Used to keep money rules ahead of travel/logistics/OTP: a delivery notice
    that also says "Rs 250 debited to SWIGGY" is a payment, and a filter that
    files it as logistics silently drops ₹250 from the ledger.

    Two exceptions, both deliberate:

    * a future movement ("will be debited") has not happened yet;
    * a booking confirmation that names a PNR is the ticket record. Its fare was
      charged by the payment message, and treating it as spending here would
      double count the trip.
    """
    if amount is None:
        return False
    if _FUTURE_MOVEMENT.search(lower):
        return False
    if not COMPLETED_MOVEMENT.search(lower):
        return False
    if _ACCOUNT_MARKER.search(lower):
        return True
    return not (pnr and _BOOKING_LANGUAGE.search(lower))

DOMAIN_BY_INTENT: dict[Intent, str] = {
    Intent.EXPENSE: "finance",
    Intent.INCOME: "finance",
    Intent.CC_BILL_PAYMENT: "finance",
    Intent.SELF_TRANSFER: "finance",
    Intent.REFUND_INITIATED: "finance",
    Intent.REFUND_CREDITED: "finance",
    Intent.CASH_WITHDRAWAL: "finance",
    Intent.INVESTMENT: "finance",
    Intent.INSTRUMENT_LOAD: "finance",
    Intent.FASTAG_TOLL: "finance",
    Intent.UPCOMING_DEBIT: "reminder",
    Intent.BILL_DUE: "reminder",
    Intent.OTP: "security",
    Intent.VOUCHER_ISSUED: "perks",
    Intent.LOGISTICS: "logistics",
    Intent.TRAVEL: "travel",
    Intent.TRADE: "finance",
    Intent.DIVIDEND: "finance",
    Intent.IPO: "finance",
    Intent.CORPORATE_ACTION: "finance",
    Intent.GOVT_NOTICE: "government",
    Intent.HEALTH: "health",
    Intent.BANK_UPSELL: "marketing",
    Intent.COMMERCIAL_PROMO: "marketing",
    Intent.PHISHING_SUSPECT: "security",
    Intent.UNKNOWN: "other",
}


def _any(patterns: tuple[str, ...], text: str) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def _fmt_inr(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"₹{value:,.2f}"


@dataclass
class _Ctx:
    text: str
    lower: str
    sender: SenderInfo
    forensic: forensics.ForensicReport
    amounts: list[extract.AmountHit]
    balance: Optional[float]
    credit_limit: Optional[float]
    total_due: Optional[float]
    minimum_due: Optional[float]
    last4: Optional[str]
    reference: Optional[str]
    rrn: Optional[str]
    otp: Optional[tuple[str, str]]
    handover: Optional[str]
    pnr: Optional[str]
    tracking: Optional[str]
    dates: list[extract.DateHit]
    now: datetime

    @property
    def amount(self) -> Optional[extract.AmountHit]:
        return self.amounts[0] if self.amounts else None


def _build_context(text: str, sender: SenderInfo, now: datetime) -> _Ctx:
    balance_hit = extract.find_balance(text)
    skip = (balance_hit[1],) if balance_hit else ()
    amounts = extract.find_amounts(text, skip_spans=skip)
    reference, rrn = extract.find_reference(text)
    total_due, minimum_due = extract.find_due_amounts(text)
    return _Ctx(
        text=text,
        lower=text.lower(),
        sender=sender,
        forensic=forensics.analyse(text, sender),
        amounts=amounts,
        balance=balance_hit[0] if balance_hit else None,
        credit_limit=extract.find_credit_limit(text),
        total_due=total_due,
        minimum_due=minimum_due,
        last4=extract.find_last4(text),
        reference=reference,
        rrn=rrn,
        otp=extract.find_otp(text),
        handover=extract.find_handover_code(text),
        pnr=extract.find_pnr(text),
        tracking=extract.find_tracking(text),
        dates=extract.find_dates(text, now),
        now=now,
    )


def _decide(ctx: _Ctx) -> tuple[Intent, float, list[str], list[str]]:
    """Return ``(intent, confidence, evidence, flags)``."""
    lower = ctx.lower
    evidence: list[str] = []
    flags: list[str] = []
    amount = ctx.amount

    # 1. Quarantine-grade phishing ----------------------------------------
    if ctx.forensic.quarantine:
        evidence.append(f"forensic score {ctx.forensic.score:.2f} (>= 0.70 quarantine)")
        evidence.extend(ctx.forensic.reasons[:4])
        return Intent.PHISHING_SUSPECT, min(0.99, 0.55 + ctx.forensic.score * 0.5), evidence, ["phishing_signals"]

    # 2. Credit-card bill payment (the classic double-count) ---------------
    if _any(CC_BILL_PATTERNS, lower):
        evidence.append("message reports a card bill payment")
        return Intent.CC_BILL_PAYMENT, 0.85, evidence, flags

    # 3. Refunds and reversals ---------------------------------------------
    if re.search(r"\brefund\b|\brevers(?:al|ed|e)\b", lower):
        if _any(REFUND_INITIATED_PATTERNS, lower):
            evidence.append("refund promised but not yet confirmed")
            return Intent.REFUND_INITIATED, 0.8, evidence, flags
        if _any(REFUND_CREDITED_PATTERNS, lower):
            evidence.append("refund credited")
            return Intent.REFUND_CREDITED, 0.85, evidence, flags
        # A reversal is money coming back, not new spending and not income: the
        # ₹500 debit may already be in the ledger from its own message, and
        # counting this one as a second expense (or as salary-like income)
        # bends both totals. Whether it already happened decides the intent:
        # "will be reversed" is a promise, "has been reversed" is a credit.
        if re.search(r"\brevers(?:al|ed|e)\b", lower):
            pending = re.search(
                r"\b(?:will|shall|being|would|expected)\b|\bwithin\s+\d+\s+(?:\w+\s+)?days?\b",
                lower,
            )
            flags.append("reversal_pending" if pending else "reversal")
            evidence.append(
                "reversal promised, not yet credited" if pending else "transaction reversed: money returned"
            )
            return (
                (Intent.REFUND_INITIATED, 0.72) if pending else (Intent.REFUND_CREDITED, 0.75)
            ) + (evidence, flags)

    # 3.5 Demat: dividends, IPO, corporate actions, order executions --------
    # These describe shares, not rupees. Checked before generic money language
    # because "Rs 120 dividend credited" is income, not spending, and "10 shares
    # of INFY bought" moves no money in this message at all.
    if _any(DIVIDEND_PATTERNS, lower):
        evidence.append("dividend language")
        return Intent.DIVIDEND, 0.88, evidence, flags
    ipo_hit = _any(IPO_PATTERNS, lower)
    if ipo_hit:
        evidence.append(f"IPO/ASBA language ({ipo_hit.strip()})")
        return Intent.IPO, 0.85, evidence, flags
    corp_hit = _any(CORPORATE_ACTION_PATTERNS, lower)
    if corp_hit:
        evidence.append(f"corporate action ({corp_hit.strip()})")
        return Intent.CORPORATE_ACTION, 0.85, evidence, flags
    trade_hit = _any(TRADE_PATTERNS, lower)
    # A SIP debit reads like an allotment ("units allotted") but the money really
    # did leave the bank account. When a debit verb names an account, this is a
    # bank message: it falls through to INVESTMENT so the rupees are excluded as
    # an investment rather than silently dropped as "no money in this message".
    money_in_bank = bool(DEBIT_VERBS.search(lower)) and bool(
        re.search(r"\b(?:a/c|acct|account|xx\d{4}|\*{2,}\d{4})\b", lower)
    )
    if trade_hit and not money_in_bank and not (ctx.balance is not None and DEBIT_VERBS.search(ctx.text)):
        evidence.append(f"trade language ({trade_hit.strip()})")
        return Intent.TRADE, 0.8, evidence, flags

    # 4. Travel and logistics (before OTP: handover codes are not auth OTPs) --
    # A ticket is a booking, a parcel is a delivery, and the two must not be
    # mixed: one is retained until the journey, the other is dead on arrival.
    money_moved = _money_already_moved(lower, amount, ctx.pnr)
    is_travel = _any(TRAVEL_PATTERNS, lower)
    if is_travel and not TRACKING_ONLY.search(lower) and not money_moved:
        mode_hit = next((mode for mode, pat in TRAVEL_MODE_PATTERNS if re.search(pat, lower)), "other")
        evidence.append(f"travel booking ({mode_hit})" + (f" / PNR {ctx.pnr}" if ctx.pnr else ""))
        return Intent.TRAVEL, 0.88 if ctx.pnr else 0.8, evidence, flags

    is_status = _any(LOGISTICS_PATTERNS, lower)
    if (ctx.pnr or ctx.tracking or is_status or ctx.handover) and not money_moved:
        handover_ok = bool(ctx.handover and DELIVERY_CONTEXT.search(ctx.text) and not AUTH_CONTEXT.search(ctx.text))
        if ctx.pnr or ctx.tracking or is_status or handover_ok:
            if ctx.handover and handover_ok:
                evidence.append(f"delivery handover code {ctx.handover}")
            evidence.append("logistics language" + (f" / PNR {ctx.pnr}" if ctx.pnr else ""))
            return Intent.LOGISTICS, 0.85 if (ctx.pnr or ctx.tracking) else 0.72, evidence, flags

    # 5. OTP ---------------------------------------------------------------
    if ctx.otp and not money_moved:
        evidence.append(f"OTP keyword '{ctx.otp[1]}' with a nearby code")
        return Intent.OTP, 0.9, evidence, flags
    if ctx.otp and money_moved:
        # "Rs 5,000 credited to A/c XX4092. Do not share OTP 482913." The money
        # really moved, so it belongs in the ledger; the OTP is still treated as
        # a secret (masked in storage, never sent anywhere).
        evidence.append("OTP warning alongside a completed money movement")
        flags.append("otp_present")

    # 6. FASTag ------------------------------------------------------------
    if re.search(r"\bfastag\b", lower):
        if re.search(r"\b(?:toll|plaza|deducted|debited|paid)\b", lower):
            evidence.append("FASTag toll deduction")
            return Intent.FASTAG_TOLL, 0.85, evidence, flags
        if re.search(r"\b(?:recharge|top\s*-?\s*up|loaded|credit)\b", lower):
            evidence.append("FASTag wallet load")
            return Intent.INSTRUMENT_LOAD, 0.82, evidence, flags

    # 7. Instrument loads (UPI Lite / wallet) ------------------------------
    if re.search(r"\bupi\s*lite\b", lower) and re.search(r"\b(?:top\s*-?\s*up|loaded|added|recharge)\b", lower):
        evidence.append("UPI Lite top-up")
        return Intent.INSTRUMENT_LOAD, 0.82, evidence, flags
    if re.search(r"\bwallet\b", lower) and re.search(r"\b(?:loaded|top\s*-?\s*up|recharged?|added)\b", lower):
        evidence.append("wallet load")
        return Intent.INSTRUMENT_LOAD, 0.75, evidence, flags

    # 8. ATM ---------------------------------------------------------------
    if re.search(r"\batm\b", lower) and re.search(r"\b(?:wdl|withdrawal|withdrawn|cash)\b", lower):
        evidence.append("ATM cash withdrawal")
        return Intent.CASH_WITHDRAWAL, 0.85, evidence, flags

    # 9. Investments -------------------------------------------------------
    invest = _any(INVESTMENT_PATTERNS, lower)
    if invest and (amount or re.search(r"\b(?:units?|nav|folio|allotment)\b", lower)):
        evidence.append(f"investment language ({invest.strip()})")
        return Intent.INVESTMENT, 0.75, evidence, flags
    if amount and re.search(r"\b(?:fund\s*transfer|money\s+added|add\s+money)\b", lower) and re.search(
        r"\b(?:zerodha|groww|upstox|angel\s*one|indmoney|kuvera|coin|mutual\s+fund|sip)\b", lower
    ):
        evidence.append("broker/scheme transfer language")
        return Intent.INVESTMENT, 0.72, evidence, flags

    # 10. Pre-debit notices / mandates ------------------------------------
    upcoming = _any(UPCOMING_DEBIT_PATTERNS, lower)
    if upcoming and not DEBIT_VERBS.search(lower.replace("will be debited", "").replace("will be deducted", "")):
        evidence.append(f"pre-debit language ({upcoming.strip()})")
        return Intent.UPCOMING_DEBIT, 0.8 if amount else 0.68, evidence, flags

    # 11. Bill due ---------------------------------------------------------
    bill = _any(BILL_DUE_PATTERNS, lower)
    # "Rs 3,499 spent at CROMA … Total due Rs 12,480" is a spend with a running
    # balance, not a bill. A movement verb outranks the due line.
    spend_sentence = DEBIT_VERBS.search(lower) and not re.search(r"\b(?:card|bill)\s+payment\b", lower)
    if bill and (amount or ctx.total_due or ctx.minimum_due) and not spend_sentence:
        evidence.append(f"bill/statement due language ({bill.strip()})")
        return Intent.BILL_DUE, 0.78, evidence, flags

    # 12. Vouchers ---------------------------------------------------------
    if _any(VOUCHER_PATTERNS, lower) and not VOUCHER_REJECT.search(ctx.text):
        code = extract.find_voucher_code(ctx.text)
        if code or re.search(r"\bexpi(?:re|res|ry)\b", lower):
            evidence.append("issued voucher with a code" if code else "issued voucher with an expiry")
            return Intent.VOUCHER_ISSUED, 0.85 if code else 0.7, evidence, flags

    # 13. Government -------------------------------------------------------
    gov = _any(GOVT_PATTERNS, lower)
    if gov:
        evidence.append(f"government/utility language ({gov.strip()})")
        return Intent.GOVT_NOTICE, 0.72, evidence, flags

    # 13.5 Telecom recharge ------------------------------------------------
    if (
        amount
        and (re.search(r"\brecharge\b|\btop\s*-?\s*up\b", lower))
        and _RECHARGE_DONE.search(lower)
        and not re.search(r"\b(?:fastag|wallet|upi\s*lite)\b", lower)
    ):
        evidence.append("telecom recharge confirmation")
        return Intent.EXPENSE, 0.78, evidence, flags

    # 13.6 Offers that talk about credit without moving money --------------
    # "Get a credit limit upgrade up to Rs 5,00,000 ... Apply now!" contains the
    # word "credit". It must never become income.
    upsell = _any(UPSELL_PATTERNS, lower)
    if upsell and not STRONG_MOVEMENT.search(lower):
        bank_like = ctx.sender.entity_kind in {EntityKind.BANK, EntityKind.FINTECH}
        evidence.append(f"lending/limit language ({upsell.strip()}) with no money movement")
        return (
            Intent.BANK_UPSELL if bank_like else Intent.COMMERCIAL_PROMO,
            0.8 if bank_like else 0.7,
            evidence,
            ["bank_marketing"] if bank_like else [],
        )

    # 13.7 Own-account moves ------------------------------------------------
    # A transfer between the user's own accounts is not spending; it is the same
    # rupee changing pocket. Checked before the generic debit rule.
    self_move = _any(SELF_TRANSFER_PATTERNS, lower)
    self_verb = DEBIT_VERBS.search(ctx.text)
    if self_move and self_verb:
        evidence.append(f"own-account transfer ({self_move.strip()}) with '{self_verb.group(0)}'")
        return Intent.SELF_TRANSFER, 0.85, evidence, flags

    # 14. Real money movement ---------------------------------------------
    debit = _debit_signal(ctx.text)
    # "Credit Card" and "credit limit" are nouns, not credits. Matching them as
    # a credit verb made every card payment read as ambiguous.
    credit_text = re.sub(r"credit\s+(?:card|limit)", " ", ctx.text, flags=re.IGNORECASE)
    credit = CREDIT_VERBS.search(credit_text)
    if amount:
        if debit and not credit:
            evidence.append(f"debit verb '{debit.group(0)}' with amount")
            return Intent.EXPENSE, 0.9 if ctx.sender.is_known_brand else 0.82, evidence, flags
        if credit and not debit:
            evidence.append(f"credit verb '{credit.group(0)}' with amount")
            return Intent.INCOME, 0.88 if ctx.sender.is_known_brand else 0.8, evidence, flags
        if debit and credit:
            if amount.direction == "credit":
                evidence.append("both debit and credit verbs; amount context reads as credit")
                return Intent.INCOME, 0.65, evidence, ["ambiguous_direction"]
            evidence.append("both debit and credit verbs; amount context reads as debit")
            return Intent.EXPENSE, 0.65, evidence, ["ambiguous_direction"]

    # 15. Bank upsell that also mentions a movement verb ------------------
    if upsell:
        bank_like = ctx.sender.entity_kind in {EntityKind.BANK, EntityKind.FINTECH}
        evidence.append(f"lending/upsell language ({upsell.strip()}) with no money movement")
        return (
            Intent.BANK_UPSELL if bank_like else Intent.COMMERCIAL_PROMO,
            0.8 if bank_like else 0.7,
            evidence,
            ["bank_marketing"] if bank_like else [],
        )

    if _any(PROMO_PATTERNS, lower):
        evidence.append("promotional language")
        return Intent.COMMERCIAL_PROMO, 0.7, evidence, []

    evidence.append("no rule matched")
    return Intent.UNKNOWN, 0.3, evidence, ["no_rule_matched"]


def _detect_brand(text: str) -> Optional[str]:
    upper = text.upper()
    best: tuple[int, str] | None = None
    for alias, entity in ALIAS_INDEX.items():
        if len(alias) < 4:
            continue
        if re.search(rf"(?<![A-Z0-9]){re.escape(alias)}(?![A-Z0-9])", upper):
            if best is None or len(alias) > best[0]:
                best = (len(alias), entity.name)
    return best[1] if best else None


def _detect_institution(text: str, sender: SenderInfo) -> Optional[Institution]:
    if sender.is_known_brand and sender.display_name:
        entity = ALIAS_INDEX.get(sender.display_name.upper())
        kind = entity.kind if entity else sender.entity_kind
        return Institution(name=sender.display_name, kind=kind)
    brand = _detect_brand(text)
    if brand:
        entity = ALIAS_INDEX.get(brand.upper())
        return Institution(name=brand, kind=entity.kind if entity else EntityKind.UNKNOWN)
    return None


def _rail(lower: str) -> str:
    if "fastag" in lower:
        return "fastag"
    if "upi lite" in lower:
        return "upi_lite"
    if re.search(r"\bupi\b|@[a-z]{3,}", lower):
        return "upi"
    if "credit card" in lower or "cc " in lower:
        return "credit_card"
    if re.search(r"\batm\b", lower):
        return "atm"
    if "nach" in lower or "mandate" in lower or "e-mandate" in lower:
        return "nach"
    if re.search(r"\b(?:neft|imps|rtgs)\b", lower):
        return "bank_transfer"
    if "wallet" in lower:
        return "wallet"
    return "unknown"


def _otp_purpose(lower: str) -> tuple[str, str]:
    if re.search(r"\b(?:mandate|autopay|auto\s*pay|e-?mandate|standing\s+instruction|si\b)\b", lower):
        return "mandate_registration", "high"
    if re.search(r"\b(?:beneficiary|payee|add\s+(?:a\s+)?new)\b", lower):
        return "beneficiary_addition", "high"
    if re.search(r"\b(?:delivery|dac|handover|parcel)\b", lower):
        return "delivery_handover", "low"
    if re.search(r"\b(?:login|log\s*in|netbanking|app|password|sign\s*in)\b", lower):
        return "login", "medium"
    if re.search(r"\b(?:payment|txn|transaction|purchase|transfer|swipe|pay)\b", lower):
        return "payment", "medium"
    return "unknown", "low"


#: Intents where a message date means "this happened on", not "this is due on".
_MOVEMENT_INTENTS = {
    Intent.EXPENSE,
    Intent.INCOME,
    Intent.SELF_TRANSFER,
    Intent.CC_BILL_PAYMENT,
    Intent.REFUND_CREDITED,
    Intent.REFUND_INITIATED,
    Intent.CASH_WITHDRAWAL,
    Intent.INSTRUMENT_LOAD,
    Intent.INVESTMENT,
    Intent.FASTAG_TOLL,
}


def _summary(intent: Intent, amount: Optional[float], merchant: Optional[Merchant], institution: Optional[Institution], extra: str = "") -> str:
    who = (merchant.clean if merchant and merchant.clean else None) or (institution.name if institution else None) or ""
    if intent == Intent.EXPENSE:
        return f"{_fmt_inr(amount)} spent{(' at ' + who) if who else ''}".strip()
    if intent == Intent.INCOME:
        return f"{_fmt_inr(amount)} received{(' from ' + who) if who else ''}".strip()
    if intent == Intent.CC_BILL_PAYMENT:
        return f"Card bill payment {_fmt_inr(amount)} (not counted as spend)".strip()
    if intent == Intent.SELF_TRANSFER:
        return f"Own-account transfer {_fmt_inr(amount)}"
    if intent == Intent.REFUND_INITIATED:
        return f"Refund {_fmt_inr(amount)} initiated{(' by ' + who) if who else ''} — tracking"
    if intent == Intent.REFUND_CREDITED:
        return f"Refund {_fmt_inr(amount)} credited{(' from ' + who) if who else ''}"
    if intent == Intent.INSTRUMENT_LOAD:
        return f"{_fmt_inr(amount)} loaded into a wallet/FASTag (not spend)"
    if intent == Intent.FASTAG_TOLL:
        return f"FASTag toll {_fmt_inr(amount)}"
    if intent == Intent.CASH_WITHDRAWAL:
        return f"ATM withdrawal {_fmt_inr(amount)}"
    if intent == Intent.INVESTMENT:
        return f"Investment event {_fmt_inr(amount)}".strip()
    if intent == Intent.UPCOMING_DEBIT:
        return f"Upcoming auto-debit {_fmt_inr(amount)} {extra}".strip()
    if intent == Intent.BILL_DUE:
        return f"Bill due {_fmt_inr(amount)} {extra}".strip()
    if intent == Intent.OTP:
        return f"OTP for {extra}".strip()
    if intent == Intent.VOUCHER_ISSUED:
        return f"Voucher {extra}".strip()
    if intent == Intent.TRAVEL:
        return f"Travel booking {extra}".strip()
    if intent == Intent.TRADE:
        return f"Trade {extra}".strip()
    if intent == Intent.DIVIDEND:
        return f"Dividend {_fmt_inr(amount)} {extra}".strip()
    if intent == Intent.IPO:
        return f"IPO update {extra}".strip()
    if intent == Intent.CORPORATE_ACTION:
        return f"Corporate action {extra}".strip()
    if intent == Intent.LOGISTICS:
        return f"Delivery/travel update {extra}".strip()
    if intent == Intent.GOVT_NOTICE:
        return f"Government notice {extra}".strip()
    if intent == Intent.BANK_UPSELL:
        return "Bank marketing (loan / EMI / card pitch)"
    if intent == Intent.COMMERCIAL_PROMO:
        return "Promotional message"
    if intent == Intent.PHISHING_SUSPECT:
        return "Suspected phishing — do not tap links"
    return "Unclassified message"


_ROUTE_RE = re.compile(
    r"\bfrom\s+(?P<origin>[A-Z][A-Za-z ]{2,20}?)\s+to\s+(?P<destination>[A-Z][A-Za-z ]{2,20}?)(?:\s+on|\s*[,.]|$)",
    re.IGNORECASE,
)
_ROUTE_ARROW_RE = re.compile(
    r"\b(?P<origin>[A-Z][A-Za-z ]{2,20}?)\s*(?:->|→|to)\s*(?P<destination>[A-Z][A-Za-z ]{2,20}?)\s+(?:on|at|bus|train|flight)\b",
    re.IGNORECASE,
)
#: "Bengaluru to Chennai, 2 passengers" — case-sensitive on purpose, because
#: "Rs 500 to VPA" lowercased would otherwise look like a route.
_ROUTE_PAIR_RE = re.compile(
    r"\b(?P<origin>[A-Z][a-z]{2,18})\s+to\s+(?P<destination>[A-Z][a-z]{2,18})\s*(?:,|\.|$)"
)
_PASSENGERS_RE = re.compile(r"\b(?P<count>\d)\s*(?:adult|passenger|pax|traveller)", re.IGNORECASE)


def _route(text: str) -> tuple[Optional[str], Optional[str]]:
    """Origin/destination, only when the message spells the pattern out.

    Deliberately narrow: guessing a city from a station code is how a Delhi
    traveller ends up with a Chennai itinerary.
    """
    for pattern in (_ROUTE_RE, _ROUTE_ARROW_RE, _ROUTE_PAIR_RE):
        match = pattern.search(text)
        if match:
            origin = match.group("origin").strip(" .")
            destination = match.group("destination").strip(" .")
            if origin.lower() != destination.lower():
                return origin, destination
    return None, None


def _passenger_count(lower: str) -> Optional[int]:
    match = _PASSENGERS_RE.search(lower)
    return int(match.group("count")) if match else None


def _trade_info(
    intent: "Intent",
    text: str,
    lower: str,
    merchant: Optional["Merchant"],
    template_hit: Optional[templates.TemplateHit],
    amount_hit: Optional[extract.AmountHit],
    ctx: "_Ctx",
) -> "TradeInfo":
    """Describe the security. Never the rupees — those belong to another message."""
    scheme = extract.find_scheme(text)
    symbol = extract.find_symbol(text) or (merchant.raw if merchant and merchant.raw and merchant.raw.isupper() else None)
    if scheme:
        # A mutual-fund allotment names a scheme, not a ticker. Reporting
        # "PARAG PARIKH FLEXI CAP" as a symbol would break any later price lookup.
        symbol = None
    quantity = extract.find_quantity(text)
    price = None
    price_match = re.search(
        r"(?:@|\bat\b|\bnav(?:\s+of)?)\s*(?:Rs\.?\s*|INR\s*)?(?P<price>[\d,]+(?:\.\d{1,2})?)\s*(?:per\s+share)?",
        text,
        re.IGNORECASE,
    )
    if price_match:
        try:
            price = float(price_match.group("price").replace(",", ""))
        except ValueError:
            price = None
    if template_hit is not None:
        if not quantity and template_hit.fields.get("quantity"):
            quantity = extract.find_quantity(template_hit.fields["quantity"] + " qty")
        if price is None and template_hit.fields.get("price"):
            try:
                price = float(template_hit.fields["price"].replace(",", ""))
            except ValueError:
                price = None
        symbol = symbol or (template_hit.merchant if template_hit.template_id.startswith("broker.") else None)

    if intent is Intent.DIVIDEND:
        kind = "dividend"
    elif intent is Intent.IPO:
        kind = "ipo_refund" if re.search(r"\brefund|unblock", lower) else ("ipo_allot" if re.search(r"\ballot", lower) else "ipo_apply")
    elif intent is Intent.CORPORATE_ACTION:
        kind = (
            "bonus"
            if "bonus" in lower
            else "split"
            if "split" in lower or "sub-division" in lower
            else "rights"
            if "rights" in lower
            else "buyback"
        )
    else:
        kind = "sell" if re.search(r"\b(?:sold|sell|selling|sale)\b", lower) else "buy"

    return TradeInfo(
        kind=kind,  # type: ignore[arg-type]
        symbol=symbol,
        isin=extract.find_isin(text),
        quantity=quantity,
        price=price,
        exchange="NSE" if re.search(r"\bnse\b", lower) else "BSE" if re.search(r"\bbse\b", lower) else None,
        broker=merchant.clean if merchant else None,
        scheme=scheme,
        folio=(m.group(1) if (m := re.search(r"\bfolio\b[^A-Za-z0-9]{0,4}([A-Z0-9]{3,12})", text, re.IGNORECASE)) else None),
    )


def _nearest_future(dates: list[extract.DateHit], now: datetime) -> Optional[datetime]:
    today = now.date()
    upcoming = [d.value for d in dates if d.value.date() >= today - timedelta(days=1)]
    if upcoming:
        return min(upcoming)
    return max((d.value for d in dates), default=None)


def analyse(
    body: str,
    sender_raw: str,
    now: Optional[datetime] = None,
    merchants: Optional[MerchantDictionary] = None,
    source: str = "manual",
) -> SmsEvent:
    """Turn one raw SMS into a fully explained :class:`SmsEvent`."""
    now = now or utcnow()
    text = extract.normalize(body)
    sender = extract.parse_sender(sender_raw)
    ctx = _build_context(text, sender, now)

    # Bank-specific templates fill gaps the generic extractors leave (and audit
    # which sentence shape was recognised).
    template_hit = templates.match(text, sender_raw)
    if template_hit is not None:
        if ctx.balance is None and template_hit.balance is not None:
            ctx.balance = template_hit.balance
        evidence_hint = f"matched {template_hit.bank} template {template_hit.template_id}"
    else:
        evidence_hint = None

    intent, score, evidence, flags = _decide(ctx)

    # An upsell inside a real transaction is not a separate event. Keep the
    # transaction, flag the ad, never create an EMI.
    if intent in {Intent.EXPENSE, Intent.INCOME, Intent.CC_BILL_PAYMENT} and _any(UPSELL_PATTERNS, ctx.lower):
        flags.append("embedded_upsell")
        evidence.append("message also contains a lending/EMI pitch; the transaction is still counted once")

    if ctx.forensic.suspicious and not ctx.forensic.quarantine:
        flags.append("suspicious_link")
        evidence.extend(f"unverified: {reason}" for reason in ctx.forensic.reasons[:3])
        score = min(score, 0.55)

    institution = _detect_institution(text, sender)
    last4 = ctx.last4 or (template_hit.last4 if template_hit else None)
    if institution and last4:
        digit_only = last4
        if re.search(r"\bcard\b", ctx.lower):
            institution.card_last4 = digit_only
        else:
            institution.account_last4 = digit_only

    merchant_raw = extract.find_merchant_raw(text) or (template_hit.merchant if template_hit else None)
    merchant: Optional[Merchant] = None
    if merchants is not None:
        match = merchants.resolve(merchant_raw or "") or merchants.resolve(text)
        if match:
            merchant = Merchant(raw=merchant_raw or match.raw, clean=match.clean, category=match.category, source=match.source, confidence=match.score)
            if merchant_raw and re.search(r"[A-Za-z]{3}", merchant_raw):
                merchants.learn(merchant_raw, match.clean, match.category, source="learned")
    if merchant is None and merchant_raw:
        merchant = Merchant(raw=merchant_raw, source="none", confidence=0.3)
        flags.append("merchant_unresolved")
    if merchant is None and institution is not None and intent == Intent.EXPENSE:
        # A carrier or a brand can be the payee in its own message: "Recharge of
        # Rs 349 successful" from AD-JIO-S is spending at Jio. Banks are
        # deliberately excluded: their SMS names the account, not a shop, so
        # "SMS charges" must not become a merchant called HDFC Bank.
        if institution.kind in {EntityKind.TELECOM, EntityKind.BRAND}:
            category = catalog.category_of_clean(institution.name) or "Uncategorized"
            merchant = Merchant(
                raw=institution.name,
                clean=institution.name,
                category=category,
                source="institution",
                confidence=0.6,
            )

    amount_hit = ctx.amount
    amount = None
    if amount_hit is None and template_hit is not None and template_hit.amount is not None:
        # The bank's own template found an amount the generic patterns missed.
        direction = "credit" if template_hit.kind == "credit" else "debit"
        amount = Amount(
            value=template_hit.amount,
            currency="INR",
            raw=str(template_hit.fields.get("amount", "")),
            direction=direction,  # type: ignore[arg-type]
            balance_after=ctx.balance,
        )
    elif amount_hit:
        direction = amount_hit.direction
        if intent == Intent.INCOME or intent == Intent.REFUND_CREDITED:
            direction = "credit"
        elif intent in {Intent.EXPENSE, Intent.FASTAG_TOLL, Intent.CASH_WITHDRAWAL}:
            direction = "debit"
        amount = Amount(
            value=amount_hit.value,
            currency=amount_hit.currency,
            raw=amount_hit.raw,
            direction=direction,
            balance_after=ctx.balance,
            credit_limit_after=ctx.credit_limit,
        )

    if amount is not None and amount.value <= 0:
        # "Rs 0.00 debited" is a system message, not a payment. Keeping it as a
        # zero-rupee expense just clutters the ledger.
        amount = None
        flags.append("zero_amount")

    if evidence_hint:
        evidence.append(evidence_hint)

    event = SmsEvent(
        received_at=now,
        source=source,  # type: ignore[arg-type]
        sender_raw=sender_raw,
        body=body,
        sender=sender,
        intent=intent,
        domain=DOMAIN_BY_INTENT.get(intent, "other"),
        amount=amount,
        institution=institution,
        merchant=merchant,
        links=ctx.forensic.links,
        evidence=evidence,
        flags=flags,
    )

    # A declined or failed transaction moved nothing. Saying so explicitly is
    # better than an "expense" of money that never left the account.
    if (
        event.intent in {Intent.EXPENSE, Intent.UNKNOWN}
        and _DECLINED.search(ctx.lower)
        and not re.search(r"\b(?:reversed|refunded|declined\s+fee)\b", ctx.lower)
    ):
        event.evidence.append("transaction was declined: no money moved")
        event.flags.append("declined_transaction")
        event.intent = Intent.UNKNOWN
        event.domain = DOMAIN_BY_INTENT.get(Intent.UNKNOWN, "other")
        event.amount = None
        event.ledger = Ledger.for_intent(Intent.UNKNOWN)
        event.confidence = Confidence.from_score(min(event.confidence.score, 0.45))

    # ---- intent-specific enrichment -------------------------------------
    extra = ""
    if intent == Intent.OTP and ctx.otp:
        purpose, risk = _otp_purpose(ctx.lower)
        service = _detect_brand(text) or (institution.name if institution else None)
        event.otp = OtpInfo(
            code=ctx.otp[0],
            purpose=purpose,  # type: ignore[arg-type]
            risk=risk,  # type: ignore[arg-type]
            service=service,
            associated_amount=amount_hit.value if amount_hit else None,
        )
        extra = purpose.replace("_", " ") + (f" — {service}" if service else "")

    elif intent == Intent.VOUCHER_ISSUED:
        code = extract.find_voucher_code(text)
        brand = (merchant.clean if merchant else None) or _detect_brand(text)
        event.voucher = VoucherInfo(
            brand=brand,
            value=amount_hit.value if amount_hit else None,
            code=code,
            pin=extract.find_pin(text),
            expiry=_nearest_future(ctx.dates, now),
        )
        extra = " ".join(
            part
            for part in [
                _fmt_inr(event.voucher.value),
                brand or "",
                f"expires {event.voucher.expiry:%d %b %Y}" if event.voucher.expiry else "",
            ]
            if part
        )

    elif intent in {Intent.UPCOMING_DEBIT, Intent.BILL_DUE}:
        biller = (institution.name if institution else None) or (merchant.clean if merchant else None) or _detect_brand(text)
        event.reminder = ReminderInfo(
            kind="upcoming_debit" if intent == Intent.UPCOMING_DEBIT else "bill_due",
            biller=biller,
            amount_due=ctx.total_due or (amount_hit.value if amount_hit else None),
            minimum_due=ctx.minimum_due,
            due_date=_nearest_future(ctx.dates, now),
            target_account_last4=ctx.last4,
            autopay=bool(re.search(r"\b(?:mandate|autopay|auto\s*pay|nach|standing\s+instruction)\b", ctx.lower)),
        )
        if event.reminder.due_date:
            extra = f"on {event.reminder.due_date:%d %b %Y}"
        if biller:
            extra = f"{biller} {extra}".strip()

    elif intent == Intent.TRAVEL:
        mode = next((m for m, pat in TRAVEL_MODE_PATTERNS if re.search(pat, ctx.lower)), "other")
        provider = next((name for name, pat in TRAVEL_PROVIDERS if re.search(pat, ctx.lower)), None)
        origin, destination = _route(text)
        travel_status = "confirmed"
        for keyword, label in (
            ("cancelled", "cancelled"),
            ("canceled", "cancelled"),
            ("delayed", "delayed"),
            ("late by", "delayed"),
            ("rescheduled", "rescheduled"),
            ("diverted", "diverted"),
            ("chart prepared", "chart_prepared"),
            ("waitlist", "waitlisted"),
            ("wl", "waitlisted"),
            ("confirmed", "confirmed"),
        ):
            if re.search(rf"\b{re.escape(keyword)}\b", ctx.lower):
                travel_status = label
                break
        event.logistics = LogisticsInfo(
            mode=mode,  # type: ignore[arg-type]
            status=travel_status,
            provider=provider or (merchant.clean if merchant else None),
            pnr=ctx.pnr,
            journey_number=extract.find_journey(text),
            seat=extract.find_seat(text),
            origin=origin,
            destination=destination,
            passengers=_passenger_count(ctx.lower),
            event_at=_nearest_future(ctx.dates, now),
        )
        bits = [
            event.logistics.provider or "",
            f"PNR {ctx.pnr}" if ctx.pnr else "",
            f"{origin} to {destination}" if origin and destination else "",
        ]
        extra = " ".join(b for b in bits if b)

    elif intent in {Intent.TRADE, Intent.DIVIDEND, Intent.IPO, Intent.CORPORATE_ACTION}:
        event.trade = _trade_info(intent, text, ctx.lower, merchant, template_hit, amount_hit, ctx)
        trade = event.trade
        # A trade confirmation quotes a price, not a payment. When the only rupee
        # figure in the message is the price ("NAV Rs 75.20", "@ Rs 1,450"), the
        # money leg is missing and inventing an amount would double-count it.
        if (
            intent is Intent.TRADE
            and event.amount is not None
            and trade.price is not None
            and abs(event.amount.value - trade.price) < 0.005
        ):
            event.amount = None
            event.evidence.append("only rupee figure was the per-unit price, not a payment")
        name = trade.symbol or trade.scheme or ""
        bits = [
            f"{trade.quantity:g} {name}".strip() if trade.quantity else name,
            f"@ ₹{trade.price:,.2f}" if trade.price else "",
            trade.broker or "",
        ]
        extra = " ".join(b for b in bits if b)

    elif intent == Intent.LOGISTICS:
        mode = "parcel"
        if re.search(r"\b(?:train|irctc|coach|berth|pnr)\b", ctx.lower):
            mode = "train"
        elif re.search(r"\b(?:flight|boarding|gate)\b", ctx.lower):
            mode = "flight"
        elif re.search(r"\b(?:blinkit|zepto|instamart|swiggy|quick\s+commerce)\b", ctx.lower):
            mode = "quick_commerce"
        elif re.search(r"\b(?:cab|ride|driver|ola|uber|rapido)\b", ctx.lower):
            mode = "cab"
        status = None
        for keyword, label in (
            ("out for delivery", "out_for_delivery"),
            ("delivered", "delivered"),
            ("shipped", "shipped"),
            ("dispatched", "dispatched"),
            ("in transit", "in_transit"),
            ("chart prepared", "chart_prepared"),
            ("ready for pickup", "ready_for_pickup"),
            ("delayed", "delayed"),
            ("cancelled", "cancelled"),
        ):
            if keyword in ctx.lower:
                status = label
                break
        event.logistics = LogisticsInfo(
            mode=mode,  # type: ignore[arg-type]
            status=status,
            provider=(merchant.clean if merchant else None) or _detect_brand(text),
            pnr=ctx.pnr,
            tracking_id=ctx.tracking,
            journey_number=extract.find_journey(text),
            seat=extract.find_seat(text),
            event_at=_nearest_future(ctx.dates, now),
            handover_code=ctx.handover,
        )
        bits = [event.logistics.provider or "", event.logistics.status or "", f"PNR {ctx.pnr}" if ctx.pnr else ""]
        extra = " ".join(b for b in bits if b)

    elif intent == Intent.GOVT_NOTICE:
        kind = "other"
        if "challan" in ctx.lower:
            kind = "challan"
        elif re.search(r"\b(?:income\s+tax|itr|tds)\b", ctx.lower):
            kind = "tax"
        elif "epfo" in ctx.lower or re.search(r"\bpf\b", ctx.lower):
            kind = "epfo"
        elif "passport" in ctx.lower:
            kind = "passport"
        elif re.search(r"\b(?:vahan|rto|pucc|fastag)\b", ctx.lower):
            kind = "vehicle"
        event.gov = GovInfo(
            kind=kind,  # type: ignore[arg-type]
            reference=ctx.reference,
            amount=amount_hit.value if amount_hit else None,
            vehicle_number=extract.find_vehicle(text),
            due_date=_nearest_future(ctx.dates, now),
        )
        extra = kind

    event.ledger = Ledger.for_intent(intent)
    if (
        event.amount is not None
        and event.amount.currency.upper() != "INR"
        and event.ledger.treatment in {LedgerTreatment.EXPENSE, LedgerTreatment.INCOME}
    ):
        # A foreign-currency swipe is real spending, but the rupee figure is not
        # in the message. Keeping the face value would put ₹12.99 in the ledger
        # for a $12.99 charge.
        event.ledger = Ledger(
            treatment=LedgerTreatment.FOREIGN_CURRENCY,
            counts_toward_spend=False,
            counts_as_income=False,
            reduces_spend=False,
            reason=(
                f"amount is in {event.amount.currency}; the rupee value is unknown until "
                "the bank posts the conversion"
            ),
        )
        event.flags.append("foreign_currency")
        flags.append("foreign_currency")
        evidence.append(f"foreign-currency amount ({event.amount.currency}); kept out of rupee totals")
    normalised = normalize_text(body)
    event.analysis = {
        # Which Indian scripts the message used, and how many phrases we had to
        # translate before the rules could read it. Recording this is what makes
        # multilingual support auditable instead of aspirational.
        "scripts": normalised.scripts,
        "digit_scripts": normalised.digit_scripts,
        "translations": normalised.translations,
        "multilingual": normalised.is_multilingual,
        "template": template_hit.as_dict() if template_hit is not None else None,
        "rail": _rail(ctx.lower),
        "reference": ctx.reference,
        "upi_rrn": ctx.rrn,
        "balance_after": ctx.balance,
        "credit_limit_after": ctx.credit_limit,
        "total_due": ctx.total_due,
        "minimum_due": ctx.minimum_due,
        "claimed_brands": ctx.forensic.claimed_brands,
        "forensic_score": ctx.forensic.score,
        "forensic_reasons": ctx.forensic.reasons,
    }
    event.summary = _summary(intent, amount_hit.value if amount_hit else None, merchant, institution, extra)

    route = ROUTING[intent]
    event.routing = Routing(
        apple_action=route.action,
        apple_subaction=effective_subaction(route.subaction),
        retention=route.retention,
        # A quarantined junk message is safe to clear; a merely suspicious
        # message that we allowed is not.
        safe_to_bulk_delete=route.safe_to_bulk_delete
        and (ctx.forensic.quarantine or not ctx.forensic.suspicious),
        rationale=route.rationale,
    )
    event.confidence = Confidence.from_score(score)
    if event.ledger.treatment is LedgerTreatment.FOREIGN_CURRENCY:
        event.confidence = Confidence(
            score=min(score, 0.45),
            band="low",
            needs_review=True,
        )
    event.dedup = DedupInfo()

    redaction = redact(
        text,
        otp=event.otp.code if event.otp else None,
        codes=tuple(c for c in [event.voucher.code if event.voucher else None, event.voucher.pin if event.voucher else None] if c),
    )
    event.redacted_body = redaction.text
    event.analysis["redaction"] = redaction.replaced

    # The bank's own date, not the delivery time. A message that arrives after
    # midnight ("... on 28-02-25") belongs to February, and weekly/monthly
    # totals must agree with the statement. Only movement intents get one:
    # a bill's due date is not the day it was paid.
    if intent in _MOVEMENT_INTENTS and ctx.dates:
        past = [hit.value for hit in ctx.dates if hit.value.date() <= now.date()]
        if past:
            event.analysis["posted_on"] = max(past).date().isoformat()
    return event
