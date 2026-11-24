"""Optional LLM enrichment — off by default, redacted, and never in control.

The rule chain is the product. This module exists for the long tail the rules
cannot name: an unfamiliar local shop, a mall, a new fintech. It is deliberately
narrow, and every part of it is auditable.

Five hard rules
---------------
1. **Nothing goes out unless it is redacted.** OTPs, voucher codes/PINs, PAN,
   long numbers, phone numbers, e-mail addresses, VPA handles and URLs are
   masked before a request is built.
2. **The LLM cannot overrule a confident rule.** It may name an unresolved
   merchant, pick a category, or give an opinion on a message the rules could
   not classify (which is queued for review anyway). It can never change a
   confident intent, an amount, or a ledger treatment.
3. **Every response is untrusted.** Strict JSON, allow-listed keys, category
   allow-list, type checks, length caps, and any failure leaves the
   deterministic result untouched.
4. **It is not on the filter path.** Apple's network deferral has a tight
   deadline; a 14B model is not part of a folder decision.
5. **Local first.** LM Studio / llama.cpp / vLLM on the home network are
   first-class; the chain is ordered and the first valid answer wins.

What "deeper" means here
------------------------
* a versioned prompt with the full taxonomy, the JSON schema, few-shot
  examples and explicit anti-guessing instructions;
* a validator that returns *problems* rather than silently dropping fields;
* a merge policy that records ``applied`` **and** ``ignored`` (with reasons);
* provider health: a provider that fails is skipped for a cooling-off period
  instead of being retried on every message;
* a wall-clock budget for the whole chain;
* an optional ``explain()`` mode used by the family-safety digest, which is
  display-only and never touches the ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .catalog import CATEGORIES as CATALOG_CATEGORIES
from .models import Intent, Merchant, SmsEvent
from .registry import CATEGORIES

#: Bump when the prompt or schema changes; recorded on every event so a bad
#: batch can be traced back to the prompt that produced it.
PROMPT_VERSION = "2025-11-1"
SCHEMA_VERSION = 1

ALLOWED_INTENT_HINTS = {
    "expense",
    "income",
    "refund",
    "cc_bill_payment",
    "self_transfer",
    "instrument_load",
    "investment",
    "bill_due",
    "upcoming_debit",
    "otp",
    "voucher",
    "logistics",
    "govt",
    "bank_upsell",
    "promo",
    "unknown",
}

#: Categories the model may choose. Derived from the catalog so a new category
#: does not need a second list. "Uncategorized" is allowed but not rewarded.
ALLOWED_CATEGORIES = tuple(sorted({*(c.label for c in CATALOG_CATEGORIES), *CATEGORIES}))

#: The response contract, published to the model *and* enforced by the validator.
EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "merchant_clean": {"type": ["string", "null"], "maxLength": 40},
        "category": {"type": ["string", "null"], "enum": [*ALLOWED_CATEGORIES, None]},
        "intent_hint": {"type": ["string", "null"], "enum": [*sorted(ALLOWED_INTENT_HINTS), None]},
        "is_bank_upsell": {"type": "boolean"},
        "counterparty_kind": {"type": ["string", "null"], "enum": ["merchant", "person", "government", "bank", None]},
        "note": {"type": ["string", "null"], "maxLength": 60},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

SYSTEM_PROMPT = f"""You label Indian bank / UPI / delivery SMS for a personal finance organiser.

Return ONLY a JSON object; no prose, no markdown. Shape (prompt v{PROMPT_VERSION}):
{{
  "merchant_clean": "brand as people say it (Swiggy, Zepto, IndiGo) or null",
  "category": "one of the allowed categories or null",
  "intent_hint": "one of the allowed intents or null",
  "is_bank_upsell": true/false,
  "counterparty_kind": "merchant | person | government | bank | null",
  "note": "at most 60 characters, or null",
  "confidence": 0.0-1.0
}}

Hard rules — a violation makes the answer useless:
1. Never invent an amount, date, reference, card number or account number.
   If the message does not state it, it does not exist.
2. A bank message advertising a loan / EMI conversion / card upgrade has
   is_bank_upsell = true, even when it also reports a real transaction.
3. Paying a credit-card bill is "cc_bill_payment", never "expense".
4. A transfer between the user's own accounts, a wallet/FASTag top-up, an ATM
   withdrawal or an investment is NOT "expense".
5. Numbers between angle brackets (<otp>, <code>, <ref>) are redactions. Never
   guess what they were and never repeat them.
6. If you are unsure, return null for that field and a low confidence. Guessing
   is worse than an empty answer, because a human reviews the empty one.
"""

#: Few-shot examples. Kept tiny (two) so a local 7B model stays fast, but they
#: pin the two mistakes models actually make: upsell demotion and transfers.
FEW_SHOT: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "Sender: AD-HDFCBK-T\nRedacted SMS: Rs.1,450.00 debited from A/c XX4092 to VPA "
        "<vpa> (UPI Ref <ref>). Convert this spend into EasyEMI!",
        {
            "merchant_clean": None,
            "category": "Groceries & Quick Commerce",
            "intent_hint": "expense",
            "is_bank_upsell": True,
            "counterparty_kind": "merchant",
            "note": "real debit with an EMI pitch",
            "confidence": 0.88,
        },
    ),
    (
        "Sender: AD-ICICIB-S\nRedacted SMS: Payment of Rs 18,450.00 towards your Credit Card "
        "XX4092 has been received.",
        {
            "merchant_clean": None,
            "category": "Bank Fees & Charges",
            "intent_hint": "cc_bill_payment",
            "is_bank_upsell": False,
            "counterparty_kind": "bank",
            "note": "card bill paid: a transfer, not spending",
            "confidence": 0.95,
        },
    ),
)

EXPLAIN_PROMPT = """Explain this SMS to a non-technical parent in one sentence of at most 25 words.

Rules:
- Say what it is and what (if anything) they must do.
- Never say "click", "tap the link" or give a phone number.
- If it looks like a scam, say which detail gives it away.
- No numbers longer than four digits. No account or card numbers.
"""

#: Fields the merge policy may use, and the confidence each needs.
MERGE_RULES = {
    "merchant_clean": 0.5,
    "category": 0.5,
    "counterparty_kind": 0.5,
    "is_bank_upsell": 0.0,
    "intent_hint": 0.7,
    "note": 0.0,
}

_MASKED = re.compile(r"<(?:otp|code|ref|vpa|url|pan[\w]*|phone|account|card)>", re.IGNORECASE)
_MONEY_LIKE = re.compile(r"\b\d{5,}\b")


@dataclass
class Provider:
    """One OpenAI-compatible endpoint."""

    name: str
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 4.0
    enabled: bool = True
    failures: int = 0
    successes: int = 0
    last_latency_ms: int = 0
    #: Monotonic time after which this provider may be retried.
    retry_after: float = 0.0

    def propose(self, user_prompt: str) -> Optional[dict[str, Any]]:
        import httpx

        started = time.monotonic()
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 300,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = self.base_url.rstrip("/") + "/chat/completions"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        self.last_latency_ms = int((time.monotonic() - started) * 1000)
        return _parse_json(content)

    def propose_text(self, user_prompt: str) -> Optional[str]:
        """Plain-text completion, used only by the explanation mode."""
        import httpx

        payload = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": 120,
            "messages": [
                {"role": "system", "content": EXPLAIN_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = self.base_url.rstrip("/") + "/chat/completions"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        return content if isinstance(content, str) else None

    def healthy(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.monotonic()
        return self.enabled and now >= self.retry_after

    def record_failure(self, cooldown: float = 300.0) -> None:
        self.failures += 1
        self.retry_after = time.monotonic() + cooldown

    def record_success(self) -> None:
        self.successes += 1
        self.failures = 0
        self.retry_after = 0.0

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "host": re.sub(r"^(https?://[^/]+).*$", r"\1", self.base_url),
            "enabled": self.enabled,
            "healthy": self.healthy(),
            "successes": self.successes,
            "failures": self.failures,
            "last_latency_ms": self.last_latency_ms,
        }


def _parse_json(content: str) -> Optional[dict[str, Any]]:
    """LLMs sometimes wrap JSON in prose or fences. Be forgiving, not credulous."""
    if not isinstance(content, str):
        return None
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def providers_from_env(env: Optional[dict[str, str]] = None) -> list[Provider]:
    env = dict(os.environ if env is None else env)
    chain = env.get("SMSWIZ_LLM_CHAIN")
    providers: list[Provider] = []
    if chain:
        try:
            for item in json.loads(chain):
                if not item.get("base_url"):
                    continue
                api_key = item.get("api_key") or env.get(item.get("api_key_env", ""), "")
                providers.append(
                    Provider(
                        name=item.get("name") or item["base_url"],
                        base_url=item["base_url"],
                        model=item.get("model") or "local-model",
                        api_key=api_key or "",
                        timeout=float(item.get("timeout", 4.0)),
                    )
                )
        except (json.JSONDecodeError, TypeError, KeyError):
            providers = []
    base_url = env.get("SMSWIZ_LLM_BASE_URL")
    if not providers and base_url:
        providers.append(
            Provider(
                name="primary",
                base_url=base_url,
                model=env.get("SMSWIZ_LLM_MODEL", "local-model"),
                api_key=env.get("SMSWIZ_LLM_API_KEY", ""),
                timeout=float(env.get("SMSWIZ_LLM_TIMEOUT", "4.0")),
            )
        )
    return providers


# --------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------


def _category_lines() -> str:
    lines = []
    for category in CATALOG_CATEGORIES:
        lines.append(f"- {category.label} ({category.group}{', essential' if category.essential else ''})")
    return "\n".join(lines)


@dataclass
class PromptBuilder:
    """Builds the user prompt. Deterministic, and provably PII-free upstream."""

    include_few_shot: bool = True

    def build(self, event: SmsEvent) -> str:
        parts = [
            f"Sender: {event.sender.normalized or event.sender_raw}",
            f"Sender kind: {event.sender.entity_kind.value}",
            f"Redacted SMS: {event.redacted_body}",
            f"Rule engine guess: {event.intent.value} (confidence {event.confidence.score:.2f})",
            "Allowed categories:",
            _category_lines(),
            f"Allowed intents: {', '.join(sorted(ALLOWED_INTENT_HINTS))}",
            f"JSON schema: {json.dumps(EXTRACTION_SCHEMA, ensure_ascii=False)}",
        ]
        if self.include_few_shot:
            parts.append("Examples:")
            for prompt, answer in FEW_SHOT:
                parts.append(prompt)
                parts.append(json.dumps(answer, ensure_ascii=False))
        return "\n".join(parts)


# --------------------------------------------------------------------------
# Validation and merge
# --------------------------------------------------------------------------


def validate_proposal(proposal: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return ``(clean, problems)``. Anything not understood is dropped."""
    clean: dict[str, Any] = {}
    problems: list[str] = []
    if not isinstance(proposal, dict):
        return {}, ["proposal is not an object"]

    for key in proposal:
        if key.startswith("_"):
            continue
        if key not in EXTRACTION_SCHEMA["properties"]:
            problems.append(f"unknown key '{key}'")
    for key in ("merchant_clean", "category", "intent_hint", "counterparty_kind", "note"):
        if key not in proposal or proposal.get(key) is None:
            continue
        value = proposal[key]
        if not isinstance(value, str):
            problems.append(f"{key} is not a string")
            continue
        value = value.strip()
        if not value:
            continue
        if _MASKED.search(value):
            problems.append(f"{key} repeats a redaction")
            continue
        if key == "merchant_clean":
            if len(value) > 40:
                problems.append("merchant_clean too long")
                continue
            clean[key] = value
        elif key == "category":
            if value not in ALLOWED_CATEGORIES:
                problems.append(f"category '{value}' is not allowed")
                continue
            clean[key] = value
        elif key == "intent_hint":
            if value not in ALLOWED_INTENT_HINTS:
                problems.append(f"intent_hint '{value}' is not allowed")
                continue
            clean[key] = value
        elif key == "counterparty_kind":
            if value not in {"merchant", "person", "government", "bank"}:
                problems.append(f"counterparty_kind '{value}' is not allowed")
                continue
            clean[key] = value
        elif key == "note":
            clean[key] = value[:60]

    if isinstance(proposal.get("is_bank_upsell"), bool):
        clean["is_bank_upsell"] = proposal["is_bank_upsell"]
    elif proposal.get("is_bank_upsell") is not None:
        problems.append("is_bank_upsell is not a boolean")

    raw_confidence = proposal.get("confidence", 0.0)
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        problems.append("confidence is not a number")
        confidence = 0.0
    if confidence < 0 or confidence > 1:
        problems.append("confidence clamped into [0, 1]")
    clean["confidence"] = round(max(0.0, min(1.0, confidence)), 3)
    return clean, problems


@dataclass
class LLMEnricher:
    providers: list[Provider] = field(default_factory=list)
    #: Total wall-clock budget for the whole chain, seconds.
    budget_seconds: float = 6.0
    #: How long a failing provider is skipped, seconds.
    cooldown_seconds: float = 300.0
    prompt_builder: PromptBuilder = field(default_factory=PromptBuilder)

    @property
    def enabled(self) -> bool:
        return any(p.enabled for p in self.providers)

    @classmethod
    def from_env(cls, env: Optional[dict[str, str]] = None) -> "LLMEnricher":
        env = dict(os.environ if env is None else env)
        budget = float(env.get("SMSWIZ_LLM_BUDGET", "6.0") or 6.0)
        return cls(providers=providers_from_env(env), budget_seconds=budget)

    # -- prompt ------------------------------------------------------------
    def prompt_for(self, event: SmsEvent) -> str:
        return self.prompt_builder.build(event)

    def propose(self, event: SmsEvent) -> Optional[dict[str, Any]]:
        """First healthy provider with a valid JSON object wins."""
        prompt = self.prompt_for(event)
        deadline = time.monotonic() + self.budget_seconds
        for provider in self.providers:
            healthy = getattr(provider, "healthy", lambda: True)()
            if not healthy or time.monotonic() >= deadline:
                continue
            try:
                result = provider.propose(prompt)
            except Exception:
                if hasattr(provider, "record_failure"):
                    provider.record_failure(self.cooldown_seconds)  # type: ignore[attr-defined]
                continue
            if not isinstance(result, dict):
                if hasattr(provider, "record_failure"):
                    provider.record_failure(self.cooldown_seconds)  # type: ignore[attr-defined]
                continue
            if hasattr(provider, "record_success"):
                provider.record_success()  # type: ignore[attr-defined]
            result["_provider"] = provider.name
            result["_latency_ms"] = getattr(provider, "last_latency_ms", 0)
            return result
        return None

    # -- merge -------------------------------------------------------------
    def enrich(self, event: SmsEvent, merchants=None) -> SmsEvent:
        if not self.enabled:
            return event
        prompt = self.prompt_for(event)
        proposal = self.propose(event)
        if not proposal:
            event.analysis["llm"] = {"status": "unavailable", "prompt_version": PROMPT_VERSION}
            return event

        clean, problems = validate_proposal(proposal)
        confidence = clean.get("confidence", 0.0)
        applied: dict[str, Any] = {"provider": proposal.get("_provider")}
        ignored: dict[str, str] = {}

        # Merchant naming: only when the dictionary had nothing better.
        clean_name = clean.get("merchant_clean")
        if clean_name and confidence >= MERGE_RULES["merchant_clean"]:
            if event.merchant is None or event.merchant.source == "none":
                category = clean.get("category") or "Uncategorized"
                event.merchant = Merchant(
                    raw=(event.merchant.raw if event.merchant else None),
                    clean=clean_name,
                    category=category,
                    source="llm",
                    confidence=round(confidence, 3),
                )
                applied["merchant_clean"] = clean_name
                if merchants is not None and event.merchant.raw:
                    merchants.learn(event.merchant.raw, clean_name, category, source="llm")
            else:
                ignored["merchant_clean"] = f"dictionary already says {event.merchant.clean}"
        elif clean_name:
            ignored["merchant_clean"] = "below confidence floor"

        # Category may fill a gap, never relabel a confident classification.
        if clean.get("category") and event.merchant is not None and not event.merchant.category:
            event.merchant.category = clean["category"]
            applied["category"] = clean["category"]

        # Intent: only allowed to rescue an unclassified message, and the result
        # is queued for human review rather than trusted.
        hint = clean.get("intent_hint")
        if hint:
            if event.intent is Intent.UNKNOWN and confidence >= MERGE_RULES["intent_hint"]:
                applied["intent_hint"] = hint
                event.flags.append("llm_intent_hint")
                event.confidence.needs_review = True
            elif event.intent is not Intent.UNKNOWN:
                ignored["intent_hint"] = f"rules already decided {event.intent.value}"

        if clean.get("is_bank_upsell") is True and "embedded_upsell" not in event.flags:
            event.flags.append("llm_spotted_upsell")
            applied["is_bank_upsell"] = True

        if clean.get("counterparty_kind"):
            event.analysis.setdefault("counterparty", clean["counterparty_kind"])
            if clean["counterparty_kind"] == "person":
                event.analysis["p2p"] = True
            applied["counterparty_kind"] = clean["counterparty_kind"]

        if clean.get("note"):
            applied["note"] = clean["note"]

        event.analysis["llm"] = {
            "status": "ok",
            **applied,
            "ignored": ignored,
            "confidence": confidence,
            "provider": proposal.get("_provider"),
            "latency_ms": proposal.get("_latency_ms", 0),
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "problems": problems,
            "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
        }
        event.raw_ai = {k: v for k, v in proposal.items() if not k.startswith("_")}
        return event

    # -- display-only explanation -----------------------------------------
    def explain(self, event: SmsEvent) -> Optional[str]:
        """One plain-language sentence for the family-safety digest.

        Display only: the result never changes intent, ledger or routing, and it
        is rejected if it leaks a redaction or a long number.
        """
        if not self.enabled:
            return None
        prompt = (
            f"Sender: {event.sender.normalized or event.sender_raw}\n"
            f"Redacted SMS: {event.redacted_body}\n"
            f"Our classification: {event.intent.value}\n"
            f"Risk flags: {', '.join(event.flags) or 'none'}"
        )
        for provider in self.providers:
            if not getattr(provider, "healthy", lambda: True)():
                continue
            try:
                result = provider.propose_text(prompt) if hasattr(provider, "propose_text") else None
            except Exception:
                result = None
            if not result:
                continue
            text = result if isinstance(result, str) else str(result.get("text", ""))
            text = re.sub(r"\s+", " ", text).strip()
            if not text or len(text) > 240:
                continue
            if _MASKED.search(text) or _MONEY_LIKE.search(text):
                continue
            return text
        return None

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "budget_seconds": self.budget_seconds,
            "providers": [p.describe() for p in self.providers],
        }
