"""Generate the cross-language fixtures for the filter-rule DSL.

Why generate rather than hand-write
-----------------------------------
``FilterRules.swift`` has to reach the same verdict as ``rules.py`` for every
rule in ``contracts/filter_rules.json``. Writing 34 (message, verdict) pairs by
hand invites the classic failure: the fixture is what the author *thought* the
rule says. Generating the bodies from the rules themselves, then recording what
Python actually answers, tests the one property the Swift code must have —
agreement — and the Python tests separately pin the semantics.

The file is checked in so the Swift test can read it without running Python
first. ``--check`` makes it a gate: change a rule without regenerating and
``make verify`` fails.

    ./.venv/bin/python scripts/gen_rule_fixtures.py           # write
    ./.venv/bin/python scripts/gen_rule_fixtures.py --check   # verify
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sms_wiz import rules  # noqa: E402

TARGET = ROOT / "tests" / "fixtures" / "filter_rules" / "cases.json"

#: Sent to the evaluator when a rule constrains nothing about the sender.
DEFAULT_SENDERS = {
    "personal_mobile": "9876543210",
    "unknown": "57575",
    "shortcode": "57575",
    "bank": "AD-HDFCBK-T",
    "fintech": "AD-PAYTM-S",
    "brand": "AD-AMAZON-S",
    "govt": "AD-GOVTIN-S",
    "telecom": "AD-JIO-S",
}
LINK = "http://bit.ly/verify-now"


def _sender_for(rule: rules.Rule) -> str:
    kinds = rule.when.get("sender_kinds") or ["unknown"]
    prefixes = rule.when.get("sender_prefixes") or []
    if prefixes:
        return f"AD-{prefixes[0]}-T"
    return DEFAULT_SENDERS.get(str(kinds[0]), "9876543210")


def _candidate_bodies(rule: rules.Rule) -> list[str]:
    """Bodies built from the rule's own terms, narrowest first."""
    when = rule.when
    all_terms = [str(t) for t in when.get("all", [])]
    any_terms = [str(t) for t in when.get("any", [])]
    has_link = bool(when.get("has_link"))
    tail = f" {LINK}" if has_link else ""
    candidates: list[str] = []
    if any_terms:
        for term in any_terms:
            candidates.append((" ".join([*all_terms, term]) + tail).strip())
    candidates.append((" ".join([*all_terms, *any_terms]) + tail).strip())
    # A very plain sentence as a last resort: the rule may key on a sender kind
    # or an amount only, in which case no term needs to appear.
    candidates.append(("Rs 1,250.00 spent on your card at a shop" + tail).strip())
    seen: set[str] = set()
    unique = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _verdict(sender: str, body: str, kind: str, active: list[rules.Rule]) -> dict:
    """What the *whole* rule file answers — the only answer the extension gives."""
    verdict = rules.evaluate(sender, body, active, sender_kind=kind)
    return {
        "matched": verdict is not None,
        "rule_id": verdict.rule_id if verdict else None,
        "action": verdict.action.value if verdict else "allow",
        "sub_action": verdict.sub_action.value if verdict else "none",
    }


def _case_for(rule: rules.Rule, active: list[rules.Rule]) -> dict:
    sender = _sender_for(rule)
    kind = str((rule.when.get("sender_kinds") or ["unknown"])[0])
    bodies = _candidate_bodies(rule)
    body = bodies[0]
    for candidate in bodies:
        verdict = rules.evaluate(sender, candidate, active, sender_kind=kind)
        # Prefer a body where this rule is the one that answers: it keeps the
        # case about its own rule. Order still decides, so a shadowed rule
        # records the winner instead of pretending.
        if verdict is not None and verdict.rule_id == rule.id:
            body = candidate
            break
    expect = _verdict(sender, body, kind, active)
    case = {
        "id": f"rule.{rule.id}",
        "intended_rule": rule.id,
        "sender": sender,
        "sender_kind": kind,
        "body": body,
        "expect": expect,
    }
    if expect["rule_id"] != rule.id:
        case["shadowed_by"] = expect["rule_id"]
    return case


#: Cases that exist because getting them wrong is a real-world failure, not
#: because a rule needs coverage.
HAND_WRITTEN = [
    {
        "id": "edge.plain_debit_is_allowed",
        "sender": "AD-HDFCBK-T",
        "sender_kind": "bank",
        "body": "Rs.1,450.00 debited from A/c XX4092 on 25-02-25 to VPA KIRANAKART@axisbank. UPI Ref 505612345678.",
    },
    {
        "id": "edge.otp_never_junked",
        "sender": "AD-HDFCBK-T",
        "sender_kind": "bank",
        "body": "123456 is your OTP for txn of Rs.1,450.00 at Amazon. Do not share it with anyone.",
    },
    {
        "id": "edge.promo_with_debit_word_is_not_promoted",
        "sender": "AD-HDFCBK-T",
        "sender_kind": "bank",
        "body": "Convert your purchase to Easy EMI at 0% interest. Pre-approved for you. Rs 3,499 debited at CROMA.",
    },
    {
        "id": "edge.case_insensitive",
        "sender": "9876543210",
        "sender_kind": "personal_mobile",
        "body": "Please INSTALL APP from this link to receive your prize",
    },
]


def build() -> dict:
    active = rules.load()
    cases = [_case_for(rule, active) for rule in active]
    for case in HAND_WRITTEN:
        # The expectation is what the file answers, not what the author hoped:
        # this fixture exists to prove the two implementations agree.
        rows = dict(case)
        rows["expect"] = _verdict(case["sender"], case["body"], case["sender_kind"], active)
        cases.append(rows)
    return {
        "about": [
            "Generated by scripts/gen_rule_fixtures.py — do not edit by hand.",
            "Each case is a (sender, body, sender_kind) triple and the verdict Python gives it.",
            "FilterRulesParityTests replays the same file against contracts/filter_rules.json,",
            "so the extension's offline evaluator cannot drift from the server's fast path.",
        ],
        "syntax": rules.SYNTAX,
        "rules_file": "contracts/filter_rules.json",
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="generate filter-rule parity fixtures")
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args(argv)

    document = build()
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if not TARGET.exists():
            print(f"{TARGET.relative_to(ROOT)} is missing — run scripts/gen_rule_fixtures.py")
            return 1
        current = TARGET.read_text(encoding="utf-8")
        if current != text:
            print(
                f"{TARGET.relative_to(ROOT)} is stale: the rule file changed since it was "
                "generated. Run ./.venv/bin/python scripts/gen_rule_fixtures.py"
            )
            return 1
        print(f"rule fixtures up to date ({len(document['cases'])} cases)")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(document['cases'])} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
