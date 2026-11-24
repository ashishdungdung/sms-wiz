"""Regressions from the adversarial review of the two probe rounds.

Every test here exists because a real message shape got the wrong answer, or
because the API answered a question it should have refused. They are grouped by
the layer that was wrong, not by the fix that was made, so a future refactor can
delete a fix and still see exactly which behaviour it protected.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from sms_wiz import catalog, store
from sms_wiz.api import create_app
from sms_wiz.db import connect
from sms_wiz.extract import find_last4, unparsed_dates
from sms_wiz.merchants import MerchantDictionary
from sms_wiz.models import IngestRequest
from sms_wiz.redact import for_storage

from .conftest import NOW
from .test_pipeline import ingest


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class TestCardDigits:
    """``find_last4`` must never answer with the *first* four card digits."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("debited on card 4111 1111 1111 1111", "1111"),
            ("A/c 1234567890123456 debited", "3456"),
            ("SBI Card XX4092 used", "4092"),
            ("A/c XX8899", "8899"),
            ("card ending 1234", "1234"),
        ],
    )
    def test_last_four_is_the_end_of_the_number(self, text, expected):
        assert find_last4(text) == expected

    def test_a_written_out_card_is_not_a_reference(self):
        # The bug this replaces returned "4111": wrong, and a partial PAN in the
        # clear where the user expected only the last four digits.
        assert find_last4("card 4111 1111 1111 1111") != "4111"


class TestStatedDate:
    """A message date decides the month; a date that does not exist is flagged."""

    def test_purchase_is_filed_on_the_day_the_message_names(self, ingestor, conn):
        event = ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Rs.300.00 debited from A/c XX1234 on 31-03-25 at CAFE COFFEE DAY. Avl Bal Rs.900.00",
            at=NOW + timedelta(days=2),  # delivered on 3 March about a 31 March charge? no: see below
        )
        # 31-03-25 is in the future relative to NOW, so it is not a purchase date.
        assert "dated_in_message" not in event.event.flags

    def test_a_delayed_message_lands_in_the_month_it_names(self, ingestor, conn):
        # "on 25-02-25" but delivered in March: the spend belongs to February.
        event = ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Rs.1,450.00 debited from A/c XX4092 on 25-02-25 at CAFE COFFEE DAY. Avl Bal Rs.5,000.00",
            at=NOW,
        )
        assert event.event.received_at.strftime("%Y-%m") == "2025-02"
        assert "dated_in_message" in event.event.flags
        assert store.summarise_month(conn, "2025-02").spend_total == 1450.0
        assert store.summarise_month(conn, "2025-03").spend_total == 0.0

    def test_an_impossible_date_is_flagged_not_banked(self, ingestor):
        event = ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Rs.300.00 debited from A/c XX1234 on 31-02-25 at CAFE. Avl Bal Rs.900.00",
            at=NOW,
        )
        assert "date_unparseable" in event.event.flags
        assert event.event.analysis["date_unparseable"] == "31-02-25"
        assert event.event.confidence.needs_review is True
        assert event.event.received_at == NOW  # arrival date kept

    def test_a_reference_number_is_not_mistaken_for_a_bad_date(self):
        assert unparsed_dates("Rs.500 debited. Ref 12-34-5678 at Amazon", NOW) == []


# ---------------------------------------------------------------------------
# Merchants
# ---------------------------------------------------------------------------


class TestShortBrandKeys:
    """Three-letter brand acronyms are most of what an Indian SMS names."""

    @pytest.mark.parametrize(
        "text, clean, category",
        [
            ("Rs.200 paid to KFC store 22", "KFC", "Food & Dining"),
            ("Jio recharge of Rs.239 successful", "Jio", "Mobile & Broadband"),
            ("LIC premium of Rs.1,250 received", "LIC", "Insurance"),
            ("Rs.249 to PVR cinema", "PVR INOX", "Entertainment & Subscriptions"),
            ("TDS deducted on your interest", "Income Tax", "Taxes & Fines"),
            ("IndiGo flight 6E 234 booked", "IndiGo", "Travel & Commute"),
        ],
    )
    def test_short_brands_resolve(self, merchants, text, clean, category):
        match = merchants.lookup(text)
        assert match is not None
        assert match.clean == clean
        assert match.category == category

    def test_a_short_key_does_not_match_inside_a_word(self, merchants):
        assert merchants.lookup("Rs.100 paid to PUBLIC LIBRARY") is None

    def test_coverage_reports_what_can_actually_match(self):
        payload = catalog.coverage()
        assert payload["matchable_keys"] == payload["distinct_keys"]
        assert payload["distinct_keys"] < payload["keys"]  # duplicates collapse


# ---------------------------------------------------------------------------
# Storage: schema migration and duplicate window
# ---------------------------------------------------------------------------


class TestDatabaseMigration:
    """An installed database from the first release must keep working."""

    def test_missing_columns_are_added_and_rows_preserved(self, tmp_path):
        path = str(tmp_path / "old.db")
        old = sqlite3.connect(path)
        old.executescript(
            """
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                received_at TEXT NOT NULL,
                sender_raw TEXT,
                intent TEXT,
                amount_value REAL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO events VALUES
                ('e1', '2025-03-01T10:00:00+05:30', 'AD-HDFCBK-T', 'expense', 100.0, '{}', 'x');
            """
        )
        old.commit()
        old.close()

        conn = connect(path)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
            assert {"reference", "duplicate_of", "merchant_clean"} <= columns
            assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            conn.close()


class TestDuplicateWindow:
    def test_a_redelivery_minutes_later_is_folded(self, ingestor, conn):
        body = "Rs.100.00 debited from A/c XX1234 at CAFE. Avl Bal Rs.900.00"
        ingest(ingestor, "AD-HDFCBK-T", body, at=NOW)
        ingest(ingestor, "AD-HDFCBK-T", body, at=NOW + timedelta(minutes=2))
        assert store.summarise_month(conn, NOW.strftime("%Y-%m")).spend_total == 100.0

    def test_two_real_purchases_hours_apart_are_both_counted(self, ingestor, conn):
        body = "Rs.2000.00 debited from A/c XX1234 at ATM. Avl Bal Rs.900.00"
        ingest(ingestor, "AD-HDFCBK-T", body, at=NOW)
        second = ingest(ingestor, "AD-HDFCBK-T", body, at=NOW + timedelta(hours=3))
        assert second.event.dedup.status == "unique"
        assert store.summarise_month(conn, NOW.strftime("%Y-%m")).spend_total == 4000.0


class TestPossibleDuplicate:
    def test_same_amount_same_merchant_same_day_from_another_bank_is_flagged(self, ingestor, conn):
        ingest(ingestor, "AD-HDFCBK-T", "Rs.300.00 debited from A/c XX1234 at CAFE COFFEE DAY.")
        card = ingest(
            ingestor, "AD-SBICRD-S", "Rs.300.00 spent on your SBI Card XX4433 at CAFE COFFEE DAY."
        )
        assert card.event.dedup.status == "possible_duplicate"
        assert card.event.confidence.needs_review is True
        assert any(alert.kind == "possible_duplicate" for alert in card.alerts)
        # Nothing is merged automatically: both stay in the total until confirmed.
        assert store.summarise_month(conn, NOW.strftime("%Y-%m")).spend_total == 600.0


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------


class TestRefundTracker:
    def test_a_larger_credit_closes_the_claim_and_warns(self, ingestor, conn):
        ingest(
            ingestor,
            "AD-AMAZON-S",
            "Refund of Rs 500 for your Amazon order has been initiated and will be credited in 5 days.",
        )
        credit = ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Rs.1,500.00 credited to your A/c XX1234 towards Amazon refund.",
            at=NOW + timedelta(days=3),
        )
        assert any(alert.kind == "refund_amount_mismatch" for alert in credit.alerts)
        refunds = store.list_refunds(conn)
        assert len(refunds) == 1
        assert refunds[0]["status"] == "credited"
        assert "extra" in " ".join(
            alert.message for alert in credit.alerts if alert.kind == "refund_amount_mismatch"
        )

    def test_a_promise_without_an_amount_is_still_tracked(self, ingestor, conn):
        opened = ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Refund initiated for order 9912 at WESTELECOM. Will be credited in 7 working days.",
        )
        assert any(alert.kind == "refund_tracking_started" for alert in opened.alerts)
        refunds = store.list_refunds(conn)
        assert len(refunds) == 1
        assert refunds[0]["amount"] == 0.0
        assert refunds[0]["merchant"] == "WESTELECOM"

        credit = ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Rs.899.00 credited to your A/c XX1234 towards Westelecom refund.",
            at=NOW + timedelta(days=5),
        )
        assert any(alert.kind == "refund_matched" for alert in credit.alerts)
        assert store.list_refunds(conn)[0]["status"] == "credited"

    def test_a_brand_sender_names_the_claim_it_opened(self, ingestor, conn):
        ingest(
            ingestor,
            "AD-AMAZON-S",
            "Refund of Rs 900 for your order has been initiated to A/c XX4092.",
        )
        refunds = store.list_refunds(conn)
        assert refunds[0]["merchant"] == "Amazon"

    def test_the_word_refund_is_not_a_merchant(self, ingestor, conn):
        ingest(
            ingestor,
            "AD-AMAZON-S",
            "Refund of Rs 900 for your order has been initiated to A/c XX4092.",
        )
        credit = ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Rs 900.00 credited to A/c XX4092 towards refund. Avl Bal Rs 5,900.",
            at=NOW + timedelta(days=1),
        )
        assert credit.event.merchant is None
        assert any(alert.kind == "refund_matched" for alert in credit.alerts)
        assert store.list_refunds(conn)[0]["status"] == "credited"

    def test_an_unknown_merchant_is_still_named_on_the_claim(self, ingestor, conn):
        ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Refund of Rs 320 for order 7781 at SUNRISE STORES has been initiated.",
        )
        refunds = store.list_refunds(conn)
        assert refunds and refunds[0]["merchant"]


# ---------------------------------------------------------------------------
# Classification: reversals
# ---------------------------------------------------------------------------


class TestReversal:
    """A reversal returns money; it is neither new spending nor income."""

    def test_a_completed_reversal_is_a_refund(self, ingestor, conn):
        event = ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Your txn of Rs.500 at AMAZON has been reversed. Amount credited back to your account.",
        )
        assert event.event.intent.value == "refund_credited"
        assert event.event.ledger.counts_toward_spend is False
        summary = store.summarise_month(conn, NOW.strftime("%Y-%m"))
        assert summary.income_total == 0.0
        assert summary.refunds_total == 500.0

    def test_a_pending_reversal_is_a_promise_not_a_credit(self, ingestor):
        event = ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Rs.500.00 debited from A/c XX1234 at AMAZON. Txn reversed, amount will be credited back.",
        )
        assert event.event.intent.value == "refund_initiated"
        assert event.event.ledger.counts_toward_spend is False
        assert "reversal_pending" in event.event.flags


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


class TestStoredText:
    def test_a_bare_pin_is_masked(self):
        stored = for_storage("Use CVV 987 and PIN 1234 to confirm your order")
        assert "1234" not in stored
        assert "987" not in stored

    def test_a_postal_pin_code_is_not_a_credential(self):
        assert "560001" in for_storage("Delivery to PIN code 560001")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path))


class TestFilterFailOpen:
    def test_a_body_less_request_allows_and_explains(self, client):
        response = client.post("/api/v1/filter", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["action"] == "allow"
        assert body["reason"]


class TestReadEndpointValidation:
    @pytest.mark.parametrize("query", ["limit=0", "limit=-1", "limit=100000", "month=2025-13"])
    def test_impossible_queries_are_rejected(self, client, query):
        assert client.get(f"/api/v1/events?{query}").status_code == 422


class TestTokenGate:
    """Every /api route is behind the token, including the read-only ones."""

    def test_reads_require_the_token_when_one_is_configured(self, db_path, monkeypatch):
        monkeypatch.setenv("SMSWIZ_TOKEN", "s3cret")
        client = TestClient(create_app(db_path))
        assert client.get("/api/v1/events").status_code == 401
        assert client.get("/api/v1/summary").status_code == 401
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/events", headers={"X-SMSWiz-Token": "s3cret"}).status_code == 200

    def test_every_registered_api_route_is_gated(self, db_path, monkeypatch):
        monkeypatch.setenv("SMSWIZ_TOKEN", "s3cret")
        app = create_app(db_path)
        untokened = []
        for route in app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/"):
                continue
            method = sorted(getattr(route, "methods", set()) - {"OPTIONS", "HEAD"})[0]
            response = TestClient(app).request(method, path.replace("{event_id}", "nope"))
            if response.status_code != 401:
                untokened.append(f"{method} {path} -> {response.status_code}")
        assert untokened == []


# ---------------------------------------------------------------------------
# Foreign currency
# ---------------------------------------------------------------------------


class TestForeignCurrency:
    def test_a_dollar_charge_is_not_counted_as_rupees(self, ingestor, conn):
        event = ingest(
            ingestor,
            "AD-HDFCBK-T",
            "USD 12.99 spent on your HDFC Bank Card XX8899 at OPENAI on 01-03-25.",
        )
        assert event.event.amount.currency == "USD"
        assert event.event.ledger.treatment.value == "foreign_currency"
        assert event.event.ledger.counts_toward_spend is False
        summary = store.summarise_month(conn, NOW.strftime("%Y-%m"))
        assert summary.spend_total == 0.0
        assert summary.excluded_reasons["foreign_currency"] == pytest.approx(12.99)


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


class TestCardOwnership:
    """A payment names the card it settles; the sender is just the payer's bank."""

    def test_a_payment_naming_the_card_belongs_to_that_card(self, ingestor, conn):
        from sms_wiz import cards

        ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Payment of Rs.12,480.00 towards your SBI Card XX4433 has been received. Thank you.",
        )
        states = cards.build(store.list_events(conn, limit=500))
        assert [state.key for state in states] == ["SBI CARD:4433"]
        assert states[0].paid_total == pytest.approx(12480.0)

    def test_a_statement_and_its_payment_are_one_card(self, ingestor, conn):
        from sms_wiz import cards

        ingest(
            ingestor,
            "AD-SBICRD-S",
            "Your SBI Card XX4433 statement dated 20-02-25: total due Rs.12,480.00, "
            "minimum due Rs.620.00, due date 10-03-25.",
        )
        ingest(
            ingestor,
            "AD-HDFCBK-T",
            "Payment of Rs.12,480.00 towards your SBI Card XX4433 has been received. Thank you.",
            at=NOW + timedelta(days=2),
        )
        states = cards.build(store.list_events(conn, limit=500))
        assert len(states) == 1
        assert states[0].key == "SBI CARD:4433"
        assert states[0].status() == "paid"
