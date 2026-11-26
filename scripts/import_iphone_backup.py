#!/usr/bin/env python3
"""Read-only batch importer from an unencrypted iPhone Finder backup (`sms.db`) to SMS Wiz.

Usage:
  1. Connect your iPhone and make an unencrypted local backup in Finder.
  2. Grant Full Disk Access to Terminal.app (System Settings → Privacy & Security).
  3. Run:
     ./.venv/bin/python scripts/import_iphone_backup.py --all
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
SMS_DB_HASH = "3d0d7e5fb2ce288813306e4d4636395e047a3d28"
DEFAULT_BACKUP_ROOT = Path.home() / "Library/Application Support/MobileSync/Backup"


def _apple_ns_to_iso(raw_date: int | None) -> str | None:
    if not raw_date:
        return None
    seconds = raw_date / 1_000_000_000 if raw_date > 1_000_000_000_000 else raw_date
    return (APPLE_EPOCH + timedelta(seconds=seconds)).isoformat()


def find_sms_db(backup_root: Path, udid: str | None) -> Path:
    candidates = [backup_root / udid] if udid else sorted(
        (p for p in backup_root.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for folder in candidates:
        for rel in (Path(SMS_DB_HASH[:2]) / SMS_DB_HASH, Path(SMS_DB_HASH)):
            p = folder / rel
            if p.exists():
                return p
    raise FileNotFoundError(f"Could not find {SMS_DB_HASH} under {backup_root}")


def load_sms_from_backup(db_path: Path, days: int, limit: int) -> list[dict[str, str]]:
    where_clauses = ["m.is_from_me = 0", "m.service = 'SMS'", "m.text IS NOT NULL", "length(trim(m.text)) > 0"]
    params: list[int] = []
    if days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_ns = int((cutoff - APPLE_EPOCH).total_seconds() * 1_000_000_000)
        where_clauses.append("m.date >= ?")
        params.append(cutoff_ns)

    limit_sql = ""
    if limit > 0:
        limit_sql = "LIMIT ?"
        params.append(limit)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            f"""
            SELECT COALESCE(h.id, '') AS sender, m.text, m.date
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE {' AND '.join(where_clauses)}
            ORDER BY m.date DESC
            {limit_sql}
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    out: list[dict[str, str]] = []
    for sender, text, raw_date in rows:
        item: dict[str, str] = {"sender": sender, "body": text.strip(), "source": "import"}
        ts = _apple_ns_to_iso(raw_date)
        if ts:
            item["received_at"] = ts
        out.append(item)
    return out


def post_message(url: str, token: str | None, payload: dict[str, str]) -> dict:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/v1/ingest-sms",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"X-SMSWiz-Token": token} if token else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Import SMS from an unencrypted iPhone Finder backup")
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument("--udid", default=None, help="Specific iPhone backup UDID (default: most recent)")
    parser.add_argument("--days", type=int, default=30, help="Look back N days, 0 for all (default: 30)")
    parser.add_argument("--limit", type=int, default=500, help="Max messages, 0 for all (default: 500)")
    parser.add_argument("--all", action="store_true", help="Import full SMS history")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.all:
        args.days = 0
        args.limit = 0

    try:
        db_path = find_sms_db(args.backup_root, args.udid)
        messages = load_sms_from_backup(db_path, days=args.days, limit=args.limit)
    except (FileNotFoundError, PermissionError, sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
        print(f"Cannot read iPhone backup sms.db: {exc}", file=sys.stderr)
        print(
            "Make sure you created an unencrypted Finder backup and granted Terminal Full Disk Access.",
            file=sys.stderr,
        )
        return 1

    print(f"Found {len(messages)} SMS messages in {db_path}.")
    if args.dry_run:
        return 0

    imported = duplicates = failed = 0
    for msg in reversed(messages):
        try:
            res = post_message(args.url, args.token, msg)
            if res.get("duplicate_of"):
                duplicates += 1
            else:
                imported += 1
        except urllib.error.URLError as exc:
            failed += 1
            print(f"POST failed: {exc}", file=sys.stderr)

    print(f"Done: {imported} imported, {duplicates} suppressed duplicates, {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
