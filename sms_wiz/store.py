"""Persistence helpers on top of :mod:`sms_wiz.db`.

Ledger invariants enforced here (not in SQL, so they stay readable):

* a superseded duplicate is stored but never summed;
* a "possible duplicate" is stored, summed, and flagged for a human;
* a card-bill payment, wallet load, own-account transfer, cash withdrawal and
  investment are stored but excluded from spend.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .models import Alert, Intent, LedgerTreatment, MonthSummary, SmsEvent

MONEY_TREATMENTS = {
    LedgerTreatment.EXPENSE.value,
    LedgerTreatment.INCOME.value,
    LedgerTreatment.REFUND.value,
    LedgerTreatment.INTERNAL_TRANSFER.value,
    LedgerTreatment.INSTRUMENT_LOAD.value,
    LedgerTreatment.CASH_WITHDRAWAL.value,
    LedgerTreatment.INVESTMENT.value,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_event(conn: sqlite3.Connection, event: SmsEvent) -> None:
    payload = event.model_dump(mode="json")
    conn.execute(
        """
        INSERT OR REPLACE INTO events (
            event_id, received_at, sender_raw, sender_name, entity_kind, intent, domain,
            amount_value, amount_currency, institution, account_last4, merchant_raw,
            merchant_clean, category, ledger_treatment, counts_toward_spend, apple_action,
            apple_subaction, retention, confidence, needs_review, dedup_status, duplicate_of,
            reference, body, payload, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            event.received_at.isoformat(),
            event.sender_raw,
            event.sender.display_name,
            event.sender.entity_kind.value,
            event.intent.value,
            event.domain,
            event.amount.value if event.amount else None,
            event.amount.currency if event.amount else None,
            event.institution.name if event.institution else None,
            (event.institution.account_last4 or event.institution.card_last4) if event.institution else None,
            event.merchant.raw if event.merchant else None,
            event.merchant.clean if event.merchant else None,
            event.merchant.category if event.merchant else None,
            event.ledger.treatment.value,
            1 if event.ledger.counts_toward_spend and event.dedup.status != "suppressed_duplicate" else 0,
            event.routing.apple_action.value,
            event.routing.apple_subaction.value,
            event.routing.retention.value,
            event.confidence.score,
            1 if event.confidence.needs_review else 0,
            event.dedup.status,
            event.dedup.duplicate_of,
            event.analysis.get("reference"),
            event.body,
            json.dumps(payload, ensure_ascii=False),
            _now(),
        ),
    )
    conn.commit()


def get_event(conn: sqlite3.Connection, event_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
    return dict(row) if row else None


def list_events(
    conn: sqlite3.Connection,
    limit: int = 50,
    month: Optional[str] = None,
    needs_review: Optional[bool] = None,
    intent: Optional[str] = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if month:
        clauses.append("substr(received_at, 1, 7) = ?")
        params.append(month)
    if needs_review is not None:
        clauses.append("needs_review = ?")
        params.append(1 if needs_review else 0)
    if intent:
        clauses.append("intent = ?")
        params.append(intent)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM events {where} ORDER BY received_at DESC LIMIT ?", params
    ).fetchall()
    return [_with_language(dict(row)) for row in rows]


def _with_language(row: dict[str, Any]) -> dict[str, Any]:
    """Surface the scripts a message was written in.

    The full analysis lives inside the stored ``payload``; the list view gets a
    decoded copy so the app can say "read from Devanagari" without downloading
    every event body.
    """
    try:
        analysis = json.loads(row.get("payload") or "{}").get("analysis") or {}
    except (TypeError, ValueError):  # pragma: no cover - defensive
        analysis = {}
    row["scripts"] = analysis.get("scripts", [])
    row["multilingual"] = bool(analysis.get("multilingual"))
    return row


def find_by_reference(conn: sqlite3.Connection, reference: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM events WHERE reference = ? AND dedup_status != 'suppressed_duplicate'"
        " ORDER BY created_at ASC LIMIT 1",
        (reference,),
    ).fetchone()
    return dict(row) if row else None


def find_possible_duplicate(
    conn: sqlite3.Connection,
    amount: float,
    day: str,
    last4: Optional[str],
    sender_raw: str,
) -> Optional[dict[str, Any]]:
    """Same amount, same day, different sender, overlapping instrument.

    This only *suggests* a link. Amount alone is never enough to merge two
    transactions — two coffees on the same day are not the same coffee.
    """
    if last4:
        row = conn.execute(
            "SELECT * FROM events WHERE amount_value = ? AND substr(received_at, 1, 10) = ?"
            " AND account_last4 = ? AND sender_raw != ? AND dedup_status != 'suppressed_duplicate'"
            " ORDER BY created_at ASC LIMIT 1",
            (amount, day, last4, sender_raw),
        ).fetchone()
    else:
        row = None
    return dict(row) if row else None


# --------------------------------------------------------------------------
# Balances
# --------------------------------------------------------------------------


def upsert_balance(conn: sqlite3.Connection, last4: str, institution: Optional[str], balance: float, at: str) -> None:
    conn.execute(
        "INSERT INTO balances (account_last4, institution, balance, updated_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(account_last4) DO UPDATE SET balance=excluded.balance,"
        " institution=COALESCE(excluded.institution, balances.institution), updated_at=excluded.updated_at",
        (last4, institution, balance, at),
    )
    conn.commit()


def get_balance(conn: sqlite3.Connection, last4: str) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM balances WHERE account_last4 = ?", (last4,)).fetchone()
    return dict(row) if row else None


def list_balances(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM balances ORDER BY account_last4")]


# --------------------------------------------------------------------------
# Refund watchdog
# --------------------------------------------------------------------------


def open_refund(
    conn: sqlite3.Connection,
    event: SmsEvent,
    deadline_days: int = 10,
) -> int:
    opened = event.received_at
    deadline = opened + timedelta(days=deadline_days)
    cur = conn.execute(
        "INSERT INTO refunds (merchant, amount, currency, instrument_last4, status, opened_at,"
        " deadline_at, opened_event_id, raw_sms) VALUES (?, ?, ?, ?, 'awaiting', ?, ?, ?, ?)",
        (
            event.merchant.clean if event.merchant else None,
            event.amount.value if event.amount else 0.0,
            event.amount.currency if event.amount else "INR",
            (event.institution.account_last4 or event.institution.card_last4) if event.institution else None,
            opened.isoformat(),
            deadline.isoformat(),
            event.event_id,
            event.body,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def find_open_refund(conn: sqlite3.Connection, amount: float, merchant: Optional[str]) -> tuple[Optional[dict], bool]:
    """Return ``(row, merchant_conflict)``.

    A refund is matched on amount **and** merchant (or on amount alone when the
    claim has no merchant). If the amount matches but the merchant does not, the
    claim is *not* closed — the caller gets a warning instead.
    """
    rows = conn.execute(
        "SELECT * FROM refunds WHERE status = 'awaiting' AND abs(amount - ?) < 0.01 ORDER BY opened_at ASC",
        (amount,),
    ).fetchall()
    if not rows:
        return None, False
    if merchant:
        for row in rows:
            if row["merchant"] and row["merchant"].strip().lower() == merchant.strip().lower():
                return dict(row), False
        for row in rows:
            if not row["merchant"]:
                return dict(row), False
        return None, True
    return dict(rows[0]), False


def resolve_refund(conn: sqlite3.Connection, refund_id: int, event_id: str, at: str) -> None:
    conn.execute(
        "UPDATE refunds SET status = 'credited', resolved_at = ?, matched_event_id = ? WHERE id = ?",
        (at, event_id, refund_id),
    )
    conn.commit()


def list_refunds(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM refunds ORDER BY opened_at DESC").fetchall()
    out = []
    now = datetime.now(timezone.utc)
    for row in rows:
        item = dict(row)
        if item.get("deadline_at") and item["status"] == "awaiting":
            item["overdue"] = datetime.fromisoformat(item["deadline_at"]) < now
        out.append(item)
    return out


# --------------------------------------------------------------------------
# Vouchers
# --------------------------------------------------------------------------


def save_voucher(conn: sqlite3.Connection, event: SmsEvent) -> bool:
    voucher = event.voucher
    if not voucher or not voucher.code:
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO vouchers (brand, value, currency, code, pin, expires_on, event_id, created_at, raw_sms)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            voucher.brand,
            voucher.value,
            event.amount.currency if event.amount else "INR",
            voucher.code,
            voucher.pin,
            voucher.expiry.date().isoformat() if voucher.expiry else None,
            event.event_id,
            _now(),
            event.body,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def list_vouchers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM vouchers ORDER BY (expires_on IS NULL), expires_on ASC").fetchall()
    today = datetime.now(timezone.utc).date().isoformat()
    out = []
    for row in rows:
        item = dict(row)
        item["expired"] = bool(item.get("expires_on") and item["expires_on"] < today)
        out.append(item)
    return out


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------


def save_alerts(conn: sqlite3.Connection, event_id: str, alerts: Iterable[Alert]) -> None:
    rows = [
        (event_id, alert.severity, alert.kind, alert.message, _now())
        for alert in alerts
    ]
    if rows:
        conn.executemany(
            "INSERT INTO alerts (event_id, severity, kind, message, created_at) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()


def list_alerts(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))]


# --------------------------------------------------------------------------
# Monthly rollup
# --------------------------------------------------------------------------


def summarise_month(conn: sqlite3.Connection, month: str) -> MonthSummary:
    rows = conn.execute(
        "SELECT intent, ledger_treatment, amount_value, category, counts_toward_spend,"
        " dedup_status, needs_review FROM events WHERE substr(received_at, 1, 7) = ?",
        (month,),
    ).fetchall()

    summary = MonthSummary(period=month)
    for row in rows:
        if row["dedup_status"] == "suppressed_duplicate":
            continue
        summary.event_count += 1
        if row["needs_review"]:
            summary.needs_review_count += 1
        amount = row["amount_value"] or 0.0
        treatment = row["ledger_treatment"]
        if row["counts_toward_spend"]:
            summary.spend_total += amount
            category = row["category"] or "Uncategorized"
            summary.by_category[category] = round(summary.by_category.get(category, 0.0) + amount, 2)
        elif treatment == LedgerTreatment.INCOME.value:
            summary.income_total += amount
        elif treatment == LedgerTreatment.REFUND.value:
            summary.refunds_total += amount
        elif treatment in MONEY_TREATMENTS:
            summary.excluded_total += amount
            summary.excluded_reasons[treatment] = round(summary.excluded_reasons.get(treatment, 0.0) + amount, 2)

    summary.spend_total = round(summary.spend_total, 2)
    summary.income_total = round(summary.income_total, 2)
    summary.refunds_total = round(summary.refunds_total, 2)
    summary.excluded_total = round(summary.excluded_total, 2)
    return summary


def spend_by_intent(conn: sqlite3.Connection, month: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT intent, COUNT(*) as n FROM events WHERE substr(received_at, 1, 7) = ?"
        " AND dedup_status != 'suppressed_duplicate' GROUP BY intent",
        (month,),
    ).fetchall()
    return {row["intent"]: row["n"] for row in rows}


__all__ = [
    "save_event",
    "get_event",
    "list_events",
    "find_by_reference",
    "find_possible_duplicate",
    "upsert_balance",
    "get_balance",
    "list_balances",
    "open_refund",
    "find_open_refund",
    "resolve_refund",
    "list_refunds",
    "save_voucher",
    "list_vouchers",
    "save_alerts",
    "list_alerts",
    "summarise_month",
    "spend_by_intent",
    "Intent",
]
