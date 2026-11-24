"""Merchant dictionary: curated catalog + VPA hints + user corrections.

Indian bank SMS show the *legal* entity, not the brand:

    "KIRANAKART TECHNOLOGIES PVT LTD"  ->  Zepto
    "BUNDL TECHNOLOGIES"               ->  Swiggy
    "kiranakart@axisbank"              ->  Zepto (via the VPA handle)

Resolution order is fixed and deliberately boring:

    user  >  seed (catalog)  >  VPA hint  >  llm  >  learned

* **user** — a correction the person made. Always wins, even over the catalog.
* **seed** — curated in :mod:`sms_wiz.catalog`; reviewed like code.
* **VPA hint** — the handle in ``name@psp``, used only when the readable name
  found nothing (``@ybl`` means PhonePe, not a merchant).
* **llm / learned** — suggestions. Never overwrite a stronger source.

A miss is a *result*, not a failure: the event is flagged ``needs_review`` so a
human can teach the dictionary. Guessing by string similarity would corrupt
category totals silently, which is worse than asking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .catalog import CATEGORY_BY_LABEL, VPA_HINTS, iter_seed_rows
from .db import connect
from .registry import SEED_MERCHANTS

_LEGAL_SUFFIXES = re.compile(
    r"\b(?:PVT|PRIVATE|LIMITED|LTD|LLP|INC|CORP|CORPORATION|COMPANY|CO|TECHNOLOGIES|TECHNOLOGY|"
    r"SOLUTIONS|SERVICES|ENTERPRISES|INDUSTRIES|INTERNATIONAL|INDIA|IN)\b\.?",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")  # @ and . become separators
_WS = re.compile(r"\s+")

#: Source strength. Higher wins; ties keep the existing row.
PRIORITY: dict[str, int] = {"learned": 0, "llm": 1, "vpa": 2, "seed": 3, "user": 4}

_MIN_KEY_LEN = 4


def normalize_key(raw: str) -> str:
    """Upper-case, drop legal suffixes and punctuation, collapse spaces."""
    text = _NON_ALNUM.sub(" ", raw.upper())
    text = _LEGAL_SUFFIXES.sub(" ", text)
    return _WS.sub(" ", text).strip()


def vpa_handle(text: str) -> str | None:
    """Return the local part of the first VPA-looking token, e.g. ``kiranakart``."""
    match = re.search(r"\b([a-z0-9][a-z0-9._\-]{1,60})@([a-z][a-z0-9]{1,20})\b", text, re.IGNORECASE)
    return match.group(1).lower() if match else None


def vpa_category(handle: str, psp: str | None = None) -> tuple[str, str] | None:
    """Resolve a handle to ``(clean_name, category_label)`` using the hint table."""
    key = normalize_key(handle).lower().replace(" ", "")
    if key in VPA_HINTS:
        return VPA_HINTS[key]
    if psp and psp.lower() in VPA_HINTS:
        return VPA_HINTS[psp.lower()]
    return None


@dataclass
class MerchantMatch:
    raw: str
    clean_code: str
    clean: str
    category: str
    source: str
    score: float

    @property
    def essential(self) -> bool:
        category = CATEGORY_BY_LABEL.get(self.category)
        return bool(category and category.essential)


class MerchantDictionary:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        self._ensure_seeds()

    # -- internals ---------------------------------------------------------
    def _conn(self):
        return connect(self.db_path)

    def _ensure_seeds(self) -> None:
        """Insert catalog + legacy seeds once. User rows are never touched."""
        conn = self._conn()
        try:
            existing = {row[0] for row in conn.execute("SELECT raw_key FROM merchant_dictionary")}
            now = datetime.now(timezone.utc).isoformat()
            rows = []
            # Legacy seeds first (they are the ones already in shipped databases),
            # then the larger catalog; both are "seed" strength.
            for raw, clean, category in (*SEED_MERCHANTS, *iter_seed_rows()):
                key = normalize_key(raw)
                if key in existing or len(key) < _MIN_KEY_LEN:
                    continue
                existing.add(key)
                rows.append((key, clean, category, "seed", now))
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
                if len(key) < _MIN_KEY_LEN or f" {key} " not in haystack:
                    continue
                base = {"user": 0.97, "seed": 0.9, "vpa": 0.86}.get(row["source"], 0.8)
                score = base - min(0.12, len(key) / 400)  # prefer longer, more specific keys
                candidate = MerchantMatch(
                    raw=key,
                    clean_code=key,
                    clean=row["clean_name"],
                    category=row["category"] or "Uncategorized",
                    source=row["source"],
                    score=round(score, 3),
                )
                # Longest key wins; equal length keeps the stronger source.
                if best is None or len(key) > len(best.raw) or (
                    len(key) == len(best.raw) and PRIORITY.get(candidate.source, 0) > PRIORITY.get(best.source, 0)
                ):
                    best = candidate
            return best
        finally:
            conn.close()

    def resolve(self, text: str, vpa: str | None = None) -> MerchantMatch | None:
        """Full resolution: readable name first, VPA hint second.

        A name match always beats a handle: ``"to VPA kiranakart@ybl"`` should
        read as Zepto from the name, not PhonePe from the PSP.
        """
        match = self.lookup(text)
        if match is not None:
            return match

        handle = vpa or vpa_handle(text)
        if not handle:
            return None
        # A number@psp is a person, not a shop: leave it to the P2P rules.
        if handle.isdigit():
            return None
        hint = vpa_category(handle)
        if hint is None:
            return None
        clean, category = hint
        return MerchantMatch(
            raw=handle,
            clean_code=handle.upper(),
            clean=clean,
            category=category,
            source="vpa",
            score=0.84,
        )

    def learn(self, raw: str, clean: str, category: str = "Uncategorized", source: str = "learned") -> None:
        """Add or improve an entry. Stronger sources are never overwritten."""
        key = normalize_key(raw)
        if len(key) < 3:
            return
        # "XX4092" is an account, not a shop. Learning it would attach the last
        # merchant you paid to every future message that mentions the account.
        if not re.search(r"[A-Z]", key) or re.fullmatch(r"[X*\d ]+", key):
            return
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            row = conn.execute("SELECT source FROM merchant_dictionary WHERE raw_key = ?", (key,)).fetchone()
            if row is not None and PRIORITY.get(source, 0) <= PRIORITY.get(row["source"], 0):
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

    def forget(self, raw: str) -> bool:
        """Remove a user row (used by the review UI's 'wrong brand' action)."""
        key = normalize_key(raw)
        conn = self._conn()
        try:
            row = conn.execute("SELECT source FROM merchant_dictionary WHERE raw_key = ?", (key,)).fetchone()
            if row is None or row["source"] == "seed":
                return False  # seeds are code, not user data
            conn.execute("DELETE FROM merchant_dictionary WHERE raw_key = ?", (key,))
            conn.commit()
            return True
        finally:
            conn.close()

    def all(self) -> list[dict]:
        conn = self._conn()
        try:
            return [dict(row) for row in conn.execute("SELECT * FROM merchant_dictionary ORDER BY raw_key")]
        finally:
            conn.close()

    def by_source(self, source: str) -> list[dict]:
        conn = self._conn()
        try:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM merchant_dictionary WHERE source = ? ORDER BY raw_key", (source,)
                )
            ]
        finally:
            conn.close()

    def stats(self) -> dict[str, int]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT source, COUNT(*) AS n FROM merchant_dictionary GROUP BY source"
            ).fetchall()
            counts = {row["source"]: row["n"] for row in rows}
        finally:
            conn.close()
        counts["total"] = sum(counts.values())
        return counts

    def categories(self) -> list[dict]:
        """Category taxonomy with counts, for the review UI's dropdown."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT category, COUNT(*) AS n FROM merchant_dictionary GROUP BY category ORDER BY n DESC"
            ).fetchall()
        finally:
            conn.close()
        out = []
        for row in rows:
            label = row["category"] or "Uncategorized"
            meta = CATEGORY_BY_LABEL.get(label)
            out.append(
                {
                    "label": label,
                    "count": row["n"],
                    "slug": meta.slug if meta else "uncategorized",
                    "group": meta.group if meta else "Other",
                    "essential": meta.essential if meta else False,
                }
            )
        return out


__all__ = [
    "MerchantDictionary",
    "MerchantMatch",
    "normalize_key",
    "vpa_handle",
    "vpa_category",
    "PRIORITY",
]
