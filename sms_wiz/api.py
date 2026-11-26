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

import hmac
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from . import analytics, cards, export, notify, rules, subscriptions, templates
from .catalog import CATEGORIES as CATEGORY_TAXONOMY
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
from . import extract, store
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


_MONTH_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")


def _check_month(month: Optional[str]) -> Optional[str]:
    """A month is ``YYYY-MM``. Silently returning an empty list for ``2026-13``
    looks like "you spent nothing", which is a lie a ledger must not tell."""
    if month is None:
        return None
    if not _MONTH_RE.match(month):
        raise HTTPException(status_code=422, detail=f"month must be YYYY-MM, got {month!r}")
    return month


def _expected_token() -> Optional[str]:
    return os.environ.get("SMSWIZ_TOKEN") or None


def _token_ok(token: Optional[str]) -> bool:
    expected = _expected_token()
    if not expected:
        return True
    if not token:
        return False
    # Constant-time: a shared secret compared with == leaks its prefix length
    # through timing, which is enough to guess it one character at a time.
    return hmac.compare_digest(token, expected)


def _check_token(token: Optional[str]) -> None:
    if not _token_ok(token):
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
    return "", ""


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

    @app.middleware("http")
    async def require_token(request, call_next):
        """One gate for the whole API.

        The token used to be checked handler by handler, which meant every new
        read endpoint was unauthenticated until somebody remembered. The data
        behind these routes is a bank statement: amounts, merchants, balances.
        ``/`` (the landing page) and ``/health`` stay open so the phone can find
        the server before it has a token.
        """
        path = request.url.path
        if (
            path.startswith("/api/")
            and request.method != "OPTIONS"
            and not _token_ok(request.headers.get("x-smswiz-token"))
        ):
            return JSONResponse(
                {"detail": "invalid or missing X-SMSWiz-Token"}, status_code=401
            )
        return await call_next(request)


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
        if not body.strip():
            # Apple's extension must always get an answer it can act on. A 4xx
            # here means the filter silently stops filtering; `allow` is the safe
            # default because it changes nothing about the user's inbox.
            return FilterResponse(
                action=AppleAction.ALLOW,
                subAction=AppleSubAction.NONE,
                reason="no message body in the request; allowed rather than misfiled",
            ).model_dump()
        # Fast path: issuer-authored rules answer most folder decisions without
        # touching the full chain. The chain still runs on ingest, where it is
        # authoritative; this only has to beat Apple's deadline.
        sender_info = extract.parse_sender(sender)
        verdict = rules.evaluate(
            sender,
            body,
            sender_kind=sender_info.entity_kind.value,
        )
        if verdict is not None:
            return FilterResponse(action=verdict.action, subAction=verdict.sub_action).model_dump(
                exclude_none=True
            )

        event = analyse(body, sender, merchants=merchants, source="filter")
        return FilterResponse(
            action=event.routing.apple_action,
            subAction=event.routing.apple_subaction,
        ).model_dump(exclude_none=True)

    @app.get("/api/v1/events")
    def events(
        limit: int = Query(default=50, ge=1, le=500),
        month: Optional[str] = None,
        needs_review: Optional[bool] = None,
        intent: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        c = conn()
        try:
            return list_events(
                c, limit=limit, month=_check_month(month), needs_review=needs_review, intent=intent
            )
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
            rows = list_events(c, limit=500, month=_check_month(month))
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
        """User corrections always outrank seeds and anything the model learned.

        A correction is not learning for the future only: events already stored
        with this raw string are renamed here too, or the review list the user
        is looking at would never shrink.
        """
        merchants.learn(raw, clean, category, source="user")
        c = conn()
        try:
            updated = store.reassign_merchant(c, raw, clean, category if category != "Uncategorized" else None)
        finally:
            c.close()
        return {
            "status": "ok",
            "raw": raw,
            "clean": clean,
            "category": category,
            "events_updated": updated,
        }

    @app.get("/api/v1/merchants/unresolved")
    def merchant_unresolved(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        """The review queue: merchants the ledger recorded but could not name.

        Each row carries ``suggested`` — what the dictionary *would* call it
        now, if anything — so the UI can offer a one-tap accept instead of an
        empty text field.
        """
        c = conn()
        try:
            rows = store.unresolved_merchants(c, limit=limit)
        finally:
            c.close()
        out = []
        for row in rows:
            match = merchants.resolve(row["raw"] or "")
            out.append(
                {
                    **row,
                    "suggested": (
                        {"clean": match.clean, "category": match.category, "confidence": match.score}
                        if match
                        else None
                    ),
                }
            )
        return out

    @app.get("/api/v1/apple/capabilities")
    def apple_capabilities() -> dict[str, Any]:
        """What the filter extension should declare. Keep it to five per group."""
        from .registry import DECLARED_PROMOTIONAL, DECLARED_TRANSACTIONAL

        return {
            "transactional": [s.value for s in DECLARED_TRANSACTIONAL],
            "promotional": [s.value for s in DECLARED_PROMOTIONAL],
            "note": "Apple allows up to five per group; iOS 18 has been reported to drop folders when more are declared.",
        }

    # ------------------------------------------------------------------
    # Analytics, subscriptions, cards — all derived from stored events
    # ------------------------------------------------------------------

    def _rows(limit: int = 2000) -> list[dict[str, Any]]:
        c = conn()
        try:
            return list_events(c, limit=limit)
        finally:
            c.close()

    @app.get("/api/v1/categories")
    def category_taxonomy() -> list[dict[str, Any]]:
        """The category taxonomy, with the group and essential flag."""
        return [
            {
                "slug": c.slug,
                "label": c.label,
                "group": c.group,
                "essential": c.essential,
                "note": c.note,
            }
            for c in CATEGORY_TAXONOMY
        ]

    @app.get("/api/v1/overview")
    def overview(month: Optional[str] = None) -> dict[str, Any]:
        """One call for the home screen: month, weeks, trend, UPI, cards, top merchants."""
        return analytics.overview(_rows(), month=_check_month(month))

    @app.get("/api/v1/analytics/weekly")
    def analytics_weekly(weeks: int = Query(default=8, ge=1, le=52)) -> list[dict[str, Any]]:
        return analytics.weekly(_rows(), weeks=weeks)

    @app.get("/api/v1/analytics/monthly")
    def analytics_monthly(months: int = Query(default=6, ge=1, le=36)) -> list[dict[str, Any]]:
        return analytics.monthly(_rows(), months=months)

    @app.get("/api/v1/analytics/upi")
    def analytics_upi(month: Optional[str] = None) -> dict[str, Any]:
        month = _check_month(month)
        rows = _rows()
        if month:
            rows = [r for r in rows if str(r.get("received_at", "")).startswith(month)]
        return analytics.upi_stats(rows)

    @app.get("/api/v1/analytics/investments")
    def analytics_investments() -> dict[str, Any]:
        """Units and corporate actions. Not a portfolio value — see `caveat`."""
        return analytics.investment_book(_rows())

    @app.get("/api/v1/analytics/travel")
    def analytics_travel() -> dict[str, Any]:
        return analytics.travel_log(_rows())

    @app.get("/api/v1/analytics/bills")
    def analytics_bills() -> dict[str, Any]:
        """Due notices and paid utilities, kept apart from the spend total."""
        return analytics.bill_calendar(_rows())

    @app.get("/api/v1/analytics/loans")
    def analytics_loans() -> dict[str, Any]:
        """Loan EMI reminders and debits grouped by loan account."""
        return analytics.loan_summary(_rows())

    @app.post("/api/v1/reclassify")
    def reclassify_events(x_sms_wiz_token: Optional[str] = Header(default=None, alias="X-SMSWiz-Token")) -> dict[str, int]:
        """Re-run deterministic classification on stored events after rule updates."""
        _check_token(x_sms_wiz_token)
        from .pipeline import reclassify_stored

        c = conn()
        try:
            return reclassify_stored(c, merchants)
        finally:
            c.close()

    @app.get("/api/v1/threats")
    def get_threats(
        kind: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = Query(default=200, le=500),
    ) -> list[dict[str, Any]]:
        from . import threats

        c = conn()
        try:
            threats.ensure_seeds(c)
            return threats.list_threats(c, kind=kind, category=category, limit=limit)
        finally:
            c.close()

    @app.post("/api/v1/threats")
    def add_threat(
        payload: dict[str, Any] = Body(...),
        x_sms_wiz_token: Optional[str] = Header(default=None, alias="X-SMSWiz-Token"),
    ) -> dict[str, Any]:
        _check_token(x_sms_wiz_token)
        from . import threats

        kind = str(payload.get("kind") or "sender").strip()
        indicator = str(payload.get("indicator") or "").strip()
        category = str(payload.get("category") or "phishing").strip()
        severity = str(payload.get("severity") or "high").strip()
        notes = payload.get("notes")
        if not indicator:
            raise HTTPException(status_code=400, detail="indicator is required")
        c = conn()
        try:
            return threats.record_threat(
                c, kind=kind, indicator=indicator, category=category, severity=severity, source="user", notes=notes
            )
        finally:
            c.close()

    @app.delete("/api/v1/threats/{threat_id}")
    def remove_threat(
        threat_id: int,
        x_sms_wiz_token: Optional[str] = Header(default=None, alias="X-SMSWiz-Token"),
    ) -> dict[str, Any]:
        _check_token(x_sms_wiz_token)
        from . import threats

        c = conn()
        try:
            if not threats.delete_threat(c, threat_id):
                raise HTTPException(status_code=404, detail="threat not found")
            return {"status": "deleted", "id": threat_id}
        finally:
            c.close()

    @app.post("/api/v1/threats/backfill")
    def backfill_threats(
        x_sms_wiz_token: Optional[str] = Header(default=None, alias="X-SMSWiz-Token"),
    ) -> dict[str, int]:
        _check_token(x_sms_wiz_token)
        from . import threats

        c = conn()
        try:
            return threats.backfill_from_events(c)
        finally:
            c.close()

    @app.get("/api/v1/subscriptions")
    def subscription_list() -> dict[str, Any]:
        rows = _rows()
        return {
            **subscriptions.summary(rows),
            "price_changes": subscriptions.price_changes(rows)[:10],
        }

    @app.get("/api/v1/subscriptions/calendar")
    def subscription_calendar(days: int = Query(default=45, ge=1, le=180)) -> list[dict[str, Any]]:
        return subscriptions.calendar(_rows(), days=days)

    @app.get("/api/v1/cards")
    def card_list() -> dict[str, Any]:
        return cards.summary(_rows())

    # ------------------------------------------------------------------
    # Merchants, templates, rules
    # ------------------------------------------------------------------

    @app.get("/api/v1/merchants/stats")
    def merchant_stats() -> dict[str, Any]:
        return {"stats": merchants.stats(), "categories": merchants.categories()}

    @app.delete("/api/v1/merchants/{key}")
    def merchant_delete(key: str) -> dict[str, Any]:
        """Remove a user/learned row. Seeds are code and cannot be deleted here."""
        deleted = merchants.forget(key)
        if not deleted:
            raise HTTPException(status_code=404, detail="not found, or a curated seed")
        return {"status": "deleted", "key": key}

    @app.get("/api/v1/templates")
    def template_catalog() -> list[dict[str, Any]]:
        return templates.catalog()

    @app.get("/api/v1/export.csv", response_class=PlainTextResponse)
    def export_csv(month: Optional[str] = None) -> PlainTextResponse:
        """Every event as CSV. The exit door: no lock-in, no binary format."""
        c = conn()
        try:
            text = export.to_csv(export.event_rows(c, month=_check_month(month)))
        finally:
            c.close()
        name = export.filename(datetime.now(timezone.utc), "csv")
        return PlainTextResponse(
            text,
            media_type="text/csv; charset=utf-8",
            headers={"content-disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/v1/export.json", response_class=PlainTextResponse)
    def export_json(month: Optional[str] = None) -> PlainTextResponse:
        """The whole store in one object: events, refunds, vouchers, balances, dictionary."""
        c = conn()
        try:
            text = export.to_json(c, month=_check_month(month))
        finally:
            c.close()
        name = export.filename(datetime.now(timezone.utc), "json")
        return PlainTextResponse(
            text,
            media_type="application/json; charset=utf-8",
            headers={"content-disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/v1/rules")
    def rule_catalog() -> dict[str, Any]:
        active = rules.load()
        return {**rules.stats(active), "rules": [r.as_dict() for r in active]}

    @app.post("/api/v1/rules/evaluate")
    def rule_evaluate(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Debug endpoint: which offline rule fires for this message, if any."""
        sender = str(payload.get("sender") or "")
        body = str(payload.get("body") or "")
        if not body:
            raise HTTPException(status_code=400, detail="body is required")
        kind = str(payload.get("sender_kind") or extract.parse_sender(sender).entity_kind.value)
        verdict = rules.evaluate(sender, body, sender_kind=kind)
        return {
            "matched": verdict is not None,
            "verdict": verdict.as_dict() if verdict else None,
            "fallback": "full rule chain on ingest",
        }

    @app.post("/api/v1/filter/explain")
    def filter_explain(
        payload: dict[str, Any] = Body(...),
        x_sms_wiz_token: Optional[str] = Header(default=None, alias="X-SMSWiz-Token"),
    ) -> dict[str, Any]:
        """Everything the filter decided, for debugging on a real device.

        Never on Apple's path: the extension only ever gets {action, subAction}.
        """
        _check_token(x_sms_wiz_token)
        sender, body = _filter_payload(payload)
        if not body.strip():
            # Apple's extension must always get an answer it can act on. A 4xx
            # here means the filter silently stops filtering; `allow` is the safe
            # default because it changes nothing about the user's inbox.
            return FilterResponse(
                action=AppleAction.ALLOW,
                subAction=AppleSubAction.NONE,
                reason="no message body in the request; allowed rather than misfiled",
            ).model_dump()
        event = analyse(body, sender, merchants=merchants, source="filter")
        verdict = rules.evaluate(sender, body, sender_kind=event.sender.entity_kind.value)
        return {
            "rule": verdict.as_dict() if verdict else None,
            "intent": event.intent.value,
            "routing": {
                "action": event.routing.apple_action.value,
                "subAction": event.routing.apple_subaction.value,
                "retention": event.routing.retention.value,
            },
            "confidence": event.confidence.model_dump(),
            "flags": event.flags,
            "evidence": event.evidence,
            "forensics": {
                "score": event.analysis.get("forensic_score"),
                "reasons": event.analysis.get("forensic_reasons"),
            },
            "template": event.analysis.get("template"),
        }

    # ------------------------------------------------------------------
    # Notifications and digest
    # ------------------------------------------------------------------

    @app.get("/api/v1/notifications")
    def notification_plan(limit: int = Query(default=50, le=200)) -> list[dict[str, Any]]:
        """Alerts as delivery decisions: push / digest / silent, quiet hours applied."""
        c = conn()
        try:
            stored = list_alerts(c, limit=limit)
        finally:
            c.close()
        planned = notify.plan(stored)
        return [n.as_dict() for n in planned]

    @app.get("/api/v1/digest")
    def weekly_digest(days: int = Query(default=7, ge=1, le=90)) -> dict[str, Any]:
        c = conn()
        try:
            stored = list_alerts(c, limit=500)
        finally:
            c.close()
        planned = notify.plan(stored)
        return notify.digest(planned, period=f"{days} days")

    @app.get("/api/v1/health/llm")
    def llm_health() -> dict[str, Any]:
        """Whether enrichment is configured, and which providers are alive."""
        return ingestor.enricher.health()

    return app


app = create_app()
