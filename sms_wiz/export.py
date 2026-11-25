"""Get the ledger out: CSV for a spreadsheet, one JSON file for everything else.

Why this exists
---------------
An app that reads your bank SMS is holding a record of your life. If the only
copy of it is a SQLite file inside an app's container, the user is a hostage: a
failed migration, a lost phone, or a decision to stop using the app all end the
same way. Export is not a feature bolted on at the end; it is the exit door, and
it has to be plain enough to read with tools that already exist.

Two shapes, deliberately different:

* **CSV** — one row per event, fixed column order, RFC 4180 quoting. Opens in
  Excel, Numbers, LibreOffice and ``pandas.read_csv`` with no code. It is a
  *view*: the columns that matter for a ledger, not every field.
* **JSON** — the whole store, including the voucher vault, refund claims,
  balances, alerts and the learned merchant dictionary, wrapped with a format
  tag and a schema version so a future importer can tell what it is reading.

Both are lossy in exactly one direction: the *body* stored in the database is
already masked at write time (``redact.for_storage``), so an export cannot leak
a card number that was never stored. Nothing here re-derives secrets.

CSV injection
-------------
A merchant name can start with ``=``, ``+``, ``-`` or ``@``. Spreadsheets treat
those as formulas, so ``=HYPERLINK(...)`` in a merchant field becomes a live
formula when the user opens the export. Every such cell is prefixed with an
apostrophe, which both Excel and Numbers treat as "literal text". This is the
one place where the exported text differs from the stored text, and it is
recorded in the header row's comment in ``EXPORT_NOTES``.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from . import store

#: Format tag inside the JSON snapshot. Bump only on a breaking change.
FORMAT = "sms-wiz.export/v1"

#: Column order for the CSV. Chosen so a spreadsheet opened left-to-right reads
#: as a ledger: when, what, who, how much, and why it is or is not counted.
CSV_COLUMNS: tuple[str, ...] = (
    "event_id",
    "received_at",
    "intent",
    "ledger_treatment",
    "counts_toward_spend",
    "amount_value",
    "amount_currency",
    "category",
    "merchant_clean",
    "merchant_raw",
    "institution",
    "account_last4",
    "reference",
    "dedup_status",
    "needs_review",
    "confidence",
    "apple_action",
    "apple_subaction",
)

EXPORT_NOTES: dict[str, str] = {
    "csv.text_safety": (
        "Cells beginning with = + - @ are prefixed with an apostrophe so a "
        "spreadsheet cannot execute them as formulas."
    ),
    "csv.body": "Message bodies are not exported; the stored copies are already masked.",
    "json.tables": "events, refunds, vouchers, balances, alerts, merchant_dictionary",
}


def _cell(value: Any) -> Any:
    """Make one value safe for a spreadsheet without changing what it means."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def event_rows(conn: sqlite3.Connection, month: Optional[str] = None, limit: int = 200_000) -> list[dict[str, Any]]:
    """Every stored event, oldest first, as flat dicts."""
    rows = list(store.list_events(conn, limit=limit, month=month))
    rows.sort(key=lambda row: (row.get("received_at") or "", row.get("created_at") or ""))
    return rows


def to_csv(rows: Iterable[dict[str, Any]]) -> str:
    """RFC 4180 CSV, UTF-8, with a trailing newline and no BOM.

    A BOM would break ``csv`` readers that do not expect one; Excel opens a
    plain UTF-8 CSV correctly when the file has a ``.csv`` extension and the
    user accepts the import prompt. The ₹ sign is *not* used in any cell for
    this reason — amounts are plain numbers with a currency column.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([_cell(row.get(column)) for column in CSV_COLUMNS])
    return buffer.getvalue()


def snapshot(conn: sqlite3.Connection, month: Optional[str] = None) -> dict[str, Any]:
    """The whole store in one object. Safe to hand to the user as a file."""
    events = event_rows(conn, month=month)
    return {
        "format": FORMAT,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "app": {"name": "SMS Wiz", "contract": "sms_event"},
        "notes": EXPORT_NOTES,
        "month": month,
        "counts": {
            "events": len(events),
            "refunds": len(store.list_refunds(conn)),
            "vouchers": len(store.list_vouchers(conn)),
            "balances": len(store.list_balances(conn)),
        },
        "events": events,
        "refunds": store.list_refunds(conn),
        "vouchers": store.list_vouchers(conn),
        "balances": store.list_balances(conn),
        "alerts": store.list_alerts(conn, limit=2000),
        "merchant_dictionary": [dict(row) for row in conn.execute(
            "SELECT raw_key, clean_name, category, source, updated_at FROM merchant_dictionary ORDER BY raw_key"
        )],
    }


def to_json(conn: sqlite3.Connection, month: Optional[str] = None) -> str:
    """Stable, human-diffable JSON: sorted keys, two-space indent, real UTF-8."""
    return json.dumps(snapshot(conn, month=month), ensure_ascii=False, indent=2, sort_keys=True, default=str)


def filename(moment: datetime, kind: str) -> str:
    """``sms-wiz-2026-11-24.csv`` — sorts by date in any file browser."""
    return f"sms-wiz-{moment:%Y-%m-%d}.{kind}"
