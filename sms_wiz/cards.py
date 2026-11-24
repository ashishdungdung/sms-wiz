"""Credit-card statements and payments, reconciled per card.

The ledger rule for cards is simple: **a bill payment is a transfer, never new
spend** (the spending already happened at the merchant). But the *card* itself
still needs state:

    statement generated (bill_due) + payment (cc_bill_payment) = paid / partial / overdue

Cards are identified by last four digits, which is all an Indian SMS ever
carries. Two banks can share a last4, so the bank name is part of the key and an
ambiguous last4 is reported as ambiguous rather than merged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

from .analytics import IST, payload, received


@dataclass
class CardState:
    key: str
    last4: str
    bank: Optional[str] = None
    statements: list[dict[str, Any]] = field(default_factory=list)
    payments: list[dict[str, Any]] = field(default_factory=list)
    minimum_due: Optional[float] = None
    due_date: Optional[date] = None
    limit: Optional[float] = None
    spend_since_statement: float = 0.0

    @property
    def statement_amount(self) -> Optional[float]:
        return self.statements[-1]["amount"] if self.statements else None

    @property
    def paid_total(self) -> float:
        if not self.statements:
            return sum(p["amount"] for p in self.payments)
        cutoff = self.statements[-1]["date"]
        return sum(p["amount"] for p in self.payments if p["date"] >= cutoff)

    @property
    def outstanding(self) -> Optional[float]:
        amount = self.statement_amount
        if amount is None:
            return None
        return round(max(0.0, amount - self.paid_total), 2)

    def status(self, today: Optional[date] = None) -> str:
        today = today or date.today()
        amount = self.statement_amount
        if amount is None:
            return "payment_only" if self.payments else "no_statement"
        paid = self.paid_total
        if paid >= amount - 1:  # rupee rounding
            return "paid"
        if paid > 0:
            return "partial"
        if self.due_date and self.due_date < today:
            return "overdue"
        if self.due_date and (self.due_date - today).days <= 5:
            return "due_soon"
        return "unpaid"

    def as_dict(self, today: Optional[date] = None) -> dict[str, Any]:
        utilisation = None
        if self.limit and self.limit > 0 and self.statement_amount is not None:
            utilisation = round(min(1.0, self.statement_amount / self.limit), 3)
        return {
            "key": self.key,
            "last4": self.last4,
            "bank": self.bank,
            "statement_amount": round(self.statement_amount, 2) if self.statement_amount else None,
            "minimum_due": round(self.minimum_due, 2) if self.minimum_due else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "paid": round(self.paid_total, 2),
            "outstanding": self.outstanding,
            "status": self.status(today),
            "utilisation": utilisation,
            "statements": len(self.statements),
            "payments": len(self.payments),
            "spend_since_statement": round(self.spend_since_statement, 2),
        }


def _card_last4(row: dict[str, Any]) -> Optional[str]:
    data = payload(row)
    institution = data.get("institution") or {}
    return institution.get("card_last4") or None


#: Card issuers as they are named inside the message. Needed because the card
#: and the account that pays for it are usually different banks: "Payment of
#: Rs 12,480 towards your SBI Card XX4433" arrives from HDFC, and filing it under
#: HDFC split one card into two.
_CARD_ISSUERS: tuple[tuple[str, str], ...] = (
    (r"\bsbi\s*card\b|\bsbi\b", "SBI Card"),
    (r"\bicici\b", "ICICI Bank"),
    (r"\bhdfc\b", "HDFC Bank"),
    (r"\baxis\b", "Axis Bank"),
    (r"\bkotak\b", "Kotak Mahindra Bank"),
    (r"\bidfc\b", "IDFC FIRST Bank"),
    (r"\bindusind\b", "IndusInd Bank"),
    (r"\brbl\b", "RBL Bank"),
    (r"\byes\s*bank\b|\byes\b", "YES Bank"),
    (r"\bamex\b|\bamerican\s+express\b", "American Express"),
    (r"\bbank\s+of\s+baroda\b|\bbob\b", "Bank of Baroda"),
    (r"\bcanara\b", "Canara Bank"),
    (r"\bfederal\b", "Federal Bank"),
    (r"\bpnb\b|\bpunjab\s+national\b", "Punjab National Bank"),
    (r"\bau\s+(?:small\s+finance|bank)\b", "AU Small Finance Bank"),
    (r"\bstandard\s+chartered\b|\bsc\s+bank\b", "Standard Chartered"),
    (r"\bciti\b", "Citibank India"),
    (r"\bhsbc\b", "HSBC India"),
    (r"\bonecard\b", "OneCard"),
)


def _issuer(row: dict[str, Any]) -> Optional[str]:
    """Which bank owns the *card*, not which bank sent the SMS."""
    text = str(row.get("body") or "")
    for pattern, name in _CARD_ISSUERS:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    # No issuer named in the text: the sender is the card's own bank for
    # statements and swipes. A bill payment that names another bank was caught
    # above, which is the case that used to split one card into two.
    data = payload(row)
    institution = data.get("institution") or {}
    return institution.get("name") or row.get("institution")


def _amount(row: dict[str, Any]) -> float:
    value = row.get("amount_value")
    return float(value) if value is not None else 0.0


def _due_date(row: dict[str, Any]) -> Optional[date]:
    reminder = payload(row).get("reminder") or {}
    raw = reminder.get("due_date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).date()
    except ValueError:
        return None


def build(rows: Iterable[dict[str, Any]]) -> list[CardState]:
    cards: dict[str, CardState] = {}
    for row in rows:
        if row.get("dedup_status") == "suppressed_duplicate":
            continue
        last4 = _card_last4(row)
        if not last4:
            continue
        bank = _issuer(row)
        key = f"{(bank or 'card').upper()}:{last4}"
        card = cards.setdefault(key, CardState(key=key, last4=last4, bank=bank))
        when = received(row)
        day = when.date() if when else date.today()
        if row.get("intent") == "bill_due":
            card.statements.append({"date": day, "amount": _amount(row)})
            reminder = payload(row).get("reminder") or {}
            if reminder.get("minimum_due"):
                card.minimum_due = float(reminder["minimum_due"])
            due = _due_date(row)
            if due:
                card.due_date = due
        elif row.get("intent") == "cc_bill_payment":
            card.payments.append({"date": day, "amount": _amount(row)})
        elif row.get("intent") == "expense" and row.get("counts_toward_spend"):
            card.spend_since_statement += _amount(row)

    for card in cards.values():
        card.statements.sort(key=lambda item: item["date"])
        card.payments.sort(key=lambda item: item["date"])
    return sorted(cards.values(), key=lambda c: c.key)


def summary(rows: Iterable[dict[str, Any]], today: Optional[date] = None) -> dict[str, Any]:
    cards = build(rows)
    as_dicts = [c.as_dict(today) for c in cards]
    return {
        "cards": as_dicts,
        "total_outstanding": round(sum(c["outstanding"] or 0 for c in as_dicts), 2),
        "payments_total": round(sum(c["paid"] for c in as_dicts), 2),
        "needs_attention": [c for c in as_dicts if c["status"] in {"overdue", "due_soon", "partial"}],
    }
