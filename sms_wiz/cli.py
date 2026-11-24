"""Command line entry point.

    python -m sms_wiz.cli demo          # run the Indian edge-case benchmark
    python -m sms_wiz.cli serve         # run the HTTP API on :8000
    python -m sms_wiz.cli ingest -s AD-HDFCBK-T -b "Rs 500 debited..."
    python -m sms_wiz.cli summary --month 2025-03
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import analytics, cards, notify, subscriptions
from .db import connect
from .merchants import MerchantDictionary
from .models import IngestRequest
from .pipeline import Ingestor
from .rules import stats as rule_stats
from .rules import validate_file as validate_rules
from .store import list_alerts, list_events, summarise_month

#: Realistic Indian messages, each with the outcome the engine must produce.
#: The dates are relative so the demo keeps working next year.
SAMPLE_MESSAGES: list[dict[str, str]] = [
    # -- the money path ---------------------------------------------------
    {
        "name": "Bank debit with an EMI ad",
        "sender": "AD-HDFCBK-T",
        "body": (
            "Rs.1,450.00 debited from A/c XX4092 on {d0} to VPA KIRANAKART@axisbank "
            "(UPI Ref 505612345678). Avl Bal: Rs.5,000.00. Convert this spend into EasyEMI! bit.ly/hdfcemi"
        ),
        "expect": "expense",
    },
    {
        "name": "Same payment, second app",
        "sender": "AX-PHONPE-S",
        "body": "Paid Rs. 1450 to Zepto (KIRANAKART TECHNOLOGIES) via HDFC Bank XX4092. UPI Ref: 505612345678.",
        "expect": "expense (suppressed duplicate)",
    },
    {
        "name": "NACH pre-debit, balance too low",
        "sender": "VM-HDFCBK-S",
        "body": (
            "Dear Customer, your e-Mandate for Rs 12500.00 towards BAJAJ FINANCE EMI will be presented "
            "on {d2} on A/c XX4092. Please maintain sufficient balance."
        ),
        "expect": "upcoming_debit + shortfall alert",
    },
    {
        "name": "Bank loan spam on a service header",
        "sender": "AD-HDFCBK-S",
        "body": (
            "Congrats! You have a Pre-Approved Personal Loan of Rs 5,00,000 in 10 seconds with zero "
            "documentation. Apply now!"
        ),
        "expect": "bank_upsell",
    },
    {
        "name": "Fake e-challan APK from a mobile number",
        "sender": "9876543210",
        "body": (
            "RTO Parivahan Notice: Vehicle Challan of Rs 1000 pending. Download official app to view "
            "photo and pay: http://parivahan-echallan-pay.com/echallan.apk"
        ),
        "expect": "phishing_suspect",
    },
    {
        "name": "Credit card bill paid from bank account",
        "sender": "AD-ICICIB-S",
        "body": "Payment of Rs 18,450.00 towards your ICICI Credit Card XX4092 has been received. Thank you.",
        "expect": "cc_bill_payment (excluded from spend)",
    },
    {
        "name": "Refund promised",
        "sender": "AD-AMAZON-S",
        "body": (
            "Refund of Rs 1,850 for your Amazon order has been initiated and will be credited to your "
            "HDFC Bank A/c XX4092 within 5-7 working days."
        ),
        "expect": "refund_initiated",
    },
    {
        "name": "Refund actually credited",
        "sender": "AD-HDFCBK-T",
        "body": (
            "Rs 1,850.00 credited to your A/c XX4092 on {d5} towards Amazon refund. "
            "Avl Bal: Rs 12,300.00"
        ),
        "expect": "refund_credited + claim closed",
    },
    {
        "name": "Voucher with code and expiry",
        "sender": "AD-AMAZON-S",
        "body": (
            "You have received a Rs 1,000 Amazon Pay gift card. Voucher code: AMZP-4K9Q-7T2X. "
            "PIN: 982104. Expires {d30}."
        ),
        "expect": "voucher_issued",
    },
    {
        "name": "High-risk mandate OTP",
        "sender": "VM-NETFLX-S",
        "body": (
            "123456 is your OTP to register e-mandate of Rs 15,000/month for Netflix AutoPay. "
            "Valid 10 mins. Do not share."
        ),
        "expect": "otp (high risk)",
    },
    {
        "name": "Delivery handover code",
        "sender": "AD-AMAZON-S",
        "body": "Your Amazon delivery OTP is 8492. Share it with the delivery agent only.",
        "expect": "logistics + handover code",
    },
    {
        "name": "FASTag toll",
        "sender": "AD-ICICIB-T",
        "body": (
            "Rs 65.00 deducted from your FASTag (ICICI Bank) for Electronic City Toll Plaza on {d1}. "
            "FASTag balance: Rs 420.00"
        ),
        "expect": "fastag_toll",
    },
    {
        "name": "IRCTC booking",
        "sender": "AD-IRCTC-S",
        "body": (
            "Your ticket has been booked. PNR: 2459182736, Train 12627, Coach B2, Berth 45. Chart prepared."
        ),
        "expect": "logistics (train)",
    },
    {
        "name": "Salary credit",
        "sender": "AD-HDFCBK-T",
        "body": (
            "Rs 85,000.00 credited to your A/c XX1094 on {d1} by NEFT from INFOSYS LTD. "
            "Avl Bal: Rs 91,000.00"
        ),
        "expect": "income",
    },
    # -- everyday spending across rails -----------------------------------
    {
        "name": "ICICI UPI debit at BigBasket",
        "sender": "AD-ICICIB-T",
        "body": (
            "INR 1,299.00 has been debited from your a/c XX7781 on {d0} to BIGBASKET@ybl. "
            "UPI Ref 509988776655. Avl Bal INR 12,431.55"
        ),
        "expect": "expense (groceries)",
    },
    {
        "name": "Uber ride paid by UPI",
        "sender": "AD-HDFCBK-T",
        "body": "Rs. 248.00 debited from A/c XX4092 to VPA UBER@axisbank (UPI Ref 507700112233) on {d0}.",
        "expect": "expense (transport)",
    },
    {
        "name": "Jio recharge confirmation",
        "sender": "AD-JIO-S",
        "body": "Recharge of Rs 349 successful for 9876543210. Validity 28 days.",
        "expect": "expense (utilities)",
    },
    {
        "name": "ATM cash withdrawal",
        "sender": "AD-HDFCBK-T",
        "body": "Rs.2,000.00 withdrawn from A/c XX4092 at HDFC ATM, MG Road on {d0}. Avl Bal: Rs.3,000.00",
        "expect": "cash_withdrawal (not spend)",
    },
    {
        "name": "FASTag recharge",
        "sender": "AD-HDFCBK-T",
        "body": "FASTag recharge of Rs 500 successful on your HDFC FASTag. Balance Rs 920.",
        "expect": "instrument_load (not spend)",
    },
    {
        "name": "Card spend at Croma",
        "sender": "SBICRD-T",
        "body": "Rs 3,499 spent at CROMA on your SBI Card XX4433 on {d0}. Total due Rs 12,480.",
        "expect": "expense (shopping)",
    },
    # -- recurring payments and bills -------------------------------------
    {
        "name": "Netflix monthly debit",
        "sender": "AD-HDFCBK-T",
        "body": "Rs.649.00 debited from A/c XX4092 on {d0} to VPA NETFLIX@icici (UPI Ref 507711223344).",
        "expect": "expense (subscription)",
    },
    {
        "name": "Netflix debit last month",
        "sender": "AD-HDFCBK-T",
        "body": "Rs.649.00 debited from A/c XX4092 on {m30} to VPA NETFLIX@icici (UPI Ref 506611223344).",
        "expect": "expense (subscription history)",
    },
    {
        "name": "Spotify auto-pay notice",
        "sender": "VM-SPOTFY-S",
        "body": "Your Spotify AutoPay of Rs 119 will be debited from A/c XX4092 on {d2}.",
        "expect": "upcoming_debit (subscription)",
    },
    {
        "name": "Licence premium due",
        "sender": "AD-LICIND-S",
        "body": "LIC Policy 887766: Premium of Rs 15,340 is due on {d30}. Pay now to avoid lapse.",
        "expect": "bill_due",
    },
    {
        "name": "Kotak NACH EMI",
        "sender": "AD-KOTAKB-S",
        "body": (
            "NACH Mandate: Rs 9,999 will be debited from your Kotak A/c XX5566 on {d2} towards "
            "BAJAJ FINANCE."
        ),
        "expect": "upcoming_debit",
    },
    {
        "name": "SBI card statement",
        "sender": "SBICRD-T",
        "body": (
            "Your SBI Card XX4433 statement is generated. Total due Rs 12,480, minimum due Rs 624, "
            "due date {d30}."
        ),
        "expect": "bill_due (card)",
    },
    # -- investments -------------------------------------------------------
    {
        "name": "Groww SIP debit",
        "sender": "AD-GROWW-S",
        "body": "SIP of Rs 5,000 for Groww Nifty 50 Index Fund debited from XX4092 via mandate. Units allotted.",
        "expect": "investment (not spend)",
    },
    {
        "name": "Dividend credited",
        "sender": "AD-CDSL-S",
        "body": "Dividend of Rs 1,200 credited to your demat account for INFOSYS LTD on {d0}.",
        "expect": "investment (not income)",
    },
    # -- multilingual ------------------------------------------------------
    {
        "name": "Hindi SBI debit",
        "sender": "VM-SBIN-T",
        "body": (
            "प्रिय ग्राहक, आपके खाते XX1094 से रु २,५००.०० दिनांक {d0} को SWIGGY को "
            "डेबिट किए गए हैं (UPI Ref ५१२३४५६७८९०१). उपलब्ध शेष: रु ८,२००.००"
        ),
        "expect": "expense (Devanagari)",
    },
    {
        "name": "Tamil debit",
        "sender": "AD-ICICIB-T",
        "body": "உங்கள் கணக்கு XX7781 இல் ரூ. 899 பற்று. இருப்பு ரூ. 4,200.",
        "expect": "expense (Tamil)",
    },
    {
        "name": "Telugu debit",
        "sender": "AD-HDFCBK-T",
        "body": "మీ ఖాతా XX4092 నుండి రూ. 1,150 డెబిట్. బ్యాలెన్స్ రూ. 3,850.",
        "expect": "expense (Telugu)",
    },
    # -- safety and government --------------------------------------------
    {
        "name": "Fake KYC link from a shortcode",
        "sender": "57575",
        "body": "Dear SBI customer, your KYC expires today. Update now: http://sbi-kyc-verify.online/upd",
        "expect": "phishing_suspect",
    },
    {
        "name": "Legit government challan",
        "sender": "AD-VAHAN-S",
        "body": "Parivahan e-Challan: Rs 500 for KA01AB1234 on {d0}. Pay via the official Parivahan portal.",
        "expect": "govt_notice",
    },
]

def _fmt_date(now: datetime, days: int) -> str:
    return (now + timedelta(days=days)).strftime("%d-%m-%y")


def run_demo(db_path: str | None = None, verbose: bool = False) -> int:
    tmp = None
    if db_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = tmp.name

    ingestor = Ingestor(db_path)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    rows: list[tuple[str, str, str, str, str]] = []
    failures = 0

    print(f"database: {db_path}\n")
    header = f"{'#':>2}  {'message':38} {'intent':17} {'amount':>11}  {'apple folder':32} review"
    print(header)
    print("-" * len(header))

    for index, sample in enumerate(SAMPLE_MESSAGES, 1):
        body = sample["body"].format(
            d0=_fmt_date(now, -1),
            d1=_fmt_date(now, 0),
            d2=_fmt_date(now, 2),
            d5=_fmt_date(now, 3),
            d30=_fmt_date(now, 30),
            m7=_fmt_date(now, -7),
            m30=_fmt_date(now, -30),
        )
        received = now + timedelta(minutes=index)
        response = ingestor.ingest(
            IngestRequest(sender=sample["sender"], body=body, received_at=received, source="import")
        )
        event = response.event
        amount = f"₹{event.amount.value:,.2f}" if event.amount else "-"
        folder = f"{event.routing.apple_action.value}/{event.routing.apple_subaction.value}"
        review = "yes" if event.confidence.needs_review else "no"
        if event.dedup.status == "suppressed_duplicate":
            folder = "duplicate of earlier event"
        print(f"{index:>2}  {sample['name'][:38]:38} {event.intent.value:17} {amount:>11}  {folder:32} {review}")
        if verbose:
            for item in event.evidence:
                print(f"      · {item}")
            for alert in response.alerts:
                print(f"      ! [{alert.severity}] {alert.message}")
        rows.append((sample["name"], sample["expect"], event.intent.value, event.ledger.treatment.value, review))
        if event.confidence.band == "low":
            failures += 1

    conn = connect(db_path)
    try:
        month = now.strftime("%Y-%m")
        summary = summarise_month(conn, month)
        print("\nledger for", month)
        print(f"  spend (counted)   ₹{summary.spend_total:,.2f}")
        print(f"  income            ₹{summary.income_total:,.2f}")
        print(f"  refunds           ₹{summary.refunds_total:,.2f}")
        print(f"  excluded          ₹{summary.excluded_total:,.2f}  {summary.excluded_reasons}")
        print(f"  needs review      {summary.needs_review_count} of {summary.event_count} events")
        alerts = conn.execute("SELECT kind, severity, message FROM alerts ORDER BY id").fetchall()
        alerts_for_digest = [dict(row) for row in alerts]
        print("\nalerts")
        for row in alerts:
            print(f"  [{row['severity']:8}] {row['message']}")
        print("\nkeyed events (detail)")
        for row in list_events(conn, limit=5):
            print(f"  {row['intent']:18} {row['sender_raw']:14} {row['dedup_status']:20} {row['merchant_clean'] or ''}")

        # ---- derived views: the same rows, read four more ways ----------
        all_rows = list_events(conn, limit=500)
    finally:
        conn.close()

    overview = analytics.overview(all_rows, month=month)
    print("\nweekly spend (last 4 ISO weeks)")
    for bucket in overview["weeks"]:
        print(f"  {bucket['label']}  ₹{bucket['spend']:>10,.2f}  {bucket['count']:>2} events  essential {bucket['essential_share']*100:>4.0f}%")
    upi = overview["upi"]
    print(f"\nUPI: {upi['transactions']} payments, ₹{upi['spend']:,.2f} (avg ₹{upi['average']:,.2f})")
    if overview["top_merchants"]:
        print("top merchants")
        for merchant in overview["top_merchants"]:
            print(f"  {merchant['merchant']:24} ₹{merchant['spend']:>9,.2f}  {merchant['category']}")

    subs = subscriptions.summary(all_rows)
    print(f"\nsubscriptions: {subs['count']} recurring, ₹{subs['monthly_commitment']:,.2f}/month committed")
    for series in subs["series"][:6]:
        due = series["next_due"] or "-"
        change = series["price_change"]
        note = f"  (price {change['from']}→{change['to']})" if change else ""
        print(f"  {series['name']:22} {series['cadence']:10} ₹{(series['amount'] or 0):>8,.2f} due {due}{note}")

    card_view = cards.summary(all_rows)
    if card_view["cards"]:
        print("\ncredit cards")
        for card in card_view["cards"]:
            print(f"  {card['bank'] or 'card'} XX{card['last4']}: statement ₹{card['statement_amount'] or 0:,.2f} "
                  f"paid ₹{card['paid']:,.2f} → {card['status']}")

    planned = notify.plan([
        {"kind": alert["kind"], "severity": alert["severity"], "message": alert["message"], "data": {}}
        for alert in alerts_for_digest
    ])
    print("\nnotifications")
    for item in planned:
        print(f"  [{item.severity:8}] {item.channel:6} {item.title}")
    print(f"\ndigest: {notify.digest(planned)['headline']}")

    if tmp is not None:
        os.unlink(db_path)
    print(f"\n{len(SAMPLE_MESSAGES)} messages processed, {failures} low-confidence")
    return 0


def _db_path(explicit: str | None) -> str:
    """Where the CLI reads from: --db, else SMSWIZ_DB, else the default file."""
    return explicit or os.environ.get("SMSWIZ_DB") or str(Path.home() / ".sms_wiz" / "sms_wiz.db")


def _stored_rows(explicit: str | None) -> list[dict[str, Any]]:
    connection = connect(_db_path(explicit))
    try:
        return list_events(connection, limit=2000)
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sms_wiz", description="Indian SMS brain")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="run the built-in benchmark")
    demo.add_argument("--db", default=None)
    demo.add_argument("-v", "--verbose", action="store_true")

    serve = sub.add_parser("serve", help="run the HTTP API")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--db", default=None)

    ingest = sub.add_parser("ingest", help="classify one message")
    ingest.add_argument("-s", "--sender", required=True)
    ingest.add_argument("-b", "--body", required=True)
    ingest.add_argument("--db", default=None)
    ingest.add_argument("--json", action="store_true")

    summary = sub.add_parser("summary", help="monthly rollup")
    summary.add_argument("--month", required=True)
    summary.add_argument("--db", default=None)

    analytics_cmd = sub.add_parser("analytics", help="month / week / UPI views over what is stored")
    analytics_cmd.add_argument("--month", default=None)
    analytics_cmd.add_argument("--db", default=None)

    subs = sub.add_parser("subscriptions", help="recurring series and the next 45 days")
    subs.add_argument("--days", type=int, default=45)
    subs.add_argument("--db", default=None)

    cards_cmd = sub.add_parser("cards", help="credit-card statement vs payment state")
    cards_cmd.add_argument("--db", default=None)

    merchants_cmd = sub.add_parser("merchants", help="dictionary stats, or search with --q")
    merchants_cmd.add_argument("--q", default=None)
    merchants_cmd.add_argument("--db", default=None)

    digest_cmd = sub.add_parser("digest", help="what would have been pushed vs digested")
    digest_cmd.add_argument("--db", default=None)

    rules_cmd = sub.add_parser("rules-check", help="validate rules/filter_rules.json")
    rules_cmd.add_argument("--path", default=None)

    args = parser.parse_args(argv)

    if args.command == "demo":
        return run_demo(args.db, verbose=args.verbose)

    if args.command == "serve":
        import uvicorn

        if args.db:
            os.environ["SMSWIZ_DB"] = args.db
        uvicorn.run("sms_wiz.api:app", host=args.host, port=args.port, reload=False)
        return 0

    if args.command == "ingest":
        ingestor = Ingestor(args.db)
        response = ingestor.ingest(IngestRequest(sender=args.sender, body=args.body))
        if args.json:
            print(json.dumps(response.model_dump(mode="json"), indent=2, ensure_ascii=False))
        else:
            event = response.event
            print(f"intent      {event.intent.value}  ({event.confidence.score:.2f}, review={event.confidence.needs_review})")
            print(f"summary     {event.summary}")
            print(f"ledger      {event.ledger.treatment.value}  counts_toward_spend={event.ledger.counts_toward_spend}")
            print(f"apple       {event.routing.apple_action.value} / {event.routing.apple_subaction.value}")
            print(f"retention   {event.routing.retention.value}")
            print("evidence    " + "\n            ".join(event.evidence))
            for alert in response.alerts:
                print(f"alert       [{alert.severity}] {alert.message}")
        return 0

    if args.command == "summary":
        conn = connect(args.db)
        try:
            result: Any = summarise_month(conn, args.month)
        finally:
            conn.close()
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0

    if args.command == "rules-check":
        problems = validate_rules(Path(args.path) if args.path else None)
        if problems:
            for problem in problems:
                print(f"  ✗ {problem}")
            print("\nrule file rejected — nothing would ship")
            return 1
        stats = rule_stats()
        print(f"rules OK — {stats['rules']} rules {stats['by_action']}")
        print("sub-actions used: " + ", ".join(stats["sub_actions_used"]))
        return 0

    if args.command in {"analytics", "subscriptions", "cards", "merchants", "digest"}:
        rows = _stored_rows(args.db)
        if args.command == "analytics":
            overview = analytics.overview(rows, month=args.month)
            month = overview["month"]
            print(f"month {month['label']}  spend ₹{month['spend']:,.2f}  income ₹{month['income']:,.2f}  refunds ₹{month['refunds']:,.2f}")
            print(f"excluded ₹{month['excluded']:,.2f}  {month['by_treatment']}")
            print(f"essential share {month['essential_share'] * 100:.0f}%  ·  {month['count']} counted events")
            for week in overview["weeks"]:
                print(f"  {week['label']}  ₹{week['spend']:>10,.2f}  {week['count']:>2} events")
            upi = overview["upi"]
            print(f"UPI {upi['transactions']} payments ₹{upi['spend']:,.2f} (avg ₹{upi['average']:,.2f})")
            return 0

        if args.command == "subscriptions":
            summary_data = subscriptions.summary(rows)
            print(f"{summary_data['count']} recurring · ₹{summary_data['monthly_commitment']:,.2f}/month · ₹{summary_data['annualised']:,.2f}/year")
            for series in summary_data["series"]:
                print(f"  {series['name']:22} {series['cadence']:10} ₹{(series['amount'] or 0):>9,.2f}  conf {series['confidence']:.2f}  due {series['next_due'] or '-'}")
            print(f"\nnext {args.days} days")
            for entry in subscriptions.calendar(rows, days=args.days):
                print(f"  {entry['date']}  {entry['name']:22} ₹{entry['amount']:>9,.2f}")
            return 0

        if args.command == "cards":
            view = cards.summary(rows)
            if not view["cards"]:
                print("no card statements or payments stored")
            for card in view["cards"]:
                print(
                    f"  {card['bank'] or 'card'} XX{card['last4']}: statement ₹{(card['statement_amount'] or 0):,.2f}"
                    f"  paid ₹{card['paid']:,.2f}  outstanding ₹{(card['outstanding'] or 0):,.2f}  → {card['status']}"
                )
            print(f"total outstanding ₹{view['total_outstanding']:,.2f}")
            return 0

        if args.command == "merchants":
            dictionary = MerchantDictionary(_db_path(args.db))
            if args.q:
                needle = args.q.strip().upper()
                matches = [
                    row
                    for row in dictionary.all()
                    if needle in row["raw_key"] or needle in (row["clean_name"] or "").upper()
                ]
                for row in matches[:40]:
                    print(f"  {row['raw_key']:28} {row['clean_name'] or '':24} {row['category'] or '':28} [{row['source']}]")
                print(f"{len(matches)} match{'es' if len(matches) != 1 else ''} for {args.q!r}")
            else:
                stats = dictionary.stats()
                print(f"{stats['total']} keys  {stats}")
                for category in dictionary.categories():
                    print(f"  {category['label']:32} {category['count']:>3} keys  [{category['group']}]")
            return 0

        if args.command == "digest":
            connection = connect(_db_path(args.db) if args.db else None)
            try:
                stored = list_alerts(connection, limit=500)
            finally:
                connection.close()
            planned = notify.plan(stored)
            for item in planned:
                print(f"  [{item.severity:8}] {item.channel:6} {item.kind:24} {item.title}")
            print("\n" + notify.digest(planned)["headline"])
            return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
