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
  MessageFilterExtension/MessageFilterExtension.swift   ILMessageFilterExtension
  SMSWiz/IngestSMSIntent.swift                          App Intent (Shortcuts bridge)
  SMSWiz/Models.swift                                   Decodable mirror of the API
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

5. **App Intent**: add `IngestSMSIntent.swift` + `Models.swift` to the app
   target, enable *App Intents* in capabilities. In Shortcuts the action appears
   as "Analyse SMS with SMS Wiz" — with two text parameters, so it works with a
   manual shortcut, a share-sheet shortcut, or an automation.

6. **Server settings** live in the App Group (`serverBaseURL`, `serverToken`),
   written from an in-app settings screen. Do not ship a token in the bundle.

7. **Local rules** (`blockedSenders`, `junkKeywords`, `promoKeywords`) are also
   App Group strings arrays, written by the app's rule builder. The extension
   applies them before any network call, so user rules always win and always
   work offline.

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
