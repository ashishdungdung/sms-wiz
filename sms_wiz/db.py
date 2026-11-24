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
"""


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DEFAULT_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    init(conn)
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
