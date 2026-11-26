"""Ingest pipeline: classify -> deduplicate -> persist -> raise alerts.

This is the only place where an SMS becomes state. Everything before it is a
pure function; everything after it is a database write. That split makes the
whole thing replayable: feed the same messages again, get the same ledger.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from . import store
from . import extract
from .classify import analyse
from .db import connect
from .llm import LLMEnricher
from .merchants import MerchantDictionary
from .models import Alert, IngestRequest, IngestResponse, Intent, SmsEvent

IST = timezone(timedelta(hours=5, minutes=30))


def merchant_label(event: SmsEvent, fallback: str = "the merchant") -> str:
    """Never render ``None`` into a user-facing alert."""
    if event.merchant:
        return event.merchant.clean or event.merchant.raw or fallback
    return fallback


def _localise(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        # Indian users type local times; treat naive input as IST, not UTC,
        # so month boundaries do not silently shift.
        return value.replace(tzinfo=IST)
    return value


#: How far back a date written in a message may pull an event. Bank SMS arrive
#: same-day; a wider window is for archive imports and for a phone that was
#: offline for a week.
STATED_DATE_WINDOW_DAYS = 45


def _apply_stated_date(event: SmsEvent, received_at: datetime) -> SmsEvent:
    """Attribute the money to the day the message says it happened.

    "Rs.300 debited ... on 31-03-26", delivered on 2 April, belongs to March: a
    ledger filed by arrival time quietly moves spending between months. The time
    of day stays the arrival time, because the message only carries a date.

    Only a past date inside the window is used: "your ticket for 20-03-26" is a
    journey, not a purchase, and a date that is implausible for this message
    (future, or months old) is not trusted at all.
    """
    if event.ledger.treatment.value not in store.MONEY_TREATMENTS:
        return event

    stated = [
        hit.value.date()
        for hit in extract.find_dates(event.body, received_at)
        if received_at.date() - timedelta(days=STATED_DATE_WINDOW_DAYS)
        <= hit.value.date()
        <= received_at.date()
    ]
    if stated:
        best = max(stated)
        if best != received_at.date():
            event.received_at = received_at.replace(year=best.year, month=best.month, day=best.day)
            event.analysis["stated_date"] = best.isoformat()
            event.flags.append("dated_in_message")
        return event

    bad = extract.unparsed_dates(event.body, received_at)
    if bad:
        # "31-02-26" is not a day. Keep the arrival date and say so, rather than
        # silently banking a month's spend into the next one.
        event.flags.append("date_unparseable")
        event.analysis["date_unparseable"] = bad[0]
        event.confidence.needs_review = True
    return event


class Ingestor:
    def __init__(self, db_path: Optional[str] = None, enricher: Optional[LLMEnricher] = None) -> None:
        self.db_path = db_path
        self.merchants = MerchantDictionary(db_path)
        # Disabled unless SMSWIZ_LLM_* is configured. See llm.py for the rules.
        self.enricher = enricher if enricher is not None else LLMEnricher.from_env()

    # -- public ------------------------------------------------------------
    def ingest(self, request: IngestRequest) -> IngestResponse:
        received_at = _localise(request.received_at)
        event = analyse(
            request.body,
            request.sender,
            now=received_at,
            merchants=self.merchants,
            source=request.source,
        )
        event = _apply_stated_date(event, received_at)

        if self.enricher.enabled:
            event = self.enricher.enrich(event, self.merchants)

        conn = connect(self.db_path)
        try:
            alerts: list[Alert] = []
            duplicate_of = self._deduplicate(conn, event, alerts)
            store.save_event(conn, event)

            self._track_balance(conn, event)
            self._track_refund(conn, event, alerts)
            self._track_voucher(conn, event, alerts)
            self._risk_alerts(conn, event, alerts)

            store.save_alerts(conn, event.event_id, alerts)
        finally:
            conn.close()

        return IngestResponse(
            event=event,
            alerts=alerts,
            stored=True,
            duplicate_of=duplicate_of,
        )

    # -- internals ---------------------------------------------------------
    def _deduplicate(self, conn, event: SmsEvent, alerts: list[Alert]) -> Optional[str]:
        reference = event.analysis.get("reference")
        is_money = event.ledger.treatment.value in store.MONEY_TREATMENTS

        if is_money:
            # Byte-identical re-delivery (iCloud sync, carrier duplicate) is the
            # cheapest duplicate to catch and the one users hit most often.
            day = event.received_at.strftime("%Y-%m-%d")
            same = store.find_identical(
                conn,
                event.sender_raw,
                event.body,
                day,
                event.otp.code if event.otp else None,
                at=event.received_at,
            )
            if same:
                event.dedup.status = "suppressed_duplicate"
                event.dedup.duplicate_of = same["event_id"]
                event.dedup.reason = "identical message from the same sender was already recorded today"
                event.analysis["duplicate_kind"] = "identical_redelivery"
                alerts.append(
                    Alert(
                        severity="info",
                        kind="duplicate_suppressed",
                        message="Identical message delivered twice — the second copy was not counted again.",
                        data={"duplicate_of": same["event_id"]},
                    )
                )
                return same["event_id"]

        if reference and is_money:
            existing = store.find_by_reference(conn, str(reference))
            if existing:
                event.dedup.status = "suppressed_duplicate"
                event.dedup.duplicate_of = existing["event_id"]
                event.dedup.reason = (
                    f"reference {reference} already recorded as {existing['intent']}"
                    f" on {existing['sender_name'] or existing['sender_raw']}"
                )
                alerts.append(
                    Alert(
                        severity="info",
                        kind="duplicate_suppressed",
                        message=(
                            f"Linked duplicate suppressed — {event.summary or 'this message'} shares "
                            f"reference {reference} with an event already counted."
                        ),
                        data={"duplicate_of": existing["event_id"], "reference": reference},
                    )
                )
                return existing["event_id"]

        refund_intents = {Intent.REFUND_INITIATED.value, Intent.REFUND_CREDITED.value}
        if (
            is_money
            and event.amount
            and event.amount.currency == "INR"
            and event.intent.value not in refund_intents
        ):
            last4 = None
            if event.institution:
                last4 = event.institution.account_last4 or event.institution.card_last4
            day = event.received_at.strftime("%Y-%m-%d")
            candidate = store.find_possible_duplicate(
                conn,
                event.amount.value,
                day,
                last4,
                event.sender_raw,
                merchant=event.merchant.clean if event.merchant else None,
            )
            if candidate and candidate["intent"] in refund_intents:
                candidate = None
            if candidate:
                event.dedup.status = "possible_duplicate"
                event.dedup.duplicate_of = candidate["event_id"]
                shared = (
                    f"the same card ending {last4}"
                    if candidate.get("match_kind") == "instrument"
                    else f"the same merchant ({candidate.get('merchant_clean')})"
                )
                event.dedup.reason = (
                    f"same amount ₹{event.amount.value:,.2f} on the same day from a different sender "
                    f"({candidate['sender_name'] or candidate['sender_raw']}) and {shared}; "
                    "not merged automatically"
                )
                event.confidence.needs_review = True
                event.flags.append("possible_duplicate")
                alerts.append(
                    Alert(
                        severity="warning",
                        kind="possible_duplicate",
                        message=(
                            f"Two messages for ₹{event.amount.value:,.2f} today from different senders. "
                            "Both are counted until you confirm they are the same payment."
                        ),
                        data={"other_event": candidate["event_id"]},
                    )
                )
        return None

    def _track_balance(self, conn, event: SmsEvent) -> None:
        balance = event.analysis.get("balance_after")
        if not balance or not event.institution:
            return
        last4 = event.institution.account_last4 or event.institution.card_last4
        if not last4:
            return
        store.upsert_balance(
            conn,
            last4=last4,
            institution=event.institution.name,
            balance=float(balance),
            at=event.received_at.isoformat(),
        )

    def _track_refund(self, conn, event: SmsEvent, alerts: list[Alert]) -> None:
        if event.intent is Intent.REFUND_INITIATED:
            # Even without a figure this is a promise somebody owes you; a claim
            # with amount 0 means "amount unknown" and is matched by merchant.
            refund_id = store.open_refund(conn, event)
            # The claim stores the sender's brand when the text names none, so
            # the alert should say the same name the tracker will match on.
            merchant = store.refund_merchant(event) or merchant_label(event)
            money = (
                f"the ₹{event.amount.value:,.2f} refund"
                if event.amount
                else "the refund"
            )
            alerts.append(
                Alert(
                    severity="info",
                    kind="refund_tracking_started",
                    message=(
                        f"Tracking {money} from {merchant}"
                        + ("" if event.amount else " (amount not stated)")
                        + ". I will flag it if no credit is seen within 10 days."
                    ),
                    data={"refund_id": refund_id, "amount_known": bool(event.amount)},
                )
            )
        elif event.intent is Intent.REFUND_CREDITED and event.amount:
            merchant = merchant_label(event, fallback="") or None
            row, conflict = store.find_open_refund(conn, event.amount.value, merchant)
            if row:
                store.resolve_refund(conn, row["id"], event.event_id, event.received_at.isoformat())
                alerts.append(
                    Alert(
                        severity="info",
                        kind="refund_matched",
                        message=(
                            f"Refund matched and closed: ₹{event.amount.value:,.2f}"
                            f"{(' from ' + merchant) if merchant else ''} is now confirmed credited."
                        ),
                        data={"refund_id": row["id"]},
                    )
                )
            elif conflict:
                alerts.append(
                    Alert(
                        severity="warning",
                        kind="refund_amount_mismatch",
                        message=(
                            f"A ₹{event.amount.value:,.2f} credit arrived, but the merchant does not match "
                            "an open refund claim. Left open — check before closing it."
                        ),
                    )
                )
            else:
                # Refunds rarely land on the exact figure. A credit that is at
                # least the claimed amount, from the same merchant, is almost
                # always that claim (partly, or together with another order).
                cover = store.find_covering_refund(
                    conn, event.amount.value, merchant, at=event.received_at
                )
                if cover:
                    store.resolve_refund(conn, cover["id"], event.event_id, event.received_at.isoformat())
                    claimed = float(cover["amount"])
                    credit = event.amount.value
                    if credit > claimed + 0.01:
                        note = (
                            f"Closed, but the extra ₹{credit - claimed:,.2f} may belong to another order."
                        )
                    elif credit < claimed - 0.01:
                        note = "Closed as a partial refund."
                    else:
                        note = "Closed."
                    alerts.append(
                        Alert(
                            severity="warning",
                            kind="refund_amount_mismatch",
                            message=(
                                f"₹{credit:,.2f} credited against a ₹{claimed:,.2f} refund claim "
                                f"from {merchant}. {note}"
                            ),
                            data={"refund_id": cover["id"], "claimed": claimed},
                        )
                    )
                    return
                alerts.append(
                    Alert(
                        severity="info",
                        kind="refund_no_claim",
                        message=(
                            f"₹{event.amount.value:,.2f} credited as a refund, but there was no open claim. "
                            "Recorded as a refund, not income."
                        ),
                    )
                )

    def _track_voucher(self, conn, event: SmsEvent, alerts: list[Alert]) -> None:
        if event.intent is not Intent.VOUCHER_ISSUED or not event.voucher:
            return
        if store.save_voucher(conn, event):
            parts = [event.voucher.brand or "Voucher"]
            if event.voucher.value:
                parts.append(f"₹{event.voucher.value:,.0f}")
            if event.voucher.expiry:
                parts.append(f"expires {event.voucher.expiry:%d %b %Y}")
            alerts.append(
                Alert(
                    severity="info",
                    kind="voucher_saved",
                    message="Saved to voucher vault: " + " · ".join(parts),
                )
            )

    def _risk_alerts(self, conn, event: SmsEvent, alerts: list[Alert]) -> None:
        if event.intent is Intent.PHISHING_SUSPECT:
            alerts.append(
                Alert(
                    severity="critical",
                    kind="phishing_suspect",
                    message=(
                        "Suspected phishing — quarantined. Do not tap the link or call any number in it. "
                        + (event.analysis.get("forensic_reasons") or [""])[0]
                    ),
                    data={"reasons": event.analysis.get("forensic_reasons", [])},
                )
            )

        if event.otp and event.otp.risk == "high":
            purpose = event.otp.purpose.replace("_", " ")
            alerts.append(
                Alert(
                    severity="warning",
                    kind="high_risk_otp",
                    message=(
                        f"High-risk OTP: {purpose}"
                        + (f" for {event.otp.service}" if event.otp.service else "")
                        + ". Sharing this code can authorise a recurring debit. Verify you started this."
                    ),
                    data={"purpose": event.otp.purpose, "service": event.otp.service},
                )
            )

        if event.flags and "suspicious_link" in event.flags:
            alerts.append(
                Alert(
                    severity="warning",
                    kind="suspicious_link",
                    message=(
                        "This message has signals we could not verify (link/sender mismatch). "
                        "Use the official app instead of the link."
                    ),
                    data={"reasons": event.analysis.get("forensic_reasons", [])},
                )
            )

        if event.intent is Intent.UPCOMING_DEBIT and event.amount and event.reminder:
            last4 = event.reminder.target_account_last4 or (
                event.institution.account_last4 if event.institution else None
            )
            if last4:
                balance = store.get_balance(conn, last4)
                if balance and balance["balance"] < event.amount.value:
                    shortfall = event.amount.value - balance["balance"]
                    alerts.append(
                        Alert(
                            severity="critical",
                            kind="debit_shortfall",
                            message=(
                                f"Possible bounce: ₹{event.amount.value:,.2f} auto-debit on "
                                f"{balance['institution'] or 'bank'} XX{last4}, but last known balance is "
                                f"₹{balance['balance']:,.2f}. Shortfall ₹{shortfall:,.2f}."
                            ),
                            data={
                                "account_last4": last4,
                                "required": event.amount.value,
                                "known_balance": balance["balance"],
                                "balance_as_of": balance["updated_at"],
                            },
                        )
                    )
                elif balance:
                    alerts.append(
                        Alert(
                            severity="info",
                            kind="debit_covered",
                            message=(
                                f"₹{event.amount.value:,.2f} auto-debit looks covered "
                                f"(last known balance ₹{balance['balance']:,.2f} on XX{last4}, "
                                f"as of {balance['updated_at'][:10]})."
                            ),
                        )
                    )
                else:
                    alerts.append(
                        Alert(
                            severity="warning",
                            kind="debit_balance_unknown",
                            message=(
                                f"₹{event.amount.value:,.2f} auto-debit on XX{last4}, but I have no balance "
                                "for that account yet — I cannot check for a shortfall."
                            ),
                        )
                    )

        if event.intent is Intent.UNKNOWN:
            alerts.append(
                Alert(
                    severity="info",
                    kind="needs_review",
                    message="Could not classify this message confidently. Kept in the inbox and queued for review.",
                )
            )


def reclassify_stored(conn: object, merchants: MerchantDictionary) -> dict[str, int]:
    """Re-run the deterministic classifier over all stored events in `conn`.

    Preserves `event_id`, `received_at`, `dedup_status`, and `duplicate_of`,
    and skips already-redacted OTP messages so masked `<otp>` tokens are not
    downgraded to `unknown`.
    """
    import json
    from .models import DedupInfo

    rows = conn.execute(
        "SELECT event_id, received_at, sender_raw, body, intent, dedup_status, duplicate_of, payload FROM events"
    ).fetchall()
    updated = 0
    for row in rows:
        body = row["body"] or ""
        try:
            payload = json.loads(row["payload"])
        except Exception:
            payload = {}
        if payload.get("body_redacted") or ("<otp>" in body and row["intent"] == Intent.OTP.value):
            continue
        recv = datetime.fromisoformat(row["received_at"])
        source = payload.get("source", "import")
        new_event = analyse(body, row["sender_raw"] or "", merchants=merchants, source=source)
        new_event.received_at = recv
        new_event = _apply_stated_date(new_event, recv)
        new_event.event_id = row["event_id"]
        new_event.dedup = DedupInfo(
            status=row["dedup_status"],
            duplicate_of=row["duplicate_of"],
            reason=payload.get("dedup", {}).get("reason", ""),
        )
        if (
            new_event.intent.value != row["intent"]
            or (new_event.merchant and new_event.merchant.clean != (payload.get("merchant") or {}).get("clean"))
        ):
            updated += 1
        store.save_event(conn, new_event)
    conn.commit()
    return {"processed": len(rows), "updated": updated, "unchanged": len(rows) - updated}
