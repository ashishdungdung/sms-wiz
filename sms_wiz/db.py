"""SQLite storage. One file, no server, easy to back up and inspect."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = os.environ.get("SMSWIZ_DB", str(Path.cwd() / "sms_wiz.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    sender_raw TEXT,
    sender_name TEXT,
    entity_kind TEXT,
    intent TEXT,
    domain TEXT,
    amount_value REAL,
    amount_currency TEXT,
    institution TEXT,
    account_last4 TEXT,
    merchant_raw TEXT,
    merchant_clean TEXT,
    category TEXT,
    ledger_treatment TEXT,
    counts_toward_spend INTEGER DEFAULT 0,
    apple_action TEXT,
    apple_subaction TEXT,
    retention TEXT,
    confidence REAL,
    needs_review INTEGER DEFAULT 0,
    dedup_status TEXT,
    duplicate_of TEXT,
    reference TEXT,
    body TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_month ON events (substr(received_at, 1, 7));
CREATE INDEX IF NOT EXISTS idx_events_reference ON events (reference);
CREATE INDEX IF NOT EXISTS idx_events_review ON events (needs_review);

CREATE TABLE IF NOT EXISTS merchant_dictionary (
    raw_key TEXT PRIMARY KEY,
    clean_name TEXT NOT NULL,
    category TEXT,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS balances (
    account_last4 TEXT PRIMARY KEY,
    institution TEXT,
    balance REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refunds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant TEXT,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'INR',
    instrument_last4 TEXT,
    status TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    deadline_at TEXT,
    resolved_at TEXT,
    opened_event_id TEXT,
    matched_event_id TEXT,
    raw_sms TEXT
);

CREATE TABLE IF NOT EXISTS vouchers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT,
    value REAL,
    currency TEXT DEFAULT 'INR',
    code TEXT UNIQUE,
    pin TEXT,
    expires_on TEXT,
    event_id TEXT,
    created_at TEXT NOT NULL,
    raw_sms TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT,
    severity TEXT,
    kind TEXT,
    message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS threats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    indicator TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'high',
    source TEXT NOT NULL DEFAULT 'seed',
    hit_count INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(kind, indicator)
);
"""


#: Columns added after the first release. ``CREATE TABLE IF NOT EXISTS`` is a
#: no-op on an existing database, so a shipped install would otherwise keep the
#: old shape forever and every new query would fail with "no such column".
#: Additive only: dropping or retyping a column needs a data migration, and this
#: store is somebody's bank history.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "events": {
        "event_id": "TEXT NOT NULL DEFAULT ''",
        "received_at": "TEXT NOT NULL DEFAULT ''",
        "sender_raw": "TEXT",
        "sender_name": "TEXT",
        "entity_kind": "TEXT",
        "intent": "TEXT",
        "domain": "TEXT",
        "amount_value": "REAL",
        "amount_currency": "TEXT",
        "institution": "TEXT",
        "account_last4": "TEXT",
        "merchant_raw": "TEXT",
        "merchant_clean": "TEXT",
        "category": "TEXT",
        "ledger_treatment": "TEXT",
        "counts_toward_spend": "INTEGER DEFAULT 0",
        "apple_action": "TEXT",
        "apple_subaction": "TEXT",
        "retention": "TEXT",
        "confidence": "REAL",
        "needs_review": "INTEGER DEFAULT 0",
        "dedup_status": "TEXT",
        "duplicate_of": "TEXT",
        "reference": "TEXT",
        "body": "TEXT",
        "payload": "TEXT NOT NULL DEFAULT '{}'",
        "created_at": "TEXT NOT NULL DEFAULT ''",
    },
    "refunds": {
        "merchant": "TEXT",
        "amount": "REAL NOT NULL DEFAULT 0",
        "currency": "TEXT DEFAULT 'INR'",
        "instrument_last4": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'awaiting'",
        "opened_at": "TEXT NOT NULL DEFAULT ''",
        "deadline_at": "TEXT",
        "resolved_at": "TEXT",
        "opened_event_id": "TEXT",
        "matched_event_id": "TEXT",
        "raw_sms": "TEXT",
    },
    "vouchers": {
        "brand": "TEXT",
        "value": "REAL",
        "currency": "TEXT DEFAULT 'INR'",
        "code": "TEXT",
        "pin": "TEXT",
        "expires_on": "TEXT",
        "event_id": "TEXT",
        "created_at": "TEXT NOT NULL DEFAULT ''",
        "raw_sms": "TEXT",
    },
}

SCHEMA_VERSION = 1


def connect(path: str | None = None) -> sqlite3.Connection:
    target = Path(path or DEFAULT_DB_PATH)
    # A first run must not fail with "unable to open database file" just
    # because ~/.sms_wiz does not exist yet.
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL lets a read (the app, the filter's explain call) run while an ingest is
    # writing. Without it SQLite serialises everything and a sync of a few
    # hundred queued SMS blocks the UI.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    init(conn)
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:  # pragma: no cover - corrupt file
        return set()


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Bring an existing database up to the current column set. Returns a log."""
    applied: list[str] = []
    for table, columns in ADDED_COLUMNS.items():
        existing = _columns(conn, table)
        if not existing:
            continue  # table does not exist yet; SCHEMA will create it
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                applied.append(f"{table}.{name}")
    if applied:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    return applied


def init(conn: sqlite3.Connection) -> None:
    # Order matters: an old database needs its columns before the schema script
    # runs, because that script creates indexes on them (`reference`), and an
    # index on a missing column aborts the whole script.
    migrate(conn)
    conn.executescript(SCHEMA)
    conn.commit()
