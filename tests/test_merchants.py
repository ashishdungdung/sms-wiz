"""Merchant dictionary: catalog, VPA hints, precedence, and honest misses."""

from __future__ import annotations

import pytest

from sms_wiz import catalog
from sms_wiz.merchants import MerchantDictionary, normalize_key, vpa_handle


class TestCatalog:
    def test_catalog_is_large_and_unique(self):
        keys = [row[0] for row in catalog.iter_seed_rows()]
        assert len(keys) >= 300
        assert len({k.upper() for k in keys}) == len(keys)

    def test_every_category_used_by_a_merchant_exists(self):
        for _raw, _clean, slug, _aliases in catalog.MERCHANTS:
            assert slug in catalog.CATEGORY_BY_SLUG, slug

    def test_categories_are_grouped_and_essential_flags_are_meaningful(self):
        assert catalog.is_essential("Groceries & Quick Commerce") is True
        assert catalog.is_essential("Entertainment & Subscriptions") is False
        assert catalog.CATEGORY_BY_LABEL["Utilities & Bills"].group == "Essentials"

    def test_old_labels_map_onto_the_taxonomy(self):
        assert catalog.category_slug("Travel & Commute") == "travel"
        assert catalog.category_slug("Fees & Charges") == "fees"
        assert catalog.category_slug(None) == "uncategorized"


class TestResolution:
    def test_legal_entity_becomes_a_brand(self, merchants):
        match = merchants.lookup("debited to VPA KIRANAKART@axisbank")
        assert match is not None
        assert match.clean == "Zepto"
        assert match.category == "Groceries & Quick Commerce"

    def test_longest_key_wins(self, merchants):
        assert merchants.lookup("SWIGGY INSTAMART order").clean == "Swiggy Instamart"
        assert merchants.lookup("SWIGGY order").clean == "Swiggy"

    def test_vpa_handle_resolves_when_the_name_does_not(self, merchants):
        match = merchants.resolve("Paid to bigbasket@ybl UPI Ref 123456789012")
        assert match is not None
        assert match.clean == "BigBasket"
        assert match.source in {"seed", "vpa"}

    def test_person_to_person_vpa_is_not_a_merchant(self, merchants):
        # A number@psp is a person; inventing a merchant would corrupt categories.
        assert merchants.resolve("Sent Rs 500 to 9876543210@ybl") is None

    def test_bank_psp_handle_never_becomes_a_merchant(self, merchants):
        assert merchants.resolve("Sent Rs 500 to 9876543210@okhdfcbank") is None

    def test_unknown_merchant_is_a_miss_not_a_guess(self, merchants):
        assert merchants.lookup("PAID TO SAI TEJA GENERAL STORES") is None


class TestPrecedence:
    def test_user_beats_seed(self, merchants):
        merchants.learn("SWIGGY", "Swiggy Bangalore", "Food & Dining", source="user")
        match = merchants.lookup("SWIGGY order")
        assert match.clean == "Swiggy Bangalore"
        assert match.source == "user"

    def test_learned_never_overwrites_a_seed(self, merchants):
        merchants.learn("KIRANAKART", "Something Else", "Shopping", source="learned")
        assert merchants.lookup("KIRANAKART").clean == "Zepto"

    def test_llm_can_be_upgraded_by_a_seed(self, merchants):
        merchants.learn("SAI TEJA STORES", "Sai Teja", "Groceries & Quick Commerce", source="llm")
        assert merchants.lookup("SAI TEJA STORES").source == "llm"
        merchants.learn("SAI TEJA STORES", "Sai Teja Kirana", "Groceries & Quick Commerce", source="seed")
        assert merchants.lookup("SAI TEJA STORES").clean == "Sai Teja Kirana"

    def test_account_numbers_are_never_learned(self, merchants):
        before = merchants.stats()["total"]
        merchants.learn("XX4092", "Groww", "Investments", source="learned")
        assert merchants.stats()["total"] == before
        assert merchants.lookup("A/c XX4092 debited") is None


class TestMaintenance:
    def test_forget_removes_user_rows_only(self, merchants):
        merchants.learn("MY KIRANA", "My Kirana", "Groceries & Quick Commerce", source="user")
        assert merchants.forget("MY KIRANA") is True
        assert merchants.forget("KIRANAKART") is False  # curated seed

    def test_stats_and_categories(self, merchants):
        stats = merchants.stats()
        assert stats["seed"] >= 300
        assert stats["total"] == stats["seed"]
        categories = merchants.categories()
        assert any(row["slug"] == "groceries" for row in categories)

    def test_normalize_key_drops_legal_suffixes(self):
        assert normalize_key("Kiranakart Technologies Pvt Ltd") == "KIRANAKART"
        assert normalize_key("Bundl Technologies Private Limited") == "BUNDL"

    def test_vpa_handle_extraction(self):
        assert vpa_handle("paid to swiggy@icici") == "swiggy"
        assert vpa_handle("no vpa here") is None
