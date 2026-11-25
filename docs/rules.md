# The filter rule syntax (`sms-wiz.filter/v1`)

The message filter extension gets **one message, a few milliseconds, and no
guaranteed network**. The Python chain in `classify.py` is right for the app, but
the extension needs something it can ship as data and evaluate without a runtime.

So folder decisions can be authored as rules:

```
rules/filter_rules.json        ← humans (and issuers) edit this
        │  sms_wiz/rules.py validates + compiles
        ▼
contracts/filter_rules.json    ← deterministic artifact, ships in the app
        │
        ├── Python: fast pre-pass for POST /api/v1/filter
        └── Swift:  FilterRules.swift, inside the extension (offline)
                          │
        tests/fixtures/filter_rules/cases.json  ← both replay this
```

`FilterRules.swift` is **written, not compiled** here (no Swift toolchain); the
fixtures are what make it reviewable. `scripts/gen_rule_fixtures.py` generates a
case per rule plus four hand-written edges, the Python suite asserts those
answers, and `FilterRulesParityTests` asserts the same answers from Swift. A rule
change without regenerating fails `make verify`.

The Python pre-pass is an optimisation. The full chain still runs on ingest and
stays authoritative; a rule can pick a folder, never a ledger entry.

---

## A rule

```jsonc
{
  "id": "bank.emi_offer",                       // lowercase, dotted, unique
  "when": {
    "sender_prefixes": ["HDFCBK", "ICICIB"],    // optional: DLT entity codes
    "sender_kinds": ["bank", "fintech"],        // optional: registry judgement
    "any":  ["easy emi", "pre-approved"],       // at least one present
    "all":  ["card"],                           // every one present
    "none": ["debited", "spent"],               // none present
    "has_link": true,                           // optional boolean
    "min_amount": 1000,                         // optional bounds, in rupees
    "max_amount": 100000
  },
  "then": {
    "action": "promotion",                      // allow|junk|promotion|transaction
    "sub_action": "promotionalOffers",          // must be declared by the app
    "confidence": 0.85,
    "reason": "lending pitch with no money movement"
  }
}
```

Matching is **case-insensitive substring** on the normalised body — the same text
`classify.py` sees, with Devanagari/Tamil/Telugu digits already folded to ASCII.
That matters: a rule written against `Rs.1,450` also matches `रु. १,४५०`.

### Field reference

| Field | Type | Meaning |
| --- | --- | --- |
| `sender_prefixes` | list | Compare against `templates.sender_prefix()` — `AD-HDFCBK-T` → `HDFCBK` |
| `sender_kinds` | list | `bank`, `fintech`, `brand`, `govt`, `personal_mobile`, `shortcode`, `unknown` |
| `any` | list | At least one term present (empty list = no constraint) |
| `all` | list | Every term present |
| `none` | list | If any term present, the rule cannot match |
| `has_link` | bool | Whether the body contains `http(s)://` or `www.` |
| `min_amount` / `max_amount` | number | Only applied when the caller knows the amount |
| `confidence` | 0–1 | Recorded in the verdict; **never** used to override money logic |

Priority is **file order**: first match wins. Put the narrow, dangerous rules
above the broad marketing ones.

---

## What the validator refuses to ship

A rule file that would produce a wrong or impossible decision fails the build
(`./.venv/bin/python -m sms_wiz.cli rules-check`, and `make verify`). Current rejections:

| Rejection | Why |
| --- | --- |
| Unknown `action` or `sub_action` | Apple's enum is fixed; an undeclared sub-action is silently dropped by iOS |
| A sub-action that belongs to another action | `promotion` + `transactionalOrders` describes nothing iOS can do |
| `junk` paired with any sub-action | Junk has no sub-action; it is a folder, not a category |
| `junk` without a danger signal | Junking on the word "offer" teaches users to ignore the folder |
| No conditions at all | A rule that matches everything hides every other rule |
| Duplicate `id`, uppercase `id` | Rules are referenced by id in logs and tests |
| `confidence` outside (0, 1] | A number that cannot be compared |
| Unknown `syntax` version | Old files must fail loudly, not be reinterpreted |

The "danger signal" list lives in `rules.py` (`DANGER_TERMS`): `apk`,
`install app`, `kyc`, `winner`, `won`, `lottery`, `prize`, `blocked`,
`suspended`, `verify`, `refund request`, `account will be`, `otp`, `password`,
`pin`, `cvv`, `click here`, plus the India-specific scripts the corpus turned up:
`digital arrest`, `cbi case`, `police case`, `money laundering`, `narcotics`,
`illegal items`, `customs duty`, `guaranteed return`, `sure shot`,
`registration fee`, `stock tips`.

A plain debit or credit alert is deliberately **not** matched by any rule: the
answer is `allow`, iOS keeps it in the inbox, and the app classifies it on
ingest. The offline pass only rescues messages that would otherwise be buried —
due dates, pre-debits, OTPs, parcel codes, trade confirmations. A message with
money in it never becomes `promotion` just because an offer rides along.

---

## Writing a rule that survives real traffic

1. **Start from a real message**, redacted, and add it to
   `tests/fixtures/bank_templates/`. If you cannot find one, you do not yet know
   the shape.
2. **Prefer the sender, not the prose.** Marketing copy changes monthly; DLT
   headers change rarely.
3. **Never junk a money movement.** If `debited`, `spent`, `withdrawn`,
   `credited` can co-occur, put those words in `none`.
4. **Leave OTPs alone.** An OTP must land in Transactions — Apple's Reminders
   sub-action is where users actually look for it. `otp.never_junk` guards this.
5. **Test both directions.** `POST /api/v1/rules/evaluate` with
   `{"sender": "...", "body": "..."}` returns `matched` + `verdict`; run the
   message that should *not* match too.

### Try it

```bash
./.venv/bin/python -m sms_wiz.cli rules-check          # validate authoring file
./.venv/bin/python scripts/gen_rule_fixtures.py        # after adding or editing a rule
make verify                                            # fails if the fixtures are stale

curl -s localhost:8000/api/v1/rules/evaluate -H 'content-type: application/json' \
  -d '{"sender":"AD-HDFCBK-T","body":"Convert your purchase to Easy EMI at 0% interest. Pre-approved for you."}'
# {"matched": true, "verdict": {"action": "promotion", "sub_action": "promotionalOffers", ...}}

curl -s localhost:8000/api/v1/rules | head                # what shipped, and why
curl -s localhost:8000/api/v1/templates | head           # bank sentence shapes
```

---

## Fails open, always

If no rule matches, the extension asks the server; if the server times out, the
message is allowed through. A bouncer that guesses is worse than no bouncer,
because the failure mode of a wrong Junk folder is a missed bank alert.
