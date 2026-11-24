// Contracts.swift
//
// Language-neutral mirror of `contracts/enums.json`, which is generated from
// `sms_wiz/models.py`. The raw values are the wire format: the Python backend
// writes them, the iOS app and the filter extension read them.
//
// Do not add, rename or reorder cases here without changing the Python enum and
// running `python scripts/export_contracts.py`. `make verify-contracts` (and
// ContractsParityTests below) fail on any drift.
//
// Swift case names are camelCased for readability; the *raw values* are what
// must match the contract exactly.

import Foundation

/// What the message is about. Independent of how iOS routes it.
public enum SMSEventIntent: String, CaseIterable, Codable, Sendable {
    case expense = "expense"
    case income = "income"
    case refundInitiated = "refund_initiated"
    case refundCredited = "refund_credited"
    case ccBillPayment = "cc_bill_payment"
    case selfTransfer = "self_transfer"
    case cashWithdrawal = "cash_withdrawal"
    case instrumentLoad = "instrument_load"
    case fastagToll = "fastag_toll"
    case investment = "investment"
    case upcomingDebit = "upcoming_debit"
    case billDue = "bill_due"
    case otp = "otp"
    case voucherIssued = "voucher_issued"
    case logistics = "logistics"
    case travel = "travel"
    case trade = "trade"
    case dividend = "dividend"
    case ipo = "ipo"
    case corporateAction = "corporate_action"
    case govtNotice = "govt_notice"
    case health = "health"
    case bankUpsell = "bank_upsell"
    case commercialPromo = "commercial_promo"
    case phishingSuspect = "phishing_suspect"
    case unknown = "unknown"
}

/// Whether an event touches money totals. `intent` alone never decides that.
public enum LedgerTreatment: String, CaseIterable, Codable, Sendable {
    case expense = "expense"
    case income = "income"
    case refund = "refund"
    case internalTransfer = "internal_transfer"
    case instrumentLoad = "instrument_load"
    case cashWithdrawal = "cash_withdrawal"
    case investment = "investment"
    case foreignCurrency = "foreign_currency"
    case none = "none"

    /// Only these two ever move the spend / income ring.
    public var countsTowardSpend: Bool { self == .expense }
    public var countsTowardIncome: Bool { self == .income }
}

/// How long an event is allowed to live before the app prunes it.
public enum Retention: String, CaseIterable, Codable, Sendable {
    case keepForever = "keep_forever"
    case expire30d = "expire_30d"
    case expire7d = "expire_7d"
    case expire24h = "expire_24h"
    case deleteAfterEvent = "delete_after_event"
    case review = "review"
}

/// The only four answers `ILMessageFilterExtension` may return.
public enum AppleAction: String, CaseIterable, Codable, Sendable {
    case allow = "allow"
    case junk = "junk"
    case promotion = "promotion"
    case transaction = "transaction"
}

/// `ILMessageFilterSubAction` as of iOS 16+, as fixed identifiers.
///
/// A sub-action only renders as a folder if the extension declared it in
/// `ILMessageFilterCapabilitiesQueryResponse`, and Apple caps the declaration at
/// five per group. Returning a pair whose sub-action was not declared makes iOS
/// keep the action and silently drop the sub-action — hence `.none`.
public enum AppleSubAction: String, CaseIterable, Codable, Sendable {
    case none = "none"
    case transactionalOthers = "transactionalOthers"
    case transactionalFinance = "transactionalFinance"
    case transactionalOrders = "transactionalOrders"
    case transactionalReminders = "transactionalReminders"
    case transactionalHealth = "transactionalHealth"
    case transactionalWeather = "transactionalWeather"
    case transactionalCarrier = "transactionalCarrier"
    case transactionalRewards = "transactionalRewards"
    case transactionalPublicServices = "transactionalPublicServices"
    case promotionalOthers = "promotionalOthers"
    case promotionalOffers = "promotionalOffers"
    case promotionalCoupons = "promotionalCoupons"

    /// The five transactional sub-actions this app declares. Keep in sync with
    /// `MessageFilterExtension.capabilities` and docs/ios-constraints.md.
    public static let declaredTransactional: [AppleSubAction] = [
        .transactionalFinance,
        .transactionalOrders,
        .transactionalReminders,
        .transactionalRewards,
        .transactionalPublicServices,
    ]

    /// Three of the five promotional slots are used, deliberately leaving room.
    public static let declaredPromotional: [AppleSubAction] = [
        .promotionalOffers,
        .promotionalCoupons,
        .promotionalOthers,
    ]

    public var isDeclared: Bool {
        self == .none
            || AppleSubAction.declaredTransactional.contains(self)
            || AppleSubAction.declaredPromotional.contains(self)
    }
}

/// Sender reputation buckets resolved by the backend registry.
public enum EntityKind: String, CaseIterable, Codable, Sendable {
    case bank = "bank"
    case fintech = "fintech"
    case govt = "govt"
    case brand = "brand"
    case telecom = "telecom"
    case personalMobile = "personal_mobile"
    case shortcode = "shortcode"
    case unknown = "unknown"
}

/// A classification must explain itself; the app shows this in the detail view.
public struct EvidenceLine: Codable, Hashable, Sendable {
    public let rule: String
    public let detail: String
    public let weight: Double

    public init(rule: String, detail: String, weight: Double) {
        self.rule = rule
        self.detail = detail
        self.weight = weight
    }
}

/// Dedup outcome for one message.
public enum DedupStatus: String, CaseIterable, Codable, Sendable {
    /// Nothing matched — count it.
    case unique
    /// Same reference/RRN already recorded — do not count it.
    case suppressedDuplicate = "suppressed_duplicate"
    /// Same amount/day/last4 from a *different* sender — show for review, still count.
    case possibleDuplicate = "possible_duplicate"
}
