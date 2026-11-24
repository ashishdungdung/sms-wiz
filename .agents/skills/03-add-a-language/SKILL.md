---
name: 03-add-a-language
title: Add or extend an Indian script
summary: Normalise digits, currency, and decision-bearing phrases so a new script produces the same canonical event
---

# Skill 03 — Add a language

## When to use

- An SMS in a new script (Odia, Assamese, Konkani…) is not parsed.
- A money sentence in Tamil/Telugu/Kannada is misread or lands in `needs_review`
  for the wrong reason.
- A regional numeral or rupee marker is being missed.

Read `LANGUAGE.md` first: it defines what "supported" means and lists current
coverage honestly.

## The model

We do **not** translate. `sms_wiz/normalize.py` canonicalises before the English
rule chain runs:

```
raw (any script) ─► digits→ASCII ─► currency→"Rs " ─► phrases→English ─► existing rules
                                                          │
                                    records scripts[], digit_scripts[], translations
```

Postpositions are the one exception: Indian grammar puts "to" *after* the name
(`SWIGGY को डेबिट`), so `extract.py` carries a language-aware merchant pattern.

## Procedure

1. **Field sample first.** Get 3–5 real messages in that script, ideally from
   different banks. Note the exact verb forms — they vary (डेबिट / निकासी /
   काटे / भुगतान).

2. **Digits.** Check `INDIC_DIGIT_BLOCKS` in `normalize.py`; all nine major
   blocks are already present. A new script needs its zero code point (the nine
   digits are contiguous), and its block goes into `SCRIPT_RANGES` too.

3. **Currency.** Add the marker to `_CURRENCY` — **longest alternative first**,
   or `रुपये` is partially eaten by `रु` (this was a real bug). Test that the
   whole word is consumed.

4. **Phrases.** Add `(script_phrase, english)` pairs to `PHRASE_MAP`, longest
   first. Priorities, in order:
   - money verbs: debit, credit, withdrawal, refund, reversal
   - `balance` / `available balance`
   - `OTP`, `mandate`, `beneficiary`, `Aadhaar`, `challan`, `fine`
   - `account`

   Skip courtesy words; they cost matches without changing a decision.

5. **Postposition merchant (if applicable).** Extend the pattern in
   `extract.py` `_MERCHANT_PATTERNS` (the one with `को|க்கு|కు|ಗೆ`). Add the
   script's dative marker and its debit verb.

6. **Tests** in `tests/test_normalize.py`:
   - digits: full 0–9 round trip, asserted per script;
   - currency: `"<marker> 1,200"` → `"Rs 1,200"`;
   - one realistic money sentence: intent, amount, balance, last4;
   - the script appears in `detect_scripts()` and in
     `event.analysis["scripts"]`.

7. **Fixture** in `contracts/fixtures/` if the sentence is one users will hit
   often (see `09_sbi_hindi_devanagari_debit.json`). It proves the whole
   pipeline — including that a Devanagari RRN deduplicates against an ASCII one.

8. **Update `LANGUAGE.md`** status table to the level the tests actually prove.
   Digits-only is a valid, honest status.

## Pitfalls

- **Never machine-translate a bank's wording.** Real SMS use idiosyncratic
  forms; a translation table for "amount debited" will miss "राशि काटी गई".
- Do not map a phrase to another phrase that is itself mapped (chains cause
  double translation counts and unpredictable output).
- Do not break ASCII input: `normalize_text("Rs 500 Dr").text` must be unchanged.
- Keep normalisation idempotent: running it twice must not change the text.
- Do not add the language to the UI before the data path is tested; a Hindi
  label over an English-only parser is a lie.

## Verify

```bash
./.venv/bin/python -c "from sms_wiz.normalize import normalize_text; print(normalize_text('<sample>'))"
./.venv/bin/python -m sms_wiz.cli ingest -s 'VM-SBIN-T' -b '<sample>'
make verify
```

## Done

- [ ] Digits + currency + at least one money sentence tested.
- [ ] `analysis["scripts"]` reports the script.
- [ ] Fixture added for a high-frequency money message.
- [ ] `LANGUAGE.md` updated to the proven level.
- [ ] `make verify` green.
