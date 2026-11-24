from __future__ import annotations

import pytest

from sms_wiz.classify import analyse
from sms_wiz.models import AppleAction, AppleSubAction, Intent, LedgerTreatment, Retention
from sms_wiz.registry import DECLARED_PROMOTIONAL, DECLARED_TRANSACTIONAL, effective_subaction

from .conftest import NOW


def classify(body: str, sender: str, merchants):
    return analyse(body, sender, now=NOW, merchants=merchants)


class TestRoutingContract:
    def test_every_intent_routes_to_a_declared_or_broad_folder(self):
        from sms_wiz.models import Intent as I
        from sms_wiz.registry import ROUTING

        for intent in I:
            route = ROUTING[intent]
            effective = effective_subaction(route.subaction)
            assert effective in (AppleSubAction.NONE, *DECLARED_TRANSACTIONAL, *DECLARED_PROMOTIONAL)

    def test_undeclared_subaction_is_not_promised(self):
        # Apple only renders sub-actions the app declared; health is not on our
        # declared list, so the API must fall back to the broad folder.
        assert effective_subaction(AppleSubAction.TRANSACTIONAL_HEALTH) is AppleSubAction.NONE
        assert effective_subaction(AppleSubAction.TRANSACTIONAL_FINANCE) is AppleSubAction.TRANSACTIONAL_FINANCE

    def test_junk_is_only_used_for_quarantine(self):
        from sms_wiz.registry import ROUTING

        junk_intents = [i for i, route in ROUTING.items() if route.action is AppleAction.JUNK]
        assert junk_intents == [Intent.PHISHING_SUSPECT]


class TestBankMarketing:
    def test_loan_pitch_on_bank_header_is_not_finance(self, merchants):
        event = classify(
            "Congrats! You have a Pre-Approved Personal Loan of Rs 5,00,000 in 10 seconds. Apply now!",
            "AD-HDFCBK-S",
            merchants,
        )
        assert event.intent is Intent.BANK_UPSELL
        assert event.routing.apple_action is AppleAction.PROMOTION
        assert event.ledger.counts_toward_spend is False
        assert event.routing.safe_to_bulk_delete is True

    def test_real_debit_with_emi_pitch_stays_one_expense(self, merchants):
        event = classify(
            "Rs.1,450.00 debited from A/c XX4092 on 25-02-25 to VPA KIRANAKART@axisbank "
            "(UPI Ref 505612345678). Avl Bal: Rs.5,000.00. Convert this spend into EasyEMI! bit.ly/hdfcemi",
            "AD-HDFCBK-T",
            merchants,
        )
        assert event.intent is Intent.EXPENSE
        assert "embedded_upsell" in event.flags
        assert event.amount and event.amount.value == 1450.0
        assert event.ledger.counts_toward_spend is True
        assert event.routing.apple_subaction is AppleSubAction.TRANSACTIONAL_FINANCE

    def test_plain_promo_with_short_link_is_promotion_not_junk(self, merchants):
        event = classify(
            "Get 10% cashback on BigBasket. Shop now bit.ly/bb10off", "AD-HDFCBK-P", merchants
        )
        assert event.intent is Intent.COMMERCIAL_PROMO
        assert event.routing.apple_action is AppleAction.PROMOTION
        assert "suspicious_link" not in event.flags


class TestPhishing:
    def test_apk_challan_from_personal_number_is_quarantined(self, merchants):
        event = classify(
            "RTO Parivahan Notice: Vehicle Challan of Rs 1000 pending. Download official app to view "
            "photo and pay: http://parivahan-echallan-pay.com/echallan.apk",
            "9876543210",
            merchants,
        )
        assert event.intent is Intent.PHISHING_SUSPECT
        assert event.routing.apple_action is AppleAction.JUNK
        assert event.routing.safe_to_bulk_delete is True
        assert any("APK" in reason for reason in event.evidence)

    def test_brand_impersonation_from_mobile_number(self, merchants):
        event = classify(
            "Dear customer, your SBI account will be blocked today. Verify KYC now: "
            "http://sbi-kyc-verify.xyz/login",
            "9876500011",
            merchants,
        )
        assert event.intent is Intent.PHISHING_SUSPECT
        assert event.analysis["forensic_score"] >= 0.7

    def test_kyc_link_from_unregistered_number_is_quarantined(self, merchants):
        event = classify(
            "Your SBI KYC is pending, verify at http://sbi-kyc.xyz/a", "9876500011", merchants
        )
        assert event.intent is Intent.PHISHING_SUSPECT
        assert event.routing.apple_action is AppleAction.JUNK

    def test_registered_bank_promo_with_link_is_not_quarantined(self, merchants):
        event = classify(
            "HDFC Bank: get 10% cashback on BigBasket. Shop now bit.ly/bb10off", "AD-HDFCBK-P", merchants
        )
        assert event.intent is not Intent.PHISHING_SUSPECT
        assert event.routing.apple_action is not AppleAction.JUNK

    def test_real_bank_message_on_official_domain_is_not_flagged(self, merchants):
        event = classify(
            "Your HDFC Bank credit card statement for Feb is ready. View at https://www.hdfcbank.com/statement",
            "AD-HDFCBK-S",
            merchants,
        )
        assert event.intent is not Intent.PHISHING_SUSPECT
        assert event.routing.apple_action is not AppleAction.JUNK


class TestSecurity:
    def test_mandate_otp_is_high_risk(self, merchants):
        event = classify(
            "123456 is your OTP to register e-mandate of Rs 15,000/month for Netflix AutoPay. Do not share.",
            "VM-NETFLX-S",
            merchants,
        )
        assert event.intent is Intent.OTP
        assert event.otp and event.otp.code == "123456"
        assert event.otp.purpose == "mandate_registration"
        assert event.otp.risk == "high"
        assert event.routing.retention is Retention.EXPIRE_24H

    def test_beneficiary_otp_is_high_risk(self, merchants):
        event = classify(
            "OTP 445566 to add a new beneficiary. Valid 10 mins.", "AD-ICICIB-S", merchants
        )
        assert event.otp and event.otp.purpose == "beneficiary_addition"
        assert event.otp.risk == "high"

    def test_delivery_handover_code_is_logistics_not_auth(self, merchants):
        event = classify(
            "Your Amazon delivery OTP is 8492. Share it with the delivery agent only.",
            "AD-AMAZON-S",
            merchants,
        )
        assert event.intent is Intent.LOGISTICS
        assert event.logistics and event.logistics.handover_code == "8492"
        assert event.otp is None

    def test_login_otp_is_medium_low_risk(self, merchants):
        event = classify(
            "Your OTP for HDFC Bank netbanking login is 998877. Valid for 10 minutes.",
            "AD-HDFCBK-S",
            merchants,
        )
        assert event.intent is Intent.OTP
        assert event.otp and event.otp.risk == "medium"


class TestLedgerCorrectness:
    def test_card_bill_payment_is_not_spend(self, merchants):
        event = classify(
            "Payment of Rs 18,450.00 towards your ICICI Credit Card XX4092 has been received. Thank you.",
            "AD-ICICIB-S",
            merchants,
        )
        assert event.intent is Intent.CC_BILL_PAYMENT
        assert event.ledger.treatment is LedgerTreatment.INTERNAL_TRANSFER
        assert event.ledger.counts_toward_spend is False

    def test_fastag_toll_counts_but_recharge_does_not(self, merchants):
        toll = classify(
            "Rs 65.00 deducted from your FASTag for Electronic City Toll Plaza on 26-02-25.",
            "AD-ICICIB-T",
            merchants,
        )
        load = classify("Rs 500 recharge successful on your ICICI FASTag. Balance: Rs 920", "AD-ICICIB-T", merchants)
        assert toll.intent is Intent.FASTAG_TOLL and toll.ledger.counts_toward_spend
        assert load.intent is Intent.INSTRUMENT_LOAD and not load.ledger.counts_toward_spend

    def test_upi_lite_topup_is_not_spend(self, merchants):
        event = classify("Rs 500 added to your UPI Lite balance.", "AD-PAYTM-S", merchants)
        assert event.intent is Intent.INSTRUMENT_LOAD
        assert event.ledger.counts_toward_spend is False

    def test_atm_withdrawal_is_not_spend(self, merchants):
        event = classify("Rs 5,000.00 withdrawn from ATM A/c XX1094. Avl Bal: Rs 3,200.", "AD-HDFCBK-T", merchants)
        assert event.intent is Intent.CASH_WITHDRAWAL
        assert event.ledger.counts_toward_spend is False

    def test_income(self, merchants):
        event = classify(
            "Rs 85,000.00 credited to your A/c XX1094 on 01-03-25 by NEFT from INFOSYS LTD.",
            "AD-HDFCBK-T",
            merchants,
        )
        assert event.intent is Intent.INCOME
        assert event.ledger.counts_as_income is True


class TestLifecycle:
    def test_refund_initiated_then_credited(self, merchants):
        initiated = classify(
            "Refund of Rs 1,850 for your Amazon order has been initiated and will be credited within 5-7 days.",
            "AD-AMAZON-S",
            merchants,
        )
        credited = classify(
            "Rs 1,850.00 credited to your A/c XX4092 towards Amazon refund.", "AD-HDFCBK-T", merchants
        )
        assert initiated.intent is Intent.REFUND_INITIATED
        assert credited.intent is Intent.REFUND_CREDITED
        assert credited.ledger.reduces_spend is True
        assert credited.ledger.counts_as_income is False

    def test_pre_debit_is_not_a_debit(self, merchants):
        event = classify(
            "Your e-Mandate for Rs 12,500.00 towards BAJAJ FINANCE EMI will be presented on 05-03-25 "
            "on A/c XX1094. Maintain sufficient balance.",
            "VM-HDFCBK-S",
            merchants,
        )
        assert event.intent is Intent.UPCOMING_DEBIT
        assert event.ledger.counts_toward_spend is False
        assert event.reminder and event.reminder.due_date is not None
        assert event.reminder.autopay is True

    def test_bill_due_keeps_amounts_separate(self, merchants):
        event = classify(
            "Your ICICI Credit Card statement is generated. Total Amount Due: Rs 18,450.00, "
            "Minimum Due: Rs 920.00. Due date 05-03-25.",
            "AD-ICICIB-S",
            merchants,
        )
        assert event.intent is Intent.BILL_DUE
        assert event.reminder and event.reminder.amount_due == 18450.0
        assert event.reminder.minimum_due == 920.0


class TestPerksAndTravel:
    def test_voucher(self, merchants):
        event = classify(
            "You have received a Rs 1,000 Amazon Pay gift card. Voucher code: AMZP-4K9Q-7T2X. "
            "PIN: 982104. Expires 30-Apr-2025.",
            "AD-AMAZON-S",
            merchants,
        )
        assert event.intent is Intent.VOUCHER_ISSUED
        assert event.voucher.code == "AMZP-4K9Q-7T2X"
        assert event.voucher.pin == "982104"
        assert event.voucher.value == 1000.0
        assert event.voucher.expiry is not None and event.voucher.expiry.year == 2025

    def test_voucher_bait_is_promo(self, merchants):
        event = classify(
            "Congrats! Win a Rs 1,000 voucher. Shop now to claim.", "AD-MYNTRA-P", merchants
        )
        assert event.intent is Intent.COMMERCIAL_PROMO

    def test_irctc(self, merchants):
        event = classify(
            "Your ticket has been booked. PNR: 2459182736, Train 12627, Coach B2, Berth 45.",
            "AD-IRCTC-S",
            merchants,
        )
        # A ticket is a booking record now, not a delivery: TRAVEL, treatment none.
        assert event.intent is Intent.TRAVEL
        assert event.logistics.mode == "train"
        assert event.logistics.pnr == "2459182736"
        assert event.ledger.treatment.value == "none"
        assert event.logistics.seat == "B2 / 45"

    def test_government_challan(self, merchants):
        event = classify(
            "E-Challan of Rs 1,000 for vehicle KA01MJ1234 is pending. Pay at echallan.parivahan.gov.in",
            "JD-ECHALN-G",
            merchants,
        )
        assert event.intent is Intent.GOVT_NOTICE
        assert event.gov.kind == "challan"
        assert event.gov.vehicle_number == "KA01MJ1234"


class TestUncertainty:
    def test_personal_message_is_allowed_and_queued(self, merchants):
        event = classify("Hey, are we still meeting at 5?", "9876543210", merchants)
        assert event.intent is Intent.UNKNOWN
        assert event.routing.apple_action is AppleAction.ALLOW
        assert event.confidence.needs_review is True
        assert "no_rule_matched" in event.flags

    def test_summary_never_contains_the_otp(self, merchants):
        event = classify("482910 is your OTP for a txn of Rs 14,500 at MakeMyTrip.", "AD-HDFCBK-S", merchants)
        assert "482910" not in event.summary
        assert "482910" not in event.redacted_body


class TestUnseenMessageProbe:
    """Cases found by classifying messages the suite had never seen.

    Each of these was a real miss: a limit offer counted as income, a premium
    notice nobody recognised, a recharge nobody recognised, a voucher code that
    was never extracted. They are pinned here so the fixes cannot regress.
    """

    def test_credit_limit_offer_is_not_income(self, merchants):
        event = classify(
            "Get a credit limit upgrade up to Rs 5,00,000 on your Axis Bank Card "
            "XX9911. Apply now!",
            "AD-AXISBK-S",
            merchants,
        )
        assert event.intent is Intent.BANK_UPSELL
        assert event.ledger.counts_toward_spend is False
        assert event.ledger.counts_as_income is False
        assert event.routing.apple_action is AppleAction.PROMOTION
        assert "bank_marketing" in event.flags

    def test_limit_offer_never_enters_income_totals(self, ingestor, db_path):
        from sms_wiz import store
        from sms_wiz.db import connect
        from sms_wiz.models import IngestRequest

        ingestor.ingest(
            IngestRequest(
                sender="AD-AXISBK-S",
                body="Avail credit limit increase to Rs 5,00,000 on your card. Apply now.",
                received_at=NOW,
            )
        )
        conn = connect(db_path)
        try:
            summary = store.summarise_month(conn, "2025-03")
        finally:
            conn.close()
        assert summary.income_total == 0
        assert summary.spend_total == 0

    def test_insurance_premium_due_is_a_bill(self, merchants):
        event = classify(
            "LIC Policy 887766: Premium of Rs 15,340 is due on 28-03-25. "
            "Pay now to avoid lapse.",
            "AD-LICIND-S",
            merchants,
        )
        assert event.intent is Intent.BILL_DUE
        assert event.amount.value == pytest.approx(15340.0)
        assert event.ledger.counts_toward_spend is False

    def test_premium_debit_is_still_an_expense(self, merchants):
        # The `due` requirement in the bill rule keeps a paid premium in the ledger.
        event = classify(
            "Rs 15,340 debited from A/c XX4092 towards LIC premium. Avl Bal: Rs 20,000.",
            "AD-HDFCBK-T",
            merchants,
        )
        assert event.intent is Intent.EXPENSE
        assert event.ledger.counts_toward_spend is True

    def test_telecom_recharge_is_spend(self, merchants):
        event = classify(
            "Recharge of Rs 349 successful for 9876543210. Validity 28 days.",
            "AD-JIO-S",
            merchants,
        )
        assert event.intent is Intent.EXPENSE
        assert event.ledger.counts_toward_spend is True
        assert event.amount.value == pytest.approx(349.0)

    def test_fastag_recharge_is_still_a_load(self, merchants):
        # The recharge rule must not swallow instrument loads.
        event = classify(
            "FASTag recharge of Rs 500 successful on your HDFC FASTag. Balance Rs 720.",
            "AD-HDFCBK-T",
            merchants,
        )
        assert event.intent is Intent.INSTRUMENT_LOAD
        assert event.ledger.counts_toward_spend is False

    def test_voucher_with_plain_code_is_saved(self, merchants):
        event = classify(
            "You've earned a Rs 150 Swiggy voucher. Code: SWG150X. Valid till 31-03-2025.",
            "AD-SWIGGY-S",
            merchants,
        )
        assert event.intent is Intent.VOUCHER_ISSUED
        assert event.voucher is not None and event.voucher.code == "SWG150X"
        assert event.routing.apple_subaction is AppleSubAction.TRANSACTIONAL_REWARDS

    def test_otp_code_is_not_mistaken_for_a_voucher(self, merchants):
        event = classify("Your OTP code 482917. Do not share it.", "VM-SBIN-T", merchants)
        assert event.intent is Intent.OTP
        assert event.voucher is None

    def test_real_debit_with_pitch_is_still_one_expense(self, merchants):
        # Guard for the new "no strong movement verb" upsell rule.
        event = classify(
            "Rs.1,450.00 debited from A/c XX4092 to VPA KIRANAKART@axisbank "
            "(UPI Ref 505612345678). Convert this spend into EasyEMI! bit.ly/hdfcemi",
            "AD-HDFCBK-T",
            merchants,
        )
        assert event.intent is Intent.EXPENSE
        assert "embedded_upsell" in event.flags
