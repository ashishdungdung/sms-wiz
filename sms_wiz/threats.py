"""Spam & threat indicator database for Indian SMS.

Stores known malicious senders, scam domains/hosts, and coercive patterns in
SQLite (`threats` table). Populated from curated India-first seeds, user blocks,
and auto-learned whenever an incoming message is quarantined as `phishing_suspect`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from .extract import find_urls, url_domain
from .models import EntityKind, Intent, SmsEvent

SEED_THREATS: tuple[tuple[str, str, str, str, str], ...] = (
    ("domain", "mparivahan-gov.hbmc.net", "echallan_scam", "critical", "Fake e-Challan Parivahan phishing domain"),
    ("domain", "mparivahan.giize.com", "echallan_scam", "critical", "Fake e-Challan Parivahan phishing domain"),
    ("domain", "parivahan-echallan-pay.com", "apk_scam", "critical", "Fake e-Challan APK distribution domain"),
    ("domain", "sbi-kyc-verify.online", "kyc_scam", "critical", "Fake SBI KYC phishing domain"),
    ("domain", "sbi-kyc.xyz", "kyc_scam", "critical", "Fake SBI KYC phishing domain"),
    ("pattern", ".apk", "apk_scam", "critical", "Executable Android package download link"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_seeds(conn: sqlite3.Connection) -> int:
    """Insert built-in threat seeds if absent; return count inserted."""
    now = _now()
    inserted = 0
    for kind, indicator, category, severity, notes in SEED_THREATS:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO threats
                (kind, indicator, category, severity, source, hit_count, notes, first_seen, last_seen)
            VALUES (?, ?, ?, ?, 'seed', 1, ?, ?, ?)
            """,
            (kind, indicator.lower(), category, severity, notes, now, now),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


def record_threat(
    conn: sqlite3.Connection,
    kind: str,
    indicator: str,
    category: str,
    severity: str = "high",
    source: str = "user",
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Upsert a threat indicator, incrementing hit_count if already known."""
    norm = indicator.strip().lower()
    now = _now()
    conn.execute(
        """
        INSERT INTO threats (kind, indicator, category, severity, source, hit_count, notes, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(kind, indicator) DO UPDATE SET
            hit_count = threats.hit_count + 1,
            last_seen = excluded.last_seen,
            notes = COALESCE(excluded.notes, threats.notes)
        """,
        (kind, norm, category, severity, source, notes, now, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM threats WHERE kind = ? AND indicator = ?",
        (kind, norm),
    ).fetchone()
    return dict(row)


def record_from_event(conn: sqlite3.Connection, event: SmsEvent) -> list[dict[str, Any]]:
    """Auto-learn sender and suspicious URL domains from a quarantined event."""
    if event.intent is not Intent.PHISHING_SUSPECT:
        return []
    recorded: list[dict[str, Any]] = []
    if event.sender_raw and event.sender.entity_kind in {EntityKind.PERSONAL_MOBILE, EntityKind.UNKNOWN}:
        recorded.append(
            record_threat(
                conn,
                kind="sender",
                indicator=event.sender_raw,
                category="phishing",
                severity="high",
                source="auto_quarantine",
                notes=event.summary[:120] if event.summary else None,
            )
        )
    for link in event.links:
        if link.domain and not link.official_for:
            cat = "apk_scam" if link.is_apk else "phishing"
            recorded.append(
                record_threat(
                    conn,
                    kind="domain",
                    indicator=link.domain,
                    category=cat,
                    severity="critical" if link.is_apk else "high",
                    source="auto_quarantine",
                    notes=event.summary[:120] if event.summary else None,
                )
            )
    return recorded


def match_threats(conn: sqlite3.Connection, sender: str, body: str) -> list[dict[str, Any]]:
    """Return any stored threat indicators matching `sender` or URLs/patterns in `body`."""
    matches: list[dict[str, Any]] = []
    if sender:
        row = conn.execute(
            "SELECT * FROM threats WHERE kind = 'sender' AND indicator = ?",
            (sender.strip().lower(),),
        ).fetchone()
        if row:
            matches.append(dict(row))
    for url in find_urls(body):
        dom = url_domain(url)
        if dom:
            row = conn.execute(
                "SELECT * FROM threats WHERE kind = 'domain' AND indicator = ?",
                (dom.lower(),),
            ).fetchone()
            if row:
                matches.append(dict(row))
    lower = body.lower()
    for row in conn.execute("SELECT * FROM threats WHERE kind = 'pattern'").fetchall():
        if row["indicator"] in lower:
            matches.append(dict(row))
    return matches


def list_threats(
    conn: sqlite3.Connection,
    kind: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if category:
        clauses.append("category = ?")
        params.append(category)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM threats {where} ORDER BY hit_count DESC, last_seen DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def delete_threat(conn: sqlite3.Connection, threat_id: int) -> bool:
    cur = conn.execute("DELETE FROM threats WHERE id = ?", (threat_id,))
    conn.commit()
    return cur.rowcount > 0


def backfill_from_events(conn: sqlite3.Connection) -> dict[str, int]:
    """Scan existing `phishing_suspect` events and populate `threats`."""
    ensure_seeds(conn)
    rows = conn.execute(
        "SELECT sender_raw, entity_kind, body FROM events WHERE intent = ?",
        (Intent.PHISHING_SUSPECT.value,),
    ).fetchall()
    added = 0
    for row in rows:
        sender = row["sender_raw"] or ""
        kind = row["entity_kind"] or "unknown"
        body = row["body"] or ""
        if sender and kind in {EntityKind.PERSONAL_MOBILE.value, EntityKind.UNKNOWN.value}:
            record_threat(conn, "sender", sender, "phishing", severity="high", source="auto_quarantine")
            added += 1
        for url in find_urls(body):
            dom = url_domain(url)
            if dom:
                cat = "apk_scam" if ".apk" in url.lower() else "phishing"
                record_threat(conn, "domain", dom, cat, severity="critical" if cat == "apk_scam" else "high", source="auto_quarantine")
                added += 1
    total = conn.execute("SELECT count(*) FROM threats").fetchone()[0]
    return {"events_scanned": len(rows), "indicators_recorded": added, "total_threats": total}
