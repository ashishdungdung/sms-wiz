"""Optional LLM enrichment — off by default, redacted, and never in control.

Rules for this module:

1. **Nothing goes out unless it is redacted.** OTPs, voucher codes/PINs, PAN,
   long numbers, phone numbers, e-mail addresses, VPA handles and URLs are
   masked before a request is built.
2. **The LLM cannot overrule a confident rule.** It may name an unresolved
   merchant, refine a category, or give an opinion on a message the rules
   could not classify (which is then queued for review anyway).
3. **Every response is untrusted.** Strict JSON, allow-listed keys, category
   allow-list, and any failure leaves the deterministic result untouched.
4. **It is not on the filter path.** Apple's network filter has a tight
   deadline and is not a place to wait for a 14B model.

Providers are OpenAI-compatible (LM Studio, llama.cpp server, vLLM, Groq,
OpenRouter, Together, OpenAI). The chain is tried in order; the first valid
answer wins.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .models import Intent, Merchant, SmsEvent
from .registry import CATEGORIES

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

SYSTEM_PROMPT = """You label Indian bank / UPI / delivery SMS for a personal finance organiser.

Return ONLY a JSON object with these keys (all optional):
{
  "merchant_clean": "brand name, e.g. Swiggy, Zepto, IndiGo, or null",
  "category": "one of the allowed categories or null",
  "intent_hint": "one of the allowed intents or null",
  "is_bank_upsell": true/false,
  "confidence": 0.0-1.0
}

Hard rules:
- Never invent an amount, date or reference that is not in the message.
- A bank message that advertises a loan, EMI conversion or card upgrade has
  is_bank_upsell = true, even if it also reports a real transaction.
- Paying a credit-card bill is "cc_bill_payment", never a new "expense".
- If unsure, return nulls and a low confidence. Do not guess.
"""


@dataclass
class Provider:
    """One OpenAI-compatible endpoint."""

    name: str
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 4.0
    enabled: bool = True

    def propose(self, user_prompt: str) -> Optional[dict[str, Any]]:
        import httpx

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
        return _parse_json(content)


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


@dataclass
class LLMEnricher:
    providers: list[Provider] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return any(p.enabled for p in self.providers)

    @classmethod
    def from_env(cls, env: Optional[dict[str, str]] = None) -> "LLMEnricher":
        return cls(providers_from_env(env))

    # -- prompt ------------------------------------------------------------
    @staticmethod
    def _prompt(event: SmsEvent) -> str:
        return (
            f"Sender: {event.sender.normalized or event.sender_raw}\n"
            f"Sender kind: {event.sender.entity_kind.value}\n"
            f"Redacted SMS: {event.redacted_body}\n"
            f"Rule engine guess: {event.intent.value} (confidence {event.confidence.score:.2f})\n"
            f"Allowed categories: {', '.join(CATEGORIES)}\n"
            f"Allowed intents: {', '.join(sorted(ALLOWED_INTENT_HINTS))}"
        )

    def propose(self, event: SmsEvent) -> Optional[dict[str, Any]]:
        prompt = self._prompt(event)
        for provider in self.providers:
            if not provider.enabled:
                continue
            try:
                result = provider.propose(prompt)
            except Exception:  # network, auth, bad JSON from a provider: try next
                continue
            if result:
                result["_provider"] = provider.name
                return result
        return None

    # -- merge -------------------------------------------------------------
    def enrich(self, event: SmsEvent, merchants=None) -> SmsEvent:
        if not self.enabled:
            return event
        proposal = self.propose(event)
        if not proposal:
            event.analysis["llm"] = {"status": "unavailable"}
            return event

        applied: dict[str, Any] = {"provider": proposal.get("_provider")}
        confidence = proposal.get("confidence")
        try:
            confidence_value = float(confidence) if confidence is not None else 0.0
        except (TypeError, ValueError):
            confidence_value = 0.0

        category = proposal.get("category")
        clean = proposal.get("merchant_clean")

        # Merchant naming: only when the dictionary had nothing better.
        if isinstance(clean, str) and clean.strip() and confidence_value >= 0.5:
            if event.merchant is None or event.merchant.source == "none":
                event.merchant = Merchant(
                    raw=(event.merchant.raw if event.merchant else None),
                    clean=clean.strip(),
                    category=category if category in CATEGORIES else "Uncategorized",
                    source="llm",
                    confidence=round(confidence_value, 3),
                )
                applied["merchant_clean"] = clean.strip()
                if merchants is not None and event.merchant.raw:
                    merchants.learn(event.merchant.raw, clean.strip(), event.merchant.category or "Uncategorized", source="llm")

        # Intent: only allowed to rescue an unclassified message, and the result
        # is queued for human review rather than trusted.
        hint = proposal.get("intent_hint")
        if (
            event.intent is Intent.UNKNOWN
            and isinstance(hint, str)
            and hint in ALLOWED_INTENT_HINTS
            and confidence_value >= 0.7
        ):
            applied["intent_hint"] = hint
            event.flags.append("llm_intent_hint")
            event.confidence.needs_review = True

        if proposal.get("is_bank_upsell") is True and "embedded_upsell" not in event.flags:
            event.flags.append("llm_spotted_upsell")

        event.analysis["llm"] = {"status": "ok", **applied, "confidence": confidence_value}
        event.raw_ai = proposal
        return event
