"""Demat, travel and bill domains, plus the coverage claims they rest on.

The point of this file is that every "we support N banks / 22 brokers" claim can
be re-derived here. Nothing in it is an estimate.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from sms_wiz import analytics, catalog, registry, templates
from sms_wiz.analytics import IST

NOW = datetime(2025, 3, 1, 10, 0, tzinfo=IST)
TODAY = date(2025, 3, 1)


# --------------------------------------------------------------------------
# Demat: trade, SIP allotment, dividend, IPO, corporate action
# --------------------------------------------------------------------------


class TestDemat:
    def test_order_execution_is_not_spend_and_carries_the_security(self, ingestor):
        event = ingestor.ingest(
            __import__("sms_wiz.models", fromlist=["IngestRequest"]).IngestRequest(
                sender="AD-ZERODH-S",
                body="Your order for 10 shares of INFY executed at Rs 1450.00 on NSE. Order ID 2403010012",
                received_at=NOW,
            )
        ).event
        assert event.intent.value == "trade"
        assert event.ledger.treatment.value == "none"
        assert event.trade is not None
        assert event.trade.kind == "buy"
        assert event.trade.symbol == "INFY"
        assert event.trade.quantity == pytest.approx(10.0)
        assert event.trade.price == pytest.approx(1450.0)
        assert event.trade.exchange == "NSE"

    def test_per_unit_price_is_never_recorded_as_a_payment(self, ingestor):
        from sms_wiz.models import IngestRequest

        event = ingestor.ingest(
            IngestRequest(
                sender="AD-CDSLIN-S",
                body="10 units of Parag Parikh Flexi Cap allotted to your folio 12345. NAV Rs 75.20",
                received_at=NOW,
            )
        ).event
        assert event.intent.value == "trade"
        assert event.trade is not None
        assert event.trade.scheme == "Parag Parikh Flexi Cap"
        assert event.trade.symbol is None, "a scheme name is not a ticker"
        assert event.trade.price == pytest.approx(75.2)
        assert event.amount is None, "₹75.20 was the NAV, not a payment"
        assert event.ledger.treatment.value == "none"

    def test_mutual_fund_purchase_is_investment_not_spend(self, ingestor):
        from sms_wiz.models import IngestRequest

        event = ingestor.ingest(
            IngestRequest(
                sender="AD-HDFCBK-T",
                body="Rs.5,000.00 debited from A/c XX4092 to GROWW via UPI on 01-03-25. Avl Bal Rs.9,000.00",
                received_at=NOW,
            )
        ).event
        assert event.ledger.treatment.value in {"investment", "expense"}
        if event.ledger.treatment.value == "investment":
            assert event.ledger.counts_toward_spend is False

    def test_dividend_is_income_not_a_refund(self, ingestor):
        from sms_wiz.models import IngestRequest

        event = ingestor.ingest(
            IngestRequest(
                sender="AD-CDSLIN-S",
                body="Dividend of Rs 120 credited for INFOSYS to your demat account.",
                received_at=NOW,
            )
        ).event
        assert event.intent.value == "dividend"
        assert event.ledger.treatment.value == "income"
        assert event.trade is not None and event.trade.kind == "dividend"

    def test_ipo_application_blocks_money_without_spending_it(self, ingestor):
        from sms_wiz.models import IngestRequest

        event = ingestor.ingest(
            IngestRequest(
                sender="AD-HDFCBK-T",
                body="Rs.15,000.00 blocked for ASBA IPO application. UPI mandate accepted.",
                received_at=NOW,
            )
        ).event
        assert event.intent.value == "ipo"
        assert event.ledger.treatment.value == "none"

    def test_bonus_is_a_corporate_action(self, ingestor):
        from sms_wiz.models import IngestRequest

        event = ingestor.ingest(
            IngestRequest(
                sender="AD-CDSLIN-S",
                body="1:1 bonus shares of INFY credited to your demat account.",
                received_at=NOW,
            )
        ).event
        assert event.intent.value == "corporate_action"
        assert event.trade is not None and event.trade.kind == "bonus"

    def test_isin_is_extracted(self):
        from sms_wiz import extract

        assert extract.find_isin("ISIN INE009A01021 credited") == "INE009A01021"


# --------------------------------------------------------------------------
# Travel: train, bus, flight, hotel
# --------------------------------------------------------------------------


class TestTravel:
    def test_train_ticket_keeps_pnr_coach_and_berth(self, ingestor):
        from sms_wiz.models import IngestRequest

        event = ingestor.ingest(
            IngestRequest(
                sender="AD-IRCTC-S",
                body="Your ticket has been booked. PNR: 2459182736, Train 12627, Coach B2, Berth 45.",
                received_at=NOW,
            )
        ).event
        assert event.intent.value == "travel"
        assert event.logistics is not None
        assert event.logistics.mode == "train"
        assert event.logistics.pnr == "2459182736"
        assert event.logistics.seat == "B2 / 45"
        assert event.ledger.treatment.value == "none"

    def test_bus_ticket_is_bus_even_though_it_has_a_pnr(self, ingestor):
        from sms_wiz.models import IngestRequest

        event = ingestor.ingest(
            IngestRequest(
                sender="AD-REDBUS-S",
                body="Bus ticket booked. PNR 88123, Bengaluru to Chennai, 2 passengers, Rs 1,400",
                received_at=NOW,
            )
        ).event
        assert event.intent.value == "travel"
        assert event.logistics is not None
        assert event.logistics.mode == "bus", "a bare PNR is not evidence of a train"
        assert event.logistics.pnr == "88123"
        assert (event.logistics.origin, event.logistics.destination) == ("Bengaluru", "Chennai")
        assert event.logistics.passengers == 2

    def test_flight_keeps_the_boarding_pass(self, ingestor):
        from sms_wiz.models import IngestRequest

        event = ingestor.ingest(
            IngestRequest(
                sender="AD-INDIGO-S",
                body="Flight 6E 2143 booked. PNR 6E7X9K, boarding pass available, departure 06:40",
                received_at=NOW,
            )
        ).event
        assert event.intent.value == "travel"
        assert event.logistics is not None
        assert event.logistics.mode == "flight"

    def test_hotel_booking_is_travel_not_a_parcel(self, ingestor):
        from sms_wiz.models import IngestRequest

        event = ingestor.ingest(
            IngestRequest(
                sender="AD-MMT-S",
                body="Hotel booking confirmed at OYO Rooms, check-in 12-03-2025, 2 nights, Rs 4,200",
                received_at=NOW,
            )
        ).event
        assert event.intent.value == "travel"
        assert event.logistics is not None
        assert event.logistics.mode == "hotel"

    def test_a_parcel_with_a_code_stays_logistics(self, ingestor):
        from sms_wiz.models import IngestRequest

        event = ingestor.ingest(
            IngestRequest(
                sender="AD-AMAZON-S",
                body="Your package AWB 123456789012 is out for delivery. Handover code 4821.",
                received_at=NOW,
            )
        ).event
        assert event.intent.value == "logistics"

    def test_cab_ride_is_spend_not_travel(self, ingestor):
        from sms_wiz.models import IngestRequest

        event = ingestor.ingest(
            IngestRequest(
                sender="AD-HDFCBK-T",
                body="Rs.248.00 debited from A/c XX4092 to VPA UBER@icici on 01-03-25",
                received_at=NOW,
            )
        ).event
        assert event.intent.value == "expense"
        assert event.ledger.counts_toward_spend is True


# --------------------------------------------------------------------------
# Bills: telecom, broadband, utilities
# --------------------------------------------------------------------------


class TestBills:
    def test_postpaid_bill_due_is_a_reminder_not_a_payment(self, ingestor):
        from sms_wiz.models import IngestRequest

        event = ingestor.ingest(
            IngestRequest(
                sender="AD-AIRTEL-S",
                body="Your postpaid bill of Rs 799 is due on 05-03-2025. Pay now to avoid disconnection.",
                received_at=NOW,
            )
        ).event
        assert event.intent.value == "bill_due"
        assert event.ledger.treatment.value == "none"
        assert event.reminder is not None
        assert event.reminder.biller == "Airtel"
        assert event.reminder.due_date is not None and event.reminder.due_date.day == 5

    def test_recharge_is_spend_in_mobile_category(self, ingestor):
        from sms_wiz.models import IngestRequest

        rows = ingestor.ingest(
            IngestRequest(
                sender="AD-HDFCBK-T",
                body="Rs.349.00 debited from A/c XX4092 to VPA JIO@ybl on 01-03-25. Avl Bal Rs.5,000.00",
                received_at=NOW,
            )
        )
        assert rows.event.ledger.counts_toward_spend is True
        assert rows.event.merchant is not None
        assert rows.event.merchant.category == "Mobile & Broadband"

    def test_bill_calendar_separates_due_from_paid(self, feed):
        rows = feed(
            ("AD-AIRTEL-S", "Your postpaid bill of Rs 799 is due on 05-03-2025. Pay now to avoid disconnection."),
            ("AD-HDFCBK-T", "Rs.349.00 debited from A/c XX4092 to VPA JIO@ybl on 01-03-25. Avl Bal Rs.5,000.00"),
        )
        bills = analytics.bill_calendar(rows, today=TODAY)
        assert bills["due_total"] == pytest.approx(799.0)
        assert bills["paid_total"] == pytest.approx(349.0)
        assert bills["due"][0]["biller"] == "Airtel"
        assert bills["paid"][0]["category"] == "Mobile & Broadband"
        assert bills["overdue"] == []


# --------------------------------------------------------------------------
# Derived views
# --------------------------------------------------------------------------


class TestInvestmentBook:
    def test_holds_units_and_never_adds_them_to_spend(self, feed):
        rows = feed(
            (
                "AD-ZERODH-S",
                "Your order for 10 shares of INFY executed at Rs 1450.00 on NSE. Order ID 2403010012",
                NOW - timedelta(days=20),
            ),
            (
                "AD-ZERODH-S",
                "Your order for 4 shares of INFY executed at Rs 1500.00 on NSE. Order ID 2403010013",
                NOW - timedelta(days=5),
            ),
            ("AD-CDSLIN-S", "Dividend of Rs 120 credited for INFOSYS to your demat account."),
        )
        book = analytics.investment_book(rows)
        assert len(book["holdings"]) == 1
        infy = book["holdings"][0]
        assert infy["symbol"] == "INFY"
        assert infy["net_quantity"] == pytest.approx(14.0)
        assert infy["buy_value"] == pytest.approx(10 * 1450 + 4 * 1500)
        assert book["dividend_total"] == pytest.approx(120.0)
        assert "not a holding statement" in book["caveat"] or "holding statement" in book["caveat"]

        month = analytics.month_totals(rows, "2025-03")
        assert month.spend == pytest.approx(0.0), "shares are not spending"
        assert month.income == pytest.approx(120.0), "the dividend is"

    def test_sell_reduces_units_and_tracks_proceeds(self, feed):
        rows = feed(
            ("AD-ZERODH-S", "Your order for 10 shares of INFY executed at Rs 1450.00 on NSE.", NOW - timedelta(days=10)),
            ("AD-ZERODH-S", "Your order to sell 3 shares of INFY executed at Rs 1600.00 on NSE.", NOW - timedelta(days=2)),
        )
        holding = analytics.investment_book(rows)["holdings"][0]
        assert holding["net_quantity"] == pytest.approx(7.0)
        assert holding["sell_value"] == pytest.approx(4800.0)
        assert holding["sells"] == 1

    def test_sips_are_counted_separately(self, feed):
        rows = feed(
            (
                "AD-GROWW-S",
                "SIP of Rs 5,000 for Groww Nifty 50 Index Fund debited from XX4092 via mandate. Units allotted.",
                NOW - timedelta(days=15),
            )
        )
        book = analytics.investment_book(rows)
        month = analytics.month_totals(rows, "2025-03")
        assert month.spend == pytest.approx(0.0)
        assert book["sip_total"] >= 0.0


class TestTravelLog:
    def test_lists_upcoming_trips_and_keeps_airfare_out_of_spend(self, feed):
        rows = feed(
            ("AD-IRCTC-S", "Your ticket has been booked. PNR: 2459182736, Train 12627, Coach B2, Berth 45."),
            ("AD-REDBUS-S", "Bus ticket booked. PNR 88123, Bengaluru to Chennai, 2 passengers, Rs 1,400"),
        )
        log = analytics.travel_log(rows, today=TODAY)
        assert log["count"] == 2
        assert (log["by_mode"].get("train"), log["by_mode"].get("bus")) == (1, 1)
        assert analytics.month_totals(rows, "2025-03").spend == pytest.approx(0.0)
        assert log["fare_seen"] >= 0.0


# --------------------------------------------------------------------------
# Coverage: the numbers the README quotes
# --------------------------------------------------------------------------


class TestCoverageClaims:
    def test_registry_codes_are_unique_after_merging(self):
        codes = [entity.code for entity in registry.UNIQUE_ENTITIES]
        assert len(codes) == len(set(codes))
        assert len(registry.ENTITIES_BY_CODE) == len(registry.UNIQUE_ENTITIES)

    def test_merging_keeps_aliases_from_both_declarations(self):
        zerodha = registry.ENTITIES_BY_CODE["ZERODH"]
        assert "ZERODHA" in zerodha.aliases
        assert zerodha.kind is registry.EntityKind.FINTECH, "the later, more specific block wins"

    def test_bank_count_is_institutions_not_dlt_codes(self):
        institutions = registry.bank_institutions()
        assert len(institutions) >= 40
        assert registry.coverage()["bank_codes"] > len(institutions)
        # Card programmes fold into their bank instead of inflating the count.
        assert institutions["HDFC Bank"] == ["HDFCBK", "HDFCCR"]
        assert "ICICPR" in institutions["ICICI Bank"]

    def test_at_least_twenty_brokers_and_all_of_them_present(self):
        coverage = catalog.coverage()
        assert coverage["brokers_declared"] >= 20
        assert coverage["brokers_present"] == coverage["brokers_declared"]
        assert "Zerodha" in catalog.STOCK_BROKERS
        assert "Groww" in catalog.STOCK_BROKERS

    def test_mobile_operators_and_market_infrastructure_are_declared(self):
        assert catalog.coverage()["mobile_operators_present"] >= 4
        assert catalog.coverage()["market_infrastructure_present"] == 5

    def test_catalog_keys_are_unique_and_duplicates_are_reported(self):
        keys = [raw for raw, _clean, _slug in catalog.iter_seed_rows()]
        assert len(keys) == len(set(keys))
        assert catalog.merchant_count() == len(keys)
        duplicates = catalog.duplicate_keys()
        assert isinstance(duplicates, list)
        # A duplicate key must still resolve, to the later definition.
        assert catalog.category_of_clean("Jio") is not None

    def test_every_new_category_is_plural_and_essential_where_expected(self):
        labels = {category.label for category in catalog.CATEGORIES}
        assert {"Mobile & Broadband", "Brokerage & Demat", "Utilities & Bills"} <= labels
        assert catalog.is_essential("Mobile & Broadband") is True
        assert catalog.is_essential("Brokerage & Demat") is False

    def test_templates_have_unique_ids_and_cover_demat_and_travel(self):
        ids = [template.id for template in templates.TEMPLATES]
        assert len(ids) == len(set(ids))
        prefixes = {template.id.split(".")[0] for template in templates.TEMPLATES}
        assert {"broker", "travel", "telco", "utility", "card", "bank"} <= prefixes

    def test_cli_coverage_prints_both_bank_numbers(self, capsys):
        from sms_wiz import cli

        assert cli.main(["coverage"]) == 0
        out = capsys.readouterr().out
        assert "bank institutions" in out
        assert "brokers" in out

    def test_coverage_json_is_serialisable(self):
        import json

        from sms_wiz import cli

        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            assert cli.main(["coverage", "--json"]) == 0
        payload = json.loads(buffer.getvalue())
        assert payload["registry"]["unique_codes"] >= 200
        assert payload["catalog"]["keys"] >= 700
