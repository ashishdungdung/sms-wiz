"""Persistence helpers on top of :mod:`sms_wiz.db`.

Ledger invariants enforced here (not in SQL, so they stay readable):

* a superseded duplicate is stored but never summed;
* a "possible duplicate" is stored, summed, and flagged for a human;
* a card-bill payment, wallet load, own-account transfer, cash withdrawal and
  investment are stored but excluded from spend.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .models import Alert, EntityKind, Intent, LedgerTreatment, MonthSummary, SmsEvent
from .redact import for_storage

MONEY_TREATMENTS = {
    LedgerTreatment.EXPENSE.value,
    LedgerTreatment.INCOME.value,
    LedgerTreatment.REFUND.value,
    LedgerTreatment.INTERNAL_TRANSFER.value,
    LedgerTreatment.INSTRUMENT_LOAD.value,
    LedgerTreatment.CASH_WITHDRAWAL.value,
    LedgerTreatment.INVESTMENT.value,
    LedgerTreatment.FOREIGN_CURRENCY.value,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def safe_body(event: SmsEvent) -> str:
    """The body as it is allowed to be stored: secrets masked, everything else kept.

    An OTP is single-use and worthless a minute later, but it is still a
    credential. The database is on disk and `/api/v1/events` serves these rows,
    so the code is replaced before either can see it. Amounts, merchants, VPAs
    and references stay verbatim: they are the audit trail.
    """
    return for_storage(event.body, event.otp.code if event.otp else None)


def save_event(conn: sqlite3.Connection, event: SmsEvent) -> None:
    payload = event.model_dump(mode="json")
    body = safe_body(event)
    if body != event.body:
        payload["body"] = body
        payload["body_redacted"] = True
    if payload.get("otp"):
        payload["otp"]["code"] = "<otp>"
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
            body,
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
    category: Optional[str] = None,
    min_amount: Optional[float] = None,
    offset: int = 0,
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
    if category:
        clauses.append("category = ?")
        params.append(category)
    if min_amount is not None:
        clauses.append("amount_value >= ?")
        params.append(min_amount)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, max(0, offset)])
    rows = conn.execute(
        f"SELECT * FROM events {where} ORDER BY received_at DESC LIMIT ? OFFSET ?", params
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


def find_identical(
    conn: sqlite3.Connection,
    sender_raw: str,
    body: str,
    day: str,
    otp: Optional[str] = None,
    within_minutes: int = 30,
    at: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """The same message from the same sender, recently.

    iOS re-delivers SMS through iCloud sync and providers, and the second copy is
    the same money. Exact text only — a paraphrase is a different message and
    goes through the possible-duplicate path instead.

    The window matters. Two byte-identical messages hours apart are usually two
    real transactions (two ₹100 coffees at the same shop, two ATM withdrawals on
    the same evening), and suppressing those silently understates spend. A
    re-delivery arrives within seconds or minutes, so only that is folded.
    """
    # Measured from the message's own timestamp. ``created_at`` would be the
    # import time, so a backfill of a year of SMS would fold every repeat
    # purchase into one.
    reference = at or _utcnow()
    since = (reference - timedelta(minutes=within_minutes)).isoformat()
    # Stored bodies are masked (see safe_body), so the comparison has to be made
    # on the masked form too, or a re-delivered card or OTP message would slip
    # through as new.
    masked = for_storage(body, otp)
    row = conn.execute(
        "SELECT * FROM events WHERE sender_raw = ? AND body = ? AND substr(received_at, 1, 10) = ?"
        " AND received_at >= ? AND dedup_status != 'suppressed_duplicate'"
        " ORDER BY received_at ASC LIMIT 1",
        (sender_raw, masked, day, since),
    ).fetchone()
    return dict(row) if row else None


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
    merchant: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Same amount, same day, different sender — plus a second signal.

    This only *suggests* a link; nothing is merged. Amount alone is never enough
    — two ₹100 coffees on the same day are not the same coffee. Two signals are:

    * the same instrument (``account_last4``): the same card reported by the bank
      and by the card programme;
    * the same merchant: a card spend at a shop and the bank debit that settles
      it arrive from two institutions with two different account numbers, and
      counting both inflates the month.

    The returned dict carries ``match_kind`` (``instrument`` or ``merchant``) so
    the caller can explain itself.
    """
    if last4:
        row = conn.execute(
            "SELECT * FROM events WHERE amount_value = ? AND substr(received_at, 1, 10) = ?"
            " AND account_last4 = ? AND sender_raw != ? AND dedup_status != 'suppressed_duplicate'"
            " ORDER BY created_at ASC LIMIT 1",
            (amount, day, last4, sender_raw),
        ).fetchone()
        if row:
            found = dict(row)
            found["match_kind"] = "instrument"
            return found
    if merchant:
        row = conn.execute(
            "SELECT * FROM events WHERE amount_value = ? AND substr(received_at, 1, 10) = ?"
            " AND merchant_clean = ? AND sender_raw != ? AND dedup_status != 'suppressed_duplicate'"
            " ORDER BY created_at ASC LIMIT 1",
            (amount, day, merchant, sender_raw),
        ).fetchone()
        if row:
            found = dict(row)
            found["match_kind"] = "merchant"
            return found
    return None


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


def refund_merchant(event: SmsEvent) -> Optional[str]:
    """The best merchant string available for a refund claim.

    A claim message usually names a merchant the dictionary has never heard of
    ("Refund initiated for ORDER 9912"). Storing ``None`` there left the tracker
    with nothing to show and nothing to match a credit against, so the raw name
    is kept when it at least looks like words rather than a reference number.
    """
    if event.merchant:
        clean = (event.merchant.clean or "").strip()
        if clean:
            return clean
        raw = (event.merchant.raw or "").strip()
        if len(re.findall(r"[A-Za-z]{3,}", raw)) >= 1 and not re.fullmatch(r"[\d\W]+", raw):
            return raw[:60]
    # "Refund of Rs 900 for your order has been initiated" from Amazon names no
    # merchant because the sender *is* the merchant. A bank or a telco is not:
    # "refund from HDFC Bank" would be a claim against the wrong party.
    sender = event.sender
    if sender.entity_kind in (EntityKind.BRAND, EntityKind.FINTECH) and sender.display_name:
        return sender.display_name
    return None


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
            refund_merchant(event),
            event.amount.value if event.amount else 0.0,
            event.amount.currency if event.amount else "INR",
            (event.institution.account_last4 or event.institution.card_last4) if event.institution else None,
            opened.isoformat(),
            deadline.isoformat(),
            event.event_id,
            safe_body(event),
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
    # A promise that never stated a figure ("refund initiated for order 9912")
    # is stored with amount 0. It can only be matched by merchant: an amount we
    # do not know cannot be compared, and any credit would otherwise close it.
    unknown = conn.execute(
        "SELECT * FROM refunds WHERE status = 'awaiting' AND amount = 0 ORDER BY opened_at ASC"
    ).fetchall()
    rows = list(rows) + list(unknown)
    if not rows:
        return None, False
    if merchant:
        wanted = merchant.strip().lower()
        for row in rows:
            stored = (row["merchant"] or "").strip().lower()
            if not stored:
                continue
            # "Amazon" and "Amazon Pay India" are the same shop; a claim stored
            # from a raw merchant string rarely matches character for character.
            if stored == wanted or (len(stored) >= 4 and stored in wanted) or (
                len(wanted) >= 4 and wanted in stored
            ):
                return dict(row), False
        for row in rows:
            if not row["merchant"] and row["amount"] > 0:
                return dict(row), False
        return None, True
    return dict(rows[0]), False


def find_covering_refund(
    conn: sqlite3.Connection,
    amount: float,
    merchant: Optional[str],
    days: int = 45,
    at: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """An open claim this credit is big enough to cover.

    Refunds frequently do not arrive at the claimed figure: a partial refund, or
    one credit for two returned orders (a ₹1,500 credit against a ₹500 claim).
    Exact-amount matching leaves that claim hanging forever and files the credit
    as "no claim", which is how a tracker stops being trusted.
    """
    # Measured from the credit's own timestamp, not the wall clock: importing a
    # months-old SMS archive must not silently expire every claim.
    reference = at or _utcnow()
    since = (reference - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM refunds WHERE status = 'awaiting' AND amount <= ? AND opened_at >= ?"
        " ORDER BY opened_at ASC",
        (amount, since),
    ).fetchall()
    for row in rows:
        stored = (row["merchant"] or "").strip().lower()
        wanted = (merchant or "").strip().lower()
        if not stored or not wanted:
            continue
        if stored in wanted or wanted in stored:
            return dict(row)
    return None


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
            safe_body(event),
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
# Merchant review
# --------------------------------------------------------------------------


def unresolved_merchants(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    """Raw merchant strings the dictionary could not name, most frequent first.

    This is the queue the review UI shows. It reads the *events*, not the
    dictionary, because the question is "what did the ledger fail to name",
    and a merchant that was never stored cannot answer that.
    """
    rows = conn.execute(
        """
        SELECT merchant_raw AS raw,
               COUNT(*) AS seen,
               MAX(received_at) AS last_seen,
               ROUND(SUM(COALESCE(amount_value, 0)), 2) AS total_amount,
               MIN(event_id) AS example_event_id,
               MAX(category) AS category
        FROM events
        WHERE merchant_raw IS NOT NULL
          AND TRIM(merchant_raw) != ''
          AND (merchant_clean IS NULL OR TRIM(merchant_clean) = '')
        GROUP BY LOWER(TRIM(merchant_raw))
        ORDER BY seen DESC, last_seen DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def reassign_merchant(
    conn: sqlite3.Connection,
    raw: str,
    clean: str,
    category: Optional[str] = None,
) -> int:
    """Rename a merchant on the events already stored, and return how many.

    Learning a name only affects *future* messages, which makes the review UI a
    lie: the user corrects a shop and the list does not change. Both the indexed
    columns and the stored payload are rewritten, so `/events`, `/overview` and
    the CSV export agree afterwards.
    """
    wanted = raw.strip().lower()
    if not wanted:
        return 0
    rows = conn.execute(
        "SELECT event_id, payload, category FROM events WHERE LOWER(TRIM(merchant_raw)) = ?",
        (wanted,),
    ).fetchall()
    for row in rows:
        new_category = category or row["category"]
        conn.execute(
            "UPDATE events SET merchant_clean = ?, category = COALESCE(?, category) WHERE event_id = ?",
            (clean, category, row["event_id"]),
        )
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        merchant = payload.get("merchant") or {}
        merchant.update({"clean": clean, "source": "user", "confidence": 1.0})
        if new_category:
            merchant["category"] = new_category
        payload["merchant"] = merchant
        if new_category:
            payload["category"] = new_category
        flags = payload.get("flags")
        if isinstance(flags, list) and "merchant_unresolved" in flags:
            flags.remove("merchant_unresolved")
        conn.execute("UPDATE events SET payload = ? WHERE event_id = ?", (json.dumps(payload), row["event_id"]))
    conn.commit()
    return len(rows)


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
    """Month totals, by the same rules as ``/api/v1/overview``.

    This used to be its own SQL aggregate filtered on ``received_at``, which
    meant a message delivered on the 1st about a payment on the 30th landed in
    a different month than the analytics views. One definition now: the
    analytics bucket, which prefers the bank's posted date.
    """
    from .analytics import month_totals

    rows = [dict(row) for row in conn.execute("SELECT * FROM events").fetchall()]
    totals = month_totals(rows, month).as_dict()
    return MonthSummary(
        period=month,
        spend_total=totals["spend"],
        income_total=totals["income"],
        refunds_total=totals["refunds"],
        excluded_total=totals["excluded"],
        by_category=totals["by_category"],
        excluded_reasons=totals["by_treatment"],
        event_count=totals["events"],
        needs_review_count=totals["review_count"],
    )


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
