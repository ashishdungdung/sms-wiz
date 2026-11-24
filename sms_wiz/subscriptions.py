"""Recurring payments: mandates, subscriptions, EMIs, and what is due next.

A subscription is not a new message type — it is a *pattern* over messages the
user already receives:

* pre-debit notices (``upcoming_debit``) and bill reminders (``bill_due``) give
  an exact due date and amount;
* repeated expenses to the same merchant give the cadence;
* the same merchant with a different amount gives a price change, which is the
  thing people actually want to know ("Netflix went from ₹649 to ₹799").

Cadence inference is deliberately conservative:

* one interval (two occurrences) is **not** a cadence — two grocery runs a week
  apart are not a subscription, and calling them one would inflate the
  "committed" total;
* two or more consistent intervals give weekly / monthly / quarterly / annual;
* a pre-debit or bill notice promotes a single occurrence to ``monthly`` with
  medium confidence, because that is what "will be debited on the 5th" means.

Anything else stays ``irregular`` with low confidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

from .analytics import IST, payload, received

CADENCES: tuple[tuple[str, int, int], ...] = (
    ("weekly", 6, 8),
    ("fortnightly", 13, 16),
    ("monthly", 26, 35),
    ("quarterly", 85, 95),
    ("half_yearly", 175, 190),
    ("annual", 350, 380),
)
CADENCE_DAYS = {"weekly": 7, "fortnightly": 14, "monthly": 30, "quarterly": 91, "half_yearly": 182, "annual": 365}
#: Multiplier to convert a cadence's amount into a monthly commitment.
MONTHLY_FACTOR = {
    "weekly": 52 / 12,
    "fortnightly": 26 / 12,
    "monthly": 1.0,
    "quarterly": 1 / 3,
    "half_yearly": 1 / 6,
    "annual": 1 / 12,
}


@dataclass
class Series:
    key: str
    name: str
    category: Optional[str] = None
    cadence: str = "irregular"
    confidence: float = 0.3
    amounts: list[tuple[date, float]] = field(default_factory=list)
    due_dates: list[date] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)
    #: Reference day for "next due". Injectable so the calendar is testable and
    #: the app can render a what-if date without touching the clock.
    today: Optional[date] = None

    @property
    def latest_amount(self) -> Optional[float]:
        return self.amounts[-1][1] if self.amounts else None

    @property
    def previous_amount(self) -> Optional[float]:
        return self.amounts[-2][1] if len(self.amounts) > 1 else None

    @property
    def next_due(self) -> Optional[date]:
        reference = self.today or date.today()
        future = [d for d in self.due_dates if d >= reference]
        if future:
            return min(future)
        if not self.amounts:
            return None
        last_day = max(d for d, _ in self.amounts)
        if self.cadence == "irregular":
            return None
        return last_day + timedelta(days=CADENCE_DAYS[self.cadence])

    def as_dict(self) -> dict[str, Any]:
        amount = self.latest_amount
        previous = self.previous_amount
        change = None
        if amount is not None and previous and previous > 0:
            delta = (amount - previous) / previous
            if abs(delta) >= 0.02:
                change = {
                    "from": round(previous, 2),
                    "to": round(amount, 2),
                    "pct": round(delta * 100, 1),
                }
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category,
            "cadence": self.cadence,
            "confidence": round(self.confidence, 2),
            "amount": round(amount, 2) if amount is not None else None,
            "monthly_commitment": (
                round(amount * MONTHLY_FACTOR[self.cadence], 2)
                if amount is not None and self.cadence in MONTHLY_FACTOR
                else None
            ),
            "next_due": self.next_due.isoformat() if self.next_due else None,
            "occurrences": len(self.amounts),
            "price_change": change,
            "sources": sorted(self.sources),
        }


def _key(row: dict[str, Any]) -> str:
    """Name plus account: two Bajaj EMIs on different accounts are two series."""
    name = row.get("merchant_clean") or row.get("institution") or "unknown"
    account = row.get("account_last4") or ""
    return f"{str(name).strip().upper()}:{account}" if account else str(name).strip().upper()


def _name(row: dict[str, Any]) -> str:
    return row.get("merchant_clean") or row.get("institution") or "Unknown biller"


def _is_recurring_candidate(row: dict[str, Any]) -> bool:
    if row.get("dedup_status") == "suppressed_duplicate":
        return False
    if row.get("intent") not in {"upcoming_debit", "bill_due", "expense"}:
        return False
    if _money(row) <= 0:
        return False
    data = payload(row)
    institution = data.get("institution") or {}
    # A credit-card statement is a card to settle, not a subscription. The card
    # view (cards.py) owns it; counting it here would double the "committed" total.
    if institution.get("card_last4"):
        return False
    # A bare bank debit ("SMS charges") is not a subscription either: we need a
    # payee, not the account that paid.
    if not row.get("merchant_clean") and str(institution.get("kind") or "") == "bank":
        return False
    return True


def _money(row: dict[str, Any]) -> float:
    value = row.get("amount_value")
    return float(value) if value is not None else 0.0


def _due_date(row: dict[str, Any]) -> Optional[date]:
    data = payload(row)
    reminder = data.get("reminder") or {}
    for key in ("due_date", "event_at"):
        raw = reminder.get(key)
        if raw:
            try:
                return datetime.fromisoformat(str(raw)).date()
            except ValueError:
                continue
    return received(row).date() if received(row) else None


def _infer_cadence(days: list[int]) -> tuple[str, float]:
    if len(days) < 2:
        return "irregular", 0.25
    gaps = sorted(days)
    median = gaps[len(gaps) // 2]
    for name, low, high in CADENCES:
        if low <= median <= high:
            # Spacing consistency raises confidence; one lucky gap does not.
            spread = max(gaps) - min(gaps)
            tolerance = (high - low) or 1
            confidence = 0.9 if spread <= tolerance else 0.7
            if len(gaps) >= 3:
                confidence = min(0.95, confidence + 0.05)
            return name, confidence
    return "irregular", 0.35


def build(rows: Iterable[dict[str, Any]], today: Optional[date] = None) -> list[Series]:
    """Group rows into recurring series, newest amounts last."""
    groups: dict[str, Series] = {}
    for row in rows:
        if not _is_recurring_candidate(row):
            continue
        key = _key(row)
        series = groups.setdefault(
            key, Series(key=key, name=_name(row), category=row.get("category"), today=today)
        )
        occurred = received(row)
        amount = _money(row)
        if occurred is not None and amount > 0:
            series.amounts.append((occurred.date(), amount))
        due = _due_date(row)
        if due is not None and row.get("intent") in {"upcoming_debit", "bill_due"}:
            series.due_dates.append(due)
        series.sources.add(str(row.get("intent")))

    out: list[Series] = []
    for series in groups.values():
        series.amounts.sort(key=lambda pair: pair[0])
        series.due_dates = sorted(set(series.due_dates))
        dates = [day for day, _ in series.amounts]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
        cadence, confidence = _infer_cadence(gaps)
        # One interval is enough when the amount is identical to the rupee: the
        # same subscription charged the same price ~30 days apart.
        if cadence == "irregular" and len(gaps) == 1 and len(series.amounts) == 2:
            first, second = series.amounts[0][1], series.amounts[1][1]
            same_amount = abs(first - second) <= max(1.0, first * 0.01)
            if same_amount and 26 <= gaps[0] <= 35:
                cadence, confidence = "monthly", 0.6
        # A pre-debit notice is strong evidence of a subscription even before a
        # second occurrence exists.
        if cadence == "irregular" and series.sources & {"upcoming_debit", "bill_due"}:
            cadence, confidence = "monthly", 0.6
        series.cadence = cadence
        series.confidence = confidence
        out.append(series)
    return sorted(_merge_duplicate_billers(out), key=lambda s: (s.next_due or date.max, s.name))


def _merge_duplicate_billers(series: list[Series]) -> list[Series]:
    """Fold a bill notice into the payment series it describes.

    An Airtel bill arrives twice a month in real life: "your bill of Rs 799 is
    due on the 5th" and "Rs 799 debited to AIRTEL". They are the same commitment,
    and the second one carries the account, so the keys differ (``AIRTEL`` vs
    ``AIRTEL:4092``). Counting both would report a ₹799 bill as ₹1,598 committed.

    Only a *notice-only* series (no money at all) is folded in. Two series that
    both carry payments are left alone even when the biller matches: paying the
    same biller from two accounts really is two commitments.
    """
    by_name: dict[str, list[Series]] = {}
    for item in series:
        by_name.setdefault(item.name.strip().upper(), []).append(item)

    kept: list[Series] = []
    for group in by_name.values():
        if len(group) == 1:
            kept.extend(group)
            continue
        # "Payment" means an actual expense row. A bill_due row carries an amount
        # too (the amount due), so amounts alone cannot tell the two apart.
        payers = [item for item in group if "expense" in item.sources]
        notices = [item for item in group if item not in payers]
        if not payers:
            kept.extend(group)
            continue
        target = max(payers, key=lambda s: len(s.amounts))
        for notice in notices:
            target.due_dates = sorted(set(target.due_dates) | set(notice.due_dates))
            target.sources |= notice.sources
        kept.extend(payers)
    return kept


def summary(rows: Iterable[dict[str, Any]], today: Optional[date] = None) -> dict[str, Any]:
    today = today or date.today()
    series = build(rows, today=today)
    active = [s for s in series if s.cadence != "irregular"]
    committed = sum(
        (s.latest_amount or 0) * MONTHLY_FACTOR[s.cadence] for s in active if s.cadence in MONTHLY_FACTOR
    )
    upcoming = [s.as_dict() for s in active if s.next_due and s.next_due <= today + timedelta(days=14)]
    return {
        "count": len(active),
        "irregular_count": len(series) - len(active),
        "monthly_commitment": round(committed, 2),
        "annualised": round(committed * 12, 2),
        "upcoming_14d": upcoming,
        "series": [s.as_dict() for s in series],
    }


def calendar(rows: Iterable[dict[str, Any]], days: int = 45, today: Optional[date] = None) -> list[dict[str, Any]]:
    """Dated list of expected payments, for the app's calendar view."""
    today = today or date.today()
    entries: list[dict[str, Any]] = []
    for series in build(rows, today=today):
        if series.cadence == "irregular" or series.latest_amount is None:
            continue
        step = CADENCE_DAYS[series.cadence]
        anchor = series.next_due or today
        # Walk forward month-ish buckets from the anchor inside the window.
        when = anchor
        guard = 0
        while when <= today + timedelta(days=days) and guard < 24:
            if when >= today:
                entries.append(
                    {
                        "date": when.isoformat(),
                        "name": series.name,
                        "amount": round(series.latest_amount, 2),
                        "cadence": series.cadence,
                        "confidence": round(series.confidence, 2),
                    }
                )
            when = when + timedelta(days=step)
            guard += 1
    return sorted(entries, key=lambda item: (item["date"], item["name"]))


def price_changes(rows: Iterable[dict[str, Any]], min_pct: float = 5.0) -> list[dict[str, Any]]:
    out = []
    for series in build(rows):
        amount, previous = series.latest_amount, series.previous_amount
        if amount is None or not previous:
            continue
        change = (amount - previous) / previous * 100
        if abs(change) >= min_pct:
            out.append(
                {
                    "name": series.name,
                    "from": round(previous, 2),
                    "to": round(amount, 2),
                    "change_pct": round(change, 1),
                    "direction": "increase" if change > 0 else "decrease",
                }
            )
    return sorted(out, key=lambda item: -abs(item["change_pct"]))
