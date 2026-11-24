"""Self-learning merchant dictionary.

Indian bank SMS show the *legal* entity, not the brand:

    "KIRANAKART TECHNOLOGIES PVT LTD"  ->  Zepto
    "BUNDL TECHNOLOGIES"               ->  Swiggy

A small seed list plus an append-only learner gets most of the way. User
corrections always outrank learned entries, and learned entries never override
a seed or a user correction for the same key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .db import connect
from .registry import SEED_MERCHANTS

_LEGAL_SUFFIXES = re.compile(
    r"\b(?:PVT|PRIVATE|LIMITED|LTD|LLP|INC|CORP|CORPORATION|COMPANY|CO|TECHNOLOGIES|TECHNOLOGY|"
    r"SOLUTIONS|SERVICES|ENTERPRISES|INDUSTRIES|INTERNATIONAL|INDIA|IN)\b\.?",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")


def normalize_key(raw: str) -> str:
    """Upper-case, drop legal suffixes and punctuation, collapse spaces."""
    text = _NON_ALNUM.sub(" ", raw.upper())
    text = _LEGAL_SUFFIXES.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class MerchantMatch:
    raw: str
    clean_code: str
    clean: str
    category: str
    source: str
    score: float


class MerchantDictionary:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        self._ensure_seeds()

    # -- internals ---------------------------------------------------------
    def _conn(self):
        return connect(self.db_path)

    def _ensure_seeds(self) -> None:
        conn = self._conn()
        try:
            existing = {row[0] for row in conn.execute("SELECT raw_key FROM merchant_dictionary")}
            now = datetime.now(timezone.utc).isoformat()
            rows = [
                (normalize_key(raw), clean, category, "seed", now)
                for raw, clean, category in SEED_MERCHANTS
                if normalize_key(raw) not in existing
            ]
            if rows:
                conn.executemany(
                    "INSERT OR IGNORE INTO merchant_dictionary (raw_key, clean_name, category, source, updated_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
        finally:
            conn.close()

    # -- public ------------------------------------------------------------
    def lookup(self, text: str) -> MerchantMatch | None:
        """Find the longest dictionary key contained in ``text``."""
        haystack = f" {normalize_key(text)} "
        conn = self._conn()
        try:
            best: MerchantMatch | None = None
            for row in conn.execute("SELECT raw_key, clean_name, category, source FROM merchant_dictionary"):
                key = row["raw_key"]
                if len(key) < 4 or f" {key} " not in haystack:
                    continue
                score = 0.95 if row["source"] == "user" else 0.9 if row["source"] == "seed" else 0.8
                score -= min(0.15, len(key) / 400)  # prefer longer, more specific keys
                candidate = MerchantMatch(
                    raw=key,
                    clean_code=key,
                    clean=row["clean_name"],
                    category=row["category"] or "Uncategorized",
                    source=row["source"],
                    score=round(score, 3),
                )
                if best is None or len(key) > len(best.raw):
                    best = candidate
            return best
        finally:
            conn.close()

    def learn(self, raw: str, clean: str, category: str = "Uncategorized", source: str = "learned") -> None:
        """Add or improve an entry. User corrections always win."""
        key = normalize_key(raw)
        if len(key) < 3:
            return
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            row = conn.execute("SELECT source FROM merchant_dictionary WHERE raw_key = ?", (key,)).fetchone()
            if row is not None:
                priority = {"user": 3, "seed": 2, "llm": 1, "learned": 0}
                if priority.get(source, 0) <= priority.get(row["source"], 0):
                    return
            conn.execute(
                "INSERT INTO merchant_dictionary (raw_key, clean_name, category, source, updated_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(raw_key) DO UPDATE SET clean_name=excluded.clean_name,"
                " category=excluded.category, source=excluded.source, updated_at=excluded.updated_at",
                (key, clean, category, source, now),
            )
            conn.commit()
        finally:
            conn.close()

    def all(self) -> list[dict]:
        conn = self._conn()
        try:
            return [dict(row) for row in conn.execute("SELECT * FROM merchant_dictionary ORDER BY raw_key")]
        finally:
            conn.close()
