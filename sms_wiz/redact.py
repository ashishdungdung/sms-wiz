"""PII redaction before anything leaves the device / server.

Banking OTPs, voucher codes, card numbers, PAN, Aadhaar numbers and UPI
handles are the things an Indian SMS actually contains that must never be
shipped to a third-party LLM. Amounts and dates are kept: they are the signal
the model is being asked about, and they are already known to the bank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_LONG_DIGITS_RE = re.compile(r"\b\d{10,18}\b")
_PHONE_RE = re.compile(r"\b(?:\+?91[\s-]?)?[6-9]\d{9}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
_VPA_RE = re.compile(r"\b([A-Za-z0-9.\-_]{2,})@([A-Za-z][A-Za-z0-9.\-]{2,})\b")
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)


@dataclass
class Redaction:
    text: str
    replaced: dict[str, int] = field(default_factory=dict)

    def _note(self, kind: str) -> None:
        self.replaced[codes := kind] = self.replaced.get(codes, 0) + 1


def redact(text: str, otp: str | None = None, codes: tuple[str, ...] = ()) -> Redaction:
    result = Redaction(text=text)

    if otp:
        pattern = re.compile(rf"(?<!\d){re.escape(otp)}(?!\d)")
        text, count = pattern.subn("<otp>", text)
        if count:
            result.replaced["otp"] = count

    for code in codes:
        if not code or len(code) < 4:
            continue
        pattern = re.compile(re.escape(code), re.IGNORECASE)
        text, count = pattern.subn("<code>", text)
        if count:
            result.replaced["code"] = result.replaced.get("code", 0) + count

    text, count = _PAN_RE.subn("<pan>", text)
    if count:
        result.replaced["pan"] = count

    text, count = _LONG_DIGITS_RE.subn("<number>", text)
    if count:
        result.replaced["number"] = count

    text, count = _PHONE_RE.subn("<phone>", text)
    if count:
        result.replaced["phone"] = count

    text, count = _EMAIL_RE.subn("<email>", text)
    if count:
        result.replaced["email"] = count

    def _vpa(match: re.Match[str]) -> str:
        handle, domain = match.group(1), match.group(2)
        if domain.lower() in {"rs", "inr"} or len(handle) < 2:
            return match.group(0)
        return f"<vpa>@{domain}"

    text, count = _VPA_RE.subn(_vpa, text)
    if count:
        result.replaced["vpa"] = count

    text, count = _URL_RE.subn("<url>", text)
    if count:
        result.replaced["url"] = count

    result.text = text
    return result


# --------------------------------------------------------------------------
# Storage redaction
# --------------------------------------------------------------------------

#: 13-19 digits, optionally in 4-digit groups: card numbers and long account
#: numbers. Shorter digit runs are references (AWB, UTR) and must survive, or the
#: ledger loses the key it deduplicates on.
_LONG_CARD_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_CVV_RE = re.compile(r"\b(?:cvv|cvc|card\s+verification)\b[^0-9\n]{0,6}(\d{3,4})\b", re.IGNORECASE)
#: A bare "PIN 1234" is a PIN: bank templates write it that way ("Use CVV 987 and
#: PIN 1234"). "PIN code" is a postal code, so it is excluded explicitly.
_PIN_RE = re.compile(
    r"\b(?:atm\s+pin|upi\s+pin|login\s+pin|card\s+pin|pin)(?!\s*code)\b[^0-9\n]{0,6}(\d{4,6})\b",
    re.IGNORECASE,
)


def for_storage(text: str, otp: str | None = None) -> str:
    """Mask the things that must not sit in a database or a JSON payload.

    Everything here is *single-use*: an OTP, a CVV, a card number, a PAN, a PIN.
    The amount, the merchant, the VPA and the reference are kept verbatim on
    purpose — they are the audit trail, and an app that rewrites them cannot
    explain itself later. Unlike :func:`redact`, this is not for sending text to
    a model; it is for the local store and the API that serves it.
    """
    if not text:
        return text

    if otp:
        text = re.sub(rf"(?<!\d){re.escape(otp)}(?!\d)", "<otp>", text)

    text = _CVV_RE.sub("cvv <cvv>", text)
    text = _PIN_RE.sub(lambda m: m.group(0).replace(m.group(1), "<pin>"), text)
    text = _PAN_RE.sub("<pan>", text)

    def _card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 13:
            return match.group(0)
        return "*" * (len(digits) - 4) + digits[-4:]

    text = _LONG_CARD_RE.sub(_card, text)
    return text
