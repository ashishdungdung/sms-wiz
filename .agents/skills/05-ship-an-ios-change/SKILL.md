---
name: 05-ship-an-ios-change
title: Change the filter extension, App Intent, or Swift models
summary: Work in Swift without the Xcode GUI, keep the filter inside Apple's limits, and be honest about what has been compiled
---

# Skill 05 — Ship an iOS change

## When to use

- Changing the message filter extension's decision or capabilities.
- Changing the App Intent / Shortcuts path.
- Changing `SMSCore` models or `ios/SMSWiz/Models.swift`.
- Wiring a new backend field into the app.

## Before you touch Swift

1. Re-read `docs/ios-constraints.md`. The limits are not negotiable:
   - unknown-sender SMS/MMS only; no inbox, no history, no deletion, no iMessage;
   - 4 actions; fixed sub-actions; ≤5 declared per group;
   - a sub-action that was not declared is silently dropped;
   - network only via `deferQueryRequestToNetwork`, which needs the
     `messagefilter:` associated domain.
2. Decide which part you are changing:

   | Part | File | Testable on Linux? |
   | --- | --- | --- |
   | Shared models/enums | `ios/Packages/SMSCore/Sources/SMSCore/*.swift` | yes (`swift test`) |
   | Filter decision | `ios/MessageFilterExtension/MessageFilterExtension.swift` | partly — pure helpers only |
   | App Intent | `ios/SMSWiz/IngestSMSIntent.swift` | no (needs the app target) |
   | App DTOs | `ios/SMSWiz/Models.swift` | no, but `make verify` checks enums textually |

## Procedure

1. **Put logic in `SMSCore` if it can be pure.** Anything that decides *what a
   message is* or *what the response shape is* belongs in the package, so it can
   be tested without a simulator. The extension should be a thin adapter.
2. **Never hardcode the backend URL** outside `AppConfig`. It is already a single
   constant; keep it that way.
3. **Fail open.** Any error, timeout, or unexpected status returns
   `ILMessageFilterAction.none` (allow). Mis-filing a bank SMS is worse than one
   spam in the inbox.
4. **Keep the response small.** The extension sends `{action, subAction}` and gets
   the same back. Do not add fields the extension cannot use.
5. **Sub-action changes**: update `AppleSubAction.declared*`,
   `MessageFilterExtension.capabilities`, and `docs/ios-constraints.md`
   together. `ContractsParityTests` asserts the ≤5 limits.
6. **Run what you can**:
   ```bash
   make swift-test     # SMSCore parity + model tests
   make verify         # enum parity against Python
   ```
7. **State honestly what you did not run.** "Written, not compiled — no Xcode on
   this machine" is a required sentence when it is true. Do not claim a build
   succeeded because the source looks right.

## Building without the Xcode GUI (proposed flow)

The repo is designed for `swift build` + XcodeGen, but the project spec and the
build pipeline are **not written yet**. Until they are:

- `ios/Packages/SMSCore` builds and tests with `swift test` (SPM, no project).
- The app target still needs an `.xcodeproj`, produced once by Xcode or later by
  an XcodeGen `project.yml` (Proposed).

Do not document a command that does not exist.

## Pitfalls

- `ILMessageFilterCapabilitiesQueryResponse` with too many sub-actions collapses
  the folder list on iOS 18 (FB15385442). Leave headroom; we declare 5 + 3.
- Do not call `deferQueryRequestToNetwork` for messages you can decide locally;
  Apple throttles redundant deferrals (`RedundantNetworkDeferral`).
- App Group identifiers must match between the app and the extension or the vault
  and settings silently diverge.
- Decode failures must not crash the extension: every decode is `try?` with a
  conservative fallback.
- Do not add `UserNotifications` or Live Activities to the *extension* process;
  they belong to the app.

## Done

- [ ] Pure logic in `SMSCore`, thin adapter in the extension.
- [ ] Sub-actions within Apple's limits and declared in capabilities.
- [ ] Fail-open preserved on every error path.
- [ ] `make swift-test` and `make verify` run, or an explicit statement of what
      could not run and why.
- [ ] `docs/ios-constraints.md` updated if a limit changed (it should not).
