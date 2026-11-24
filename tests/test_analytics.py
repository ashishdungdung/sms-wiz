"""Spend analytics: what counts, what never does, and the derived views."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from sms_wiz import analytics
from sms_wiz.analytics import IST

NOW = datetime(2025, 3, 1, 10, 0, tzinfo=IST)
TODAY = date(2025, 3, 1)


def inr(amount: str) -> str:
    return f"Rs.{amount}"


class TestWhatCounts:
    def test_merchant_spend_and_balance_in_one_message(self, feed):
        rows = feed(("AD-HDFCBK-T", "Rs.1,450.00 debited from A/c XX4092 on 01-03-25 to VPA KIRANAKART@axisbank. Avl Bal: Rs.8,550.00"))
        weeks = analytics.weekly(rows, weeks=1, today=TODAY)
        assert weeks[-1]["spend"] == pytest.approx(1450.0)
        assert weeks[-1]["count"] == 1

    def test_income_refund_and_spend_are_three_buckets(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", "Rs.2,000.00 debited from A/c XX4092 on 01-03-25 to VPA SWIGGY@icici"),
            ("AD-HDFCBK-T", "Rs.500.00 credited to A/c XX4092 on 01-03-25 by VPA AMAZON@apl. Refund."),
            ("AD-SALARY-T", "Rs.85,000.00 credited to A/c XX4092 on 01-03-25 by NEFT CR ACME PVT LTD salary"),
        )
        week = analytics.weekly(rows, weeks=1, today=TODAY)[-1]
        assert week["spend"] == pytest.approx(2000.0)
        assert week["refunds"] == pytest.approx(500.0)
        assert week["income"] == pytest.approx(85000.0)

    def test_self_transfer_never_becomes_spend(self, feed):
        rows = feed(("AD-HDFCBK-T", "Rs.5,000.00 debited from A/c XX4092 on 01-03-25 to A/c XX7788 (self transfer)"))
        week = analytics.weekly(rows, weeks=1, today=TODAY)[-1]
        assert week["spend"] == 0
        assert week["excluded"] == pytest.approx(5000.0)

    def test_credit_card_bill_payment_is_a_transfer(self, feed):
        rows = feed(
            ("AD-ICICIB-S", "Payment of Rs 18,450.00 towards your ICICI Credit Card XX4092 has been received."),
            ("AD-HDFCBK-T", "Rs.18,450.00 debited from A/c XX4092 on 01-03-25 to CC payment ICICI"),
        )
        overview = analytics.overview(rows, month="2025-03")
        assert overview["month"]["spend"] == 0
        assert overview["month"]["excluded"] == pytest.approx(36900.0)
        assert analytics.card_payments(rows)

    def test_wallet_load_is_not_spend_until_it_is_spent(self, feed):
        rows = feed(("AD-PAYTM-T", "Rs.500.00 loaded to your Paytm Wallet from A/c XX4092 on 01-03-25"))
        assert analytics.weekly(rows, weeks=1, today=TODAY)[-1]["spend"] == 0

    def test_cash_withdrawal_is_not_spend(self, feed):
        rows = feed(("AD-HDFCBK-T", "Rs.2,000.00 debited from A/c XX4092 on 01-03-25 at ATM CASH WDL"))
        assert analytics.weekly(rows, weeks=1, today=TODAY)[-1]["spend"] == 0

    def test_investment_is_excluded(self, feed):
        rows = feed(("AD-ZERODH-T", "Rs.6,200.00 debited from A/c XX4092 on 01-03-25 towards Zerodha fund transfer"))
        assert analytics.weekly(rows, weeks=1, today=TODAY)[-1]["spend"] == 0

    def test_suppressed_duplicate_is_counted_once(self, feed):
        body = "Rs.1,000.00 debited from A/c XX4092 on 01-03-25 to VPA SWIGGY@icici"
        rows = feed(("AD-HDFCBK-T", body), ("AD-HDFCBK-T", body))
        week = analytics.weekly(rows, weeks=1, today=TODAY)[-1]
        assert week["spend"] == pytest.approx(1000.0)
        assert week["count"] == 1


class TestBuckets:
    def test_weeks_are_iso_and_contiguous(self, feed):
        rows = feed(("AD-HDFCBK-T", "Rs.100.00 debited from A/c XX4092 on 01-03-25 to VPA SWIGGY@icici"))
        weeks = analytics.weekly(rows, weeks=4, today=TODAY)
        assert len(weeks) == 4
        assert weeks[-1]["label"] == "2025-W09"
        assert [w["spend"] for w in weeks[:3]] == [0, 0, 0]

    def test_an_event_lands_in_its_own_week_not_the_query_week(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", "Rs.100.00 debited from A/c XX4092 on 20-02-25 to VPA SWIGGY@icici"),
            ("AD-HDFCBK-T", "Rs.200.00 debited from A/c XX4092 on 01-03-25 to VPA SWIGGY@icici"),
        )
        weeks = analytics.weekly(rows, weeks=3, today=TODAY)
        assert weeks[-1]["spend"] == pytest.approx(200.0)
        assert sum(w["spend"] for w in weeks[:2]) == pytest.approx(100.0)

    def test_months_are_calendar_months_oldest_first(self, feed):
        rows = feed(("AD-HDFCBK-T", "Rs.100.00 debited from A/c XX4092 on 15-01-25 to VPA SWIGGY@icici"))
        months = analytics.monthly(rows, months=3, today=TODAY)
        assert [m["label"][:7] for m in months] == ["2025-01", "2025-02", "2025-03"]
        assert months[0]["spend"] == pytest.approx(100.0)

    def test_trend_compares_last_two_buckets(self):
        trend = analytics.trend([{"spend": 100.0}, {"spend": 150.0}])
        assert trend["direction"] == "up"
        assert trend["change_pct"] == pytest.approx(50.0)
        assert analytics.trend([{"spend": 0}])["direction"] == "unknown"

    def test_essential_share_separates_groceries_from_gadgets(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", "Rs.1,000.00 debited from A/c XX4092 on 01-03-25 to VPA KIRANAKART@axisbank"),
            ("AD-HDFCBK-T", "Rs.3,000.00 debited from A/c XX4092 on 01-03-25 to VPA CROMA@icici"),
        )
        week = analytics.weekly(rows, weeks=1, today=TODAY)[-1]
        assert week["essential_share"] == pytest.approx(0.25)


class TestFocusedViews:
    def test_top_merchants_rank_by_spend_then_trim(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", "Rs.1,000.00 debited from A/c XX4092 on 01-03-25 to VPA KIRANAKART@axisbank"),
            ("AD-HDFCBK-T", "Rs.2,000.00 debited from A/c XX4092 on 01-03-25 to VPA SWIGGY@icici"),
            ("AD-HDFCBK-T", "Rs.500.00 debited from A/c XX4092 on 01-03-25 to VPA SWIGGY@icici"),
        )
        top = analytics.top_merchants(rows, limit=2)
        assert [m["merchant"] for m in top] == ["Swiggy", "Zepto"]
        assert top[0]["spend"] == pytest.approx(2500.0)
        assert top[0]["count"] == 2

    def test_upi_stats_count_rail_and_people_separately(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", "Rs.200.00 debited from A/c XX4092 on 01-03-25 to VPA SWIGGY@icici (UPI Ref 505612345678)"),
            ("AD-HDFCBK-T", "Rs.300.00 debited from A/c XX4092 on 01-03-25 to 9876543210@ybl (UPI Ref 505612345679)"),
        )
        stats = analytics.upi_stats(rows)
        assert stats["transactions"] == 2
        assert stats["spend"] == pytest.approx(500.0)
        assert stats["average"] == pytest.approx(250.0)

    def test_category_breakdown_sums_to_spend(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", "Rs.1,000.00 debited from A/c XX4092 on 01-03-25 to VPA KIRANAKART@axisbank"),
            ("AD-HDFCBK-T", "Rs.3,000.00 debited from A/c XX4092 on 01-03-25 to VPA CROMA@icici"),
        )
        breakdown = analytics.category_breakdown(rows)
        assert sum(b["spend"] for b in breakdown) == pytest.approx(4000.0)

    def test_card_payments_are_listed_with_their_card(self, feed):
        rows = feed(("AD-ICICIB-S", "Payment of Rs 18,450.00 towards your ICICI Credit Card XX4092 has been received."))
        payments = analytics.card_payments(rows)
        assert payments and payments[0]["amount"] == pytest.approx(18450.0)
        assert payments[0]["card_last4"] == "4092"

    def test_overview_is_a_complete_month_view(self, feed):
        rows = feed(("AD-HDFCBK-T", "Rs.1,000.00 debited from A/c XX4092 on 01-03-25 to VPA KIRANAKART@axisbank"))
        data = analytics.overview(rows, month="2025-03")
        assert data["month"]["label"].startswith("2025-03")
        assert data["month"]["spend"] == pytest.approx(1000.0)
        assert {"month", "weeks", "top_merchants", "upi", "categories"} <= set(data)
        assert {"spend", "income", "refunds", "excluded", "essential_share"} <= set(data["month"])

class TestMonthRange:
    """`end` must be a date that exists: November has 30 days, not 31."""

    def test_month_end_matches_the_calendar(self):
        from sms_wiz.analytics import month_end, month_totals

        assert month_end("2026-11") == "2026-11-30"
        assert month_end("2026-02") == "2026-02-28"
        assert month_end("2024-02") == "2024-02-29"  # leap year
        assert month_end("2026-01") == "2026-01-31"
        totals = month_totals([], "2026-11")
        assert totals.start == "2026-11-01"
        assert totals.end == "2026-11-30"
