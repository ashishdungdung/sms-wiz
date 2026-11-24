# LANGUAGE.md — vocabulary, and how multilingual actually works

Two things live here: the **domain glossary** (so an agent writing copy or a rule
uses Indian finance terms correctly) and the **multilingual policy** (so "we
support Tamil" means something specific and testable).

---

## Part 1 — Domain glossary

| Term | Meaning | Why it matters here |
| --- | --- | --- |
| **DLT header** | TRAI-mandated sender ID for commercial SMS, e.g. `AD-HDFCBK-T` | `-T` transactional, `-S` service/promotional, `-P` promotional. The prefix maps to a bank/brand; the suffix predicts intent. A body claiming "HDFC" from a 10-digit number is an impersonation signal. |
| **Shortcode** | 5–6 digit sender (`57575`) | Trusted-ish carrier/bank channel; usually transactional. |
| **VPA** | Virtual Payment Address, `name@psp` (`kiranakart@axisbank`) | Merchant/UPI identification without a card number. |
| **RRN / UPI Ref** | 12-digit Retrieval Reference Number | The dedup key. The same payment reported by HDFC and PhonePe shares it. |
| **UPI Lite** | Small-value offline UPI wallet | A *load* is `instrument_load` (not spend); the later payment is the spend. |
| **FASTag** | RFID toll wallet | Recharge is a load; the toll debit is `fastag_toll` (spend). |
| **NACH / e-mandate** | Bank pre-debit authorisation (EMI, SIP, insurance, subscriptions) | The SMS is a *notice*, not a debit. Due date + amount make it an upcoming payment. |
| **UPI AutoPay** | UPI-native recurring mandate | Same treatment as NACH. |
| **ASBA** | Application Supported by Blocked Amount (IPO) | Money is blocked, not spent. Allotment refunds the block. `investment`, excluded from spend. |
| **Demat / CDSL / NSDL** | Share custody accounts | Source of IPO allotment, dividend, buyback messages. |
| **PAN / Aadhaar** | Tax and identity numbers | Never stored in clear; redacted before any model sees text. |
| **CIBIL / credit score** | Credit bureau score | Often arrives as a paid "offer" — a commercial promo, not a transaction. |
| **E-challan** | Traffic fine (Parivahan / state RTO) | Government notice with a vehicle number and a due date. Common phishing lure. |
| **RTO** | Regional Transport Office | Alias of the `VAHAN` government entity in `registry.py`. |
| **IRCTC PNR** | 10-digit rail booking reference | Drives `logistics` + `transactionalOrders`; chart-prepared means travel day. |
| **TRAI 1909** | Unsolicited-commercial-communication complaint line | We draft; the user sends. No promise of disconnection. |
| **1930** | National cyber-fraud helpline | Money-loss path, paired with the bank's fraud channel. |
| **EMI conversion** | "Convert this spend into EasyEMI" | An *upsell inside a real debit*: keep the expense, add `embedded_upsell`. |
| **Forex markup** | Card fee on foreign currency spend | Shown as a separate fee line; never merged into the purchase amount. |

## Part 2 — Multilingual policy

### Why normalise instead of translating

Indian SMS arrive in 22 scheduled languages, often **code-mixed**, often with
regional numerals:

```
आपके खाते XX1094 से रु २,५००.०० ... (UPI Ref ५१२३४५६७८९०१)
உங்கள் கணக்கு XX3312 இல் இருப்பு ரூ. 1,204.50 உள்ளது
మీ ఖాతా XX7788 బ్యాలెన్స్ రూ. 8,900.00
```

We do **not** run a translation model. We deterministically canonicalise the
text before the English rule chain sees it (`sms_wiz/normalize.py`):

1. **Regional numerals → ASCII.** All nine digit blocks (Devanagari, Bengali,
   Gurmukhi, Gujarati, Odia, Tamil, Telugu, Kannada, Malayalam). This is not
   cosmetic: the 12-digit RRN in Devanagari must equal the ASCII one PhonePe
   sends, or the same payment is counted twice.
2. **Currency markers → `Rs `.** `₹`, `रु`, `रुपये`, `ரூ`, `రూ`, `ರೂ`, `രൂ`,
   `રૂ`, `ରୁ`, `ਰੁ`, `৳`, `টাকা`, `INR`. Longest alternative first, so `रुपये`
   is not partially eaten by `रु`.
3. **High-value phrases → English.** 50 mappings for money verbs, balance,
   OTP, mandate, beneficiary, Aadhaar, challan, fine — the vocabulary that
   decides intent (`PHRASE_MAP`).
4. **Record what happened.** `NormalizedSMS.scripts`, `digit_scripts`,
   `translations`, and `event.analysis["scripts"|"multilingual"|"translations"]`
   make the work visible in the data, not just in a promise.
5. **Language-specific extraction.** Where Indian grammar puts the word *after*
   the name ("SWIGGY को डेबिट", "SWIGGY க்கு பற்று"), `extract.py` has a
   postposition pattern. This is the only place language grammar leaks past
   normalisation, and it is deliberate.

Internal enum values stay **ASCII English identifiers forever**. The UI may be
Hindi; the database is not.

### What "supported" means

A script is **supported** only when all four are true:

| Level | Requirement |
| --- | --- |
| Digits | every numeral maps to ASCII, tested per script |
| Currency | its rupee marker (if different) normalises to `Rs` |
| Money sentence | a realistic debit/credit/balance SMS produces the right intent, amount, balance, last4 |
| Tests | at least one test in `tests/test_normalize.py` and, for money, a fixture in `contracts/fixtures/` |

Current status — honest:

| Script | Digits | Currency | Money phrases | Verdict |
| --- | --- | --- | --- | --- |
| Devanagari (Hindi/Marathi) | ✅ | ✅ | ~25 phrases, debit/credit/balance/OTP/challan | **Supported, tested end to end** |
| Tamil | ✅ | ✅ | debit, credit, balance | **Supported (core), tested** |
| Telugu | ✅ | ✅ | debit, credit, balance, account | **Supported (core), tested** |
| Kannada | ✅ | ✅ | debit, credit, balance, account | **Supported (core), digit test only** |
| Malayalam | ✅ | ✅ | debit, credit, balance | **Supported (core), digit test only** |
| Gujarati | ✅ | ✅ | debit, credit, balance, account | **Supported (core), digit test only** |
| Bengali | ✅ | ✅ | debit, credit, balance, account | **Supported (core), digit test only** |
| Gurmukhi (Punjabi) | ✅ | — (uses `₹`) | debit, credit, balance | **Partial** |
| Odia | ✅ | — (uses `₹`) | none yet | **Digits only — not usable for money yet** |

"Supported (core)" means: the sentence parses, the digits and currency are
right; the phrase list is shorter than Hindi's, so odd phrasings will fall to
`needs_review` rather than be guessed. That is intended.

### Adding a language (the whole checklist)

1. Add the digit block to `INDIC_DIGIT_BLOCKS` (if not already there).
2. Add the rupee/taka marker to `_CURRENCY`, **longest alternative first**.
3. Add phrases to `PHRASE_MAP`, longest first. Prefer the words that change a
   decision (debit, credit, balance, OTP, mandate, refund, challan) over
   courtesy words.
4. If the grammar puts the merchant after a postposition, extend the
   postposition pattern in `extract.py`.
5. Add a test per script in `tests/test_normalize.py` (digits, currency, one
   realistic money sentence) and, if it is a money sentence users will see
   often, a fixture in `contracts/fixtures/`.
6. Update the status table above — including when a script is only
   digits-only. Do not promote a script past what the tests prove.

### Translation and the model layer

- Message bodies are **never** sent to a translation service.
- If an LLM is configured, it receives `redacted_body` in the original script and
  is asked for a JSON *classification*, not a translation. The reply is schema
  validated and cannot overrule a confident rule.
- The app may show the user's own script in the UI; classification labels are
  localised separately from the data.

### Copy style

- Rupees with Indian grouping: `₹1,45,000`, not `₹145,000.00`.
- Short, factual sentences. No exclamation marks in risk copy.
- Never say "we detected fraud"; say "this message asks you to install an app
  from a link — banks don't do that".
- Hindi UI copy is a translation of the canonical English string, never a
  separate rule.
