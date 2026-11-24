"""Credit-card ledger: statement vs payment, and the double-count it prevents."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from sms_wiz import cards
from sms_wiz.analytics import IST

TODAY = date(2025, 3, 1)
NOW = datetime(2025, 3, 1, 10, 0, tzinfo=IST)


def statement(due: str = "05-03-25", total: str = "12,480.00", card: str = "4433") -> tuple[str, str]:
    return (
        "SBICRD-T",
        f"Total due Rs {total} on your SBI Card XX{card}. Minimum due Rs 620.00. Due date {due}.",
    )


def payment(amount: str = "12,480.00", card: str = "4433") -> tuple[str, str]:
    return (
        "SBICRD-T",
        f"Payment of Rs {amount} towards your SBI Card XX{card} has been received. Thank you.",
    )


class TestReconciliation:
    def test_statement_without_payment_is_unpaid(self, feed):
        state = cards.build(feed(statement(due="20-03-25")))[0]
        assert state.statement_amount == pytest.approx(12480.0)
        assert state.status(TODAY) == "unpaid"
        assert state.outstanding == pytest.approx(12480.0)

    def test_full_payment_closes_the_card(self, feed):
        state = cards.build(feed(statement(), payment()))[0]
        assert state.paid_total == pytest.approx(12480.0)
        assert state.status(TODAY) == "paid"
        assert state.outstanding == pytest.approx(0.0)

    def test_partial_payment_leaves_a_balance(self, feed):
        state = cards.build(feed(statement(), payment("5,000.00")))[0]
        assert state.status(TODAY) == "partial"
        assert state.outstanding == pytest.approx(7480.0)

    def test_payment_with_no_statement_is_not_reported_as_missing_data(self, feed):
        state = cards.build(feed(payment("18,450.00", card="4092")))[0]
        assert state.statement_amount is None
        assert state.status(TODAY) == "payment_only"

    def test_due_soon_and_overdue_are_time_relative(self, feed):
        upcoming = cards.build(feed(statement(due="04-03-25")))[0]
        assert upcoming.status(TODAY) == "due_soon"
        late = cards.build(feed(statement(due="20-02-25")))[0]
        assert late.status(TODAY) == "overdue"

    def test_rupee_rounding_counts_as_paid(self, feed):
        state = cards.build(feed(statement(total="12,480.00"), payment("12,479.50")))[0]
        assert state.status(TODAY) == "paid"


class TestCardIdentity:
    def test_two_cards_are_two_states(self, feed):
        rows = feed(statement(card="4433"), statement(card="9911", total="4,000.00"))
        states = cards.build(rows)
        assert len(states) == 2
        assert {s.last4 for s in states} == {"4433", "9911"}

    def test_bank_name_comes_from_the_sender_not_the_body(self, feed):
        state = cards.build(feed(statement()))[0]
        assert state.bank == "SBI Card"

    def test_spend_since_the_statement_is_tracked_separately(self, feed):
        later = datetime(2025, 3, 2, 10, 0, tzinfo=IST)
        rows = feed(
            statement(),
            ("SBICRD-T", "Rs 3,499.00 spent on CROMA using SBI Card XX4433 on 02-03-25", later),
        )
        state = cards.build(rows)[0]
        assert state.spend_since_statement == pytest.approx(3499.0)
        assert state.statement_amount == pytest.approx(12480.0)

    def test_summary_ranks_what_needs_attention(self, feed):
        rows = feed(
            statement(due="20-02-25"),  # overdue
            statement(card="9911", total="4,000.00", due="28-03-25"),
        )
        summary = cards.summary(rows, today=TODAY)
        assert len(summary["cards"]) == 2
        assert [c["status"] for c in summary["needs_attention"]] == ["overdue"]
        assert summary["total_outstanding"] == pytest.approx(16480.0)

    def test_utilisation_needs_a_limit(self, feed):
        rows = feed(
            ("SBICRD-T", "Rs 3,499.00 spent on CROMA using SBI Card XX4433. Available limit Rs.46,501.00"),
            statement(),
        )
        utilisation = cards.build(rows)[0].as_dict(TODAY)["utilisation"]
        assert utilisation is None or 0 <= utilisation <= 1

    def test_suppressed_duplicate_payment_is_not_counted_twice(self, feed):
        rows = feed(statement(), payment("12,480.00"), payment("12,480.00"))
        state = cards.build(rows)[0]
        assert state.paid_total == pytest.approx(12480.0)
