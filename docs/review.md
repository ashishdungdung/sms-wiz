# Review: what three adversarial probes found

This is the record of a deliberate attempt to break this project, not a feature
log. It is written so that the next person can rerun the same attacks, see the
same results, and tell which answers are protected by a test.

Status vocabulary used throughout, because "it works" is not one word:

- **Built and tested** — code plus a test in `tests/`, run by `make verify`.
- **Written, not compiled** — Swift exists in `ios/`, but no toolchain ran it here.
- **Proposed** — a decision written down, no code yet.

---

## How it was attacked

Three throwaway scripts, each aimed at a different failure mode.

| Probe | Target | Shape |
| --- | --- | --- |
| 1 | the classifier | 43 real-shaped Indian SMS: bank, UPI, card, ATM, OTP, phishing, Unicode, mojibake, 2G-era typos, a 4,000-character body, a body of only emoji |
| 2 | the state layer | one month of overlapping events: a card statement plus its payment plus spends after the statement, two ₹2,000 ATM withdrawals on one evening, refunds claimed and credited at the wrong figures, balances that must reconcile, duplicate billers |
| 3 | the API | empty bodies, wrong types, null bytes, 100 kB bodies, `limit=-1`, `month=2026-13`, 8 threads writing at once, 200 sequential messages, and every route with no token, a wrong token, and the right one |

The scripts themselves are not in the repository: they are a one-off attack, and
what they proved is now pinned down by tests instead. What follows is every
finding that needed a code change, and every finding that was deliberately left
alone.

---

## Findings that were fixed

### Extraction

| Symptom | Why it happened | Fix |
| --- | --- | --- |
| `find_last4("4111 1111 1111 1111")` returned `4111` | the generic "4 digits" rules ran before the long-number rule | a long digit run is checked first and its **last** four digits win. Also a privacy fix: the old answer kept a PAN-like prefix where the user expects only the tail |
| `USD 12.99 spent … OPENAI` was booked as ₹12.99 of Indian spending | the amount parser had no currency ledger | `LedgerTreatment.FOREIGN_CURRENCY`, excluded from rupee totals and named in `excluded_reasons`, `needs_review=True` until the bank posts the converted figure |
| `on 31-02-26` silently became today | the calendar rejected it and the fallback said nothing | `date_unparseable` flag + `needs_review`; the arrival date is kept, and the reason is stored in `analysis` |
| A purchase dated `25-02-25` but delivered on 1 March was filed in March | the message date was only used for travel | for money movements, a plausible **past** date inside 45 days is the event date (`dated_in_message`); a future date is a journey, not a purchase |
| `Rs.900 credited towards refund` produced a merchant called "refund" | "towards <word>" was read as a payee | movement words are stop-words for merchant extraction |

### Classification

| Symptom | Why it happened | Fix |
| --- | --- | --- |
| A reversal counted as **income** (or as a second expense) | "reversed" was not refund language | reversal language is handled explicitly: "has been reversed" → refund credited, "will be reversed" → refund promised. Neither touches spend or income |
| A reversed debit was counted as *new* spending | the debit keyword won | the same rule covers it; the original debit message keeps the spend |
| A card bill payment named an SBI card but was filed under the sending bank | the card owner was taken from the SMS header | `cards._issuer()` — the issuer **named in the text** wins, the sender is the fallback. `Payment … towards your SBI Card XX4433` from HDFC is one SBI card, `paid` |

### Ledger and dedupe

| Symptom | Why it happened | Fix |
| --- | --- | --- |
| Two genuine ₹2,000 ATM withdrawals on one evening collapsed into one | identical-text dedupe spanned the whole day | the window is 30 minutes, measured on the **message's** timestamp (an archive import is not "just now") |
| A card spend at a shop and the bank debit that settles it were both counted, silently | possible-duplicate needed the same instrument | same amount + same day + same merchant + different sender is now `possible_duplicate` with `needs_review`. Still never merged |

### Refund tracker

| Symptom | Why it happened | Fix |
| --- | --- | --- |
| A ₹1,500 credit closed nothing; the ₹500 claim stayed open forever | exact-amount matching | `find_covering_refund`: a credit at least as large, same merchant, claim younger than 45 days closes it **with a warning** naming the extra money |
| A refund promise that never stated an amount was not tracked at all | `and event.amount` | stored as amount 0 = "unknown", matched by merchant only |
| The claim row had `merchant=None` and read "from the merchant" | only the dictionary name was stored | `refund_merchant()`: clean name → raw name → the sender's brand (never a bank) |
| A credit "towards refund" conflicted with its own claim | merchant "refund" ≠ "Amazon" | stop-words above; merchant matching also tolerates `Amazon` vs `Amazon Pay India` |

### API

| Symptom | Why it happened | Fix |
| --- | --- | --- |
| `GET /api/v1/events` returned a bank statement with **no token** | the token was checked per handler, so every new route was open by default | one `@app.middleware("http")` gate on `/api/*`; `/` and `/health` stay open so the phone can find the server. Compared with `hmac.compare_digest` |
| `POST /api/v1/filter` with no body answered 4xx | the endpoint raised on a malformed request | fail open: `{"action":"allow"}` with a `reason`. A filter that errors is a filter that stops filtering |
| `limit=-1` was accepted, `month=2026-13` returned "you spent nothing" | no validation | `limit` is `ge=1, le=500`; every month parameter must be `YYYY-MM`, otherwise 422 |

### Storage

| Symptom | Why it happened | Fix |
| --- | --- | --- |
| An existing database would keep the old column set forever | `CREATE TABLE IF NOT EXISTS` is a no-op on an existing file | `migrate()` adds missing columns before the schema script runs (which creates indexes on them), and the connection turns on WAL + `busy_timeout` |

### Merchants

| Symptom | Why it happened | Fix |
| --- | --- | --- |
| `KFC`, `JIO`, `LIC`, `NSE`, `PVR`, `OYO`, `MMT`, `CCD` never matched | minimum key length was 4 | minimum is 3 (2 for an explicit allow-list: `6E`, `PW`). The real guard is the whole-word match, which stays |
| Coverage said 753 keys, `cli merchants` said 682 | three different numbers were being called "keys" | coverage reports *declared / distinct after normalising / matchable* |

Counts after this pass: **753 declared keys, 716 distinct, 716 matchable, 333 brands, 21 categories.**

### Analytics

- **Month ranges ended in impossible dates.** `month_totals` built its window with
  `f"{month}-31"`, so November reported `2026-11-31` and February `2026-02-31`.
  The ledger totals were right — the *dates* were not, and any client that parses
  them rejects the month or displays a day that does not exist. Found while
  writing the Swift `MonthView` decoder. Now `calendar.monthrange`, with
  `TestMonthRange` pinning leap and non-leap February.

### Console

| Symptom | Why it happened | Fix |
| --- | --- | --- |
| `cli serve` bound `0.0.0.0` by default, so a laptop on café Wi-Fi published a bank ledger with no password | the host was hard-coded | default `127.0.0.1`, `--host`/`HOST=` to opt in, and a stderr warning when exposed without `SMSWIZ_TOKEN` |

---

## Findings that were deliberately left alone

- **Different senders, same amount, no other signal.** Two ₹200 payments on one day
  are not merged and not flagged. Amount alone is never evidence.
- **A refund credit larger than the claim still counts in full.** The money
  arrived; refusing to count it would understate refunds. The *claim* is closed
  with a warning instead.
- **A card and an account with the same last four digits stay separate cards.**
  Merging them would be a confident wrong answer.
- **`SMSWIZ_TOKEN` is one shared secret.** No rotation, no per-device token, no
  rate limit. On a LAN this is proportionate; it is not a public-internet story.

## Known limits

| Limit | Status | Note |
| --- | --- | --- |
| Money is stored as SQLite `REAL` | Built and tested | every total is rounded at the boundary; a paise-integer store would be stricter, and is a schema migration, not a bug fix |
| Swift side of `LedgerTreatment`/`FilterResponse` | Written, not compiled | contracts + `verify_contracts.py` prove parity; no Swift toolchain ran here |
| LLM enrichment against a live endpoint | Written, not tested here | off by default, and the suite asserts it stays off |
| Short brand keys match as whole words only | Built and tested | `jio.com` does not match `JIO`; that is intended |
| Refund cover window of 45 days | Built and tested | longer than any Indian refund SLA; measured from the credit's own timestamp |
| The iOS app, share extension and filter/rule evaluators | Written, not compiled | no Swift toolchain here; see `ios/README.md` for the status table and `make swift-test` for the one command that would change it |
| Offline sender-kind resolution | Written, not compiled | `contracts/sender_kinds.json` mirrors `parse_sender` over 1,197 senders with 0 mismatches by transliteration; the Swift side has not run |
| Export endpoints | Built and tested | CSV is formula-safe and RFC 4180; JSON is one round-trippable document. Neither has a retention promise: the file is the promise |

## How to rerun the attack

```bash
make verify                       # contracts current, rule fixtures current, 551 tests
./.venv/bin/python -m sms_wiz.cli demo
SMSWIZ_TOKEN=s3cret ./.venv/bin/python -m sms_wiz.cli serve   # then: no token → 401
```

Every row above marked *Built and tested* has a test in
`tests/test_review_regressions.py` (grouped by layer, not by commit), except the
storage, console and coverage rows, which live in `tests/test_api.py`,
`tests/test_cli.py` and `tests/test_merchants.py` next to the behaviour they
protect.
