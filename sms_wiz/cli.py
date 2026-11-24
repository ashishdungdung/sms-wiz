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
from typing import Any

from .db import connect
from .models import IngestRequest
from .pipeline import Ingestor
from .store import list_events, summarise_month

#: Realistic Indian messages, each with the outcome the engine must produce.
#: The dates are relative so the demo keeps working next year.
SAMPLE_MESSAGES: list[dict[str, str]] = [
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
        alerts = conn.execute("SELECT severity, message FROM alerts ORDER BY id").fetchall()
        print("\nalerts")
        for row in alerts:
            print(f"  [{row['severity']:8}] {row['message']}")
        print("\nkeyed events (detail)")
        for row in list_events(conn, limit=5):
            print(f"  {row['intent']:18} {row['sender_raw']:14} {row['dedup_status']:20} {row['merchant_clean'] or ''}")
    finally:
        conn.close()

    if tmp is not None:
        os.unlink(db_path)
    print(f"\n{len(SAMPLE_MESSAGES)} messages processed, {failures} low-confidence")
    return 0


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

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
