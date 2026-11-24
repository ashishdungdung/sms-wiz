# Bank message fixtures

Two kinds of fixture live here, and they are not the same thing.

## `public_messages.json` — real shapes, third-party published

Six messages published by other people: an open-source SMS parser's sample, a
security vendor's explainer, an expense-tracker's blog. Every account number,
name and VPA in them was masked before publication, and is masked again here.
Each entry records its `source` URL.

These are replayed by `tests/test_bank_fixtures.py` through the whole classifier,
so a template change is checked against text a bank actually sent, not text we
invented to match our own regex.

Covered today: HDFC UPI debit with `Avl Bal`, HDFC "Sent Rs … From HDFC Bank
A/C *5678 to <name>", HDFC UPI credit, short "Your a/c XX4821 debited …",
`INR 1,250.00 debited …` (INR prefix), and a credit with an `Avl bal:` tail.

## Your own messages — the only fixtures that prove it works on your bank

Nobody else can supply these. Do not paste a raw SMS into an issue or a PR: the
body contains your account tail, your payee names and sometimes an OTP.

```bash
# 1. see what the pipeline does, without storing anything
./.venv/bin/python -m sms_wiz.cli ingest -s "AD-HDFCBK-T" -b "<paste here>"

# 2. if the decision is wrong, redact and add a case by hand
#    - replace the account/card tail with the same masked form the bank used
#    - replace names, VPAs and merchant references
#    - KEEP the sentence structure, the punctuation and the spacing
#    - keep the UPI reference out entirely (it is a linkable identifier)
```

Then add an entry to `public_messages.json` with
`"source": "user-contributed (redacted)"` and the expected values. If the
expectation feels wrong — e.g. the bank's own date lands the money in the wrong
month — fix the pipeline first and update the fixture second.

## What is *not* here

- No message bodies from a real person's phone, in Git, ever.
- No OTPs. A fixture containing a valid OTP teaches nothing and leaks a code.
- No full account numbers, card numbers, CVVs, PINs, or customer IDs.
