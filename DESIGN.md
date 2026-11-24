# DESIGN.md — what the user actually sees

Design here means **the surfaces that exist**, plus clear labels for the ones
that are only drawn. Anything marked *Proposed* has no code; do not describe it
to a user as working.

---

## 1. The iOS reality, as a design constraint

| iOS lets us | iOS does not let us |
| --- | --- |
| Sort one unknown-sender SMS at a time into 4 actions | Read the inbox, `sms.db`, or history |
| Use a fixed set of sub-actions (≤5 declared per group) | Create, rename, or reorder folders |
| Defer an uncertain message to our server | Delete, index, or search messages |
| Open the app / run a Shortcut / App Intent on user action | Run in the background on new messages |
| Show a Live Activity / notification | Touch iMessage |

Two consequences drive the whole UI:

1. **The filter is a bouncer, not a librarian.** It cannot build a library; it
   can only stop the wrong messages entering the normal inbox and put the rest in
   Apple's nearest folder.
2. **The ledger is fed by consent.** The user pastes, shares, or Shortcuts a
   message in. The app must therefore make *one message* feel worth it, and never
   pretend it has seen everything.

## 2. Surfaces

### 2.1 Filter folders (built: decision logic; extension written, not compiled)

```
Messages
├── All (unknown senders that passed)
├── Transactions      ← transactionalFinance / Orders / Reminders / Rewards / PublicServices
├── Promotions        ← promotionalOffers / promotionalCoupons / Others
└── Junk              ← phishing_suspect (quarantined, expires in 24h)
```

Rules of the surface:

- **Fail open.** If the network is slow or the backend errors, the message is
  allowed. A misfiled bank SMS is a worse failure than one spam in the inbox.
- **Never quarantine on a hunch.** Only the combination rule (brand + unknown
  sender + risky link) or an APK/executable link earns Junk.
- **Quarantined ≠ deleted.** The folder is a holding pen with a 24h retention;
  the app can tell the user *what* is in it and *why*.
- **No sub-action we did not declare.** iOS silently drops an undeclared
  sub-action, which looks like a bug to the user.

### 2.2 The app (Proposed screens, built data)

| Screen | Job | Data exists? |
| --- | --- | --- |
| **This month** | one number: real spend, what was excluded and why | yes (`/summary`) |
| **Timeline** | events by day, tap for evidence | yes (`/events`) |
| **Review queue** | the few things the rules refused to guess | yes (`/review`) |
| **Vault** | vouchers, warranty, invoices, expiring soon | yes (`/vouchers`) |
| **Upcoming** | NACH/AutoPay/EMI/bill calendar, shortfall warnings | partly (`upcoming_debit`, alerts) |
| **Cleanup** | "Junk: 34 messages · 8.2 MB · oldest 41 days" | yes (`/cleanup-queues`) |
| **Complaint** | TRAI 1909 / cyber 1930 draft, user reviews and sends | yes (draft text) |

**This month** is the screen that must never lie. It shows:

```
₹24,180   real spend
₹18,450   excluded · credit-card bill payment (internal transfer)
₹ 1,850   refunds matched
0         double counts
```

Each excluded row opens the reason, because trust in the number depends on the
user being able to audit it in one tap.

### 2.3 Visual language

Outlined in the built dashboard (`GET /` on the API):

- **Dark, calm, numeric.** One accent colour for money-in, one for money-out, red
  reserved exclusively for *risk* (phishing, shortfall, high-risk OTP).
- **Evidence is a first-class row**, not a tooltip: `sender · rule · confidence`.
- **Never colour-code a guess.** If `needs_review`, it is grey with a dotted
  border, not green.
- **Money is tabular**, rupees with Indian grouping (₹1,45,000), never cents.
- **Expiry is a countdown**, not a date the user has to do arithmetic on.

### 2.4 Notifications and Live Activities

| Trigger | Channel | Status |
| --- | --- | --- |
| Refund overdue | local notification | Built (alert object) |
| Debit shortfall vs last balance | local notification, critical severity | Built (alert object) |
| High-risk OTP (someone asks for the code) | notification | Built (alert object) |
| Delivery out for delivery | Live Activity | Proposed (iPhone 14 supports Live Activities, no Dynamic Island) |
| Bill due today | Live Activity | Proposed |
| Phishing quarantined | silent, digest only | Built (alert, no notification by design) |

Phishing is deliberately **not** a popup. A popup trains the user to tap; a
weekly digest line ("4 messages quarantined, all fake KYC links") trains them to
check.

### 2.5 App Intent / Siri / Shortcuts

```
"Add this SMS to SMS Wiz"     → IngestSMSIntent(text, sender) → notification of result
"SMS Wiz spend this month"    → summary via App Intent (Proposed query intent)
```

The ingest intent shows one line of result: `₹1,450 · Groceries · counted`.
If it was a duplicate: `Already counted from HDFC — skipped`. Honesty on the
happy path is what makes the dedup believable.

### 2.6 The complaint flow (TRAI 1909 / 1930)

```
pick message → choose ground (unsolicited commercial / fraud / impersonation)
   → review editable draft (sender, date, category, what happened)
      → copy / open the official channel yourself
```

- Nothing is sent automatically. There is no "submit" button that talks to TRAI.
- Fraud with money loss routes to the bank channel **and** 1930, with the
  transaction references pre-filled from the event.
- The copy never promises disconnection, refund, or a timeline.

## 3. Copy rules

- Speak in rupees and dates, not in internals: "refund ₹1,850 from Myntra was
  due 3 days ago", not "refund_credited timeout".
- Every excluded amount says *why*: "credit-card bill payment — a transfer
  between your own accounts, not spending".
- Every quarantine says *which signals* fired, in words a non-technical parent
  understands: "a number that isn't a bank asked you to install an app".
- Never claim coverage we lack: "this is what you've shared with SMS Wiz",
  never "your spending".

## 4. Accessibility and India-specific ergonomics

- Dynamic Type up to AX5 on all numeric rows; tabular figures keep alignment.
- VoiceOver labels read the amount as words ("one thousand four hundred fifty
  rupees"), because "1450.00" is read badly.
- Hindi/Devanagari and Tamil labels are shown for the message's own script when
  the UI language differs (see `LANGUAGE.md`); the *classification* is always
  canonical English internally.
- Motion: no confetti, no bouncing counters. This is a money tool.
- Offline: everything except the LLM works with no network.

## 5. What we will not design

- A "block this sender" toggle that implies iOS can block SMS. It cannot; the
  best it can do is Junk-foldering future messages from that header.
- A bulk-delete button. The app can size the queue; the user clears it in
  Messages.
- A "trust score" for people. Scores are for senders and links, never contacts.
- Auto-reply to a sender, in any flow, for any reason.
