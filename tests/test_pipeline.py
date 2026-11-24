from __future__ import annotations

from datetime import timedelta

import pytest

from sms_wiz.models import IngestRequest
from sms_wiz.store import (
    list_alerts,
    list_balances,
    list_refunds,
    list_vouchers,
    summarise_month,
)

from .conftest import NOW

BANK_DEBIT = (
    "Rs.1,450.00 debited from A/c XX4092 on 25-02-25 to VPA KIRANAKART@axisbank "
    "(UPI Ref 505612345678). Avl Bal: Rs.5,000.00. Convert this spend into EasyEMI! bit.ly/hdfcemi"
)
PHONEPE_CONFIRMATION = (
    "Paid Rs. 1450 to Zepto (KIRANAKART TECHNOLOGIES) via HDFC Bank XX4092. UPI Ref: 505612345678."
)


def ingest(ingestor, sender, body, at=NOW, source="import"):
    return ingestor.ingest(IngestRequest(sender=sender, body=body, received_at=at, source=source))


class TestDeduplication:
    def test_same_upi_reference_is_counted_once(self, ingestor, conn):
        first = ingest(ingestor, "AD-HDFCBK-T", BANK_DEBIT)
        second = ingest(ingestor, "AX-PHONPE-S", PHONEPE_CONFIRMATION)

        assert first.event.dedup.status == "unique"
        assert second.event.dedup.status == "suppressed_duplicate"
        assert second.event.dedup.duplicate_of == first.event.event_id
        assert second.duplicate_of == first.event.event_id
        # Posted date, not delivery date: the body says 25-02-25.
        assert summarise_month(conn, "2025-02").spend_total == 1450.0
        assert any(alert.kind == "duplicate_suppressed" for alert in second.alerts)

    def test_same_amount_different_reference_is_not_merged(self, ingestor, conn):
        ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Rs 500.00 debited from A/c XX4092 to VPA chai@okhdfcbank (UPI Ref 111111111111).",
        )
        second = ingest(
            ingestor,
            "AD-ICICIB-T",
            "Rs 500.00 debited from A/c XX4092 to VPA coffee@okicici (UPI Ref 222222222222).",
        )
        # Both are real payments: counted, but surfaced for a human to confirm.
        assert second.event.dedup.status == "possible_duplicate"
        assert second.event.confidence.needs_review is True
        assert summarise_month(conn, NOW.strftime("%Y-%m")).spend_total == 1000.0
        assert any(alert.kind == "possible_duplicate" for alert in second.alerts)

    def test_replaying_the_same_message_does_not_double_count(self, ingestor, conn):
        for _ in range(3):
            ingest(ingestor, "AD-HDFCBK-T", BANK_DEBIT)
        assert summarise_month(conn, "2025-02").spend_total == 1450.0


class TestRefundWatchdog:
    def test_refund_claim_closes_when_credit_arrives(self, ingestor, conn):
        opened = ingest(
            ingestor,
            "AD-AMAZON-S",
            "Refund of Rs 1,850 for your Amazon order has been initiated and will be credited within 5-7 days.",
        )
        assert any(alert.kind == "refund_tracking_started" for alert in opened.alerts)

        closed = ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Rs 1,850.00 credited to your A/c XX4092 towards Amazon refund.",
            at=NOW + timedelta(days=3),
        )
        assert any(alert.kind == "refund_matched" for alert in closed.alerts)
        refunds = list_refunds(conn)
        assert len(refunds) == 1
        assert refunds[0]["status"] == "credited"
        assert refunds[0]["matched_event_id"] == closed.event.event_id

    def test_credit_with_a_different_merchant_does_not_close_the_claim(self, ingestor, conn):
        ingest(
            ingestor,
            "AD-AMAZON-S",
            "Refund of Rs 1,850 for your Amazon order has been initiated and will be credited within 5-7 days.",
        )
        other = ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Rs 1,850.00 credited to your A/c XX4092 towards Flipkart refund.",
            at=NOW + timedelta(days=3),
        )
        assert any(alert.kind == "refund_amount_mismatch" for alert in other.alerts)
        assert list_refunds(conn)[0]["status"] == "awaiting"

    def test_credit_without_a_claim_is_a_refund_not_income(self, ingestor, conn):
        event = ingest(
            ingestor, "AD-HDFCBK-T", "Rs 700.00 credited to your A/c XX4092 towards Swiggy refund."
        )
        assert any(alert.kind == "refund_no_claim" for alert in event.alerts)
        summary = summarise_month(conn, NOW.strftime("%Y-%m"))
        assert summary.income_total == 0.0
        assert summary.refunds_total == 700.0


class TestBalanceAndBounceShield:
    def test_shortfall_is_critical(self, ingestor, conn):
        ingest(ingestor, "AD-HDFCBK-T", BANK_DEBIT)  # records ₹5,000 on XX4092
        event = ingest(
            ingestor,
            "VM-HDFCBK-S",
            "Your e-Mandate for Rs 12,500.00 towards BAJAJ FINANCE EMI will be presented on 05-03-25 "
            "on A/c XX4092. Maintain sufficient balance.",
        )
        alert = next(a for a in event.alerts if a.kind == "debit_shortfall")
        assert alert.severity == "critical"
        assert "7,500" in alert.message
        assert list_balances(conn)[0]["balance"] == 5000.0

    def test_covered_debit_is_informational(self, ingestor):
        ingest(ingestor, "AD-HDFCBK-T", "Rs 500.00 debited from A/c XX9999. Avl Bal: Rs.20,000.00")
        event = ingest(
            ingestor,
            "VM-HDFCBK-S",
            "Your e-Mandate for Rs 5,000.00 towards HDFC Home Loan will be presented on 05-03-25 on A/c XX9999.",
        )
        assert any(a.kind == "debit_covered" for a in event.alerts)

    def test_unknown_balance_is_stated_not_guessed(self, ingestor):
        event = ingest(
            ingestor,
            "VM-HDFCBK-S",
            "Your e-Mandate for Rs 5,000.00 towards HDFC Home Loan will be presented on 05-03-25 on A/c XX7777.",
        )
        assert any(a.kind == "debit_balance_unknown" for a in event.alerts)


class TestVaultAndQueues:
    VOUCHER = (
        "You have received a Rs 1,000 Amazon Pay gift card. Voucher code: AMZP-4K9Q-7T2X. "
        "PIN: 982104. Expires 30-Apr-2025."
    )

    def test_voucher_saved_once(self, ingestor, conn):
        first = ingest(ingestor, "AD-AMAZON-S", self.VOUCHER)
        second = ingest(ingestor, "AD-AMAZON-S", self.VOUCHER)
        assert any(alert.kind == "voucher_saved" for alert in first.alerts)
        assert not any(alert.kind == "voucher_saved" for alert in second.alerts)
        vouchers = list_vouchers(conn)
        assert len(vouchers) == 1
        assert vouchers[0]["code"] == "AMZP-4K9Q-7T2X"
        # stored as a plain date; `expired` is computed against the wall clock,
        # which is why this fixed 2025 expiry reads as expired today.
        assert vouchers[0]["expires_on"] == "2025-04-30"

    def test_month_summary_separates_money_kinds(self, ingestor, conn):
        ingest(ingestor, "AD-HDFCBK-T", BANK_DEBIT)  # 1450 spend
        ingest(
            ingestor,
            "AD-ICICIB-S",
            "Payment of Rs 18,450.00 towards your ICICI Credit Card XX4092 has been received. Thank you.",
        )
        ingest(ingestor, "AD-PAYTM-S", "Rs 500 added to your UPI Lite balance.")
        # The HDFC message carries a date (25-02-25) so it posts to February;
        # the other two carry none and fall back to the delivery day in March.
        february = summarise_month(conn, "2025-02")
        march = summarise_month(conn, "2025-03")
        assert february.spend_total == 1450.0
        assert february.excluded_total == 0.0
        assert march.excluded_total == 18950.0
        assert march.excluded_reasons == {"internal_transfer": 18450.0, "instrument_load": 500.0}
        assert february.event_count + march.event_count == 3

    def test_unknown_message_is_queued_for_review(self, ingestor, conn):
        event = ingest(ingestor, "9876543210", "Hey, are we still meeting at 5?")
        assert any(alert.kind == "needs_review" for alert in event.alerts)
        assert summarise_month(conn, NOW.strftime("%Y-%m")).needs_review_count == 1

    def test_alerts_are_persisted(self, ingestor, conn):
        ingest(ingestor, "9876543210", "Dear customer your SBI KYC pending, click http://sbi-kyc.xyz/a")
        kinds = {row["kind"] for row in list_alerts(conn)}
        assert "phishing_suspect" in kinds or "suspicious_link" in kinds
