"""Subscription calendar: conservative cadence, honest price changes."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from sms_wiz import subscriptions
from sms_wiz.analytics import IST

TODAY = date(2025, 3, 1)


def at(days_ago: int) -> datetime:
    return datetime(2025, 3, 1, 10, 0, tzinfo=IST) - timedelta(days=days_ago)


def spend(amount: float, payee: str, sender: str = "AD-HDFCBK-T") -> str:
    return f"Rs.{amount:,.2f} debited from A/c XX4092 on 01-03-25 to VPA {payee}@icici"


class TestCadence:
    def test_three_monthly_charges_are_monthly(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", spend(649, "NETFLIX"), at(60)),
            ("AD-HDFCBK-T", spend(649, "NETFLIX"), at(30)),
            ("AD-HDFCBK-T", spend(649, "NETFLIX"), at(0)),
        )
        series = subscriptions.build(rows)
        assert len(series) == 1
        assert series[0].cadence == "monthly"
        assert series[0].confidence >= 0.7

    def test_one_interval_same_price_is_a_subscription(self, feed):
        """Two charges, 30 days apart, same rupee: Spotify, not two coffees."""
        rows = feed(
            ("AD-HDFCBK-T", spend(119, "SPOTIFY"), at(30)),
            ("AD-HDFCBK-T", spend(119, "SPOTIFY"), at(0)),
        )
        series = subscriptions.build(rows)
        assert series[0].cadence == "monthly"
        assert series[0].confidence == pytest.approx(0.6)

    def test_one_interval_different_price_stays_irregular(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", spend(300, "CROMA"), at(30)),
            ("AD-HDFCBK-T", spend(900, "CROMA"), at(0)),
        )
        assert subscriptions.build(rows)[0].cadence == "irregular"

    def test_same_price_on_random_days_is_not_a_cadence(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", spend(500, "SWIGGY"), at(75)),
            ("AD-HDFCBK-T", spend(500, "SWIGGY"), at(0)),
        )
        assert subscriptions.build(rows)[0].cadence == "irregular"

    def test_a_pre_debit_notice_makes_it_recurring_before_any_second_charge(self, feed):
        rows = feed(
            (
                "AD-HDFCBK-T",
                "Rs.12,500.00 will be debited from A/c XX4092 on 26-03-25 towards BAJAJ FINANCE NACH mandate.",
            )
        )
        series = subscriptions.build(rows, today=TODAY)
        assert series[0].cadence == "monthly"
        assert series[0].next_due == date(2025, 3, 26)

    def test_two_mandates_on_different_accounts_are_two_series(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", "Rs.9,999.00 will be debited from A/c XX5566 on 26-03-25 towards BAJAJ FINANCE NACH"),
            ("AD-HDFCBK-T", "Rs.12,500.00 will be debited from A/c XX4092 on 26-03-25 towards BAJAJ FINANCE NACH"),
        )
        series = subscriptions.build(rows)
        assert len(series) == 2
        assert {s.key.split(":")[-1] for s in series} == {"5566", "4092"}


class TestExclusions:
    def test_card_statement_is_not_a_subscription(self, feed):
        rows = feed(
            ("SBICRD-T", "Total due Rs 12,480.00 on your SBI Card XX4433. Minimum due Rs 620. Due date 05-03-25.")
        )
        assert subscriptions.build(rows) == []

    def test_suppressed_duplicate_does_not_create_a_series(self, feed):
        body = spend(649, "NETFLIX")
        rows = feed(("AD-HDFCBK-T", body, at(0)), ("AD-HDFCBK-T", body, at(0)))
        keys = {row["dedup_status"] for row in rows}
        assert "suppressed_duplicate" in keys
        assert sum(len(s.amounts) for s in subscriptions.build(rows)) == 1

    def test_unknown_biller_is_labelled_but_never_guessed(self, feed):
        rows = feed(
            ("AD-NEWCO-T", "Rs 99 debited from A/c XX7777 on 01-03-25 for monthly plan"),
        )
        series = subscriptions.build(rows)
        assert series and series[0].name == "Unknown biller"


class TestCalendarAndCommitment:
    def test_calendar_lists_only_the_window_and_includes_series_due_date(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", "Rs.12,500.00 will be debited from A/c XX4092 on 26-03-25 towards BAJAJ FINANCE NACH"),
        )
        entries = subscriptions.calendar(rows, days=45, today=TODAY)
        assert entries and entries[0]["date"] == "2025-03-26"
        assert entries[0]["amount"] == pytest.approx(12500.0)

    def test_calendar_is_sorted_and_never_in_the_past(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", "Rs.649.00 will be debited from A/c XX4092 on 10-03-25 towards NETFLIX NACH"),
            ("AD-HDFCBK-T", "Rs.119.00 will be debited from A/c XX4092 on 05-03-25 towards SPOTIFY NACH"),
        )
        entries = subscriptions.calendar(rows, days=60, today=TODAY)
        dates = [e["date"] for e in entries]
        assert dates == sorted(dates)
        assert all(d >= "2025-03-01" for d in dates)

    def test_monthly_commitment_ignores_irregular_rows(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", spend(119, "SPOTIFY"), at(30)),
            ("AD-HDFCBK-T", spend(119, "SPOTIFY"), at(0)),
            ("AD-HDFCBK-T", spend(4000, "CROMA"), at(0)),
        )
        summary = subscriptions.summary(rows, today=TODAY)
        assert summary["count"] == 1
        assert summary["monthly_commitment"] == pytest.approx(119.0)
        assert summary["irregular_count"] == 1

    def test_price_change_is_reported_percent_and_direction(self, feed):
        rows = feed(
            ("AD-HDFCBK-T", spend(649, "NETFLIX"), at(60)),
            ("AD-HDFCBK-T", spend(649, "NETFLIX"), at(30)),
            ("AD-HDFCBK-T", spend(799, "NETFLIX"), at(0)),
        )
        changes = subscriptions.price_changes(rows)
        assert changes and changes[0]["from"] == pytest.approx(649.0)
        assert changes[0]["to"] == pytest.approx(799.0)
        assert changes[0]["direction"] == "increase"
        assert subscriptions.price_changes(rows, min_pct=30) == []

    def test_cadence_inference_table(self):
        assert subscriptions._infer_cadence([30, 31, 29])[0] == "monthly"
        assert subscriptions._infer_cadence([7, 7, 8])[0] == "weekly"
        assert subscriptions._infer_cadence([90, 91])[0] == "quarterly"
        assert subscriptions._infer_cadence([5, 100])[0] == "irregular"
