# ARCHITECTURE.md — how the parts fit

## The one diagram

```
                       iPhone (iOS 17/18)
  ┌───────────────────────────────────────────────────────────────────┐
  │ Messages.app                                                      │
  │   incoming SMS from an unknown sender                             │
  │            │                                                      │
  │            ▼                                                      │
  │   ILMessageFilterExtension  ──► local rules (no network)          │
  │            │  uncertain?                                          │
  │            └──► deferQueryRequestToNetwork ──► /api/v1/filter     │
  │                                                   (header token)  │
  │            │                                                      │
  │            ▼                                                      │
  │   returns (action, subAction)  → Apple's fixed folders             │
  │                                                                   │
  │   App / Shortcut / App Intent ──► /api/v1/ingest-sms (event)     │
  └───────────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌───────────────────────────────────────────────────────────────────┐
  │ FastAPI  (sms_wiz/api.py)                                         │
  │   POST /api/v1/filter     → {action, subAction}  only, fails open  │
  │   POST /api/v1/ingest-sms → full SmsEvent + alerts                 │
  │   GET  /api/v1/summary    → month buckets, zero-double-count view  │
  │   GET  /api/v1/vouchers · /refunds · /cleanup-queues · /review     │
  │   GET  /api/v1/overview   → month + weeks + trend + UPI + cards    │
  │   GET  /api/v1/analytics/{weekly,monthly,upi}                      │
  │   GET  /api/v1/subscriptions[/calendar] · /cards                   │
  │   GET  /api/v1/notifications · /digest                             │
  │   GET  /api/v1/merchants/stats · /templates · /rules               │
  │   POST /api/v1/rules/evaluate · /filter/explain (token-guarded)    │
  └───────────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌───────────────────────────────────────────────────────────────────┐
  │ Ingestor (sms_wiz/pipeline.py)   ← the only place SMS becomes state│
  │   normalize → analyse → persist → dedup → alerts                   │
  └───────────────────────────────────────────────────────────────────┘
        │                 │                    │
        ▼                 ▼                    ▼
  normalize.py       classify.py          store.py ──► SQLite
  (scripts→ASCII)    (7-rule chain)                  events, balances,
        │                 │                          refunds, vouchers,
        ▼                 ▼                          alerts, merchant_dictionary
  extract.py        forensics.py
  (money, refs,     (sender trust,
   merchants,        URL risk,
   dates, PNR)       brand claims)
        │
        ▼
  registry.py  (≈180 Indian entities, DLT prefixes, domains, categories, routing)

  Derived views — pure functions over stored rows, no second source of truth
  ─────────────────────────────────────────────────────────────────────────
  analytics.py       month / ISO week / UPI / category / top merchant
  subscriptions.py   cadence + confidence, calendar, price changes
  cards.py           statement ↔ payment, outstanding, six states
  notify.py          push / digest / silent, quiet hours, dedupe, digest text
```

**One definition of a month.** `analytics.received()` prefers the date inside the
message (`analysis.posted_on`) over the delivery timestamp, so a payment made on
the 28th and delivered on the 1st stays in the month the statement puts it in.
`store.summarise_month` delegates to the same function; the CLI, `/summary` and
`/overview` therefore cannot disagree.

## Layering rule

**Pure functions first, state last.** Everything up to `pipeline.ingest()` is
pure: same string in, same event out, no I/O. That is why the fixture replays
(`contracts/fixtures/*.json`) are trustworthy, why the demo is deterministic,
and why a filter decision can reuse the same code on a cold path.

| Layer | Module | May do I/O? |
| --- | --- | --- |
| Text normalisation | `normalize.py` | no |
| Extraction | `extract.py` | no |
| Forensics | `forensics.py` | no |
| Rule chain | `classify.py` | no (merchant lookup is injected) |
| Persistence | `store.py`, `db.py` | yes |
| Orchestration | `pipeline.py` | yes |
| Transport | `api.py`, `cli.py` | yes |
| Optional enrichment | `llm.py` | yes, off by default |

## The seven-rule chain (order is the design)

1. **Sender identity** — DLT header, shortcode, `-S`/`-T` transactional suffix,
   10-digit personal number, unknown. Never trust a body claim over a header.
2. **Entity resolution** — DLT prefix and domain → bank/fintech/govt/brand.
   An unregistered sender claiming a bank brand is a signal, not an identity.
3. **Money verbs** — debit/credit/withdrawal/refund/reversal with an amount.
   This is what makes a ledger row, and only this decides the treatment.
4. **Mandates & dues** — NACH/AutoPay pre-debit, credit-card bill, EMI notice:
   *notices*, not movements.
5. **Upsell demotion** — a standalone loan/insurance pitch from a bank is
   `bank_upsell` → Promotions. An upsell *inside* a real debit is only a flag.
6. **Link & phishing forensics** — combination signals, see below.
7. **Fallback** — `unknown` + `needs_review`. Never silently `allow` something we
   could not read.

## Forensics: never quarantine on a single weak signal

`forensics.py` scores risk. A single signal is never enough:

| Signal | Weight | Notes |
| --- | --- | --- |
| Known shortener (`bit.ly`, …) | + | bank promos use these legitimately |
| Suspicious TLD / lookalike domain | + | `sbi-kyc.xyz`, `-pay.com` |
| APK / executable download link | ++ | almost always malicious in SMS |
| Brand claim + unregistered sender + link | **+0.30** | the combination rule |
| OTP/credential harvesting language | + | "verify", "KYC expires", "account blocked" |

Thresholds: **≥ 0.7 quarantine** (`phishing_suspect` → `junk`), **≥ 0.45
suspicious** (keep the action, flag it). The +0.30 combination rule exists
because a legitimate HDFC EMI pitch has a link and a brand name; only the
triple of brand + unregistered number + link is decisive.

## Money invariants

1. `Ledger.for_intent()` is the single source of truth for treatment.
   `counts_toward_spend` is true only for `expense`.
2. `internal_transfer` — credit-card bill payments, self-transfers, own-account
   movements — is stored, shown, and excluded with a reason.
3. `instrument_load` (FASTag / wallet / UPI Lite), `cash_withdrawal`,
   `investment` are excluded; the *later* spend through that instrument counts.
4. **Refunds never create income.** A matched refund reduces the original spend;
   an unmatched one is a claim until the credit arrives.
5. **Dedup.** Same reference/RRN anywhere → `suppressed_duplicate` (stored, not
   counted). Same amount + day + last4 from a *different* sender →
   `possible_duplicate` (counted, shown for review). Amount alone never merges.
6. Balance is a point-in-time observation per account last4; it is never summed.

## State

SQLite, single file, WAL-friendly, no migrations framework yet (init on start):

| Table | Purpose |
| --- | --- |
| `events` | one row per accepted message; the summary/ledger source |
| `balances` | last known balance per account last4 |
| `refunds` | claims, matched credits, mismatch state |
| `vouchers` | code, PIN, amount, expiry, source |
| `alerts` | dedup, shortfall, phishing, high-risk OTP, review |
| `merchant_dictionary` | user > seed > llm > learned precedence |

Retention is per-event (`keep_forever`, `expire_30d/7d/24h`,
`delete_after_event`, `review`). Quarantined junk expires in 24h; travel and
orders are pruned once the event has passed.

## Confidence contract

```
score ≥ 0.7   → confident, no review
0.45 – 0.7    → medium, needs_review for money-bearing intents
< 0.45        → low, needs_review always
```

`needs_review` is surfaced in `/api/v1/review` and in the app's review queue.
A user correction writes to `merchant_dictionary` with `source=user` and wins
over every other source on the next identical message.

## Filter path discipline

- No LLM call, ever.
- No database write on the accept path.
- Network is optional: `deferQueryRequestToNetwork` + associated domain. On any
  error or timeout the endpoint returns `{"action": "allow", "subAction": "none"}`
  — **fail open**, because mis-filing a real bank SMS is worse than letting one
  spam through.
- Response shape is only `{action, subAction}`; everything else (evidence,
  ledger, merchant) is withheld, because the extension has nowhere to put it.

## Extensibility seams

| To add… | Touch | Guard |
| --- | --- | --- |
| a bank / fintech / govt body | `registry.py` | test with a realistic SMS |
| a merchant | `SEED_MERCHANTS` or user dictionary | longest key wins; unknown → review |
| a language | `normalize.py` + tests | `LANGUAGE.md` definition of supported |
| a rule | `classify.py` (position matters) | evidence string + fixture |
| a category mapping | `ROUTING` / `effective_subaction()` | ≤5 declared per group |
| an API field | `models.py` → `make contracts` → mirror Swift | `make verify` |
| an LLM provider | `llm.py` `Provider` list | still redacted-only, never on filter |
| a bank's message shape | `templates.py` | one fixture per shape; merged, never guessed |
| a merchant | `catalog.py` | unique key, known category slug, longest key wins |
| an offline filter rule | `rules/filter_rules.json` | validator: declared sub-action, junk needs a danger signal |
| an alert kind | `notify.ALERT_POLICY` + pipeline | default is digest, never a silent drop |
| an analytics view | `analytics.py` | same bucket function as every other view |

## Deployment shapes

- **Now:** local process (`sms-wiz serve`) on the Mac/home server, SQLite file,
  iPhone reaches it over Tailscale or the LAN.
- **Proposed:** Cloudflare Worker in front of the filter endpoint for uptime
  when the home server sleeps. The Worker would terminate TLS, add the token
  header, and proxy — it must never hold message bodies at rest. Not written.

## Failure modes we accept

| Failure | Behaviour | Why acceptable |
| --- | --- | --- |
| Home server asleep | filter fails open, message lands in normal inbox | Better than a false quarantine |
| Unknown merchant | `needs_review`, one-tap fix | Guessing would corrupt spend |
| Ambiguous amount (fee vs total) | both extracted, first is primary, review flagged | Never silently pick |
| LLM unavailable | rules-only result, `raw_ai=None` | LLM is an additive layer |
| Duplicate from a new sender | `possible_duplicate`, counted, reviewable | Amount-only merging loses real payments |
