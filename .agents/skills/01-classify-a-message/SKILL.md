---
name: 01-classify-a-message
title: Fix a wrong classification, folder, or flag
summary: Reproduce a misclassified SMS as a test, then change the ordered rule chain in classify.py
---

# Skill 01 — Fix a wrong classification

## When to use

- A real SMS lands in the wrong intent (`expense` vs `cc_bill_payment`, `logistics`
  vs `commercial_promo`, wrong `bank_upsell` decision).
- The right intent but the wrong Apple folder / sub-action.
- A phishing message is allowed, or a legitimate bank promo is quarantined.
- The flags are wrong (`embedded_upsell`, `suspicious_link`, `bank_marketing`).

## Inputs

The raw SMS body **exactly as received** (copy-paste, never retyped), the sender
header, and what you expected instead. If the message contains an OTP, voucher
code, or account number, keep it — the tests need real shapes; the pipeline
redacts before anything is stored or shown.

## Procedure

1. **Reproduce with the CLI first** (no code change yet):

   ```bash
   ./.venv/bin/python -m sms_wiz.cli ingest -s 'AD-HDFCBK-T' -b '<body>'
   ```

   Read `intent`, `apple`, `ledger`, and `evidence`. The evidence line tells you
   which rule fired. If evidence is empty or generic, that is the first bug.

2. **Find the rule** in `sms_wiz/classify.py`. The chain is ordered and the first
   match wins; grep for the intent's branch (`_decide`, `_MANDATE`, `_UPSELL_PATTERNS`,
   `_GOVT`, `ROUTING`).

3. **Decide which layer is wrong** before touching code:

   | Symptom | Layer |
   | --- | --- |
   | Amount / merchant / reference missing or wrong | `extract.py` (see skill 02 for merchants) |
   | Sender judged bank when it is a personal number | `registry.py` |
   | Text did not parse because of a script | `normalize.py` (see skill 03) |
   | Right facts, wrong intent | `classify.py` |
   | Right intent, wrong folder | `ROUTING` in `registry.py` |

4. **Write the failing test** in `tests/test_classify.py` (unit level) — or a
   fixture in `contracts/fixtures/` if the case matters end-to-end (dedup,
   ledger, alerts). Fixture first is better for money behaviour.

5. **Change the smallest thing.** Prefer tightening a regex over adding a rule;
   prefer adding a rule over reordering the chain. If you reorder, run the whole
   suite — many tests depend on order.

6. **Add evidence text** to the new branch. It must name the signal, not the
   intent: `"debit verb 'debited' with amount and VPA"` — not `"matched expense rule"`.

7. **Guard against collateral damage**: re-run the demo (`make demo`) and check
   the totals did not move. If they did, you changed more than you intended.

## Pitfalls

- **Do not quarantine on one weakness.** The `+0.30` brand+unregistered+link
  combination exists because single signals junk legitimate bank mail.
- An upsell *inside* a real debit must not create a second event; it only adds
  `embedded_upsell`.
- NACH/AutoPay/EMI notices are `upcoming_debit`, never a debit that counts.
- A 10-digit sender claiming a bank is `personal_mobile`, so the brand claim is
  evidence of impersonation, not identity.
- Changing `ROUTING` can produce an undeclared sub-action; check
  `effective_subaction()` and the ≤5 declared limit (`docs/ios-constraints.md`).

## Verify

```bash
make verify
make demo     # totals must be unchanged unless the case is a money case
```

## Done

- [ ] Test fails before, passes after.
- [ ] Evidence line names the signal.
- [ ] Demo totals explained (changed on purpose, or unchanged).
- [ ] `contracts/` regenerated if any enum changed.
