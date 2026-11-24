"""Bank templates: the per-bank sentence shapes.

Each bank gets a realistic message; the template must pull the right amount,
last4, merchant and balance. A template that is merely "generous" is worse than
none, so the negative cases matter as much as the positive ones.
"""

from __future__ import annotations

import pytest

from sms_wiz import extract, templates


def parse(sender: str, body: str) -> templates.TemplateHit | None:
    return templates.match(extract.normalize(body), sender)


class TestFieldExtraction:
    def test_hdfc_debit_with_balance_merges_two_templates(self):
        hit = parse(
            "AD-HDFCBK-T",
            "Rs.1,450.00 debited from A/c XX4092 on 25-02-25 to VPA KIRANAKART@axisbank "
            "(UPI Ref 505612345678). Avl Bal: Rs.5,000.00",
        )
        assert hit is not None
        assert hit.bank == "HDFC Bank"
        assert hit.amount == pytest.approx(1450.0)
        assert hit.last4 == "4092"
        assert hit.balance == pytest.approx(5000.0)
        assert "hdfc.balance" in hit.template_ids
        assert hit.merchant is not None and "KIRANAKART" in hit.merchant

    def test_sbi_psu_format(self):
        hit = parse("VM-SBIN-T", "Your A/c no. XXXXXXXX1234 is debited by Rs.1,200.00 on 25Feb25")
        assert hit is not None and hit.template_id == "sbi.debit"
        assert hit.amount == pytest.approx(1200.0)
        assert hit.last4 == "1234"

    def test_kotak_sent_format(self):
        hit = parse("AD-KOTAKB-S", "Sent Rs.500 from Kotak Bank AC X1234 to Rahul on 25-02-25")
        assert hit is not None and hit.amount == pytest.approx(500.0)
        assert hit.last4 == "1234"

    def test_icici_card_payment(self):
        hit = parse(
            "AD-ICICIB-S",
            "Payment of Rs 18,450.00 towards your ICICI Credit Card XX4092 has been received.",
        )
        assert hit is not None
        assert hit.kind == "card"
        assert hit.amount == pytest.approx(18450.0)

    def test_idfc_inr_prefix(self):
        hit = parse("AD-IDFCFB-S", "INR 2,499.00 has been debited from your a/c XX1122 on 25-02-25")
        assert hit is not None and hit.amount == pytest.approx(2499.0)

    def test_card_spend_and_ending_are_two_templates(self):
        hit = parse("SBICRD-T", "Rs 1,200 spent on SWIGGY using SBI Card XX4433 on 25 Feb")
        assert hit is not None
        assert hit.merchant == "SWIGGY"
        assert hit.last4 == "4433"

    def test_fastag_recharge(self):
        hit = parse("AD-HDFCBK-T", "FASTag recharge of Rs 500 successful on your HDFC FASTag.")
        assert hit is not None and hit.template_id == "fastag.recharge"

    def test_balance_only_message(self):
        hit = parse("AD-HDFCBK-T", "Avl Bal: Rs.12,300.00 in your HDFC Bank A/c XX4092")
        assert hit is not None
        assert hit.balance == pytest.approx(12300.0)


class TestNegativeCases:
    def test_unknown_sender_gets_no_bank_template(self):
        assert parse("9876543210", "Rs 5,000 debited from your account") is None

    def test_other_banks_template_does_not_fire(self):
        # Kotak's shape must not be matched for an HDFC header.
        hit = parse("AD-HDFCBK-T", "Sent Rs.500 from Kotak Bank AC X1234 to Rahul")
        assert hit is None

    def test_amount_alone_is_not_enough(self):
        assert parse("AD-HDFCBK-T", "Your HDFC Bank account is ready for UPI.") is None

    def test_format_mismatch_is_detected_but_only_as_a_signal(self):
        assert templates.expected_but_missing(
            extract.normalize("Rs 50,000 debited from your account"), "AD-HDFCBK-T"
        )
        assert not templates.expected_but_missing(
            extract.normalize("Rs.1,450.00 debited from A/c XX4092 to VPA X@ybl"), "AD-HDFCBK-T"
        )

    def test_format_mismatch_needs_money_language(self):
        assert not templates.expected_but_missing("Your KYC is expiring", "AD-HDFCBK-T")


class TestCatalog:
    def test_every_template_has_a_unique_id(self):
        ids = [t.id for t in templates.TEMPLATES]
        assert len(ids) == len(set(ids))

    def test_catalog_is_serialisable_for_the_app(self):
        rows = templates.catalog()
        assert len(rows) == len(templates.TEMPLATES)
        assert all({"id", "bank", "senders", "kind"} <= set(row) for row in rows)

    def test_every_sender_prefix_is_resolvable(self):
        assert templates.sender_prefix("AD-HDFCBK-T") == "HDFCBK"
        assert templates.sender_prefix("57575") == "57575"
        assert templates.bank_of("SBICRD-T") == "card"
