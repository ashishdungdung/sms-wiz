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

public struct SubscriptionSeries: Decodable, Sendable, Identifiable, Equatable {
    public let key: String
    public let name: String
    public let category: String
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
