from __future__ import annotations

from datetime import datetime

import pytest

from sms_wiz.classify import analyse
from sms_wiz.llm import LLMEnricher, Provider, _parse_json, providers_from_env
from sms_wiz.models import Intent

from .conftest import NOW


class StubProvider:
    """Duck-typed stand-in for :class:`sms_wiz.llm.Provider` (no network)."""

    def __init__(self, result=None, error: Exception | None = None, name: str = "stub") -> None:
        self.result = result
        self.error = error
        self.name = name
        self.enabled = True
        self.prompts: list[str] = []

    def propose(self, prompt: str):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def unknown_merchant_event(merchants):
    return analyse(
        "Rs 320.00 spent at SAI TEJA STORES on 01-03-25 on your HDFC Credit Card.",
        "AD-HDFCBK-T",
        now=NOW,
        merchants=merchants,
    )


class TestConfiguration:
    def test_disabled_without_env(self):
        assert not LLMEnricher.from_env({}).enabled

    def test_single_provider_from_env(self):
        enricher = LLMEnricher.from_env({"SMSWIZ_LLM_BASE_URL": "http://localhost:1234/v1"})
        assert enricher.enabled
        assert enricher.providers[0].model == "local-model"

    def test_chain_from_env(self):
        chain = (
            '[{"name":"home","base_url":"http://192.168.0.5:1234/v1","model":"qwen2.5-14b"},'
            '{"name":"groq","base_url":"https://api.groq.com/openai/v1","model":"llama-3.3-70b",'
            '"api_key_env":"GROQ_KEY"}]'
        )
        providers = providers_from_env({"SMSWIZ_LLM_CHAIN": chain, "GROQ_KEY": "k"})
        assert [p.name for p in providers] == ["home", "groq"]
        assert providers[1].api_key == "k"

    def test_broken_chain_json_does_not_crash(self):
        assert providers_from_env({"SMSWIZ_LLM_CHAIN": "{not json"}) == []


class TestParsing:
    def test_plain_json(self):
        assert _parse_json('{"category": "Food & Dining"}') == {"category": "Food & Dining"}

    def test_fenced_json(self):
        assert _parse_json('```json\n{"category": null}\n```') == {"category": None}

    def test_prose_wrapped_json(self):
        assert _parse_json('Sure, here it is: {"clean": "Swiggy"} hope that helps') == {"clean": "Swiggy"}

    def test_garbage(self):
        assert _parse_json("I cannot help with that") is None


class TestMerging:
    def test_names_an_unresolved_merchant(self, unknown_merchant_event, merchants):
        event = unknown_merchant_event
        assert event.merchant.source == "none"

        stub = StubProvider(
            {"merchant_clean": "Sai Teja Kirana", "category": "Groceries & Quick Commerce", "confidence": 0.9}
        )
        enriched = LLMEnricher([stub]).enrich(event, merchants)

        assert enriched.merchant.clean == "Sai Teja Kirana"
        assert enriched.merchant.source == "llm"
        assert enriched.analysis["llm"]["status"] == "ok"
        assert enriched.raw_ai is not None

    def test_cannot_overwrite_a_seeded_merchant(self, merchants):
        event = analyse(
            "Rs 1450.00 debited to VPA KIRANAKART@axisbank (UPI Ref 123456789012).",
            "AD-HDFCBK-T",
            now=NOW,
            merchants=merchants,
        )
        assert event.merchant.clean == "Zepto"

        stub = StubProvider({"merchant_clean": "Something Else", "confidence": 0.99})
        enriched = LLMEnricher([stub]).enrich(event, merchants)
        assert enriched.merchant.clean == "Zepto"

    def test_low_confidence_is_ignored(self, unknown_merchant_event, merchants):
        stub = StubProvider({"merchant_clean": "Guessed Name", "confidence": 0.2})
        enriched = LLMEnricher([stub]).enrich(unknown_merchant_event, merchants)
        assert enriched.merchant.clean is None
        assert "merchant_clean" not in enriched.analysis["llm"]

    def test_falls_back_to_the_next_provider(self, unknown_merchant_event, merchants):
        broken = StubProvider(error=RuntimeError("connection refused"), name="home-gpu")
        working = StubProvider({"merchant_clean": "Sai Teja Kirana", "confidence": 0.8}, name="cloud")
        enriched = LLMEnricher([broken, working]).enrich(unknown_merchant_event, merchants)
        assert enriched.analysis["llm"]["provider"] == "cloud"

    def test_all_providers_down_is_recorded_not_raised(self, unknown_merchant_event, merchants):
        enriched = LLMEnricher([StubProvider(error=RuntimeError("down"))]).enrich(unknown_merchant_event, merchants)
        assert enriched.analysis["llm"]["status"] == "unavailable"

    def test_intent_hint_only_rescues_unknown_and_needs_review(self, merchants):
        event = analyse("Zzz qqq 123 random text", "9999999999", now=NOW, merchants=merchants)
        assert event.intent is Intent.UNKNOWN
        stub = StubProvider({"intent_hint": "promo", "confidence": 0.95})
        enriched = LLMEnricher([stub]).enrich(event, merchants)
        assert "llm_intent_hint" in enriched.flags
        assert enriched.confidence.needs_review is True
        assert enriched.intent is Intent.UNKNOWN  # never mutated silently

    def test_confident_rules_are_not_downgraded(self, merchants):
        event = analyse(
            "Payment of Rs 18,450.00 towards your ICICI Credit Card XX4092 has been received.",
            "AD-ICICIB-S",
            now=NOW,
            merchants=merchants,
        )
        stub = StubProvider({"intent_hint": "expense", "is_bank_upsell": True, "confidence": 0.99})
        enriched = LLMEnricher([stub]).enrich(event, merchants)
        assert enriched.intent is Intent.CC_BILL_PAYMENT
        assert enriched.ledger.counts_toward_spend is False


class TestRedactionBeforeSending:
    def test_otp_never_reaches_the_prompt(self, merchants):
        event = analyse(
            "482910 is your OTP to register e-mandate of Rs 15,000/month for Netflix AutoPay.",
            "VM-NETFLX-S",
            now=NOW,
            merchants=merchants,
        )
        stub = StubProvider({"confidence": 0.1})
        LLMEnricher([stub]).enrich(event, merchants)
        prompt = stub.prompts[0]
        assert "482910" not in prompt
        assert "<otp>" in prompt

    def test_voucher_code_and_pin_never_reach_the_prompt(self, merchants):
        event = analyse(
            "You have received a Rs 1,000 Amazon Pay gift card. Voucher code: AMZP-4K9Q-7T2X. PIN: 982104.",
            "AD-AMAZON-S",
            now=NOW,
            merchants=merchants,
        )
        stub = StubProvider({"confidence": 0.1})
        LLMEnricher([stub]).enrich(event, merchants)
        prompt = stub.prompts[0]
        assert "AMZP-4K9Q-7T2X" not in prompt
        assert "982104" not in prompt

    def test_upi_reference_is_masked(self, merchants):
        event = analyse(
            "Rs 1450.00 debited to VPA KIRANAKART@axisbank (UPI Ref 505612345678).",
            "AD-HDFCBK-T",
            now=NOW,
            merchants=merchants,
        )
        stub = StubProvider({"confidence": 0.1})
        LLMEnricher([stub]).enrich(event, merchants)
        assert "505612345678" not in stub.prompts[0]
