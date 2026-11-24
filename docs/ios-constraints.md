# What iOS actually allows (and what it doesn't)

This document is deliberately blunt. Most of the fun architecture in this
project is only possible if some assumptions hold, so they are written down
here with a confidence level and a way to test them.

**Nothing in this document is changed by a paid developer account, sideloading,
an EU alternative app store, or running your own server.** Those change *how
long an app stays installed*, not *what an app is allowed to read*.

---

## 1. Hard limits (high confidence, no workaround)

| Thing | Status |
| --- | --- |
| Read the iPhone's SMS database (`sms.db`) or Messages history | **Impossible.** Kernel-level sandbox. No entitlement exists for third-party apps, jailbreak aside. |
| Receive carrier SMS directly from the modem | **Impossible.** Incoming SMS is delivered to Apple's Messages app by `CommCenter`. |
| Read iMessage content | **Impossible.** |
| Delete or bulk-delete messages in Messages | **Impossible.** Deleting a record inside your own app does not touch the Messages original. |
| Create your own folder inside Messages | **Impossible.** The filter extension can only return Apple's fixed categories (see §2). |
| Auto-dial TRAI 1909 or auto-file a complaint | **Not allowed / not sensible.** The app can prepare the complaint; the user sends it. |
| Read message open/read state, or which messages you replied to | **Impossible.** Sender-reputation features must work from what *you* import. |

Practical consequence: the **organiser** (finance ledger, voucher vault, refund
watchdog, bill reminders) can only run on messages that reached the app through
an allowed path. The **filter** can only sort messages, never store them.

---

## 2. The filter extension: what it can do

`ILMessageFilterExtension` (IdentityLookup) is the supported mechanism. When the
user enables your filter in **Settings → Messages → Unknown & Spam** and turns
on **Filter Unknown Senders**, iOS calls your extension for messages whose
sender is **not in the user's contacts** and asks for one decision.

```swift
func handle(_ queryRequest: ILMessageFilterQueryRequest,
            context: ILMessageFilterExtensionContext,
            completion: @escaping (ILMessageFilterQueryResponse) -> Void)
```

* Input: `queryRequest.sender`, `queryRequest.messageBody` (one message).
* Output: `response.action` = `allow | junk | promotion | transaction`
  and, on iOS 16+, `response.subAction`.

### Sub-actions, and the limit nobody mentions

Apple's fixed set (verified against the IdentityLookup headers):

```
transactionalOthers, transactionalFinance, transactionalOrders,
transactionalReminders, transactionalHealth, transactionalWeather,
transactionalCarrier, transactionalRewards, transactionalPublicServices,
promotionalOthers, promotionalOffers, promotionalCoupons
```

* You declare the ones you support in a **capabilities response**
  (`transactionalSubActions` / `promotionalSubActions`), **up to five per group**.
* A sub-action you did not declare is silently discarded and the message falls
  back to the broad folder.
* iOS 18 has multiple developer reports of folders disappearing when a large set
  is declared. This project therefore declares exactly five and two
  (`sms_wiz/registry.py:DECLARED_*`), and `effective_subaction()` refuses to
  promise anything else — the API never returns a folder the phone cannot show.
* If `action` and `subAction` don't belong together (e.g. `transaction` +
  `promotionalCoupons`), iOS keeps the action and drops the sub-action.

Because the categories are Apple's, you cannot create "Travel" or "Garage"
folders. Our internal taxonomy (travel, vehicles, health, vouchers…) lives in
the *app*, and only maps onto Apple's folders for the purpose of sorting.

### The network path

An extension with no network can still defer one decision to a server:

```swift
context.deferQueryRequestToNetwork { networkResponse, error in ... }
```

Reality check:

* The URL is declared in the extension's `Info.plist` and the domain must be
  claimed with the `messagefilter` associated-domain service, which is why this
  realistically needs a paid account. **Verify the exact plist key and
  associated-domain form against Apple's IdentityLookup docs** — this is the
  single most version-sensitive thing in this project.
* The deadline is short and the payload is small. This is our
  `POST /api/v1/filter` endpoint: rule-based only, no LLM, two keys out
  (`action`, `subAction`).
* Errors, timeouts and non-2xx responses mean the message must fall back to
  `allow`/`.none`. Our endpoint is written to be safely skippable.
* **Do not** send the full message history here. You only ever get one message,
  for unknown senders, and it is not a place to log everything.

### What this buys you, honestly

* Spam/promo/bank-upsell demotion and transaction sorting for unknown senders.
* Junk quarantine with the tab still open, so a false positive is recoverable.
* **Not** a way to clean the existing inbox, deduplicate bank SMS with app
  confirmations, or build a ledger. That is the organiser's job (§4).

---

## 3. Shortcuts: useful, but test it before promising it

The "Message" personal automation ("When I receive a message…") can run a Shortcut
automatically. Whether the automation exposes the **message body** to the
Shortcut, and whether that body can be handed to your App Intent, has varied by
iOS version and by automation type. Treat it as an experiment:

**15-minute test on your own iPhone 14**

1. Shortcuts → Automation → **+** → *Message*.
2. Condition: `Message contains` = `debited`. Set **Run Immediately**
   (no confirmation) if offered.
3. Action: **Run Shortcut** with a shortcut containing only **Quick Look**
   (so you can see what input it received) or **Get Details of Messages**.
4. Send yourself a test SMS from another phone.
5. Read the result:
   * You see the body → the bridge works; wire it to the App Intent in
     `ios/IngestSMSIntent.swift`.
   * You see only sender/timestamp, or the automation needs a tap → treat the
     bridge as **user-assisted**, and rely on the fallbacks in §5.

Also verify: whether the automation fires when the phone is locked, whether it
fires for messages from unknown senders, and whether it fires for the *filtered*
(junk) tray. Record the answers here for your device + iOS version:

```
device: iPhone 14   iOS: ___
message automation exposes body: yes / no / partial
runs while locked: yes / no
fires for junked messages: yes / no
```

A Shortcut running `Get Contents of URL` cannot be the whole product: it has a
short execution budget, and iOS may throttle or cancel background automations.
Design the app so a missed automation is recoverable (import on next open).

---

## 4. Paths that definitely work (no iOS version roulette)

| Path | Effort | Notes |
| --- | --- | --- |
| **Paste into the app** | none | Ugly, but 100% reliable and great for the first week of tuning. |
| **Share sheet → app** (Share Extension) | low | Long-press a message → Share → your app. Works for one message or a selection, depending on iOS. |
| **Copy → app reads clipboard** | low | User copies a message; the app offers "import from clipboard". |
| **Shortcuts, manual run** | low | A shortcut with a "Message" input the user taps. Always works. |
| **Shortcuts, automation** | medium | The §3 experiment. Nice when it works, must be optional. |
| **Message filter + network** | high | Sorting only, no content retention. |

The server API is intentionally message-shaped, so all of these hit the same
`POST /api/v1/ingest-sms`. Nothing in the ledger assumes the source.

---

## 5. Live Activities and widgets

* `ActivityKit` Live Activities: **yes**, Lock Screen, iOS 16.1+, iPhone 14
  included (Dynamic Island pinning is 14 Pro / 15 Pro only).
* The **filter extension cannot start one.** Live Activities must be started by
  the containing app (or an App Intent / push-to-start where available).
* Given §3, the honest design is: the app starts a Live Activity *when it is
  open or when an App Intent fires*, not "magically on every SMS".

Good uses that survive that constraint: an active train journey, an open
out-for-delivery parcel with its handover code, an EMI shortfall you have not
resolved yet.

---

## 6. Sideloading and distribution

* **Free Apple ID**: 7-day provisioning expiry per app. Fine for a prototype.
  (SideStore/AltStore can re-sign over Wi-Fi; still no extra entitlements.)
* **Paid account ($99/yr)**: no 7-day expiry, App Groups, associated domains,
  and therefore the network filter path. Still no SMS database access, and still
  no way to make the extension delete messages.
* **EU alternative marketplaces**: change *distribution and notarisation*, not
  the sandbox. They do not give SMS access and do not remove the need for a
  developer account to notarise.
* **App Store review**: a message-filter + personal-finance organiser is a
  legitimate app category. Claiming to read SMS, or shipping an app whose core
  promise is impossible, is a rejection (and a support nightmare) either way.

---

## 7. Feature → feasibility map

| Planned feature | Automatic? | How it really works |
| --- | --- | --- |
| Spam / promo sorting | ✅ (unknown senders, opt-in) | Filter extension + network deferral |
| Junk quarantine | ✅ | `.junk` action, user-visible, recoverable |
| Bulk delete clean-up | ⚠️ assisted | App shows which Apple folder to clear and how many; user taps Delete All |
| Finance ledger, zero double counting | ⚠️ needs import | Paste / share / Shortcut → server → app |
| Refund watchdog, voucher vault, EMI shortfall | ⚠️ needs import | Same |
| Bill reminders, Live Activities | ⚠️ needs import | Local notifications + ActivityKit from the app |
| "Deep OTP classification" | ✅ when imported | Redacted, on-device; never cloud-sent |
| Scan links / APK traps | ⚠️ mixed | Analysis is offline; unshortening must be an explicit user action |
| TRAI 1909 complaint | ⚠️ assisted | App drafts the exact string; user sends from Messages |
| Household forward of delivery codes | ❌ | Not possible without reading unknown messages automatically; drop or make it a manual share |

Anything marked ⚠️ is still worth building: the value is in the classification
and the ledger, not in the delivery mechanism.
