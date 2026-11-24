---
name: 02-add-a-merchant-or-entity
title: Add a bank, fintech, brand, government body, or merchant
summary: Extend registry.py (entities, DLT prefixes, domains, aliases) or the merchant dictionary — never fuzzy-match
---

# Skill 02 — Add a merchant or entity

## When to use

- A new bank / fintech / NBFC sends SMS and is being treated as `unknown`.
- A brand (`Zomato`, `Zepto`, `IRCTC`) appears in a message but resolves to nothing.
- A government body (RTO, municipal corporation, e-challan portal) appears.
- A merchant keeps landing in `needs_review` because the dictionary missed it.

## Two different things

| | Entity (sender) | Merchant (payee) |
| --- | --- | --- |
| Answers | *who sent this* | *where the money went* |
| Lives in | `sms_wiz/registry.py` entities | `SEED_MERCHANTS` + `merchant_dictionary` |
| Matched by | DLT prefix, domain, alias (exact, upper-case) | longest dictionary key contained in the text |
| Wrong match costs | a wrong trust decision (phishing missed) | a wrong category (cosmetic) |

Never mix them: `HDFC` the sender and `HDFC Life` the merchant are different rows.

## Procedure — new entity

1. Open `sms_wiz/registry.py`. Copy the closest existing entry:
   - `_bank(code, name, domains, dlt_prefixes, aliases)`
   - `_brand(...)`, `_govt(...)`, `_fintech(...)`
2. Add:
   - **DLT prefixes** as they really appear: `HDFCBK`, `ICICIB`, `SBIN` (no dashes).
   - **Domains** you are confident about — a wrong domain becomes a trust signal.
     Do not guess `hdfc-loans.co.in`; if unsure, leave it out.
   - **Aliases** exactly as receipts print them (`KIRANAKART` → Zepto, `RTO` → VAHAN).
3. Aliases are **exact match on normalised upper-case**. "RTO Parivahan" needed an
   explicit `RTO` alias; fuzzy matching was rejected because it mis-fires.
4. If categories matter, add to `CATEGORIES`; if routing differs, `ROUTING`.
5. Test: a realistic SMS from that sender in `tests/test_classify.py`, asserting
   `event.institution.name`, `kind`, and that the intent is unchanged.

## Procedure — new merchant

1. If it is a common Indian merchant, add to `SEED_MERCHANTS`:
   `("SWIGGY LIMITED", "Swiggy", "Food & Dining")` — the **raw key** is what
   appears in SMS (upper-case, as printed), the clean name is what users see.
2. If it is personal or niche, do **not** seed it: let it land in
   `needs_review`, and let the user (or the LLM, with `source=llm`) add it to
   `merchant_dictionary`.
3. Precedence is fixed: **user > seed > llm > learned**. Never invert it.
4. Keep the longest-key-wins rule; a shorter alias must not shadow a longer one
   (that is why `lookup()` prefers the longest match).

## Adding a voucher / delivery / travel source

Those are not registry entries: they are handled by rule patterns in
`classify.py` (`voucher_issued`, `logistics`) plus extraction in `extract.py`
(PNR, order id, expiry). See skill 01.

## Pitfalls

- **Do not invent domains** to make a test pass; a wrong domain weakens real
  phishing detection.
- **Do not add a merchant for every unknown string** — that is what the review
  queue is for. Guessing corrupts spend categories silently.
- A `-S` suffix means service/promotional. Same bank, different intent: HDFC
  `-T` debit vs `-S` loan offer. Test both.
- Shortcodes (`57575`) are shared by multiple senders; resolve by content, not
  by shortcode alone.

## Verify

```bash
./.venv/bin/python -m sms_wiz.cli ingest -s '<sender>' -b '<body>'
make verify
```

Check that `institution` is right, `merchant.clean` is what a user expects, and
`merchant.source` says where it came from.

## Done

- [ ] Entity or merchant entry added with a real-world shape.
- [ ] Test with a realistic SMS, including a negative case (promo from the same bank).
- [ ] No fuzzy matching introduced.
- [ ] `make verify` green.
