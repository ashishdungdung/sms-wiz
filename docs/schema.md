# The event schema

Every SMS becomes exactly one `SmsEvent` (`sms_wiz/models.py`). The shape is
designed so that three questions are always answerable without re-parsing the
text:

1. **What happened?** — `intent`, `amount`, `merchant`, `reminder`, …
2. **Does it move the user's money totals?** — `ledger`
3. **Where should it go on the phone, and for how long is it useful?** — `routing`

Plus `evidence` / `confidence` / `flags`, which are not decoration: the UI is
supposed to show them.

**This document is prose. The machine-readable contract is generated:**
`contracts/sms_event.schema.json` and `contracts/enums.json` come from the model
via `make contracts`, and `make verify` fails if the Swift mirror drifts. Accept
cases live in `contracts/fixtures/` and are replayed through the whole pipeline
(`tests/test_fixtures.py`). If this page and the generated schema disagree, the
schema wins — then fix this page.

---

## Example

```jsonc
{
  "event_id": "evt_f6d6c079de74",
  "received_at": "2025-03-01T10:00:00+05:30",
  "source": "shortcut",
  "sender_raw": "AD-HDFCBK-T",
  "body": "Rs.1450.00 debited from A/c XX4092 ... Convert this spend into EasyEMI! bit.ly/hdfcemi",
  "redacted_body": "Rs.1450.00 debited from A/c XX4092 ... (UPI Ref <number>) ... bit.ly/hdfcemi",

  "sender": {
    "raw": "AD-HDFCBK-T", "operator": "AD", "entity_code": "HDFCBK",
    "dlt_suffix": "T", "entity_kind": "bank", "display_name": "HDFC Bank",
    "is_known_brand": true, "notes": ["DLT suffix -T: transactional header"]
  },

  "intent": "expense",
  "domain": "finance",
  "amount": { "value": 1450.0, "currency": "INR", "raw": "Rs.1450.00",
              "direction": "debit", "balance_after": 5000.0 },
  "institution": { "name": "HDFC Bank", "kind": "bank", "account_last4": "4092" },
  "merchant": { "raw": "KIRANAKART", "clean": "Zepto",
                "category": "Groceries & Quick Commerce", "source": "seed" },

  "ledger": { "treatment": "expense", "counts_toward_spend": true,
              "counts_as_income": false, "reduces_spend": false,
              "reason": "real outflow" },

  "routing": { "apple_action": "transaction", "apple_subaction": "transactionalFinance",
               "retention": "keep_forever", "safe_to_bulk_delete": false,
               "rationale": "money movement, audit trail" },

  "confidence": { "score": 0.9, "band": "high", "needs_review": false },
  "dedup": { "status": "unique", "duplicate_of": null, "reason": "" },
  "flags": ["embedded_upsell"],
  "evidence": [
    "debit verb 'debited' with amount",
    "message also contains a lending/EMI pitch; the transaction is still counted once"
  ],
  "summary": "₹1,450.00 spent at Zepto"
}
```

---

## `intent`

What the message is *about*. One per message, chosen by an ordered rule chain
(`sms_wiz/classify.py`):

```
phishing → cc_bill_payment → refund → dividend / ipo / corporate_action / trade
→ travel → logistics → otp → fastag → instrument_load → atm → investment
→ upcoming_debit → bill_due → voucher → govt → expense/income → bank_upsell
→ commercial_promo → unknown
```

Order is the design. Examples of what it protects:

* `PHISHING_SUSPECT` beats everything: no amount, link or sender claim outranks a
  high-confidence scam signal.
* A delivery handover code is `LOGISTICS`, not `OTP` — that is a code you must
  read out, not a code you must protect.
* A FASTag toll is `FASTAG_TOLL`, a FASTag recharge is `INSTRUMENT_LOAD`.
* Demat comes before travel, and travel before logistics: "10 shares of INFY
  executed" moves units, not rupees, and a PNR belongs to a journey while an AWB
  belongs to a parcel. A bus ticket with a PNR is `TRAVEL`, not a train.
* A plan for `TRAVEL` carries `logistics.mode` (`train`, `bus`, `flight`,
  `hotel`), the PNR, the route and the passenger count. `TRADE` carries
  `trade.kind` (`buy`, `sell`, `sip`, `ipo_apply`, `dividend`, `bonus`…), the
  ISIN, the quantity and the *price* — never a payment, because the bank message
  for that payment arrives separately and is the one that may count.

## `trade` — shares and units

| field | meaning | example |
| --- | --- | --- |
| `kind` | buy / sell / sip / ipo_apply / ipo_allot / dividend / bonus / split / rights | `buy` |
| `symbol` | ticker, when the message names one | `INFY` |
| `scheme` | mutual-fund scheme, when the message names one | `Parag Parikh Flexi Cap` |
| `isin` | ISIN | `INE009A01021` |
| `quantity` | units or shares | `10` |
| `price` | per-unit price, not the total | `1450.0` |
| `broker` | broker or AMC that sent it | `Zerodha` |
| `folio` | folio number when present | `12345` |

A per-unit price is never promoted to `amount`: "NAV Rs 75.20" is not a payment,
and recording it would count ₹75.20 of money that never moved.
* A bank's loan pitch reaches `BANK_UPSELL` only after every real-money rule has
  declined. A real debit that *also* advertises EMI stays `EXPENSE` and gets the
  `embedded_upsell` flag.

## `ledger` — the zero-double-counting contract

| intent | treatment | spend | income | why |
| --- | --- | --- | --- | --- |
| `expense`, `fastag_toll` | `expense` | ✅ | | real outflow |
| `income` | `income` | | ✅ | money received |
| `refund_credited` | `refund` | | | reduces earlier spend, not income |
| `cc_bill_payment` | `internal_transfer` | | | already counted at swipe time |
| `self_transfer` | `internal_transfer` | | | own accounts |
| `instrument_load` | `instrument_load` | | | wallet/FASTag/UPI Lite is still your money |
| `cash_withdrawal` | `cash_withdrawal` | | | cash is still your money |
| `investment` | `investment` | | | capital movement, not consumption |
| everything else | `none` | | | no money moved |

The two most common Indian inaccuracies this prevents:

1. **Paying a credit-card bill** is a ₹18,450 transfer, not ₹18,450 of new
   spending on top of the swipes that created the bill.
2. **Loading a wallet / FASTag / UPI Lite** is not spending until it is spent.

`counts_toward_spend` is also `false` for any event whose `dedup.status` is
`suppressed_duplicate`, so replaying the same message never inflates a total.

## `dedup`

| status | meaning | summed? |
| --- | --- | --- |
| `unique` | first time seen | yes |
| `suppressed_duplicate` | same reference (12-digit UPI RRN / UTR) already recorded | **no** |
| `possible_duplicate` | same amount + same day + same instrument, different sender, no shared reference | yes, but `needs_review = true` |

Amount alone never merges two events. Two ₹500 coffees on one day are two
coffees. When the evidence is weak, the row is kept and a human is asked.

## `routing`

`apple_action` is one of `allow | junk | promotion | transaction` — the only
four values iOS accepts.

`apple_subaction` is one of Apple's fixed identifiers, or `none` when the folder
was not declared. See `docs/ios-constraints.md` §2 for why this is capped at
five transactional + five promotional, and why we declare fewer.

`retention` is advisory for the user's clean-up habit, since no app can delete
messages:

| retention | meaning |
| --- | --- |
| `keep_forever` | audit trail (money, government, health) |
| `expire_30d` | receipts with short-term value (tolls, loads) |
| `expire_7d` | promo/offer bulk |
| `expire_24h` | OTPs and other minutes-long artefacts |
| `delete_after_event` | keep until the delivery arrives / the debit happens |
| `review` | not enough evidence to recommend anything |

`safe_to_bulk_delete` is `true` only when nothing in the message needs reading
before it goes. A quarantined junk message qualifies; a suspicious-but-allowed
message does not.

## OTPs, vouchers and other secrets

* `otp.code`, `voucher.code` and `voucher.pin` are stored, because that is the
  point of the vault.
* They are **always** masked in `redacted_body` and never appear in `summary`.
* Any LLM enrichment receives `redacted_body` only (`sms_wiz/redact.py`), with
  OTPs, codes, PAN, Aadhaar-length numbers, phone numbers, e-mail addresses, VPA
  local parts and URLs replaced by placeholders.

## Confidence

`score` is the rule's own certainty (0–1). `band` is `high ≥ 0.8`,
`medium ≥ 0.6`, `low below`. `needs_review` is `true` below 0.6, and is also
forced on for `possible_duplicate`, `suspicious_link` and LLM intent hints.

The UI contract: show `needs_review` items in a "Verify" queue with their
`evidence`, and let the user reclassify. A reclassification is stored as a user
correction (merchant dictionary `source = "user"`), which outranks everything
else.
