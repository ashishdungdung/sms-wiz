# AGENTS.md — the operating contract

This file is the entry point for **any** coding agent working in this repository:
Claude Code, Antigravity, DeepSeek, Zed, Cursor, Copilot, or a human with a
terminal. It is intentionally tool-agnostic. Nothing here requires Xcode's GUI,
a Mac-only step, or a specific vendor's IDE.

`CLAUDE.md` and `.rules` are symlinks to this file, so there is exactly one set
of instructions to maintain.

If you read only one section, read **Non-negotiables**, then **Definition of done**.

---

## 1. What this repository is

An India-first SMS brain for iOS. It takes Indian bank / UPI / delivery /
government SMS, classifies them with an ordered, explainable **rule chain**, keeps
a ledger that never double counts, and tells an iPhone which of Apple's fixed
message folders each message belongs in.

- `sms_wiz/` — Python. Pure classification logic, SQLite persistence, FastAPI, CLI.
- `ios/` — Swift. Message filter extension, App Intent, models, and a
  platform-neutral `SMSCore` Swift package with real tests.
- `contracts/` — the language-neutral handshake (generated schema + enums + fixtures).
- `tests/` — 364 pytest tests. They are the referee; docs are not.
- `.agents/skills/` — repeatable procedures for the changes this repo actually gets.

Read `PROJECT.md` for scope, `ARCHITECTURE.md` for how the parts fit,
`DESIGN.md` for the product surface, `LANGUAGE.md` for domain vocabulary and the
multilingual policy.

## 2. Non-negotiables

These are behavioural requirements, not style preferences. A patch that breaks
one is wrong even if every test passes — add the test that catches it.

1. **Zero double counting.** The ledger treatment, never the intent, decides
   whether money counts. `internal_transfer` (credit-card bill payment,
   self-transfer), `instrument_load` (FASTag / wallet / UPI Lite top-up),
   `cash_withdrawal`, and `investment` never enter spend or income. If you are
   unsure about a message, emit `needs_review` rather than a confident guess.
2. **Every assertion carries evidence.** `SmsEvent.evidence` must explain why the
   parser believes what it believes. A classification without a reason is a bug.
3. **Uncertainty is an output, not a failure.** Below the confidence threshold,
   set `needs_review = True`. Never invent a merchant, amount, or folder.
4. **Privacy is structural.** `redact()` runs inside `analyse()`. LLM calls see
   `redacted_body` only, never OTPs, voucher codes, PINs, or RRNs. The LLM is
   off by default and is **never** on the `/api/v1/filter` path.
5. **iOS reality is the boundary.** The filter extension sees one unknown-sender
   message at a time. There is no inbox access, no history, no deletion, no
   renaming Apple's folders. Apple allows 4 actions and a fixed set of
   sub-actions, max 5 declared per group. Do not "fix" these limits in code.
6. **Nothing is sent on the user's behalf.** The TRAI 1909 / 1930 workflows
   prepare a complaint and hand it to the user. No auto-send, no promise that a
   sender will be disconnected.
7. **Deterministic first.** Rules before models. A model may add a flag or a
   merchant, but it can never overrule a confident rule verdict.

## 3. Where the truth lives

| Question | File |
| --- | --- |
| What is this product, what is out of scope? | `PROJECT.md` |
| How do the pieces fit, what are the invariants? | `ARCHITECTURE.md` |
| What does the user see on an iPhone? | `DESIGN.md` |
| What does "RRN" / "ASBA" / "DLT" mean? How do languages work? | `LANGUAGE.md` |
| Why can the app not read my messages? | `docs/ios-constraints.md` |
| What is the event schema, retention, dedup contract? | `docs/schema.md` + `contracts/` |
| Which rule fires, in what order? | `sms_wiz/classify.py` |
| How is a sender or brand resolved? | `sms_wiz/registry.py` |
| How is a URL or sender judged suspicious? | `sms_wiz/forensics.py` |
| How is money extracted? | `sms_wiz/extract.py` |
| How do Indian scripts become parseable text? | `sms_wiz/normalize.py` |
| How is dedup decided? | `sms_wiz/store.py` + `sms_wiz/pipeline.py` |
| What is the API surface? | `sms_wiz/api.py` |

If this table and the code disagree, the code wins — then fix the table.

## 4. Commands

Everything is driven through `make`. The same targets work in any editor.

```bash
make install      # create .venv, install with dev extras
make test         # pytest  (364 tests)
make demo         # 14 Indian edge cases through the real pipeline
make serve        # FastAPI on 0.0.0.0:8000, landing page at /
make contracts    # regenerate contracts/ from the Python model
make verify       # regenerate-check + cross-language verify + pytest
make swift-test   # swift test for SMSCore (needs a Swift toolchain; skips loudly if absent)
make check        # what CI runs: verify + demo smoke
```

Rules about commands:

- **Never claim a command passed unless you ran it in this session.** "Written
  but not compiled" is a valid, honest status. Fake green is not.
- If a tool is missing (Swift on Linux, Xcode, a device), say so and continue
  with everything that *can* run.
- Prefer `make verify` over ad-hoc pytest invocations; it also guards the contract.

## 5. Recipes for the changes this repo actually gets

Detailed, step-by-step versions live in `.agents/skills/`. The summary:

### Change a classification
1. Reproduce it as a failing test (`tests/test_classify.py`) or fixture.
2. Adjust the **ordered** rule chain in `sms_wiz/classify.py` — order matters;
   an earlier rule wins.
3. Add evidence text. Every new branch needs a reason string.
4. If routing changes, run `make contracts` (Swift enums may need a mirror).
5. `make verify`.

### Add a bank, fintech, brand, or government body
`sms_wiz/registry.py`: entity (`_bank` / `_brand` / `_govt` helper), DLT sender
prefixes, domain(s), and aliases. Then a test with a real-looking SMS from it.
Never fuzzy-match an entity name; add an explicit alias (see the `RTO` case).

### Add a merchant
`SEED_MERCHANTS` in `sms_wiz/registry.py` for global seeds; users maintain the
rest in `merchant_dictionary`. Unknown merchants are **flagged for review**, not
guessed. Keep the longest-key-wins behaviour intact.

### Add or extend a language
`sms_wiz/normalize.py` (`PHRASE_MAP`, digit blocks, currency markers) plus a
normalisation test. See `LANGUAGE.md` for the definition of "supported".

### Change the event schema
1. Edit `sms_wiz/models.py`.
2. `make contracts` → regenerates `contracts/sms_event.schema.json` + `enums.json`.
3. Mirror any enum change in `ios/Packages/SMSCore/Sources/SMSCore/Contracts.swift`
   and `ios/SMSWiz/Models.swift`.
4. `make verify` — it fails on drift between Python, JSON, and Swift.

### Add a regression fixture
Drop a JSON file in `contracts/fixtures/` (copy an existing one). Python replays
it through the whole pipeline; the Swift tests decode its expectations. One file,
two languages, no drift.

## 6. Definition of done

A change is done when all of these are true:

- [ ] `make verify` passes locally (contracts current, cross-language parity, tests green).
- [ ] A test exists that fails without the change. Fixtures count as tests.
- [ ] `contracts/` regenerated if any model/enum changed, Swift mirrored.
- [ ] Evidence strings added for any new classification branch.
- [ ] Docs updated where the table in §3 points (`LANGUAGE.md` for vocabulary,
      `DESIGN.md` for anything user-visible, `ARCHITECTURE.md` for data flow).
- [ ] No new network call on the filter path, no secret in the repo.
- [ ] Honest status: if something is written but not compiled/run, say so in the
      summary and in `PROJECT.md` status labels.

## 7. What not to do

Learned the hard way; do not re-litigate these.

- **Do not** claim the app can read, index, export, or delete the Messages
  database, or that iMessage is reachable. It cannot.
- **Do not** add message-folders beyond Apple's fixed sub-actions, and do not
  declare more than 5 per group (iOS 18 collapses the list, FB15385442).
- **Do not** return a sub-action that was not declared — iOS keeps the action and
  silently drops the sub-action.
- **Do not** hand-edit files in `contracts/` (schema and enums are generated).
- **Do not** commit `sms_wiz.egg-info/`, `*.db`, `DerivedData/`, or `*.ipa`.
- **Do not** quarantine a message on a single weak signal. Legitimate bank
  promos have links and brand names; phishing needs a *combination*.
- **Do not** let a model overrule a confident rule, or put a model call anywhere
  the user is waiting on a folder decision.
- **Do not** mark a merchant as known when the dictionary missed it; flag it.
- **Do not** "improve" the demo numbers by dropping an awkward test message.

## 8. Skills

| Skill | Use when |
| --- | --- |
| `01-classify-a-message` | a message lands in the wrong intent, folder, or ledger bucket |
| `02-add-a-merchant-or-entity` | a new bank, fintech, brand or government sender appears |
| `03-add-a-language` | supporting a new Indian script or fixing a mis-read phrase |
| `04-change-the-contract` | touching the event schema, enums, or fixtures |
| `05-ship-an-ios-change` | changing the extension, App Intent, or Swift models |
| `06-debug-the-ledger` | a total looks wrong, or a payment seems counted twice |

Each skill is a checklist, not an essay. Follow it in order; it ends with
`make verify`.

## 9. Working agreement for agents

- Make the smallest change that satisfies the requirement, then prove it.
- Prefer adding a fixture over adding a long comment; the fixture keeps working.
- Keep comments about *why* (India-specific quirks: DLT headers, NACH
  pre-debits, ₹1 authorisation holds), not *what* the next line does.
- If a requirement is impossible on iOS, say so plainly and offer the closest
  thing that does work. The user has been told "no" before and prefers it.
- When you finish, state: what changed, what you ran, what is unverified.
