# PROJECT.md — what we are building and why

## The problem

An Indian iPhone user gets 40–80 SMS a day. Roughly a dozen are money:
salary credits, UPI debits, credit-card bills, EMI pre-debits, refunds that may
or may not arrive, FASTag tolls, IPO allotments, dividend credits, insurance
premiums. The rest is noise: loan offers, "you are pre-approved", fake
KYC-expiry links, delivery updates, one-time passwords.

Microsoft SMS Organizer solved this on Android and stopped at the platform
boundary. iOS gives an app **no inbox access at all** — the only sanctioned
foothold is a message filter extension that sees one unknown-sender message at a
time. That constraint is the product's shape, not an obstacle to wish away.

`SMS Wiz` is therefore two things that share one brain:

1. **A firewall + sorter** for the inbox: classify each incoming message from an
   unknown sender, return Apple's `(action, subAction)` pair, and quarantine
   phishing quietly without training the user to ignore the folder.
2. **An organiser** that turns the messages the user *does* hand it (paste,
   share sheet, Shortcut, App Intent) into a ledger, reminders, and vaults with
   no double counting.

## Who it is for

One person, in India, on an iPhone 14, who wants to know **"what did I actually
spend, and what is about to hit my account"** without opening six apps.
The design target is a single-user install: local SQLite, no account, no cloud
dependency, optional home-server sync.

## Principles

1. **Rules first, models second.** The deterministic chain answers; a model may
   only add. This is what makes the filter fast enough for iOS's tight budget
   and auditable when it is wrong.
2. **Explain or abstain.** Every verdict carries evidence; when the evidence is
   thin the message is `needs_review`, not a guess.
3. **Money is sacred.** One payment = one ledger row, whatever rail or sender
   reported it. Transfers between the user's own pots are never spending.
4. **India-native vocabulary.** NACH, ASBA, UPI Lite, FASTag, DLT headers, RTO
   challans, IRCTC charts are first-class concepts, not edge cases.
5. **The platform is the spec.** Features that need inbox access are replaced by
   features that work, not promised.
6. **Local by default.** Text never leaves the phone/server unless the user
   configures an LLM endpoint — including a local LM Studio one.

## Scope

### In scope for v1

| Area | What it does | Status |
| --- | --- | --- |
| Classification engine | 7-rule ordered chain over sender, entity, money verbs, mandates, links | **Built** — 426 pytest tests |
| Zero-double-count ledger | treatment per intent, RRN/reference dedup, refund matching | **Built** |
| Spam & phishing forensics | DLT/header checks, domain age heuristics, shorteners, APK links, brand-claim-on-unregistered-sender | **Built** |
| Multilingual normalisation | 9 Indian digit blocks, currency markers, 50 phrase mappings, script detection | **Built** (see `LANGUAGE.md`) |
| Apple folder routing | fixed action + declared sub-action, fails open | **Built** (backend); extension **written, not compiled** |
| API + CLI + dashboard | FastAPI, `X-SMSWiz-Token`, landing page, `demo` benchmark | **Built** |
| Refund watchdog | claims, matching, amount-mismatch and no-claim alerts | **Built** |
| Voucher vault | codes, PINs, expiry, redaction, listing | **Built** |
| Debit shortfall alerts | compares NACH/AutoPay notice against last known balance | **Built** |
| OTP intelligence | intent, risk, redaction, never stored in clear | **Built** (risk scoring is deliberately conservative) |
| Contract harness | generated schema/enums, Swift parity, 9 shared fixtures | **Built** |
| Per-bank templates | 28 sentence shapes across 12 headers, merged per message, audited | **Built** |
| Merchant dictionary | 753 keys over 333 brands, 21 categories, VPA hints, `user > seed > vpa > llm > learned` | **Built** |
| Spend analytics | weekly / monthly / UPI / category / top merchant, posted-date buckets | **Built** |
| Subscription calendar | cadence with confidence, next-due, price changes, monthly commitment | **Built** — one interval is not a cadence |
| Credit-card ledger | statement ↔ payment reconciliation per card, 6 states, outstanding | **Built** |
| Notification policy | push / digest / silent per alert kind, quiet hours, 6-hour dedupe | **Built** |
| Filter rule DSL | `sms-wiz.filter/v1`, validated, compiled to `contracts/filter_rules.json` | **Built** (Python fast path); Swift evaluator **written, not compiled** |
| Real-message fixtures | redacted Indian bank messages replayed through the pipeline | **Built** (see `tests/fixtures/bank_templates/`) |
| Swift package + tests | platform-neutral models with parity tests | **Written, not compiled here** (no Swift toolchain in CI sandbox) |
| SwiftUI app screens | ledger, review queue, vaults, complaint | **Proposed** |
| Cloudflare edge for the filter endpoint | global uptime for network deferral | **Proposed** — not written |
| XcodeGen project spec | generate the `.xcodeproj` without the GUI | **Proposed** — not written |

### Out of scope (and why)

- **Reading Messages history / `sms.db`.** Not possible on iOS, at any price or
  in any region. See `docs/ios-constraints.md`.
- **Deleting messages on the user's behalf.** The filter can sort; only the user
  can delete. We count and point, we do not press the button.
- **iMessage.** Not a filter-extension surface.
- **Auto-filing a complaint with TRAI.** The user reviews and sends. No promise
  of disconnection, ever.
- **Multi-user / SaaS tenancy.** Single-user local store keeps privacy honest.
- **Rust, native crypto, ML on-device.** v1 chose Python + Swift on purpose.

## The seven assistants

Each is a rule + a reminder + a place to look. None of them need inbox access.

1. **Refund watchdog** — "your ₹1,850 refund from Myntra is 4 days late".
   Claims are opened from the merchant's message, closed only by a matching
   credit. Built.
2. **Market radar** — IPO/ASBA allotment, demat credits, MF SIP debits,
   dividends, buyback. Built as intents + ledger exclusions; notifications are
   Proposed.
3. **Card rewards** — vouchers, milestone progress, lounge eligibility, forex
   markup. Vault + reward intents built; milestone tracker Proposed.
4. **Recurring & mandates** — UPI AutoPay, NACH, subscriptions, EMI calendar,
   balance shortfall warning. Shortfall built; calendar UI Proposed.
5. **Household & family safety** — delivery windows, and a parent-mode digest
   that explains a suspicious message in plain language. Delivery intents built;
   the digest is Proposed.
6. **Find & act** — Spotlight/Siri via App Intents, Live Activities for a
   delivery or a bill due today. Intent code written; Live Activity Proposed.
7. **Paper trail** — invoices, warranties, e-challans, insurance renewals kept
   with retention rules instead of forever. Retention + vault built; PDF attach
   Proposed.

## Success criteria

- A month of real messages produces spend that matches the bank statement to the
  rupee, with every credit-card bill payment and self-transfer excluded *and
  visibly explained*.
- The filter never returns an undeclared sub-action, and fails **open** (allow)
  when the network is slow.
- Every quarantined message can be justified in one sentence to a sceptical user.
- Adding a new bank is a ~10-line registry change plus a test, done without Xcode.
- A Hindi or Tamil SMS about money parses to the same canonical event as its
  English equivalent.

## Constraints

- iOS 17/18 target, iPhone 14 (Live Activities yes, Dynamic Island no).
- Filter budget: no network in the hot path except `deferQueryRequestToNetwork`,
  which additionally needs the associated domain `messagefilter:`.
- Everything must be editable in a plain editor: no `.xcodeproj`-only state, no
  storyboards, no GUI-required build settings. `AGENTS.md` is the contract.
- Python ≥ 3.11, SQLite only.

## How work is organised

```
AGENTS.md   → how to work (any agent, any IDE)
PROJECT.md  → what and why (this file)
ARCHITECTURE.md → how the parts fit and which invariants hold
DESIGN.md   → what the user experiences
LANGUAGE.md → vocabulary + multilingual policy
.agents/skills/*/SKILL.md → step-by-step procedures for common changes
contracts/  → the generated, cross-language handshake
tests/      → the referee
```

Status language is used deliberately throughout: **Built** (code + tests run),
**Written, not compiled** (source exists, no toolchain here), **Proposed**
(design only). Keep that discipline when updating this file.
