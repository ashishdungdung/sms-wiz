//
//  Models.swift
//  SMS Wiz — reference implementation
//
//  A decode-only mirror of the server's event schema (see docs/schema.md).
//  NOT COMPILED OR TESTED IN THIS REPOSITORY.
//
//  Rule of thumb for the UI: always render `summary` + `evidence`, and put
//  anything with `confidence.needs_review == true` in a "Verify" queue instead
//  of the ledger. Nothing here should ever be shown as certain when the server
//  said it was not.
//

import Foundation

struct IngestResponse: Decodable {
    let event: SMSEvent
    let alerts: [SMSAlert]
    let stored: Bool
    let duplicateOf: String?

    enum CodingKeys: String, CodingKey {
        case event, alerts, stored
        case duplicateOf = "duplicate_of"
    }
}

struct SMSEvent: Decodable {
    let eventID: String
    let receivedAt: String
    let senderRaw: String
    let intent: String
    let domain: String
    let summary: String
    let evidence: [String]
    let flags: [String]

    let amount: SMSAmount?
    let institution: Institution?
    let merchant: Merchant?
    let otp: OTP?
    let voucher: Voucher?
    let reminder: Reminder?
    let routing: Routing
    let ledger: Ledger
    let confidence: Confidence
    let dedup: Dedup

    enum CodingKeys: String, CodingKey {
        case intent, domain, summary, evidence, flags, amount, institution, merchant
        case otp, voucher, reminder, routing, ledger, confidence, dedup
        case eventID = "event_id"
        case receivedAt = "received_at"
        case senderRaw = "sender_raw"
    }
}

struct SMSAmount: Decodable {
    let value: Double
    let currency: String
    let direction: String
    let balanceAfter: Double?

    enum CodingKeys: String, CodingKey {
        case value, currency, direction
        case balanceAfter = "balance_after"
    }
}

struct Institution: Decodable {
    let name: String
    let kind: String
    let accountLast4: String?
    let cardLast4: String?

    enum CodingKeys: String, CodingKey {
        case name, kind
        case accountLast4 = "account_last4"
        case cardLast4 = "card_last4"
    }
}

struct Merchant: Decodable {
    let raw: String?
    let clean: String?
    let category: String?
    let source: String

    var displayName: String { clean ?? raw ?? "Unknown merchant" }
}

struct OTP: Decodable {
    let code: String
    let purpose: String
    let risk: String
    let service: String?
}

struct Voucher: Decodable {
    let brand: String?
    let value: Double?
    let code: String?
    let pin: String?
    let expiry: String?
}

struct Reminder: Decodable {
    let kind: String
    let biller: String?
    let amountDue: Double?
    let minimumDue: Double?
    let dueDate: String?
    let autopay: Bool

    enum CodingKeys: String, CodingKey {
        case kind, biller, autopay
        case amountDue = "amount_due"
        case minimumDue = "minimum_due"
        case dueDate = "due_date"
    }
}

struct Routing: Decodable {
    let appleAction: String
    let appleSubaction: String
    let retention: String
    let safeToBulkDelete: Bool
    let rationale: String

    enum CodingKeys: String, CodingKey {
        case retention, rationale
        case appleAction = "apple_action"
        case appleSubaction = "apple_subaction"
        case safeToBulkDelete = "safe_to_bulk_delete"
    }
}

struct Ledger: Decodable {
    let treatment: String
    let countsTowardSpend: Bool
    let countsAsIncome: Bool
    let reducesSpend: Bool
    let reason: String

    enum CodingKeys: String, CodingKey {
        case treatment, reason
        case countsTowardSpend = "counts_toward_spend"
        case countsAsIncome = "counts_as_income"
        case reducesSpend = "reduces_spend"
    }
}

struct Confidence: Decodable {
    let score: Double
    let band: String
    let needsReview: Bool

    enum CodingKeys: String, CodingKey {
        case score, band
        case needsReview = "needs_review"
    }
}

struct Dedup: Decodable {
    let status: String
    let duplicateOf: String?
    let reason: String

    enum CodingKeys: String, CodingKey {
        case status, reason
        case duplicateOf = "duplicate_of"
    }
}

struct SMSAlert: Decodable {
    let severity: String
    let kind: String
    let message: String

    var critical: Bool { severity == "critical" }
}

// MARK: - Presentation helpers

extension SMSEvent {
    /// Short line for a list row. Never contains an OTP, code or PIN: the
    /// server keeps those out of `summary` on purpose.
    var listRow: String {
        if dedup.status == "suppressed_duplicate" { return "Linked duplicate — not counted" }
        return summary
    }

    var amountText: String? {
        guard let amount else { return nil }
        let formatted = amount.value.formatted(.number.precision(.fractionLength(2)))
        return "\(amount.currency == "INR" ? "₹" : amount.currency + " ")\(formatted)"
    }

    var folderText: String {
        routing.appleSubaction == "none"
            ? routing.appleAction
            : "\(routing.appleAction) · \(routing.appleSubaction)"
    }
}
