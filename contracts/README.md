# contracts/ — the cross-language handshake

This directory is **generated**, except the fixtures. It exists so the Python
backend, the Swift app, and any agent share one definition of an event instead
of three drifting copies.

```
contracts/
├── sms_event.schema.json   generated from sms_wiz/models.py  (do not edit)
├── enums.json              generated from sms_wiz/models.py  (do not edit)
├── fixtures/*.json         hand-written acceptance cases        (edit these)
└── README.md               this file
```

## Commands

```bash
make contracts   # regenerate schema + enums from the Python model
make verify      # fail if generated files are stale, or Swift/Python disagree
```

`scripts/export_contracts.py --check` is a pure comparison (safe in CI).
`scripts/verify_contracts.py` additionally parses
`ios/Packages/SMSCore/Sources/SMSCore/Contracts.swift` **textually**, so parity is
checked on Linux without a Swift toolchain. On a Mac, `make swift-test` runs the
same comparison for real against `enums.json`.

## How each side consumes it

| Side | Reads | Test |
| --- | --- | --- |
| Python | `SmsEvent` model (source of truth) | `tests/test_contracts.py` |
| Swift (`SMSCore`) | `Contracts.swift` enums | `ContractsParityTests` (Mac/Linux w/ Swift) |
| Both | `fixtures/*.json` | `tests/test_fixtures.py` + `FixtureDecodeTests` |
| App DTOs | `ios/SMSWiz/Models.swift` | decode-only mirror |

## Adding a fixture

Copy the closest existing file, keep the shape:

```json
{
  "id": "unique_snake_case",
  "description": "why this case exists, in one sentence",
  "prior": [ { "sender": "...", "received_at": "...", "body": "..." } ],
  "input": { "sender": "...", "received_at": "...", "body": "..." },
  "expect": {
    "intent": "expense",
    "apple_action": "transaction",
    "apple_subaction": "transactionalFinance",
    "ledger_treatment": "expense",
    "counts_toward_spend": true,
    "amount": 1450.0,
    "merchant_clean": "Zepto",
    "account_last4": "4092",
    "flags_include": ["embedded_upsell"],
    "alerts_include": ["duplicate_suppressed"],
    "dedup_status": "unique",
    "retention": "keep_forever",
    "confidence_needs_review": false,
    "scripts_include": ["devanagari"]
  }
}
```

Notes:

- `prior` is optional; it seeds the database so dedup, balance, and refund
  behaviour can be tested. Use fixed timestamps — the suite must be
  deterministic.
- Only assert what matters to the case. `flags_include` and `alerts_include`
  are **inclusion** checks because both lists are additive.
- `expect.*` enum values must exist in `enums.json`; `verify_contracts` rejects
  a typo before it reaches Swift.
- Fixtures are replayed through the **whole pipeline** (`Ingestor.ingest`), not
  through `analyse()` directly. That is deliberate: they guard persistence and
  dedup too.

## When the schema changes

1. Edit `sms_wiz/models.py`.
2. `make contracts`.
3. Mirror enum changes in `Contracts.swift` and `ios/SMSWiz/Models.swift`.
4. `make verify` (must be green before committing).

Never hand-edit the two generated files: the `--check` target exists precisely
to catch a hand edit that would silently diverge from the model.
