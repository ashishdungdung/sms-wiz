"""HTTP surface.

Two kinds of caller are expected:

1. **The iOS app** (via a Shortcut / App Intent, or manual share/paste) posting
   to ``/api/v1/ingest-sms``.
2. **Apple's filter service**, when the filter extension defers a query to the
   network: ``POST /api/v1/filter`` must answer with nothing but Apple's
   ``action`` / ``subAction`` pair, quickly.

The filter endpoint is *not* a general SMS API: iOS only calls it for messages
from unknown senders, with the one message body it is deciding about.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from .classify import analyse
from .db import connect
from .merchants import MerchantDictionary
from .models import (
    Alert,
    AppleAction,
    AppleSubAction,
    FilterResponse,
    IngestRequest,
    IngestResponse,
    MonthSummary,
    SmsEvent,
)
from .pipeline import Ingestor
from .store import (
    list_alerts,
    list_balances,
    list_events,
    list_refunds,
    list_vouchers,
    summarise_month,
)

DEFAULT_DB = os.environ.get("SMSWIZ_DB", "sms_wiz.db")


def _check_token(token: Optional[str]) -> None:
    expected = os.environ.get("SMSWIZ_TOKEN")
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="invalid or missing X-SMSWiz-Token")


def _filter_payload(payload: dict[str, Any]) -> tuple[str, str]:
    """Accept both a flat body and Apple's nested query-request shape.

    The exact wire format of Apple's filter-service POST is not documented in
    detail; capture it once from a real device before relying on the nested
    form. The flat form is what our own tests and Shortcuts use.
    """
    candidates = [payload, payload.get("queryRequest") or {}, payload.get("request") or {}]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        sender = candidate.get("sender") or candidate.get("senderId") or ""
        body = (
            candidate.get("messageBody")
            or candidate.get("message_body")
            or candidate.get("body")
            or candidate.get("text")
            or ""
        )
        if body:
            return str(sender), str(body)
    raise HTTPException(status_code=400, detail="no message body found in payload")


def create_app(db_path: Optional[str] = None) -> FastAPI:
    db = db_path or DEFAULT_DB
    ingestor = Ingestor(db)
    merchants = MerchantDictionary(db)

    app = FastAPI(
        title="SMS Wiz — India brain",
        version="0.1.0",
        description=(
            "Rule-first Indian SMS classifier. Optional LLM enrichment is off by default. "
            "Never returns raw OTPs, voucher codes or account numbers in summaries."
        ),
    )

    def conn():
        return connect(db)


    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        import json as _json

        c = conn()
        try:
            totals = c.execute(
                "SELECT COUNT(*) AS events, SUM(needs_review) AS review FROM events"
                " WHERE dedup_status != 'suppressed_duplicate'"
            ).fetchone()
            month = c.execute("SELECT MAX(substr(received_at, 1, 7)) AS m FROM events").fetchone()["m"]
            summary = summarise_month(c, month) if month else None
            latest = list_events(c, limit=8)
        finally:
            c.close()

        def money(value: float) -> str:
            return f"₹{value:,.2f}"

        rows = "".join(
            "<tr>"
            f"<td class='mono'>{row['intent']}</td>"
            f"<td>{_json.loads(row['payload']).get('summary', '')}</td>"
            f"<td class='mono dim'>{row['apple_action']}/{row['apple_subaction']}</td>"
            f"<td class='mono'>{'review' if row['needs_review'] else ''}</td>"
            "</tr>"
            for row in latest
        ) or "<tr><td colspan='4' class='dim'>No messages yet — POST one to /api/v1/ingest-sms</td></tr>"

        stats = ""
        if summary:
            stats = (
                f"<div class='stat'><span>spend ({summary.period})</span><b>{money(summary.spend_total)}</b></div>"
                f"<div class='stat'><span>income</span><b>{money(summary.income_total)}</b></div>"
                f"<div class='stat'><span>refunds</span><b>{money(summary.refunds_total)}</b></div>"
                f"<div class='stat'><span>excluded from spend</span><b>{money(summary.excluded_total)}</b></div>"
            )

        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>SMS Wiz</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root {{ color-scheme: dark; }}
 body {{ margin:0; padding:40px 24px; background:#0b0f14; color:#e6edf3;
        font:15px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif; }}
 .wrap {{ max-width:900px; margin:0 auto; }}
 h1 {{ font-size:26px; margin:0 0 4px; letter-spacing:-.02em; }}
 h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:#7d8590; margin:36px 0 10px; }}
 p {{ color:#9da7b3; margin:6px 0; }}
 .dim {{ color:#7d8590; }}
 .mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; }}
 .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:18px; }}
 .stat {{ background:#121821; border:1px solid #1f2937; border-radius:10px; padding:12px 14px; }}
 .stat span {{ display:block; font-size:12px; color:#7d8590; }}
 .stat b {{ font-size:18px; }}
 table {{ width:100%; border-collapse:collapse; }}
 td, th {{ text-align:left; padding:8px 10px; border-bottom:1px solid #1b2430; vertical-align:top; }}
 th {{ font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:#7d8590; }}
 code {{ background:#121821; border:1px solid #1f2937; border-radius:5px; padding:1px 5px; font-size:12.5px; }}
 a {{ color:#58a6ff; text-decoration:none; }}
 .note {{ background:#121821; border-left:3px solid #d29922; border-radius:6px; padding:12px 14px; margin-top:14px; color:#c9d1d9; }}
 ul {{ margin:8px 0 0; padding-left:18px; }} li {{ margin:4px 0; }}
</style></head><body><div class="wrap">
<h1>SMS Wiz</h1>
<p>India-first SMS brain: rule-first classification, zero double counting, iOS-aware routing.</p>
<div class="stats">
  <div class="stat"><span>events</span><b>{totals['events'] or 0}</b></div>
  <div class="stat"><span>needs review</span><b>{totals['review'] or 0}</b></div>
  {stats}
</div>
<div class="note"><b>iOS reality check:</b> no app can read your Messages history or delete from it.
The filter extension sorts unknown-sender SMS into Apple's fixed folders; the ledger is built from
messages that reach the server by paste, share sheet or Shortcut. Details in <span class="mono">docs/ios-constraints.md</span>.</div>
<h2>Latest events</h2>
<table><thead><tr><th>intent</th><th>summary</th><th>apple folder</th><th></th></tr></thead>
<tbody>{rows}</tbody></table>
<h2>API</h2>
<ul>
 <li><a href="/docs">/docs</a> — OpenAPI, try the endpoints</li>
 <li><span class="mono">POST /api/v1/ingest-sms</span> — <code>{{"sender":"AD-HDFCBK-T","body":"Rs 500 debited..."}}</code></li>
 <li><span class="mono">POST /api/v1/filter</span> — Apple's network deferral: returns only action + subAction</li>
 <li><span class="mono">GET /api/v1/summary?month=YYYY-MM</span>, <span class="mono">/review</span>, <span class="mono">/vouchers</span>, <span class="mono">/refunds</span>, <span class="mono">/cleanup-queues</span></li>
</ul>
<p class="dim" style="margin-top:28px">LLM enrichment is off by default. Nothing secret leaves the server: OTPs, voucher codes and account numbers are masked before any model call.</p>
</div></body></html>"""

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "db": db,
            "llm_enabled": ingestor.enricher.enabled,
            "merchant_keys": len(merchants.all()),
        }

    @app.post("/api/v1/ingest-sms", response_model=IngestResponse)
    def ingest(request: IngestRequest, x_sms_wiz_token: Optional[str] = Header(default=None, alias="X-SMSWiz-Token")) -> IngestResponse:
        _check_token(x_sms_wiz_token)
        return ingestor.ingest(request)

    @app.post("/api/v1/filter")
    def filter_query(
        payload: dict[str, Any] = Body(...),
        x_sms_wiz_token: Optional[str] = Header(default=None, alias="X-SMSWiz-Token"),
    ) -> dict[str, str]:
        """Answer Apple's network-deferral query. Keep it tiny and fast."""
        _check_token(x_sms_wiz_token)
        sender, body = _filter_payload(payload)
        event = analyse(body, sender, merchants=merchants, source="filter")
        return FilterResponse(
            action=event.routing.apple_action,
            subAction=event.routing.apple_subaction,
        ).model_dump()

    @app.get("/api/v1/events")
    def events(
        limit: int = Query(default=50, le=500),
        month: Optional[str] = None,
        needs_review: Optional[bool] = None,
        intent: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        c = conn()
        try:
            return list_events(c, limit=limit, month=month, needs_review=needs_review, intent=intent)
        finally:
            c.close()

    @app.get("/api/v1/events/{event_id}")
    def event_detail(event_id: str) -> dict[str, Any]:
        c = conn()
        try:
            row = c.execute("SELECT payload FROM events WHERE event_id = ?", (event_id,)).fetchone()
        finally:
            c.close()
        if not row:
            raise HTTPException(status_code=404, detail="event not found")
        import json

        return json.loads(row["payload"])

    @app.get("/api/v1/review")
    def review_queue(limit: int = Query(default=50, le=200)) -> list[dict[str, Any]]:
        c = conn()
        try:
            return list_events(c, limit=limit, needs_review=True)
        finally:
            c.close()

    @app.get("/api/v1/summary", response_model=MonthSummary)
    def summary(month: str = Query(pattern=r"^\d{4}-\d{2}$")) -> MonthSummary:
        c = conn()
        try:
            return summarise_month(c, month)
        finally:
            c.close()

    @app.get("/api/v1/vouchers")
    def vouchers() -> list[dict[str, Any]]:
        c = conn()
        try:
            return list_vouchers(c)
        finally:
            c.close()

    @app.get("/api/v1/refunds")
    def refunds() -> list[dict[str, Any]]:
        c = conn()
        try:
            return list_refunds(c)
        finally:
            c.close()

    @app.get("/api/v1/accounts")
    def accounts() -> list[dict[str, Any]]:
        c = conn()
        try:
            return list_balances(c)
        finally:
            c.close()

    @app.get("/api/v1/alerts")
    def alerts(limit: int = Query(default=50, le=200)) -> list[dict[str, Any]]:
        c = conn()
        try:
            return list_alerts(c, limit=limit)
        finally:
            c.close()

    @app.get("/api/v1/cleanup-queues")
    def cleanup_queues(month: Optional[str] = None) -> dict[str, Any]:
        """What is safe to bulk-delete in Apple Messages, and what is not.

        We cannot delete from the Messages database. What we *can* do is tell
        the user which Apple folder to open and clear, and how much is in it.
        """
        c = conn()
        try:
            rows = list_events(c, limit=500, month=month)
        finally:
            c.close()
        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row["dedup_status"] == "suppressed_duplicate":
                continue
            folder = f"{row['apple_action']}/{row['apple_subaction']}"
            bucket = buckets.setdefault(
                folder,
                {"folder": folder, "count": 0, "retention": row["retention"], "examples": []},
            )
            bucket["count"] += 1
            if len(bucket["examples"]) < 3:
                try:
                    import json

                    payload = json.loads(row["payload"])
                    bucket["examples"].append(payload.get("summary", ""))
                except Exception:  # pragma: no cover - defensive
                    pass
        return {"month": month, "queues": sorted(buckets.values(), key=lambda b: -b["count"])}

    @app.get("/api/v1/merchants")
    def merchant_list() -> list[dict[str, Any]]:
        return merchants.all()

    @app.post("/api/v1/merchants")
    def merchant_correction(
        raw: str = Body(embed=True),
        clean: str = Body(embed=True),
        category: str = Body(default="Uncategorized", embed=True),
    ) -> dict[str, Any]:
        """User corrections always outrank seeds and anything the model learned."""
        merchants.learn(raw, clean, category, source="user")
        return {"status": "ok", "raw": raw, "clean": clean, "category": category}

    @app.get("/api/v1/apple/capabilities")
    def apple_capabilities() -> dict[str, Any]:
        """What the filter extension should declare. Keep it to five per group."""
        from .registry import DECLARED_PROMOTIONAL, DECLARED_TRANSACTIONAL

        return {
            "transactional": [s.value for s in DECLARED_TRANSACTIONAL],
            "promotional": [s.value for s in DECLARED_PROMOTIONAL],
            "note": "Apple allows up to five per group; iOS 18 has been reported to drop folders when more are declared.",
        }

    return app


app = create_app()
