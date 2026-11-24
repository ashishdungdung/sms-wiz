---
name: 04-change-the-contract
title: Change the event schema, enums, or fixtures
summary: Edit models.py, regenerate contracts/, mirror in Swift, prove parity with make verify
---

# Skill 04 — Change the contract

## When to use

- Adding a field to `SmsEvent` (e.g. `warranty_expires_on`).
- Adding an intent, retention, ledger treatment, or entity kind.
- Changing what a fixture expects after an intentional behaviour change.
- Adding a regression fixture for a bug that is now fixed.

`contracts/` is the handshake between Python, Swift, and the app's DTOs. Getting
this wrong is the most expensive kind of wrong: it fails at runtime on a phone
that no test on Linux will see.

## The flow

```
models.py  ──export──►  contracts/sms_event.schema.json
                       contracts/enums.json
                            │                    │
                     tests/test_contracts.py   verify_contracts.py
                            │                    │
                     make verify  ◄──────  Contracts.swift (textual parity)
```

## Procedure — add or change a field

1. Edit `sms_wiz/models.py`. Keep defaults compatible: new optional fields get a
   default, or every stored event breaks.
2. `make contracts` (regenerates the schema + enums and prints the files).
3. Mirror in Swift:
   - `ios/Packages/SMSCore/Sources/SMSCore/Contracts.swift` for enums.
   - `ios/SMSWiz/Models.swift` for the decode-only event DTO (decode-only is
     deliberate: the app must never write a shape the backend does not produce).
4. Fill the new field somewhere real (`classify.py` / `pipeline.py` /
   `store.py` — including the SQLite column, or it survives only in memory).
5. Assert it in at least one fixture.
6. `make verify`.

## Procedure — add an enum case

1. Add to the Python enum in `models.py`.
2. `make contracts`.
3. Add the same case with an **explicit raw value** in `Contracts.swift`:

   ```swift
   case transactionalWeather = "transactionalWeather"
   ```

   Swift infers raw values, but the verifier reads them textually — and explicit
   values are what the wire format deserves.
4. If it is an Apple sub-action: check the ≤5-per-group cap and that it is in
   `AppleSubAction.declared*` and in the extension's capabilities response. An
   undeclared sub-action is silently dropped by iOS.
5. `make verify && make swift-test` (the latter on a Mac).

## Procedure — add a fixture

Copy a neighbour in `contracts/fixtures/` and keep the shape documented in
`contracts/README.md`. Rules:

- Fixed timestamps only (the suite must be deterministic; the demo uses a fixed
  `NOW` of 2025-03-01 IST).
- Use `prior` when the case is about dedup, balances, or refunds.
- Assert only what the case is about; `flags_include` / `alerts_include` are
  inclusion checks because those lists grow.
- Enum values must exist in `enums.json` — `make verify` rejects typos before
  they reach Swift.

## Pitfalls

- **Never hand-edit** `sms_event.schema.json` or `enums.json`. `--check` exists
  to catch exactly that.
- Renaming an enum value is a **breaking wire change**: old events in SQLite and
  old app builds both break. Prefer adding a case and migrating on read.
- A new SQLite column needs a migration or a defensive read: `init()` does not
  alter existing tables.
- Do not add a field "for later" without filling it. Empty contract surface is
  ambiguity that the app will render as blank.
- `required` in the schema is derived from pydantic; if you add a field without
  a default, every existing caller must be updated.

## Verify

```bash
make contracts
make verify          # stale check + parity + tests
make swift-test      # on a Mac/Linux with Swift; honest skip otherwise
```

## Done

- [ ] Schema regenerated, not hand-edited.
- [ ] Swift mirrored with explicit raw values.
- [ ] Store/SQLite updated if the field must survive a restart.
- [ ] At least one fixture asserts the new surface.
- [ ] `make verify` green; `make swift-test` run or explicitly skipped.
