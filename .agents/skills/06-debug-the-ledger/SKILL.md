---
name: 06-debug-the-ledger
title: Debug a total, a refund, or a suspected double count
summary: Reconstruct one payment's path through dedup, treatment, and the summary query — never patch the total
---

# Skill 06 — Debug the ledger

## When to use

- "My spend is too high / too low."
- A credit-card bill payment or self-transfer appears in spend.
- A refund was counted as income, or never matched.
- The same payment appears twice from two senders (bank + PhonePe).
- A balance or shortfall alert looks wrong.

## The one rule

**Never adjust a total to match a bank statement.** Every wrong total has a
wrong *event* (or a wrong *query*) behind it. Find the event, fix the cause, and
let the number follow.

## Where the number comes from

```
event.amount.value
   ├── ledger.treatment      (from Ledger.for_intent(intent))
   ├── ledger.counts_toward_spend   → only treatment == expense
   ├── dedup.status          → suppressed_duplicate never counts
   └── store.summarise_month → GROUP BY category over non-suppressed rows
```

If a rupee is in the wrong bucket, it is one of those four.

## Procedure

1. **Replay the month, read the events.**

   ```bash
   ./.venv/bin/python -m sms_wiz.cli summary --month 2025-02
   sqlite3 sms_wiz.db "select received_at, sender_raw, intent, ledger_treatment, dedup_status, amount_value, merchant_clean from events order by received_at"
   ```

2. **Classify the discrepancy** using this table:

   | Symptom | Likely cause | Check |
   | --- | --- | --- |
   | CC bill payment in spend | intent not `cc_bill_payment` | `classify.py` bill rule; the word "payment … towards your credit card" |
   | Self-transfer in spend | no own-account detection | `self_transfer` rule; look for the user's own last4 on both sides |
   | Same payment twice | RRN missing or normalisation broke it | `analysis.upi_rrn` on both rows; is one Devanagari? |
   | Refund counted as income | refund matched to a wrong claim | `refunds` table; `store.match_refund` |
   | FASTag/wallet load in spend | intent `expense` instead of `instrument_load` | "recharge", "top up", "load" verbs |
   | Month totals shift after a replay | date roll-forward | date resolution uses a 30-day window (60 broke a test) |

3. **Dedup specifically.** Two rows are the same payment only when:
   - the same reference/RRN appears → `suppressed_duplicate` (second row); or
   - same amount + same day + same last4 from a **different sender** →
     `possible_duplicate` for review, still counted.

   Amount alone never merges. If both rows count, check `store.find_by_reference`
   first, then `find_possible_duplicate`.

4. **Write the failing test.** Money bugs must arrive with a fixture under
   `contracts/fixtures/` using `prior` to seed the first message. That is the
   only way to keep the fix from regressing when someone reorders a rule.

5. **Fix the cause**, not the symptom: intent, treatment, reference extraction,
   or the summary query. If the fix is in `Ledger`, it affects every intent —
   run the whole suite.

6. **Re-run the demo** and compare the documented benchmark:

   ```
   spend ₹1,515 · income ₹85,000 · refunds ₹1,850 matched · excluded ₹18,450
   duplicate suppressed on RRN 505612345678 · 0 needs-review
   ```

   If these move, either the fix is broader than intended or the demo message
   set changed (which needs a note in the PR).

## Balance and shortfall checks

- Balances are per account last4, never summed across accounts.
- A shortfall alert requires a *known* balance for that account; without one the
  alert is `debit_balance_unknown` (informational), never a guess.
- `upcoming_debit` never writes to the ledger; it only schedules and warns.

## Pitfalls

- Do not "fix" a total by editing `summarise_month` filters; the row is still
  wrong and will resurface on the next export.
- Refunds never dedup — two refunds of the same amount are a real pattern
  (partial refunds).
- Changing the roll-forward window is a behavioural change with tests attached;
  do not tune it to make one message parse.
- `counts_toward_spend` is derived. Never set it by hand.

## Done

- [ ] Fixture reproduces the exact double count / misclassification.
- [ ] Fix is in the cause layer, with evidence text.
- [ ] Demo benchmark either unchanged or intentionally updated and documented.
- [ ] `make verify` green.
