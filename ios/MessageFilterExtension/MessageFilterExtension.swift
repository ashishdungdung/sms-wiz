//
//  MessageFilterExtension.swift
//  SMS Wiz — reference implementation
//
//  NOT COMPILED OR TESTED IN THIS REPOSITORY. Read docs/ios-constraints.md
//  first. Verified against the IdentityLookup headers: the action enum and the
//  sub-action list below are Apple's, not ours.
//
//  What this extension may do:
//    • receives one message at a time, from senders NOT in the user's contacts
//    • returns allow / junk / promotion / transaction (+ a declared sub-action)
//  What it may NOT do:
//    • read history, delete messages, create folders, or run for known senders
//

import IdentityLookup
import Foundation

final class MessageFilterExtension: ILMessageFilterExtension {}

// MARK: - Capabilities
//
// Apple allows up to five sub-actions per group. Declare fewer than the maximum
// and keep them stable: a sub-action that is not declared here is silently
// discarded and the message lands in the broad folder.
//
// These must match sms_wiz/registry.py (DECLARED_TRANSACTIONAL / DECLARED_PROMOTIONAL).
//   transactional: finance, reminders, orders, rewards, publicServices
//   promotional:   offers, coupons
//
// Deliberately NOT declared: weather, carrier, health, others. Health messages
// therefore fall back to the broad Transactions folder — an honest downgrade
// rather than a folder that never appears.

extension MessageFilterExtension: ILMessageFilterCapabilitiesQueryHandling {
    func handle(_ capabilitiesQueryRequest: ILMessageFilterCapabilitiesQueryRequest,
                context: ILMessageFilterExtensionContext,
                completion: @escaping (ILMessageFilterCapabilitiesQueryResponse) -> Void) {
        let response = ILMessageFilterCapabilitiesQueryResponse()
        response.transactionalSubActions = [
            .transactionalFinance,
            .transactionalReminders,
            .transactionalOrders,
            .transactionalRewards,
            .transactionalPublicServices,
        ]
        response.promotionalSubActions = [
            .promotionalOffers,
            .promotionalCoupons,
        ]
        completion(response)
    }
}

// MARK: - Query handling

extension MessageFilterExtension: ILMessageFilterQueryHandling {

    private static let appGroupID = "group.com.example.smswiz"

    func handle(_ queryRequest: ILMessageFilterQueryRequest,
                context: ILMessageFilterExtensionContext,
                completion: @escaping (ILMessageFilterQueryResponse) -> Void) {

        let sender = queryRequest.sender ?? ""
        let body = queryRequest.messageBody ?? ""

        // 1. Offline, user-authored rules first: instant, works on a plane, and
        //    the user can always explain why a message went where it went.
        if let decision = Self.applyLocalRules(sender: sender, body: body) {
            completion(decision)
            return
        }

        // 2. Anything undecided may be deferred to the server. Keep this rare:
        //    the deadline is short and a timeout means "allow".
        context.deferQueryRequestToNetwork { networkResponse, error in
            guard error == nil,
                  let data = networkResponse?.data,
                  let decision = Self.decodeNetworkDecision(data) else {
                // Fail open. A dropped message is recoverable; a wrongly
                // quarantined bank alert might not be.
                let response = ILMessageFilterQueryResponse()
                response.action = .none
                response.subAction = .none
                completion(response)
                return
            }
            completion(decision)
        }
    }

    // MARK: Local rules (App Group)

    /// Rules are written by the app's "Cleanup / Rules" screen:
    ///   blockedSenders: ["AD-RUMMYC-P", ...]        → junk
    ///   junkKeywords:   ["loan approved", "rummy"]  → junk
    ///   promoKeywords:  ["% off", "unsubscribe"]    → promotion / coupons
    private static func applyLocalRules(sender: String, body: String) -> ILMessageFilterQueryResponse? {
        guard let defaults = UserDefaults(suiteName: appGroupID) else { return nil }

        let blocked = Set((defaults.stringArray(forKey: "blockedSenders") ?? []).map { $0.uppercased() })
        let junkKeywords = defaults.stringArray(forKey: "junkKeywords") ?? []
        let promoKeywords = defaults.stringArray(forKey: "promoKeywords") ?? []

        let upperSender = sender.uppercased()
        let lowerBody = body.lowercased()

        if blocked.contains(upperSender) || junkKeywords.contains(where: { lowerBody.contains($0.lowercased()) }) {
            let response = ILMessageFilterQueryResponse()
            response.action = .junk
            response.subAction = .none
            return response
        }

        if promoKeywords.contains(where: { lowerBody.contains($0.lowercased()) }) {
            let response = ILMessageFilterQueryResponse()
            response.action = .promotion
            response.subAction = .promotionalOffers
            return response
        }

        return nil
    }

    // MARK: Network response
    //
    // Shape produced by POST /api/v1/filter:
    //     {"action": "transaction", "subAction": "transactionalFinance"}
    // Fail closed to .none on anything unexpected.

    private static func decodeNetworkDecision(_ data: Data) -> ILMessageFilterQueryResponse? {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let actionString = json["action"] as? String else { return nil }

        let response = ILMessageFilterQueryResponse()

        switch actionString {
        case "allow":       response.action = .allow
        case "junk":        response.action = .junk
        case "promotion":   response.action = .promotion
        case "transaction": response.action = .transaction
        default:            response.action = .none
        }

        // Only pair a sub-action with its own family; iOS discards mismatches
        // anyway, and returning one is worse than returning .none.
        let subActionString = json["subAction"] as? String ?? "none"
        switch (response.action, subActionString) {
        case (.transaction, "transactionalFinance"):
            response.subAction = .transactionalFinance
        case (.transaction, "transactionalReminders"):
            response.subAction = .transactionalReminders
        case (.transaction, "transactionalOrders"):
            response.subAction = .transactionalOrders
        case (.transaction, "transactionalRewards"):
            response.subAction = .transactionalRewards
        case (.transaction, "transactionalPublicServices"):
            response.subAction = .transactionalPublicServices
        case (.promotion, "promotionalOffers"):
            response.subAction = .promotionalOffers
        case (.promotion, "promotionalCoupons"):
            response.subAction = .promotionalCoupons
        default:
            response.subAction = .none
        }

        return response
    }
}
