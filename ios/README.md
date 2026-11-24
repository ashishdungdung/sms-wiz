# iOS reference implementation

**None of the Swift in this folder has been compiled or run.** It exists so the
project shape is concrete and so the server contract has an honest client. Treat
it as a starting point to check against your Xcode SDK, especially the two
version-sensitive points called out at the bottom.

Read `../docs/ios-constraints.md` first. Short version: the filter extension
sorts unknown-sender SMS into Apple's folders and nothing else; the app builds
the ledger from messages that reach it by paste / share / Shortcut.

```
ios/
  SMSWiz/SMSWizApp.swift                the app: Ledger / Import / Merchants / Settings
  SMSWiz/LedgerView.swift               this month, straight from /api/v1/overview
  SMSWiz/ClipboardImportView.swift      paste → preview → import (the reliable path)
  SMSWiz/MerchantReviewView.swift       name what the ledger could not name
  SMSWiz/SettingsView.swift             server, token, offline queue, CSV/JSON export
  SMSWiz/ShareViewController.swift      share extension: Messages → Share → filed
  SMSWiz/IngestSMSIntent.swift          App Intent (Shortcuts bridge)
  SMSWiz/Models.swift                   Decodable mirror of the API (screens)
  ShareExtension/Info.plist             the plist for the share extension target
  MessageFilterExtension/…              ILMessageFilterExtension
  Packages/SMSCore/                     shared, platform-neutral:
      Contracts.swift                     enums mirrored from the Python models
      FilterRules.swift                   the offline rule evaluator
      APIClient.swift                     every request the app and extension make
      AppConfig.swift                     server + token in the App Group, offline queue
      PasteParser.swift                   clipboard blob → (sender, body) pairs
```

---

## Wiring it up

1. **App Group** on both targets: `group.com.example.smswiz` (change it
   everywhere: the Swift constant, both entitlements files, and the app's
   settings). The extension and the app share only through this container.

2. **Message Filter Extension target** → File ▸ New ▸ Target ▸ *Message Filter
   Extension*. Paste `MessageFilterExtension.swift` in. Keep the deployment
   target at **iOS 16.0+** if you want sub-actions; on iOS 15 only the four
   top-level actions exist and the sub-action declarations are ignored.

3. **Associated domain** for the network path — the target's entitlements need
   the `messagefilter` service, e.g.

   ```xml
   <key>com.apple.developer.associated-domains</key>
   <array>
       <string>messagefilter:filter.yourdomain.tld</string>
   </array>
   ```

   and the server must serve the association file from that host. This is what
   actually requires the paid developer account.

4. **Filter service URL** in the extension's `Info.plist` under the
   `NSExtension` attributes, pointing at `https://filter.yourdomain.tld/api/v1/filter`.
   ⚠️ **Verify the exact key name against Apple's IdentityLookup documentation
   for your SDK.** It is the one line in this repo most likely to be wrong, and
   the failure mode is silent (the deferral never happens).

5. **Share Extension target** → File ▸ New ▸ Target ▸ *Share Extension*. Replace
   its `Info.plist` with `ShareExtension/Info.plist`, add
   `SMSWiz/ShareViewController.swift`, and link **SMSCore** from the extension as
   well as the app (Package Dependencies → SMSCore → both targets). Give the
   extension the same App Group as the app. That is the whole wiring: in
   Messages, long-press a bubble → Share → "File with SMS Wiz".

6. **App Intent**: add `IngestSMSIntent.swift` + `Models.swift` to the app
   target, enable *App Intents* in capabilities. In Shortcuts the action appears
   as "Analyse SMS with SMS Wiz" — with two text parameters, so it works with a
   manual shortcut, a share-sheet shortcut, or an automation.

7. **Server settings** live in the App Group (`serverBaseURL`, `serverToken`),
   written from an in-app settings screen. Do not ship a token in the bundle.

8. **Local rules** (`blockedSenders`, `junkKeywords`, `promoKeywords`) are also
   App Group strings arrays, written by the app's rule builder. The extension
   applies them before any network call, so user rules always win and always
   work offline.

---

## The two import paths, and why they are the reliable ones

The filter extension cannot read a message body — Apple forbids it — and the
Shortcuts "Message" automation may not expose the body either (test it with
`../docs/ios-constraints.md` §3). What always works is the user handing the text
over, so both paths are first-class:

| Path | What happens | Where it lives |
| --- | --- | --- |
| **Paste** | Select bubbles in Messages → Copy → Import tab → "Paste from clipboard" → review the parsed list → Import | `ClipboardImportView.swift` + `PasteParser.swift` |
| **Share** | Long-press a bubble → Share → "File with SMS Wiz" | `ShareViewController.swift` + `ShareExtension/Info.plist` |
| Shortcut | Manual or automation, two text parameters | `IngestSMSIntent.swift` |

Both paths end in the same client call and obey the same three rules:

1. **Queue first, send second.** Whatever the user shared is written to the App
   Group (`AppConfig.appendPending`) *before* the network call. A share extension
   gets seconds of runtime and can be killed at any moment; a bank SMS that
   disappears is not recoverable.
2. **Never invent a sender.** `PasteParser` recognises DLT headers
   (`AD-HDFCBK-T`, `VM-HDFCBK`, `SBICRD-T`), short codes and Indian mobile
   numbers. A body with no header is imported with an empty sender and the server
   marks it `unknown`.
3. **Say what happened.** Every imported message gets a line in the "What
   happened" list — filed, already recorded, queued, or refused and why.

### Merchant review

`MerchantReviewView` reads `GET /api/v1/merchants/unresolved` — the merchants the
ledger recorded but could not name, ranked by frequency, with the dictionary's
current best guess — and posts corrections to `POST /api/v1/merchants`. The
server renames the events already stored (`events_updated` in the answer), which
is why the list shrinks instead of pretending the past did not happen.

### Getting the data out

Settings → "Your data" fetches `GET /api/v1/export.csv` or `.json` and hands the
file to the system share sheet. On the server the same thing is
`sms-wiz export --format csv|json`, so the phone is never the only way out.

---

## What the filter may and may not do (repeat, because it matters)

* Runs only for senders **not in the user's contacts**.
* One message at a time; no history, no conversation context.
* Returns `allow | junk | promotion | transaction` and, on iOS 16+, one of the
  sub-actions the app declared in its capabilities response (max five per group).
* Cannot delete, cannot create folders, cannot store messages.
* On timeout/error the right answer is `allow` (fail open) — a wrongly junked
  bank alert is worse than a leaky promo.

`../sms_wiz/registry.py` is the single source of truth for which sub-actions are
declared; `MessageFilterExtension.swift` must stay in sync with it, and
`GET /api/v1/apple/capabilities` reports the same list at runtime.

---

## The offline rule evaluator (`FilterRules.swift`)

`FilterRules.swift` decodes `contracts/filter_rules.json` and answers the same
question as `sms_wiz/rules.py`, inside the extension, with no network and no
Python. Three things make that safe to attempt at all:

* **One compiled artifact.** Both evaluators read the same file; the extension
  ships it as a bundle resource (`filter_rules.json` in Copy Bundle Resources),
  and `sender_kinds.json` beside it. Add both as *references* — Xcode links the
  generated file from `contracts/` rather than copying it into the repo, so a
  regeneration cannot leave a stale second copy behind.
* **Generated parity fixtures.** `scripts/gen_rule_fixtures.py` writes
  `tests/fixtures/filter_rules/cases.json` — 38 cases covering all 34 rules plus
  four hand-written edges (a plain debit, an OTP, a promo that contains a money
  word, and a case-insensitivity check). The Python suite replays them, and
  `FilterRulesParityTests` replays the same file through the Swift evaluator. Add
  a rule without regenerating and `make verify` fails.
* **Fail open, everywhere.** A missing file, malformed JSON, an unknown DSL
  version or an unknown action all end in `allow`. The failure mode of a broken
  rules file must be a leaky promo folder, never a junked bank alert.

Two rules are shadowed by earlier ones in the authoring file
(`otp.no_share_warning` is reachable only through `otp.never_junk`); the fixture
records that with `shadowed_by` rather than pretending otherwise.

## Status of the iOS work, honestly

| Piece | Status |
| --- | --- |
| `SMSCore` models, contracts, app config, queue, paste parser, API client | **Written, not compiled** — Swift package, no toolchain here |
| `FilterRules.swift` evaluator | **Written, not compiled**; algorithm checked against Python by transliteration (0/38 mismatches), Swift itself not built |
| `SenderKind.swift` (header → entity kind, offline) | **Written, not compiled**; mirror of `parse_sender` checked over 1,197 senders from the registry, 0 mismatches — `contracts/sender_kinds.json` is the data, `tests/test_contracts.py` keeps it honest |
| Filter extension, share extension, app screens | **Written, not compiled**; needs an Xcode target |
| Server side of every call they make | **Built and tested** (551 tests) |
| Xcode project, signing, entitlements, App Group registration | **Proposed** — cannot be checked in as a buildable artifact without signing |

Run `make swift-test` on a machine with Swift (macOS, or Linux with
swift-corelibs-foundation) to turn the first three rows into "built and tested".

---

## Verify these two things before trusting the design

1. **Does the Shortcuts "Message" automation expose the body?**
   `docs/ios-constraints.md` §3 has the 15-minute test. If it does not, the
   Shortcuts bridge is manual and the share extension becomes the primary
   import path.

2. **Does the network filter actually fire?**
   Log in `deferQueryRequestToNetwork`'s completion, then send yourself a test
   SMS from an unknown number. If you see no call at all, the Info.plist key or
   the associated domain is wrong — not the server. Confirm the server by
   hitting `/api/v1/filter` with `curl` first:

   ```bash
   curl -s localhost:8000/api/v1/filter \
     -H 'content-type: application/json' \
     -d '{"sender":"9876543210","messageBody":"Your SBI KYC is pending, verify at http://sbi-kyc.xyz/a"}' | jq
   # {"action":"junk","subAction":"none"}
   ```
