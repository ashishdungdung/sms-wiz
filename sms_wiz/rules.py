"""Issuer-authored filter rules: a tiny, fully offline DSL.

Why a DSL at all
----------------
The message filter extension gets one message, a few milliseconds, and no
network guarantee. The full Python chain is right for the app, but the extension
needs something it can ship as data and evaluate without a runtime.

So rules are authored in ``rules/filter_rules.json``, validated here, compiled
into ``contracts/filter_rules.json``, and evaluated in two places:

* Python — as a fast pre-pass for ``POST /api/v1/filter`` (the full chain still
  runs on ingest and stays authoritative);
* Swift — ``SMSCore/FilterRules.swift`` decodes the same compiled file and runs
  the offline pass inside the extension.

Syntax (``sms-wiz.filter/v1``)
------------------------------
::

    {
      "id": "bank.emi_offer",                 // unique, dots for namespacing
      "when": {
        "sender_prefixes": ["HDFCBK"],        // optional: DLT operator prefix
        "sender_kinds": ["personal_mobile"],  // optional: registry judgement
        "any":  ["easy emi", "pre-approved"], // at least one present (lowercase)
        "all":  ["due"],                      // every one present
        "none": ["debited"],                  // none present
        "has_link": true,                     // optional boolean
        "min_amount": 1000,                   // optional numeric bound
        "max_amount": 100000
      },
      "then": {
        "action": "promotion",                // allow | junk | promotion | transaction
        "sub_action": "promotionalOffers",    // must be declared by the app
        "confidence": 0.85,
        "reason": "lending pitch with no money movement"
      }
    }

Design rules the validator enforces
-----------------------------------
1. A rule cannot invent an action or sub-action. Apple's list is fixed, and an
   undeclared sub-action is silently dropped by iOS — so it is rejected here
   rather than shipped.
2. ``junk`` is only allowed when the rule names a *danger* signal (``apk``,
   ``kyc``, ``winner``, ``lottery``, ``blocked``...). Junking on "offer" teaches
   users to ignore the folder.
3. Rules never touch money. They return a folder, nothing else; the ledger is
   the Python chain's job.
4. First match wins, so the authoring file's order is the priority order.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import AppleAction, AppleSubAction
from .registry import DECLARED_PROMOTIONAL, DECLARED_TRANSACTIONAL

ROOT = Path(__file__).resolve().parent.parent
AUTHORING_PATH = ROOT / "rules" / "filter_rules.json"
SYNTAX = "sms-wiz.filter/v1"
DECLARED = {
    *(sub.value for sub in DECLARED_TRANSACTIONAL),
    *(sub.value for sub in DECLARED_PROMOTIONAL),
    AppleSubAction.NONE.value,
}

#: Language that may justify the Junk folder on its own.
DANGER_TERMS: tuple[str, ...] = (
    "apk",
    "install app",
    "download app",
    "kyc",
    "winner",
    "won",
    "lottery",
    "prize",
    "blocked",
    "suspended",
    "verify",
    "refund request",
    "account will be",
    "otp",
    "password",
    "pin",
    "cvv",
    "click here",
)

_WS = re.compile(r"\s+")


class RuleError(ValueError):
    """A rule file that would ship a wrong or impossible decision."""


@dataclass(frozen=True)
class Rule:
    id: str
    when: dict[str, Any]
    action: AppleAction
    sub_action: AppleSubAction
    confidence: float
    reason: str
    index: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "when": self.when,
            "then": {
                "action": self.action.value,
                "sub_action": self.sub_action.value,
                "confidence": self.confidence,
                "reason": self.reason,
            },
        }


@dataclass(frozen=True)
class RuleVerdict:
    rule_id: str
    action: AppleAction
    sub_action: AppleSubAction
    confidence: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "action": self.action.value,
            "subAction": self.sub_action.value,
            "confidence": self.confidence,
            "reason": self.reason,
        }


# --------------------------------------------------------------------------
# Loading and validation
# --------------------------------------------------------------------------


def _as_list(value: Any, field: str, rule_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RuleError(f"{rule_id}: when.{field} must be a list of strings")
    return [v.lower().strip() for v in value if v.strip()]


def parse_rule(payload: dict[str, Any], index: int) -> Rule:
    rule_id = str(payload.get("id") or "").strip()
    if not rule_id or not re.fullmatch(r"[a-z0-9]+(?:\.[a-z0-9_]+)*", rule_id):
        raise RuleError(f"rule #{index}: id must be lowercase dotted, e.g. 'bank.emi_offer'")

    when = payload.get("when")
    then = payload.get("then")
    if not isinstance(when, dict) or not isinstance(then, dict):
        raise RuleError(f"{rule_id}: needs 'when' and 'then' objects")

    action_raw = str(then.get("action") or "")
    try:
        action = AppleAction(action_raw)
    except ValueError as exc:
        raise RuleError(f"{rule_id}: unknown action '{action_raw}'") from exc

    sub_raw = str(then.get("sub_action") or "none")
    try:
        sub_action = AppleSubAction(sub_raw)
    except ValueError as exc:
        raise RuleError(f"{rule_id}: unknown sub_action '{sub_raw}'") from exc

    if sub_action.value not in DECLARED:
        raise RuleError(
            f"{rule_id}: sub_action '{sub_raw}' is not declared to iOS; "
            "it would be silently dropped"
        )
    if action is AppleAction.JUNK and sub_action is not AppleSubAction.NONE:
        raise RuleError(f"{rule_id}: Junk has no sub-action; use 'none'")
    if action in {AppleAction.ALLOW, AppleAction.PROMOTION, AppleAction.TRANSACTION} and (
        sub_action is not AppleSubAction.NONE
        and sub_action.value.startswith("transactional") != (action is AppleAction.TRANSACTION)
    ):
        raise RuleError(f"{rule_id}: sub_action '{sub_raw}' does not belong to action '{action_raw}'")

    any_terms = _as_list(when.get("any"), "any", rule_id)
    all_terms = _as_list(when.get("all"), "all", rule_id)
    none_terms = _as_list(when.get("none"), "none", rule_id)
    if not (any_terms or all_terms or when.get("has_link") or when.get("min_amount")):
        raise RuleError(f"{rule_id}: needs at least one condition")

    if action is AppleAction.JUNK:
        haystack = " ".join(any_terms + all_terms)
        if not any(term in haystack for term in DANGER_TERMS):
            raise RuleError(
                f"{rule_id}: junk needs a danger signal (apk, kyc, winner, blocked...); "
                "junking on generic marketing teaches users to ignore the folder"
            )

    confidence = float(then.get("confidence", 0.8))
    if not 0.0 < confidence <= 1.0:
        raise RuleError(f"{rule_id}: confidence must be in (0, 1]")

    clean_when = {k: v for k, v in when.items() if v not in (None, [], "")}
    return Rule(
        id=rule_id,
        when=clean_when,
        action=action,
        sub_action=sub_action,
        confidence=round(confidence, 3),
        reason=str(then.get("reason") or "").strip(),
        index=index,
    )


def parse_document(document: dict[str, Any]) -> list[Rule]:
    if document.get("syntax") not in (None, SYNTAX):
        raise RuleError(f"unknown syntax '{document.get('syntax')}'; expected {SYNTAX}")
    rules_raw = document.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise RuleError("document needs a non-empty 'rules' list")

    rules = [parse_rule(entry, i) for i, entry in enumerate(rules_raw)]
    seen: set[str] = set()
    for rule in rules:
        if rule.id in seen:
            raise RuleError(f"duplicate rule id '{rule.id}'")
        seen.add(rule.id)

    # Apple's ceiling: five declared sub-actions per group. Rules may only use
    # the ones the app declares, and the compiled file records the set used.
    used = {rule.sub_action for rule in rules if rule.sub_action is not AppleSubAction.NONE}
    if len(used) > len(DECLARED_TRANSACTIONAL) + len(DECLARED_PROMOTIONAL):
        raise RuleError("rules use more sub-actions than the app can declare")
    return rules


def load(path: Optional[Path] = None) -> list[Rule]:
    target = path or AUTHORING_PATH
    if not target.exists():
        return []
    return parse_document(json.loads(target.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def _normalise(text: str) -> str:
    return _WS.sub(" ", text.lower())


def _sender_prefix(sender: str) -> str:
    """Mirror of templates.sender_prefix so the extension and the server agree."""
    from .templates import sender_prefix

    return sender_prefix(sender)


def _matches(rule: Rule, sender: str, body: str, sender_kind: str, has_link: bool, amount: Optional[float]) -> bool:
    text = _normalise(body)
    when = rule.when

    prefixes = [str(p).upper() for p in when.get("sender_prefixes", [])]
    if prefixes and _sender_prefix(sender) not in prefixes:
        return False

    kinds = [str(k).lower() for k in when.get("sender_kinds", [])]
    if kinds and sender_kind.lower() not in kinds:
        return False

    if when.get("has_link") is True and not has_link:
        return False
    if when.get("has_link") is False and has_link:
        return False

    if amount is not None:
        if when.get("min_amount") is not None and amount < float(when["min_amount"]):
            return False
        if when.get("max_amount") is not None and amount > float(when["max_amount"]):
            return False

    for term in when.get("all", []):
        if str(term).lower() not in text:
            return False
    if any(str(term).lower() in text for term in when.get("none", [])):
        return False
    any_terms = [t.lower() for t in when.get("any", [])]
    if any_terms and not any(term in text for term in any_terms):
        return False
    return True


def evaluate(
    sender: str,
    body: str,
    rules: Optional[Iterable[Rule]] = None,
    sender_kind: str = "unknown",
    has_link: Optional[bool] = None,
    amount: Optional[float] = None,
) -> Optional[RuleVerdict]:
    """Return the first matching rule, or ``None`` (the extension then allows)."""
    active = list(rules if rules is not None else load())
    if not active:
        return None
    if has_link is None:
        has_link = bool(re.search(r"https?://|\bwww\.", body, re.IGNORECASE))
    for rule in sorted(active, key=lambda r: r.index):
        if _matches(rule, sender, body, sender_kind, has_link, amount):
            return RuleVerdict(
                rule_id=rule.id,
                action=rule.action,
                sub_action=rule.sub_action,
                confidence=rule.confidence,
                reason=rule.reason,
            )
    return None


# --------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------


def compiled(rules: Optional[Iterable[Rule]] = None) -> dict[str, Any]:
    """The artifact the Swift extension ships. Deterministic by construction."""
    active = list(rules if rules is not None else load())
    return {
        "version": 1,
        "syntax": SYNTAX,
        "generated": "scripts/export_contracts.py — do not edit by hand",
        "declared_sub_actions": sorted(DECLARED),
        "rules": [rule.as_dict() for rule in sorted(active, key=lambda r: r.index)],
    }


def validate_file(path: Optional[Path] = None) -> list[str]:
    """Return human-readable problems with the authoring file (empty = good)."""
    target = path or AUTHORING_PATH
    try:
        parse_document(json.loads(target.read_text(encoding="utf-8")))
    except RuleError as exc:
        return [str(exc)]
    except json.JSONDecodeError as exc:
        return [f"{target.name}: invalid JSON ({exc})"]
    return []


def stats(rules: Optional[Iterable[Rule]] = None) -> dict[str, Any]:
    active = list(rules if rules is not None else load())
    by_action: dict[str, int] = {}
    for rule in active:
        by_action[rule.action.value] = by_action.get(rule.action.value, 0) + 1
    return {
        "rules": len(active),
        "by_action": by_action,
        "sub_actions_used": sorted({r.sub_action.value for r in active}),
    }
