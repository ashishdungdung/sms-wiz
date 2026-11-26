#!/usr/bin/env python3
"""Read-only batch importer from macOS Messages (`~/Library/Messages/chat.db`) to SMS Wiz.

Requires macOS Full Disk Access for the terminal running this script.
Only reads SMS (`service = 'SMS'`), never modifies `chat.db`.
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


def _apple_ns_to_iso(raw_date: int | None) -> str | None:
    if not raw_date:
        return None
    # macOS Ventura+ stores nanoseconds since 2001-01-01; older DBs store seconds.
    seconds = raw_date / 1_000_000_000 if raw_date > 1_000_000_000_000 else raw_date
    return (APPLE_EPOCH + timedelta(seconds=seconds)).isoformat()


def _extract_text_from_attributed_body(blob: bytes | None) -> str:
    """Best-effort UTF-8 fallback when `message.text` is NULL (macOS Ventura+)."""
    if not blob:
        return ""
    marker = b"NSString"
    idx = blob.find(marker)
    if idx == -1:
        return ""
    # Skip `NSString` header bytes and length prefix; decode printable UTF-8 slice.
    tail = blob[idx + len(marker) :]
    start = tail.find(b"+")
    if start == -1 or start + 2 >= len(tail):
        return ""
    length_byte = tail[start + 1]
    offset = start + 2
    if length_byte == 0x81 and offset + 2 < len(tail):
        length = int.from_bytes(tail[offset : offset + 2], "little")
        offset += 2
    else:
        length = length_byte
    return tail[offset : offset + length].decode("utf-8", errors="ignore").strip()


def load_messages(db_path: Path, days: int, limit: int) -> list[dict[str, str]]:
    where_clauses = ["m.is_from_me = 0", "m.service = 'SMS'"]
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

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            f"""
            SELECT
                COALESCE(h.id, '') AS sender,
                m.text,
                m.attributedBody,
                m.date
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
    for sender, text, attr_body, raw_date in rows:
        body = (text or "").strip() or _extract_text_from_attributed_body(attr_body)
        if not body:
            continue
        item: dict[str, str] = {"sender": sender, "body": body, "source": "import"}
        received_at = _apple_ns_to_iso(raw_date)
        if received_at:
            item["received_at"] = received_at
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
    parser = argparse.ArgumentParser(description="Import SMS from macOS Messages into SMS Wiz")
    parser.add_argument("--db", type=Path, default=Path.home() / "Library/Messages/chat.db")
    parser.add_argument("--days", type=int, default=30, help="Look back N days, 0 for all (default: 30)")
    parser.add_argument("--limit", type=int, default=500, help="Max messages to read, 0 for all (default: 500)")
    parser.add_argument("--all", action="store_true", help="Import full SMS history (equivalent to --days 0 --limit 0)")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="SMS Wiz server URL")
    parser.add_argument("--token", default=None, help="Optional X-SMSWiz-Token")
    parser.add_argument("--dry-run", action="store_true", help="Print counts without posting")
    args = parser.parse_args()
    if args.all:
        args.days = 0
        args.limit = 0

    try:
        messages = load_messages(args.db, days=args.days, limit=args.limit)
    except sqlite3.OperationalError as exc:
        print(f"Cannot read {args.db}: {exc}", file=sys.stderr)
        print(
            "Grant Full Disk Access to your terminal in System Settings → Privacy & Security → Full Disk Access.",
            file=sys.stderr,
        )
        return 1

    print(f"Found {len(messages)} SMS messages in the last {args.days} days.")
    if args.dry_run:
        for msg in messages[:5]:
            print(f"  [{msg['sender'] or 'unknown'}] {msg['body'][:70].replace(chr(10), ' ')}")
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
