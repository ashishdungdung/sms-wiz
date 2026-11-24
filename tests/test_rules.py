"""The issuer-authored filter DSL: what it decides, and what it refuses to ship."""

from __future__ import annotations

import json

import pytest

from sms_wiz import rules
from sms_wiz.models import AppleAction, AppleSubAction
from sms_wiz.registry import DECLARED_PROMOTIONAL, DECLARED_TRANSACTIONAL


def doc(*entries: dict, syntax: str = rules.SYNTAX) -> dict:
    return {"syntax": syntax, "rules": list(entries)}


def rule(when: dict, action: str = "promotion", sub: str = "promotionalOffers", **then) -> dict:
    """The ``then`` half of a rule, so entries read ``{"id", "when", **rule(when)}``."""
    payload = {"action": action, "sub_action": sub, "confidence": 0.8, "reason": "test"}
    payload.update(then)
    return {"then": payload}


class TestAuthoredFile:
    def test_shipped_rules_load_and_are_ordered(self):
        loaded = rules.load()
        assert len(loaded) >= 12
        assert [r.index for r in loaded] == list(range(len(loaded)))
        assert rules.validate_file() == []

    def test_every_shipped_sub_action_is_declared_to_ios(self):
        declared = {s.value for s in DECLARED_TRANSACTIONAL} | {s.value for s in DECLARED_PROMOTIONAL}
        for entry in rules.load():
            if entry.sub_action is not AppleSubAction.NONE:
                assert entry.sub_action.value in declared

    def test_stats_cover_all_three_outcomes(self):
        stats = rules.stats()
        assert stats["rules"] == len(rules.load())
        assert stats["by_action"]["transaction"] >= 5
        assert stats["by_action"]["junk"] >= 1

    def test_compiled_artifact_is_deterministic_and_complete(self):
        first = rules.compiled()
        second = rules.compiled()
        assert first == second
        assert first["syntax"] == rules.SYNTAX
        assert len(first["rules"]) == len(rules.load())
        assert first["declared_sub_actions"]


class TestEvaluation:
    def test_clean_transaction_is_allowed_by_default(self):
        assert rules.evaluate("AD-HDFCBK-T", "Rs.1,450.00 debited from A/c XX4092 to VPA SWIGGY@icici") is None

    def test_bank_emi_pitch_goes_to_promotions(self):
        verdict = rules.evaluate(
            "AD-HDFCBK-T", "Convert your purchase to Easy EMI at 0% interest. Pre-approved for you."
        )
        assert verdict is not None
        assert verdict.action is AppleAction.PROMOTION
        assert verdict.sub_action is AppleSubAction.PROMOTIONAL_OFFERS

    def test_kyc_link_from_a_personal_number_is_junk(self):
        verdict = rules.evaluate(
            "9876543210", "Dear customer your KYC is blocked. Verify at http://sbi-kyc.online/apk"
        )
        assert verdict is not None and verdict.action is AppleAction.JUNK

    def test_otp_is_never_junk(self):
        verdict = rules.evaluate("AD-HDFCBK-T", "123456 is your OTP for login. Do not share.")
        assert verdict is not None
        assert verdict.action is AppleAction.TRANSACTION
        assert verdict.sub_action is AppleSubAction.TRANSACTIONAL_REMINDERS

    def test_first_match_wins(self):
        loaded = rules.parse_document(
            doc(
                {"id": "a.first", "when": {"any": ["netflix"]}, **rule({"any": ["netflix"]})},
                {"id": "a.second", "when": {"any": ["netflix"]}, **rule({"any": ["netflix"]}, action="transaction", sub="transactionalOrders")},
            )
        )
        verdict = rules.evaluate("X", "NETFLIX payment", rules=loaded)
        assert verdict.rule_id == "a.first"

    def test_sender_prefix_filter_is_respected(self):
        loaded = rules.parse_document(
            doc({"id": "bank.only", "when": {"sender_prefixes": ["HDFCBK"], "any": ["offer"]}, **rule({"any": ["offer"]})})
        )
        assert rules.evaluate("AD-ICICIB-T", "offer for you", rules=loaded) is None
        assert rules.evaluate("AD-HDFCBK-T", "offer for you", rules=loaded) is not None

    def test_all_requires_every_term(self):
        loaded = rules.parse_document(
            doc({"id": "x.all", "when": {"all": ["loan", "approved"]}, **rule({"all": ["loan"]})})
        )
        assert rules.evaluate("X", "loan available", rules=loaded) is None
        assert rules.evaluate("X", "your loan is approved", rules=loaded) is not None

    def test_none_blocks_a_match(self):
        loaded = rules.parse_document(
            doc({"id": "x.none", "when": {"any": ["emi"], "none": ["debited"]}, **rule({"any": ["emi"]})})
        )
        assert rules.evaluate("X", "EMI offer", rules=loaded) is not None
        assert rules.evaluate("X", "EMI debited for your loan", rules=loaded) is None

    def test_amount_bounds(self):
        loaded = rules.parse_document(
            doc({"id": "x.big", "when": {"any": ["transfer"], "min_amount": 50000}, **rule({"any": ["transfer"]})})
        )
        assert rules.evaluate("X", "transfer of Rs 60,000", rules=loaded, amount=60000) is not None
        assert rules.evaluate("X", "transfer of Rs 500", rules=loaded, amount=500) is None

    def test_verdict_serialises_for_the_api(self):
        verdict = rules.evaluate("AD-HDFCBK-T", "Easy EMI available on your card")
        payload = verdict.as_dict()
        assert payload["action"] == "promotion"
        assert payload["rule_id"] and payload["reason"]


class TestValidator:
    """A wrong rule must stop the build, not ship a wrong folder."""

    def test_rejects_unknown_action(self):
        with pytest.raises(rules.RuleError, match="unknown action"):
            rules.parse_document(
                doc({"id": "a.x", "when": {"any": ["x"]}, "then": {"action": "delete", "sub_action": "none"}})
            )

    def test_rejects_undeclared_sub_action(self):
        with pytest.raises(rules.RuleError, match="not declared"):
            rules.parse_document(
                doc(
                    {
                        "id": "a.x",
                        "when": {"any": ["x"]},
                        "then": {"action": "transaction", "sub_action": "transactionalHealth"},
                    }
                )
            )

    def test_junk_cannot_be_paired_with_a_sub_action(self):
        with pytest.raises(rules.RuleError, match="Junk has no sub-action"):
            rules.parse_document(
                doc(
                    {
                        "id": "a.x",
                        "when": {"any": ["winner"]},
                        "then": {"action": "junk", "sub_action": "promotionalOffers"},
                    }
                )
            )

    def test_junk_needs_a_danger_signal(self):
        with pytest.raises(rules.RuleError, match="danger signal"):
            rules.parse_document(
                doc({"id": "a.x", "when": {"any": ["sale"]}, "then": {"action": "junk", "sub_action": "none"}})
            )

    def test_sub_action_must_belong_to_its_action(self):
        with pytest.raises(rules.RuleError, match="does not belong"):
            rules.parse_document(
                doc(
                    {
                        "id": "a.x",
                        "when": {"any": ["x"]},
                        "then": {"action": "promotion", "sub_action": "transactionalOrders"},
                    }
                )
            )

    def test_rejects_duplicate_ids(self):
        entry = {"id": "a.same", "when": {"any": ["x"]}, **rule({"any": ["x"]})}
        with pytest.raises(rules.RuleError, match="duplicate rule id"):
            rules.parse_document(doc(entry, dict(entry)))

    def test_rejects_a_rule_with_no_conditions(self):
        with pytest.raises(rules.RuleError, match="at least one condition"):
            rules.parse_document(doc({"id": "a.empty", "when": {}, **rule({})}))

    def test_rejects_bad_confidence(self):
        with pytest.raises(rules.RuleError, match="confidence"):
            rules.parse_document(doc({"id": "a.x", "when": {"any": ["x"]}, **rule({"any": ["x"]}, confidence=1.5)}))

    def test_rejects_uppercase_id(self):
        with pytest.raises(rules.RuleError, match="lowercase dotted"):
            rules.parse_document(doc({"id": "Bad ID", "when": {"any": ["x"]}, "then": rule({"any": ["x"]})}))

    def test_rejects_unknown_syntax_version(self):
        with pytest.raises(rules.RuleError, match="unknown syntax"):
            rules.parse_document(doc(syntax="sms-wiz.filter/v9"))

    def test_rejects_empty_document(self):
        with pytest.raises(rules.RuleError, match="non-empty"):
            rules.parse_document({"syntax": rules.SYNTAX, "rules": []})

    def test_validate_file_reports_bad_json(self, tmp_path):
        bad = tmp_path / "rules.json"
        bad.write_text("{ not json", encoding="utf-8")
        assert rules.validate_file(bad) and "invalid JSON" in rules.validate_file(bad)[0]

    def test_validate_file_accepts_the_shipped_file(self, tmp_path):
        copy = tmp_path / "rules.json"
        copy.write_text(
            json.dumps({"syntax": rules.SYNTAX, "rules": [{"id": "a.ok", "when": {"any": ["x"]}, **rule({"any": ["x"]})}]}),
            encoding="utf-8",
        )
        assert rules.validate_file(copy) == []
