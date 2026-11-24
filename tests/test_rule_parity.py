"""The rule DSL has two evaluators; this is the seam that keeps them honest.

``rules.py`` answers ``POST /api/v1/filter`` on the server. ``FilterRules.swift``
answers the same question inside the message filter extension, with no network
and no Python. Both read the same compiled file, so the only thing that can
drift is the *evaluation* — which is exactly what these cases pin.

The cases are generated (``scripts/gen_rule_fixtures.py``) so that adding a rule
without adding a case fails the build, and so the recorded answers are what the
code does rather than what an author believed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sms_wiz import rules

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "fixtures" / "filter_rules" / "cases.json"
COMPILED = ROOT / "contracts" / "filter_rules.json"

DOCUMENT = json.loads(CASES.read_text(encoding="utf-8"))
ACTIVE = rules.load()


def verdict_of(case: dict) -> dict:
    verdict = rules.evaluate(case["sender"], case["body"], ACTIVE, sender_kind=case["sender_kind"])
    return {
        "matched": verdict is not None,
        "rule_id": verdict.rule_id if verdict else None,
        "action": verdict.action.value if verdict else "allow",
        "sub_action": verdict.sub_action.value if verdict else "none",
    }


class TestFixtureIsCurrent:
    def test_fixture_matches_the_rules_file(self):
        """A rule change without regenerating is a build failure, not a surprise."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "gen_rule_fixtures.py"), "--check"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_fixture_points_at_the_compiled_file(self):
        assert DOCUMENT["rules_file"] == "contracts/filter_rules.json"
        assert DOCUMENT["syntax"] == rules.SYNTAX
        assert COMPILED.exists()

    def test_every_rule_has_a_case(self):
        intended = {case.get("intended_rule") for case in DOCUMENT["cases"]}
        missing = [rule.id for rule in ACTIVE if rule.id not in intended]
        assert missing == []


@pytest.mark.parametrize("case", DOCUMENT["cases"], ids=[c["id"] for c in DOCUMENT["cases"]])
def test_python_agrees_with_the_recorded_verdict(case):
    assert verdict_of(case) == case["expect"]


class TestTheEdgesStayRight:
    """The four hand-written cases encode failures that hurt a real user."""

    def case(self, case_id: str) -> dict:
        return next(c for c in DOCUMENT["cases"] if c["id"] == case_id)

    def test_a_plain_debit_is_allowed(self):
        assert self.case("edge.plain_debit_is_allowed")["expect"]["action"] == "allow"

    def test_an_otp_is_never_junked(self):
        verdict = self.case("edge.otp_never_junked")["expect"]
        assert verdict["action"] != "junk"
        assert verdict["sub_action"] == "transactionalReminders"

    def test_money_language_beats_promo_language(self):
        assert self.case("edge.promo_with_debit_word_is_not_promoted")["expect"]["action"] == "allow"

    def test_matching_is_case_insensitive(self):
        assert self.case("edge.case_insensitive")["expect"]["rule_id"] == "security.apk_download"


class TestCompiledArtifact:
    def test_compiled_rules_equal_the_authoring_file(self):
        compiled = json.loads(COMPILED.read_text(encoding="utf-8"))
        assert len(compiled["rules"]) == len(ACTIVE)
        assert [r["id"] for r in compiled["rules"]] == [r.id for r in ACTIVE]

    def test_compiled_file_only_uses_declared_sub_actions(self):
        compiled = json.loads(COMPILED.read_text(encoding="utf-8"))
        declared = set(compiled["declared_sub_actions"])
        used = {r["then"]["sub_action"] for r in compiled["rules"]}
        assert used <= declared
