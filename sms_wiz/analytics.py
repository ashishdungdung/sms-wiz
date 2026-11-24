"""Spend analytics over stored events.

Everything here is a pure function over rows (``store.list_events`` output), so
the same numbers serve the API, the CLI, the tests and the iOS screen without a
second implementation.

Rules that keep the totals honest
---------------------------------
* A row counts only when ``ledger_treatment == "expense"`` *and* it is not a
  suppressed duplicate. Income, refunds, transfers, loads and investments are
  reported separately, never folded into spend.
* UPI is a *rail*, not a category: ``analysis.rail == "upi"`` counts an expense
  as a UPI transaction whether it was a P2P transfer or a merchant payment.
* Weeks are ISO weeks (Monday start), months are calendar months, both in IST,
  because that is how Indian statements and salary cycles line up.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .catalog import CATEGORY_BY_LABEL, category_slug, is_essential

IST = timezone(timedelta(hours=5, minutes=30))

#: Treatments that never enter spend but *are* real money leaving an account.
#: ``none`` is absent on purpose: a promotion or a due notice has no money in
#: it, so adding its amount to "excluded" would invent ₹5,00,000 of movement.
NON_SPEND_TREATMENTS = {"internal_transfer", "instrument_load", "cash_withdrawal", "investment"}


# --------------------------------------------------------------------------
# Row helpers
# --------------------------------------------------------------------------


def payload(row: dict[str, Any]) -> dict[str, Any]:
    """Decode the stored event payload, or ``{}`` if it is unreadable."""
    raw = row.get("payload")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return {}


def received(row: dict[str, Any]) -> Optional[datetime]:
    """When the money moved: the bank's date if the message carried one.

    Falls back to the delivery time, which is within a day for almost every
    Indian bank SMS. Using the posted date is what keeps a month-end salary and
    a 1st-of-month debit in the months the statement puts them in.
    """
    data = payload(row)
    posted = data.get("posted_on") or (data.get("analysis") or {}).get("posted_on")
    if posted:
        try:
            return datetime.fromisoformat(str(posted)).replace(tzinfo=IST)
        except ValueError:  # pragma: no cover - defensive
            pass
    stamp = row.get("received_at")
    if isinstance(stamp, datetime):
        return stamp.astimezone(IST) if stamp.tzinfo else stamp.replace(tzinfo=IST)
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp))
    except ValueError:  # pragma: no cover - defensive
        return None
    return parsed.astimezone(IST) if parsed.tzinfo else parsed.replace(tzinfo=IST)


def counted_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows that represent real spending: expense treatment, not a duplicate."""
    out = []
    for row in rows:
        if row.get("dedup_status") == "suppressed_duplicate":
            continue
        if row.get("ledger_treatment") != "expense":
            continue
        if not row.get("counts_toward_spend"):
            continue
        out.append(row)
    return out


def _money(row: dict[str, Any]) -> float:
    value = row.get("amount_value")
    return float(value) if value is not None else 0.0


def _label(row: dict[str, Any]) -> str:
    return row.get("category") or "Uncategorized"


def _merchant(row: dict[str, Any]) -> str:
    return row.get("merchant_clean") or "Unknown merchant"


def _rail(row: dict[str, Any]) -> str:
    return (payload(row).get("analysis") or {}).get("rail") or "other"


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------


@dataclass
class PeriodTotals:
    label: str
    start: str
    end: str
    spend: float = 0.0
    income: float = 0.0
    refunds: float = 0.0
    excluded: float = 0.0
    count: int = 0
    upi_count: int = 0
    upi_spend: float = 0.0
    card_payments: float = 0.0
    by_category: dict[str, float] = field(default_factory=dict)
    by_merchant: dict[str, float] = field(default_factory=dict)
    essential: float = 0.0
    discretionary: float = 0.0
    review_count: int = 0
    events: int = 0
    by_treatment: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        total = self.spend or 1.0
        return {
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "spend": round(self.spend, 2),
            "income": round(self.income, 2),
            "refunds": round(self.refunds, 2),
            "excluded": round(self.excluded, 2),
            "count": self.count,
            "upi": {"count": self.upi_count, "spend": round(self.upi_spend, 2)},
            "card_payments": round(self.card_payments, 2),
            "by_category": {k: round(v, 2) for k, v in sorted(self.by_category.items(), key=lambda kv: -kv[1])},
            "essential_share": round(self.essential / total, 3),
            "discretionary": round(self.discretionary, 2),
            "review_count": self.review_count,
            "events": self.events,
            # Why money was excluded, not just how much: a ₹18,450 card payment
            # and a ₹18,450 investment are very different exclusions.
            "by_treatment": {k: round(v, 2) for k, v in sorted(self.by_treatment.items(), key=lambda kv: -kv[1])},
        }


def _accumulate(totals: PeriodTotals, rows: Iterable[dict[str, Any]]) -> PeriodTotals:
    for row in rows:
        if row.get("dedup_status") == "suppressed_duplicate":
            continue
        treatment = row.get("ledger_treatment")
        amount = _money(row)
        totals.events += 1
        if row.get("needs_review"):
            totals.review_count += 1

        if treatment == "expense" and row.get("counts_toward_spend"):
            totals.spend += amount
            totals.count += 1
            category = _label(row)
            totals.by_category[category] = totals.by_category.get(category, 0.0) + amount
            totals.by_merchant[_merchant(row)] = totals.by_merchant.get(_merchant(row), 0.0) + amount
            if is_essential(category):
                totals.essential += amount
            else:
                totals.discretionary += amount
            if _rail(row) == "upi":
                totals.upi_count += 1
                totals.upi_spend += amount
        elif treatment == "income":
            totals.income += amount
        elif treatment == "refund":
            totals.refunds += amount
        elif treatment == "internal_transfer":
            totals.excluded += amount
            totals.by_treatment["internal_transfer"] = totals.by_treatment.get("internal_transfer", 0.0) + amount
            if row.get("intent") == "cc_bill_payment":
                totals.card_payments += amount
        elif treatment in NON_SPEND_TREATMENTS:
            totals.excluded += amount
            key = str(treatment)
            totals.by_treatment[key] = totals.by_treatment.get(key, 0.0) + amount
    return totals


def month_totals(rows: Iterable[dict[str, Any]], month: str) -> PeriodTotals:
    """``month`` is ``YYYY-MM`` in IST."""
    selected = [r for r in rows if (received(r) and received(r).strftime("%Y-%m")) == month]  # type: ignore[union-attr]
    totals = PeriodTotals(label=month, start=f"{month}-01", end=f"{month}-31")
    return _accumulate(totals, selected)


def week_totals(rows: Iterable[dict[str, Any]], any_day: date) -> PeriodTotals:
    """ISO week containing ``any_day`` (Monday–Sunday, IST)."""
    start = any_day - timedelta(days=any_day.weekday())
    end = start + timedelta(days=6)
    label = f"{start.isocalendar().year}-W{start.isocalendar().week:02d}"
    selected = [
        r
        for r in rows
        if (d := received(r)) is not None and start <= d.date() <= end
    ]
    totals = PeriodTotals(label=label, start=start.isoformat(), end=end.isoformat())
    return _accumulate(totals, selected)


def weekly(rows: Iterable[dict[str, Any]], weeks: int = 8, today: Optional[date] = None) -> list[dict[str, Any]]:
    today = today or datetime.now(IST).date()
    rows = list(rows)
    out = []
    for offset in range(weeks - 1, -1, -1):
        day = today - timedelta(days=7 * offset)
        out.append(week_totals(rows, day).as_dict())
    return out


def monthly(rows: Iterable[dict[str, Any]], months: int = 6, today: Optional[date] = None) -> list[dict[str, Any]]:
    today = today or datetime.now(IST).date()
    rows = list(rows)
    out = []
    year, month = today.year, today.month
    for _ in range(months):
        key = f"{year:04d}-{month:02d}"
        out.append(month_totals(rows, key).as_dict())
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(out))


def trend(series: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare the last two complete buckets in a monthly/weekly series."""
    if len(series) < 2:
        return {"direction": "unknown", "change_pct": 0.0}
    previous, current = series[-2]["spend"], series[-1]["spend"]
    if previous == 0:
        return {"direction": "up" if current else "flat", "change_pct": None, "previous": previous, "current": current}
    change = (current - previous) / previous
    return {
        "direction": "up" if change > 0.05 else "down" if change < -0.05 else "flat",
        "change_pct": round(change * 100, 1),
        "previous": round(previous, 2),
        "current": round(current, 2),
    }


# --------------------------------------------------------------------------
# Focused views
# --------------------------------------------------------------------------


def top_merchants(rows: Iterable[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for row in counted_rows(rows):
        name = _merchant(row)
        bucket = counts.setdefault(name, {"merchant": name, "spend": 0.0, "count": 0, "category": _label(row)})
        bucket["spend"] += _money(row)
        bucket["count"] += 1
    ranked = sorted(counts.values(), key=lambda b: -b["spend"])[:limit]
    for bucket in ranked:
        bucket["spend"] = round(bucket["spend"], 2)
    return ranked


def upi_stats(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """UPI is a rail: this counts merchant and P2P payments together."""
    upi_rows = [r for r in counted_rows(rows) if _rail(r) == "upi"]
    p2p = [r for r in upi_rows if _is_p2p(r)]
    merchant_rows = [r for r in upi_rows if not _is_p2p(r)]
    return {
        "transactions": len(upi_rows),
        "spend": round(sum(_money(r) for r in upi_rows), 2),
        "merchant_payments": len(merchant_rows),
        "person_to_person": len(p2p),
        "p2p_spend": round(sum(_money(r) for r in p2p), 2),
        "average": round(sum(_money(r) for r in upi_rows) / len(upi_rows), 2) if upi_rows else 0.0,
    }


def _is_p2p(row: dict[str, Any]) -> bool:
    data = payload(row)
    analysis = data.get("analysis") or {}
    if analysis.get("p2p") is True:
        return True
    merchant_name = (row.get("merchant_clean") or "").lower()
    return "p2p" in merchant_name or row.get("category") == "Transfers to People"


def card_payments(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every credit-card bill payment, which is a transfer, not spending."""
    out = []
    for row in rows:
        if row.get("dedup_status") == "suppressed_duplicate":
            continue
        if row.get("intent") != "cc_bill_payment":
            continue
        data = payload(row)
        institution = data.get("institution") or {}
        card4 = institution.get("card_last4") or institution.get("account_last4") or row.get("account_last4")
        out.append(
            {
                "date": (received(row) or datetime.now(IST)).date().isoformat(),
                "amount": round(_money(row), 2),
                "card_last4": card4,
                "bank": institution.get("name") or row.get("institution"),
                "event_id": row.get("event_id"),
            }
        )
    return sorted(out, key=lambda item: item["date"], reverse=True)


def category_breakdown(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in counted_rows(rows):
        label = _label(row)
        bucket = buckets.setdefault(
            label,
            {"category": label, "spend": 0.0, "count": 0, "essential": is_essential(label), "group": _group(label)},
        )
        bucket["spend"] += _money(row)
        bucket["count"] += 1
    total = sum(b["spend"] for b in buckets.values()) or 1.0
    out = []
    for bucket in sorted(buckets.values(), key=lambda b: -b["spend"]):
        bucket["spend"] = round(bucket["spend"], 2)
        bucket["share"] = round(bucket["spend"] / total, 3)
        out.append(bucket)
    return out


def _group(label: str) -> str:
    meta = CATEGORY_BY_LABEL.get(label)
    if meta is not None:
        return meta.group
    return "Other" if category_slug(label) == "uncategorized" else "Other"


def overview(rows: Iterable[dict[str, Any]], month: Optional[str] = None) -> dict[str, Any]:
    """One call for the app's home screen."""
    rows = list(rows)
    today = datetime.now(IST).date()
    month = month or today.strftime("%Y-%m")
    current = month_totals(rows, month).as_dict()
    current_month_rows = [r for r in rows if (d := received(r)) and d.strftime("%Y-%m") == month]
    return {
        "month": current,
        "weeks": weekly(rows, weeks=4, today=today),
        "trend": trend(monthly(rows, months=2, today=today)),
        "top_merchants": top_merchants(current_month_rows, limit=5),
        "upi": upi_stats(current_month_rows),
        "cards": card_payments(rows)[:10],
        "categories": category_breakdown(current_month_rows),
    }
