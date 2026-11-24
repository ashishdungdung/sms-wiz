# SMS Wiz

An **India-first SMS brain**: turns Indian bank / UPI / delivery / government
SMS into a clean, explainable ledger — without double counting a rupee — and
tells an iPhone which of Apple's message folders each message belongs in.

```
   raw SMS ─► script normalisation ─► sender & link forensics ─► ordered rule chain ─► ledger + routing ─► alerts
              (9 Indian scripts)      (TRAI DLT, domains)        (zero double count)   (Apple folders)     (refunds, EMI)
```

It runs as a small FastAPI service plus a CLI. The optional LLM layer is off by
default, gets redacted text only, and can never overrule a confident rule.

---

## What this is not

Read `docs/ios-constraints.md` before planning anything on top of this. The
short version:

* **No app on iOS can read your Messages history or `sms.db`** — not with a paid
  developer account, not sideloaded, not in the EU.
* A **message filter extension** can only sort one incoming SMS at a time, from
  senders not in your contacts, into **Apple's fixed categories**. It cannot
  create folders, store history, or delete anything.
* **Bulk deletion is a user action.** This project tells you which Apple folder
  to clear and how much is in it; it cannot clear it for you.

So the organiser works on messages that reach it through an allowed path
(paste, share sheet, Shortcut), and the filter narrows the inbox. Anything that
claims otherwise is selling you a feature iOS will not run.

---

## Quickstart

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"

./.venv/bin/python -m sms_wiz.cli demo          # 39 Indian edge cases, end to end
./.venv/bin/python -m sms_wiz.cli serve         # API on http://127.0.0.1:8000  (/docs for OpenAPI)
./.venv/bin/python -m pytest                    # 467 tests

# or, without remembering any of the above:
make install && make demo && make verify
```

Other commands, once you have messages stored:

```bash
./.venv/bin/python -m sms_wiz.cli analytics --month 2026-11   # spend, weeks, UPI, categories
./.venv/bin/python -m sms_wiz.cli subscriptions --days 45     # recurring + calendar
./.venv/bin/python -m sms_wiz.cli cards                       # statement vs payment per card
./.venv/bin/python -m sms_wiz.cli merchants --q swiggy        # dictionary lookup
./.venv/bin/python -m sms_wiz.cli digest                      # push vs digest vs silent
./.venv/bin/python -m sms_wiz.cli rules-check                 # validate the offline rule file
./.venv/bin/python -m sms_wiz.cli coverage                    # banks, brokers, merchants, templates
```

Classify a single message without a server:

```bash
./.venv/bin/python -m sms_wiz.cli ingest \
  -s AD-HDFCBK-T \
  -b "Rs.1450.00 debited from A/c XX4092 to VPA KIRANAKART@axisbank (UPI Ref 505612345678). Avl Bal: Rs.5,000.00. Convert this spend into EasyEMI! bit.ly/hdfcemi"
```

```
intent      expense  (0.90, review=False)
summary     ₹1,450.00 spent at Zepto
ledger      expense  counts_toward_spend=True
apple       transaction / transactionalFinance
retention   keep_forever
evidence    debit verb 'debited' with amount
            message also contains a lending/EMI pitch; the transaction is still counted once
```

### What `demo` proves

| # | Message | Result |
| --- | --- | --- |
| 1 | HDFC debit with "convert to EasyEMI" | `expense`, ⚑ `embedded_upsell` — counted **once**, no EMI event |
| 2 | PhonePe confirmation of the same UPI | suppressed duplicate (same 12-digit RRN) |
| 3 | NACH e-mandate for ₹12,500 vs ₹5,000 balance | `upcoming_debit` + **critical shortfall** alert |
| 4 | "Pre-approved personal loan" on a bank `-S` header | `bank_upsell` → Promotions, not Finance |
| 5 | Fake e-challan `.apk` from a 10-digit number | `phishing_suspect` → Junk, quarantined |
| 6 | Credit-card bill paid from bank account | `cc_bill_payment`, **excluded** from spend |
| 7-8 | Refund promised, then credited | refund claim opened, then matched and closed |
| 9 | Amazon Pay gift card with code + PIN | saved to voucher vault with expiry |
| 10 | OTP to register a Netflix e-mandate | `otp`, **high risk** |
| 11 | "Your delivery OTP is 8492" | `logistics`, handover code — *not* an auth OTP |
| 12 | FASTag toll ₹65 | `fastag_toll`, counts (a recharge would not) |
| 13 | IRCTC PNR / coach / berth | `travel` (train), seat parsed, **not** spend |
| 14 | Salary credit | `income` |
| 28 | Dividend credited for INFOSYS | `dividend` → **income**, not a refund |
| 29 | Zerodha "10 shares of INFY executed at ₹1,450" | `trade` — units recorded, no rupees counted |
| 30 | ₹15,000 blocked for an ASBA IPO | `ipo` — blocked money, not spend |
| 31 | 1:1 bonus shares credited | `corporate_action` |
| 32 | RedBus ticket, PNR, Bengaluru→Chennai, 2 pax | `travel` (bus), route and headcount parsed |
| 33 | Airtel postpaid bill due | `bill_due` → Mobile & Broadband, dated reminder |
| 34 | ACT Fibernet broadband debit | `expense` → Mobile & Broadband |

Then the month closes as: **spend ₹13,443**, **income ₹86,200** (salary plus the
dividend), **refunds ₹1,850**, **excluded ₹25,950** (card-bill payment ₹18,450,
SIP ₹5,000, ATM ₹2,000, FASTag load ₹500), 0 low-confidence. The demo also
prints the demat, travel and bill views, built from the same rows.

---

## Coverage, counted

Every number below comes from code (`cli coverage` prints the same table), so it
can be re-derived instead of believed. Codes and institutions are reported
separately on purpose: `HDFCBK` and `HDFCCR` are one bank, not two.

| Thing | Count | Where it lives |
| --- | --- | --- |
| Bank institutions | **50** | `registry.bank_institutions()` over 71 DLT codes |
| Banks with their own message template | 17 | `templates.py`; the rest use the generic shapes |
| Stock brokers / demat platforms | **22** | `catalog.STOCK_BROKERS`, asserted present by test |
| Mobile carriers | 4 | `catalog.MOBILE_OPERATORS` (Airtel, Jio, Vi, BSNL) |
| Broadband / DTH / ISP brands | 22 | `Mobile & Broadband` category |
| Market infrastructure | 5 | NSE, BSE, MCX, CDSL, NSDL |
| Merchant brands | 333 | over 753 declared keys (716 after normalising), 21 categories |
| Message templates | 59 | 17 bank names + generic bank/card/broker/travel/telco/utility + FASTag/IRCTC |
| Intents | 26 | all 26 routed to an Apple folder |
| Offline filter rules | 34 | 10 junk, 16 transaction, 8 promotion |

What is *written but not compiled*: the Swift mirror of the new `trade`,
`travel`, `dividend`, `ipo` and `corporate_action` intents decodes and is checked
textually by `scripts/verify_contracts.py`, but no Xcode build has run here.

---

## Design rules

1. **Rules first, model second.** Every classification is deterministic and
   explainable. The LLM only names an unresolved merchant or gives an opinion on
   a message the rules could not classify — and that opinion is queued for
   review, never applied silently.
2. **Evidence, not confidence theatre.** `event.evidence` says *why*. A
   classification with no reason is a bug.
3. **Money is counted once.** The ledger treatment decides totals, not the
   intent. Card-bill payments, wallet loads, own-account transfers, ATM cash and
   investments are excluded from spend by construction.
4. **Amount alone never merges two messages.** A shared UPI RRN suppresses a
   duplicate; a same-amount coincidence only asks a human.
5. **Uncertainty is an output.** Below 0.6 confidence → `needs_review = true`,
   with the reasons attached.
6. **Nothing secret leaves the device.** OTPs, voucher codes/PINs, PAN,
   Aadhaar-length numbers, phone numbers, VPAs and URLs are masked before any
   LLM request, and never appear in summaries.
7. **Never promise what iOS won't do.** The routing layer only returns
   sub-actions the app declared, and "safe to bulk delete" is false whenever the
   message needs reading first.

---

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/ingest-sms` | classify, dedupe, persist, raise alerts |
| `POST` | `/api/v1/filter` | Apple network-deferral contract: returns only `{action, subAction}` |
| `GET` | `/api/v1/events`, `/events/{id}` | ledger rows / full event |
| `GET` | `/api/v1/summary?month=YYYY-MM` | spend, income, refunds, excluded, by category |
| `GET` | `/api/v1/review` | the "verify before acting" queue |
| `GET` | `/api/v1/refunds`, `/vouchers`, `/accounts`, `/alerts` | watchdogs |
| `GET` | `/api/v1/analytics/investments` | units, SIPs, dividends, IPOs, corporate actions (not a portfolio value) |
| `GET` | `/api/v1/analytics/travel` | tickets, PNRs, routes, upcoming trips |
| `GET` | `/api/v1/analytics/bills` | what is due vs what was paid, per category |
| `GET` | `/api/v1/cleanup-queues` | counts per Apple folder — the honest bulk-delete helper |
| `GET`/`POST` | `/api/v1/merchants` | self-learning dictionary; `POST` = user correction |
| `GET` | `/api/v1/apple/capabilities` | the sub-actions to declare (≤5 per group) |
| `GET` | `/health` | db path, merchant count, whether the LLM is on |

Set `SMSWIZ_TOKEN` to require `X-SMSWiz-Token` on **every** `/api/v1/*` route, reads
included — the stored rows are a bank statement. `/` and `/health` stay open so the
phone can find the server before it has a token.

---

## Optional LLM enrichment

Off unless configured. Any OpenAI-compatible endpoint works (LM Studio, llama.cpp,
vLLM, Groq, OpenRouter, OpenAI):

```bash
# single endpoint
export SMSWIZ_LLM_BASE_URL=http://192.168.0.5:1234/v1
export SMSWIZ_LLM_MODEL=qwen2.5-14b-instruct

# or an ordered fallback chain (first valid JSON answer wins)
export SMSWIZ_LLM_CHAIN='[
  {"name":"home","base_url":"http://192.168.0.5:1234/v1","model":"qwen2.5-14b-instruct","timeout":3},
  {"name":"groq","base_url":"https://api.groq.com/openai/v1","model":"llama-3.3-70b-versatile","api_key_env":"GROQ_API_KEY"}
]'
```

The filter path never calls it: Apple's deferral has a tight deadline, and a
model in the loop is how you get slow sorts and flaky folder decisions.

---

## iPhone side

`ios/` contains **reference Swift** (not compiled here, and labelled as such):
a `MessageFilterExtension` with the network deferral and the five declared
sub-actions, an `AppIntent` for the Shortcuts bridge, and Codable models for
this API.

Read in this order:

1. `docs/ios-constraints.md` — what is possible, what is not, and the 15-minute
   test that tells you whether the Shortcuts bridge exposes message bodies on
   your iOS version.
2. `docs/schema.md` — the event model, ledger table and routing rules.
3. `docs/review.md` — what three adversarial probes broke, what was fixed, and
   what was deliberately left alone (with the test that pins each fix).
4. `ios/README.md` — wiring the extension, the App Group, the associated domain.

---

## Working on this in any IDE (the agent harness)

The repo is built so Claude Code, Antigravity, DeepSeek, Zed, or a human with
`vim` all have the same context and the same commands. No step requires the
Xcode GUI.

| File | Answers |
| --- | --- |
| `AGENTS.md` (`CLAUDE.md`, `.rules` → same file) | how to work here: non-negotiables, recipes, definition of done |
| `PROJECT.md` | what we are building, what is out of scope, honest status labels |
| `ARCHITECTURE.md` | data flow, layering, the seven-rule chain, money invariants |
| `DESIGN.md` | what the user sees on an iPhone, and what iOS refuses to allow |
| `LANGUAGE.md` | Indian finance vocabulary + the multilingual policy |
| `.agents/skills/*/SKILL.md` | six procedures: classify, add entity, add language, change contract, ship iOS, debug ledger |
| `contracts/` | generated schema/enums + 9 fixtures both languages replay |

```bash
make help        # every target
make verify      # contracts current + Python/Swift parity + tests   <- the gate
make swift-test  # real SMSCore tests (honest skip when no toolchain)
```

The contract is generated from the model, not written by hand:

```
sms_wiz/models.py ──► contracts/sms_event.schema.json + enums.json
                              │
        tests/test_contracts.py · scripts/verify_contracts.py ──► Swift enums
                              │
        contracts/fixtures/*.json  ← replayed by pytest and decoded by Swift
```

`scripts/verify_contracts.py` parses Swift **textually**, so drift is caught on
Linux without Xcode. Fixtures run through the whole pipeline, which is why they
also guard dedup, refunds, and balance alerts.

---

## Repo layout

```
sms_wiz/
  models.py      event schema + ledger rules
  registry.py    Indian sender IDs, brand domains, Apple routing table, seed merchants
  normalize.py   Devanagari/Tamil/Telugu/... digits, rupee markers, phrases → ASCII
  extract.py     amounts, last4, references, dates, OTPs, URLs, PNRs, merchants
  forensics.py   DLT/sender/link mismatch scoring with named reasons
  classify.py    the ordered rule chain + enrichment
  catalog.py     the curated merchant dictionary: 753 keys over 333 brands, 21 categories
  merchants.py   self-learning SQLite dictionary (user > seed > vpa > llm > learned)
  templates.py   59 sentence shapes: 17 bank names plus generic bank/card/broker/travel/telco/utility
  analytics.py   month/week/UPI/category views plus demat, travel and bill views
  subscriptions.py  cadence, calendar, price changes, monthly commitment
  cards.py       statement ↔ payment reconciliation per card
  rules.py       issuer-authored filter DSL + validator + compiled artifact
  notify.py      push / digest / silent policy, quiet hours, dedupe
  redact.py      PII masking for anything leaving the device
  llm.py         optional OpenAI-compatible enrichment (off by default)
  pipeline.py    dedupe → persist → balances → refunds → vouchers → alerts
  store.py       SQLite queries + monthly rollup
  api.py         FastAPI surface
  cli.py         demo / serve / ingest / summary
scripts/         export_contracts.py (generate), verify_contracts.py (parity gate)
contracts/       generated schema + enums, plus fixtures both languages replay
.agents/skills/  six step-by-step procedures for changes this repo gets
tests/           467 tests
ios/             reference Swift (filter extension, App Intent, models)
docs/            iOS constraints, schema, filter-rule syntax, review findings
```

---

## Roadmap

- [x] Multilingual normalisation (digits, currency, phrases) with tests and a fixture.
- [x] Generated contract + shared fixtures + Swift parity check without Xcode.
- [ ] Share extension + clipboard import in the iOS app (the reliable paths).
- [ ] Merchant dictionary review UI backed by `GET/POST /api/v1/merchants`.
- [x] Subscription calendar from pre-debit notices, with conservative cadence.
- [x] Lender-authored rule syntax for the filter extension's offline pass (34 rules).
- [x] Merchant dictionary as data: 753 keys over 333 brands, unknown matches flagged.
- [x] Per-bank message templates with a real-message fixture corpus.
- [x] Weekly / monthly / UPI / card-payment analytics with one month definition.
- [x] Notification policy: what earns a push, what waits for the weekly digest.
- [ ] Swift `FilterRules.swift` evaluator (spec + compiled JSON exist; not yet compiled).
- [ ] Share extension + clipboard import inside a real iOS app.
- [ ] Export to CSV / a single JSON snapshot, so the ledger is never hostage.
- [x] Demat, travel and bill domains: trades, SIP allotments, dividends, IPOs,
      corporate actions, tickets, hotels, carrier and utility bills.
- [ ] More bank templates, with real messages as test fixtures (redacted).
