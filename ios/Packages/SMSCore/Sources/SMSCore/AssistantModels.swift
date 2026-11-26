// AssistantModels.swift
//
// Wire DTOs and APIClient endpoints for the assistant screens:
// Vault (vouchers, refunds, cleanup queues), Upcoming (subscriptions, cards, alerts).

import Foundation

public struct VoucherItem: Decodable, Sendable, Identifiable, Equatable {
    public let id: Int
    public let brand: String?
    public let value: Double?
    public let currency: String?
    public let code: String?
    public let pin: String?
    public let expiresOn: String?
    public let expired: Bool?

    enum CodingKeys: String, CodingKey {
        case id, brand, value, currency, code, pin, expired
        case expiresOn = "expires_on"
    }
}

public struct RefundClaim: Decodable, Sendable, Identifiable, Equatable {
    public let id: Int
    public let merchant: String?
    public let amount: Double
    public let currency: String?
    public let instrumentLast4: String?
    public let status: String
    public let openedAt: String
    public let deadlineAt: String?
    public let overdue: Bool?

    enum CodingKeys: String, CodingKey {
        case id, merchant, amount, currency, status, overdue
        case instrumentLast4 = "instrument_last4"
        case openedAt = "opened_at"
        case deadlineAt = "deadline_at"
    }
}

public struct CleanupQueueBucket: Decodable, Sendable, Identifiable, Equatable {
    public let folder: String
    public let count: Int
    public let retention: String
    public let examples: [String]

    public var id: String { folder }
}

public struct CleanupQueuesResponse: Decodable, Sendable, Equatable {
    public let month: String?
    public let queues: [CleanupQueueBucket]
}

public struct SubscriptionSeries: Decodable, Sendable, Equatable, Identifiable {
    public let key: String
    public let name: String
    public let category: String?
    public let cadence: String
    public let confidence: Double
    public let amount: Double?
    public let nextDue: String?

    public var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, name, category, cadence, confidence, amount
        case nextDue = "next_due"
    }
}

public struct SubscriptionsSummary: Decodable, Sendable, Equatable {
    public let count: Int
    public let irregularCount: Int
    public let monthlyCommitment: Double
    public let annualised: Double
    public let series: [SubscriptionSeries]

    enum CodingKeys: String, CodingKey {
        case count, annualised, series
        case irregularCount = "irregular_count"
        case monthlyCommitment = "monthly_commitment"
    }
}

public struct CreditCardStatus: Decodable, Sendable, Identifiable, Equatable {
    public let key: String
    public let last4: String
    public let bank: String
    public let statementAmount: Double?
    public let minimumDue: Double?
    public let dueDate: String?
    public let paid: Double
    public let outstanding: Double?
    public let status: String

    public var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, last4, bank, paid, outstanding, status
        case statementAmount = "statement_amount"
        case minimumDue = "minimum_due"
        case dueDate = "due_date"
    }
}

public struct CardsSummary: Decodable, Sendable, Equatable {
    public let cards: [CreditCardStatus]
    public let totalOutstanding: Double
    public let paymentsTotal: Double

    enum CodingKeys: String, CodingKey {
        case cards
        case totalOutstanding = "total_outstanding"
        case paymentsTotal = "payments_total"
    }
}

public struct AlertItem: Decodable, Sendable, Identifiable, Equatable {
    public let id: Int
    public let eventID: String?
    public let severity: String?
    public let kind: String?
    public let message: String?
    public let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, severity, kind, message
        case eventID = "event_id"
        case createdAt = "created_at"
    }
}

public struct EventSummaryItem: Decodable, Sendable, Identifiable, Equatable {
    public let eventID: String
    public let senderRaw: String
    public let intent: String
    public let amountValue: Double?
    public let institution: String?
    public let merchantRaw: String?
    public let merchantClean: String?
    public let ledgerTreatment: String
    public let countsTowardSpend: Bool
    public let appleAction: String
    public let appleSubaction: String
    public let retention: String
    public let confidence: Double
    public let needsReview: Bool
    public let dedupStatus: String
    public let summary: String
    public let evidence: [String]

    public var id: String { eventID }

    enum CodingKeys: String, CodingKey {
        case intent, institution, retention, confidence, payload, body
        case eventID = "event_id"
        case senderRaw = "sender_raw"
        case amountValue = "amount_value"
        case merchantRaw = "merchant_raw"
        case merchantClean = "merchant_clean"
        case ledgerTreatment = "ledger_treatment"
        case countsTowardSpend = "counts_toward_spend"
        case appleAction = "apple_action"
        case appleSubaction = "apple_subaction"
        case needsReview = "needs_review"
        case dedupStatus = "dedup_status"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        eventID = try c.decode(String.self, forKey: .eventID)
        senderRaw = (try? c.decodeIfPresent(String.self, forKey: .senderRaw)) ?? ""
        intent = try c.decode(String.self, forKey: .intent)
        amountValue = try c.decodeIfPresent(Double.self, forKey: .amountValue)
        institution = try c.decodeIfPresent(String.self, forKey: .institution)
        merchantRaw = try c.decodeIfPresent(String.self, forKey: .merchantRaw)
        merchantClean = try c.decodeIfPresent(String.self, forKey: .merchantClean)
        ledgerTreatment = (try? c.decode(String.self, forKey: .ledgerTreatment)) ?? "none"
        if let flag = try? c.decode(Bool.self, forKey: .countsTowardSpend) {
            countsTowardSpend = flag
        } else {
            countsTowardSpend = ((try? c.decode(Int.self, forKey: .countsTowardSpend)) ?? 0) != 0
        }
        appleAction = (try? c.decode(String.self, forKey: .appleAction)) ?? "allow"
        appleSubaction = (try? c.decode(String.self, forKey: .appleSubaction)) ?? "none"
        retention = (try? c.decode(String.self, forKey: .retention)) ?? "review"
        confidence = (try? c.decode(Double.self, forKey: .confidence)) ?? 0
        if let nr = try? c.decode(Bool.self, forKey: .needsReview) {
            needsReview = nr
        } else {
            needsReview = ((try? c.decode(Int.self, forKey: .needsReview)) ?? 0) != 0
        }
        dedupStatus = (try? c.decode(String.self, forKey: .dedupStatus)) ?? "unique"

        var parsedSummary = (try? c.decodeIfPresent(String.self, forKey: .body)) ?? ""
        var parsedEvidence: [String] = []
        if let rawPayload = try? c.decodeIfPresent(String.self, forKey: .payload),
           let data = rawPayload.data(using: .utf8),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            if let s = obj["summary"] as? String, !s.isEmpty { parsedSummary = s }
            if let ev = obj["evidence"] as? [String] { parsedEvidence = ev }
        }
        summary = parsedSummary
        evidence = parsedEvidence
    }
}

public extension APIClient {
    func vouchers() async throws -> [VoucherItem] {
        try await fetchJSON(path: "/api/v1/vouchers")
    }

    func refunds() async throws -> [RefundClaim] {
        try await fetchJSON(path: "/api/v1/refunds")
    }

    func cleanupQueues() async throws -> CleanupQueuesResponse {
        try await fetchJSON(path: "/api/v1/cleanup-queues")
    }

    func subscriptions() async throws -> SubscriptionsSummary {
        try await fetchJSON(path: "/api/v1/subscriptions")
    }

    func cards() async throws -> CardsSummary {
        try await fetchJSON(path: "/api/v1/cards")
    }

    func alerts(limit: Int = 50) async throws -> [AlertItem] {
        try await fetchJSON(
            path: "/api/v1/alerts",
            query: [URLQueryItem(name: "limit", value: String(limit))]
        )
    }

    func events(limit: Int = 30) async throws -> [EventSummaryItem] {
        try await fetchJSON(
            path: "/api/v1/events",
            query: [URLQueryItem(name: "limit", value: String(limit))]
        )
    }

    func investments() async throws -> InvestmentsBook {
        try await fetchJSON(path: "/api/v1/analytics/investments")
    }

    func travel() async throws -> TravelLog {
        try await fetchJSON(path: "/api/v1/analytics/travel")
    }

    func loans() async throws -> LoansSummary {
        try await fetchJSON(path: "/api/v1/analytics/loans")
    }

    func reclassify() async throws -> ReclassifyResult {
        var req = try request(path: "/api/v1/reclassify", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: req)
        } catch {
            throw APIError.offline
        }
        try APIClient.validate(response, data: data)
        return try JSONDecoder().decode(ReclassifyResult.self, from: data)
    }

    private func fetchJSON<T: Decodable>(path: String, query: [URLQueryItem] = []) async throws -> T {
        let req = try request(path: path, query: query)
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession.shared.data(for: req)
        } catch {
            throw APIError.offline
        }
        try APIClient.validate(response, data: data)
        return try JSONDecoder().decode(T.self, from: data)
    }
}

public struct InvestmentHolding: Decodable, Sendable, Identifiable, Equatable {
    public let security: String
    public let broker: String?
    public let netQuantity: Double
    public let buyValue: Double
    public let lastPrice: Double?

    public var id: String { security }

    enum CodingKeys: String, CodingKey {
        case security, broker
        case netQuantity = "net_quantity"
        case buyValue = "buy_value"
        case lastPrice = "last_price"
    }
}

public struct InvestmentsBook: Decodable, Sendable, Equatable {
    public let holdings: [InvestmentHolding]
    public let sipTotal: Double
    public let dividendTotal: Double

    enum CodingKeys: String, CodingKey {
        case holdings
        case sipTotal = "sip_total"
        case dividendTotal = "dividend_total"
    }
}

public struct TravelTrip: Decodable, Sendable, Identifiable, Equatable {
    public let mode: String
    public let provider: String?
    public let pnr: String?
    public let route: String?
    public let fare: Double?

    public var id: String { (pnr ?? "") + (route ?? mode) }
}

public struct TravelLog: Decodable, Sendable, Equatable {
    public let trips: [TravelTrip]
    public let count: Int
}

public struct LoanItem: Decodable, Sendable, Identifiable, Equatable {
    public let key: String
    public let institution: String
    public let accountLast4: String
    public let emiAmount: Double?
    public let dueDate: String?
    public let count: Int

    public var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, institution, count
        case accountLast4 = "account_last4"
        case emiAmount = "emi_amount"
        case dueDate = "due_date"
    }
}

public struct LoansSummary: Decodable, Sendable, Equatable {
    public let loans: [LoanItem]
    public let count: Int
    public let monthlyEmiTotal: Double

    enum CodingKeys: String, CodingKey {
        case loans, count
        case monthlyEmiTotal = "monthly_emi_total"
    }
}

public struct ReclassifyResult: Decodable, Sendable, Equatable {
    public let processed: Int
    public let updated: Int
    public let unchanged: Int
}
