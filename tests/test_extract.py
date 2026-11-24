from __future__ import annotations

from datetime import datetime

import pytest

from sms_wiz import extract
from sms_wiz.registry import SHORTENERS

from .conftest import NOW


class TestAmounts:
    def test_indian_comma_grouping(self):
        hits = extract.find_amounts("Rs.1,450.00 debited")
        assert hits[0].value == 1450.0
        assert hits[0].direction == "debit"

    def test_lakh_style_grouping(self):
        hits = extract.find_amounts("Pre-Approved Personal Loan of Rs 5,00,000 in 10 seconds")
        assert hits[0].value == 500000.0

    def test_inr_suffix_and_symbol(self):
        assert extract.find_amounts("INR 249 debited")[0].value == 249
        assert extract.find_amounts("paid 899 INR")[0].value == 899

    def test_balance_is_not_an_amount(self):
        text = "Rs.1450.00 debited from A/c XX4092. Avl Bal: Rs.5,000.00"
        balance = extract.find_balance(text)
        assert balance is not None and balance[0] == 5000.0
        hits = extract.find_amounts(text, skip_spans=(balance[1],))
        assert [h.value for h in hits] == [1450.0]

    def test_credit_direction(self):
        hits = extract.find_amounts("Rs 85,000.00 credited to your A/c XX1094")
        assert hits[0].direction == "credit"

    def test_foreign_currency_is_kept_separate(self):
        hits = extract.find_amounts("USD 20.00 charged on your card")
        assert hits[0].currency == "USD"

    def test_due_amounts(self):
        total, minimum = extract.find_due_amounts("Total Amount Due: Rs 18,450.00, Minimum Due: Rs 920.00")
        assert total == 18450.0
        assert minimum == 920.0


class TestIdentifiers:
    @pytest.mark.parametrize(
        "text",
        [
            "debited from A/c XX4092 on",
            "A/c *1234 debited",
            "A/c no. XX1234",
            "Card ending 4092",
            "account ending with 1234",
            "your card 5678 was used",
        ],
    )
    def test_last4_forms(self, text):
        assert extract.find_last4(text) is not None

    def test_upi_rrn(self):
        ref, rrn = extract.find_reference("to VPA KIRANAKART@axisbank (UPI Ref 505612345678)")
        assert rrn == "505612345678"
        assert ref == "505612345678"

    def test_named_reference_is_not_an_rrn(self):
        ref, rrn = extract.find_reference("Txn Ref AMZN88213 completed")
        assert ref == "AMZN88213"
        assert rrn is None


class TestDates:
    def test_dd_mm_yy(self):
        hits = extract.find_dates("presented on 28-02-25 on A/c", NOW)
        assert hits[0].value == datetime(2025, 2, 28)

    def test_named_month_with_year(self):
        hits = extract.find_dates("Expires 30-Apr-2025", NOW)
        assert hits[0].value == datetime(2025, 4, 30)

    def test_missing_year_rolls_forward(self):
        hits = extract.find_dates("due on 5 Jan", NOW)
        assert hits[0].value == datetime(2026, 1, 5)

    def test_iso_date(self):
        hits = extract.find_dates("as of 2025-02-11", NOW)
        assert hits[0].value == datetime(2025, 2, 11)


class TestOtp:
    def test_picks_the_code_next_to_the_keyword(self):
        code, _ = extract.find_otp("Rs 14,500 spent. OTP for this txn is 482910. Valid 10 mins.")
        assert code == "482910"

    def test_ignores_amounts_before_keyword(self):
        code, _ = extract.find_otp("Rs 15000 debited. Your OTP is 7311")
        assert code == "7311"

    def test_none_when_absent(self):
        assert extract.find_otp("Rs 500 debited from A/c XX4092") is None


class TestUrls:
    def test_shortener_and_apk(self):
        urls = extract.find_urls("Download at http://parivahan-echallan-pay.com/echallan.apk now.")
        assert urls == ["http://parivahan-echallan-pay.com/echallan.apk"]
        domain = extract.url_domain(urls[0])
        assert domain == "parivahan-echallan-pay.com"
        assert urls[0].endswith(".apk")

    def test_shortener_detected(self):
        urls = extract.find_urls("click bit.ly/hdfcemi")
        assert urls and extract.is_shortener(extract.url_domain(urls[0]))
        assert extract.url_domain(urls[0]) in SHORTENERS

    def test_trailing_punctuation_stripped(self):
        urls = extract.find_urls("visit https://hdfcbank.com/offers.")
        assert urls == ["https://hdfcbank.com/offers"]

    def test_amounts_are_not_urls(self):
        assert extract.find_urls("Rs.1450.00 debited. Avl Bal: Rs.5000.00") == []


class TestTravel:
    def test_pnr_and_seat(self):
        text = "PNR: 2459182736, Train 12627, Coach B2, Berth 45"
        assert extract.find_pnr(text) == "2459182736"
        assert extract.find_seat(text) == "B2 / 45"
        assert extract.find_journey(text) == "12627"

    def test_vehicle_number(self):
        assert extract.find_vehicle("challan for KA 01 MJ 1234 issued") == "KA01MJ1234"

    def test_delivery_handover_code(self):
        assert extract.find_handover_code("Your delivery OTP is 8492") == "8492"


class TestSender:
    def test_dlt_header(self):
        info = extract.parse_sender("AD-HDFCBK-T")
        assert info.operator == "AD"
        assert info.entity_code == "HDFCBK"
        assert info.dlt_suffix == "T"
        assert info.display_name == "HDFC Bank"
        assert info.is_known_brand

    def test_no_suffix(self):
        info = extract.parse_sender("VM-HDFCBK")
        assert info.entity_code == "HDFCBK"

    def test_personal_mobile(self):
        info = extract.parse_sender("+919876543210")
        assert info.entity_kind.value == "personal_mobile"

    def test_unknown_code_is_not_guessed(self):
        info = extract.parse_sender("AD-ZZZZZZ-P")
        assert info.entity_code == "ZZZZZZ"
        assert not info.is_known_brand
        assert info.display_name is None


class TestMerchant:
    def test_vpa_merchant(self):
        raw = extract.find_merchant_raw(extract.normalize("debited to VPA KIRANAKART@axisbank"))
        assert raw and raw.upper().startswith("KIRANAKART")

    def test_brand_survives_stopword(self):
        # Regression: a naive stopword split truncated "Amazon" to "Amaz".
        raw = extract.find_merchant_raw(extract.normalize("towards Amazon refund credited"))
        assert raw == "Amazon"

    def test_at_merchant(self):
        raw = extract.find_merchant_raw(extract.normalize("Rs 249.00 spent at SWIGGY on your card"))
        assert raw == "SWIGGY"
