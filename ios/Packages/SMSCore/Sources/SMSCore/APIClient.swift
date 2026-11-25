// APIClient.swift
//
// The app's only way of talking to the server. One type, because three targets
// hit the same four endpoints and a second implementation is how a share
// extension ends up posting to a route that was renamed months ago.
//
// Design notes that are load-bearing:
//
//  * **Every request carries the token** when one is configured. The server
//    gates all of `/api/v1/*`, reads included; a client that only sends the
//    token on writes gets 401s on the ledger screen.
//  * **Offline is a state, not an error.** `ingest` queues the message in the
//    App Group and reports `.queued`; the user is told it will be filed later.
//    Losing a bank SMS because the laptop was asleep is not acceptable.
//  * **A 401 is surfaced as itself.** "Server rejected the token" is actionable;
//    "something went wrong" is not.
//
// Status: written, not compiled. `APIClientTests` checks request construction
// without a network, which is the part that silently breaks.

import Foundation

/// The small subset of an ingest answer the wire layer needs. `Models.swift` in
/// the app target has the full event mirror for screens.
public struct IngestResult: Decodable, Sendable, Equatable {
    public let eventID: String
    public let summary: String
    public let intent: String
    public let needsReview: Bool
    public let duplicateOf: String?
    public let alerts: [IngestAlertInfo]

    enum CodingKeys: String, CodingKey {
        case event
        case duplicateOf = "duplicate_of"
        case alerts
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        alerts = (try? container.decode([IngestAlertInfo].self, forKey: .alerts)) ?? []
        duplicateOf = try container.decodeIfPresent(String.self, forKey: .duplicateOf)
        // The server's ingest answer nests the event under `event`.
        let event = try container.decode(EventSubset.self, forKey: .event)
        eventID = event.eventID
        summary = event.summary
        intent = event.intent
        needsReview = event.confidence.needsReview
    }

    private struct EventSubset: Decodable {
        let eventID: String
        let summary: String
        let intent: String
        let confidence: ConfidenceSubset

        enum CodingKeys: String, CodingKey {
            case eventID = "event_id"
            case summary, intent, confidence
        }
    }

    private struct ConfidenceSubset: Decodable {
        let needsReview: Bool

        enum CodingKeys: String, CodingKey {
            case needsReview = "needs_review"
        }
    }
}

public struct IngestAlertInfo: Decodable, Sendable, Equatable {
    public let severity: String
    public let kind: String
    public let message: String
}

/// A merchant the ledger recorded but could not name.
public struct UnresolvedMerchant: Decodable, Sendable, Identifiable, Equatable {
    public let raw: String
    public let seen: Int
    public let lastSeen: String?
    public let totalAmount: Double?
    public let exampleEventID: String?
    public let suggested: Suggestion?

    public var id: String { raw }

    public struct Suggestion: Decodable, Sendable, Equatable {
        public let clean: String
        public let category: String
        public let confidence: Double
    }

    enum CodingKeys: String, CodingKey {
        case raw, seen, suggested
        case lastSeen = "last_seen"
        case totalAmount = "total_amount"
        case exampleEventID = "example_event_id"
    }
}

public struct MerchantCorrection: Decodable, Sendable, Equatable {
    public let status: String
    public let raw: String
    public let clean: String
    public let category: String
    /// How many stored events were renamed. This is the number that makes the
    /// review list shrink, so the UI shows it.
    public let eventsUpdated: Int

    enum CodingKeys: String, CodingKey {
        case status, raw, clean, category
        case eventsUpdated = "events_updated"
    }
}

/// The home screen's numbers. A deliberately small slice of `/api/v1/overview`:
/// what a phone can show in one glance, not the whole analytics surface.
public struct MonthView: Decodable, Sendable, Equatable {
    public let label: String
    public let spend: Double
    public let income: Double
    public let refunds: Double
    public let excluded: Double
    public let count: Int
    public let reviewCount: Int
    public let essentialShare: Double
    public let byCategory: [String: Double]

    enum CodingKeys: String, CodingKey {
        case label, spend, income, refunds, excluded, count
        case reviewCount = "review_count"
        case essentialShare = "essential_share"
        case byCategory = "by_category"
    }
}

/// `/api/v1/overview` — the month block plus the merchants worth naming.
public struct Overview: Decodable, Sendable, Equatable {
    public let month: MonthView
    public let topMerchants: [TopMerchant]

    public struct TopMerchant: Decodable, Sendable, Equatable, Identifiable {
        public let merchant: String
        public let spend: Double
        public let count: Int

        public var id: String { merchant }
    }

    enum CodingKeys: String, CodingKey {
        case month
        case topMerchants = "top_merchants"
    }
}

public struct HealthInfo: Decodable, Sendable, Equatable {
    public let status: String
    public let llmEnabled: Bool
    public let merchantKeys: Int

    enum CodingKeys: String, CodingKey {
        case status
        case llmEnabled = "llm_enabled"
        case merchantKeys = "merchant_keys"
    }
}

public enum APIError: Error, Equatable, LocalizedError {
    case notConfigured
    case badURL
    case unauthorised
    case rejected(status: Int, detail: String)
    case offline
    case decoding(String)

    public var errorDescription: String? {
        switch self {
        case .notConfigured:
            return "Set the server address in Settings first."
        case .badURL:
            return "That server address is not a URL."
        case .unauthorised:
            return "The server rejected the token. Check it in Settings."
        case let .rejected(status, detail):
            return "The server answered \(status): \(detail)"
        case .offline:
            return "Could not reach the server. The message is queued."
        case let .decoding(detail):
            return "The server answered something unexpected: \(detail)"
        }
    }
}

/// What happened to one message the user handed the app.
public enum IngestOutcome: Sendable, Equatable {
    case stored(IngestResult)
    case queued

    public var isQueued: Bool {
        if case .queued = self { return true }
        return false
    }
}

public struct APIClient: Sendable {
    public let config: AppConfig
    private let session: URLSession

    public init(config: AppConfig = .load(), session: URLSession = .shared) {
        self.config = config
        self.session = session
    }

    // MARK: - Requests (pure, so they can be tested without a network)

    public func request(path: String, method: String = "GET", query: [URLQueryItem] = []) throws -> URLRequest {
        guard config.isConfigured else { throw APIError.notConfigured }
        guard var components = URLComponents(string: config.normalisedBaseURL + path) else {
            throw APIError.badURL
        }
        if !query.isEmpty { components.queryItems = query }
        guard let url = components.url else { throw APIError.badURL }

        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 12
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if !config.token.isEmpty {
            request.setValue(config.token, forHTTPHeaderField: "X-SMSWiz-Token")
        }
        return request
    }

    // MARK: - Ingest

    /// The values `IngestRequest.source` accepts. A typo here is a 422 the user
    /// reads as "the app is broken", so the list lives next to the request.
    public enum ImportSource: String, Sendable, CaseIterable {
        case share
        case clipboard
        case shortcut
        case manual
        case filter
        case imported = "import"
    }

    /// File one message. Never throws for "offline": the message is queued and
    /// the caller gets `.queued`, because a dropped bank SMS is not recoverable.
    public func ingest(
        sender: String,
        body: String,
        receivedAt: Date? = nil,
        source: String = ImportSource.share.rawValue
    ) async throws -> IngestOutcome {
        var request = try self.request(path: "/api/v1/ingest-sms", method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        var payload: [String: Any] = ["sender": sender, "body": body, "source": source]
        if let receivedAt {
            payload["received_at"] = ISO8601DateFormatter().string(from: receivedAt)
        }
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)

        do {
            let (data, response) = try await session.data(for: request)
            try Self.validate(response, data: data)
            do {
                return .stored(try JSONDecoder().decode(IngestResult.self, from: data))
            } catch {
                throw APIError.decoding(String(describing: error))
            }
        } catch APIError.offline {
            AppConfig.appendPending(.init(sender: sender, body: body, source: source))
            return .queued
        }
    }

    /// Send the offline queue. Returns how many were accepted and how many stay
    /// queued, so the UI can say "3 sent, 1 still waiting" instead of a spinner.
    public func flushQueue() async -> (sent: Int, remaining: Int) {
        let pending = AppConfig.pendingMessages()
        guard !pending.isEmpty else { return (0, 0) }
        var remaining: [AppConfig.PendingMessage] = []
        var sent = 0
        for message in pending {
            do {
                switch try await ingest(sender: message.sender, body: message.body, source: message.source) {
                case .stored:
                    sent += 1
                case .queued:
                    remaining.append(message)
                }
            } catch {
                remaining.append(message)
            }
        }
        AppConfig.writePending(remaining)
        return (sent, remaining.count)
    }

    // MARK: - Merchants

    public func unresolvedMerchants(limit: Int = 100) async throws -> [UnresolvedMerchant] {
        let request = try self.request(
            path: "/api/v1/merchants/unresolved",
            query: [URLQueryItem(name: "limit", value: String(limit))]
        )
        let (data, response) = try await send(request)
        try Self.validate(response, data: data)
        return try JSONDecoder().decode([UnresolvedMerchant].self, from: data)
    }

    public func correctMerchant(raw: String, clean: String, category: String) async throws -> MerchantCorrection {
        var request = try self.request(path: "/api/v1/merchants", method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "raw": raw,
            "clean": clean,
            "category": category,
        ])
        let (data, response) = try await send(request)
        try Self.validate(response, data: data)
        return try JSONDecoder().decode(MerchantCorrection.self, from: data)
    }

    // MARK: - Export

    /// The exit door, from the phone: fetch the ledger file and hand it to a
    /// share sheet so it can go to Files, Mail or a desktop. `format` is "csv"
    /// or "json"; the filename comes from the server's `content-disposition`.
    public func export(format: String, month: String? = nil) async throws -> (data: Data, filename: String) {
        var query: [URLQueryItem] = []
        if let month { query.append(URLQueryItem(name: "month", value: month)) }
        let request = try self.request(path: "/api/v1/export.\(format)", query: query)
        let (data, response) = try await send(request)
        try Self.validate(response, data: data)
        let fallback = "sms-wiz-export.\(format)"
        let header = (response as? HTTPURLResponse)?.value(forHTTPHeaderField: "content-disposition") ?? ""
        let name = Self.filename(fromContentDisposition: header) ?? fallback
        return (data, name)
    }

    // MARK: - Overview

    public func overview(month: String? = nil) async throws -> Overview {
        var query: [URLQueryItem] = []
        if let month { query.append(URLQueryItem(name: "month", value: month)) }
        let request = try self.request(path: "/api/v1/overview", query: query)
        let (data, response) = try await send(request)
        try Self.validate(response, data: data)
        return try JSONDecoder().decode(Overview.self, from: data)
    }

    // MARK: - Health

    public func health() async throws -> HealthInfo {
        let request = try self.request(path: "/health")
        let (data, response) = try await send(request)
        try Self.validate(response, data: data)
        return try JSONDecoder().decode(HealthInfo.self, from: data)
    }

    // MARK: - Plumbing

    private func send(_ request: URLRequest) async throws -> (Data, URLResponse) {
        do {
            return try await session.data(for: request)
        } catch {
            throw APIError.offline
        }
    }

    static func validate(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        if http.statusCode == 401 { throw APIError.unauthorised }
        guard (200..<300).contains(http.statusCode) else {
            let detail = String(data: data, encoding: .utf8)?.prefix(200) ?? ""
            throw APIError.rejected(status: http.statusCode, detail: String(detail))
        }
    }

    /// `attachment; filename="sms-wiz-2026-11-24.csv"` → the name.
    static func filename(fromContentDisposition header: String) -> String? {
        guard let range = header.range(of: "filename=\"") else { return nil }
        let rest = header[range.upperBound...]
        guard let end = rest.firstIndex(of: "\"") else { return nil }
        let name = String(rest[..<end])
        return name.isEmpty ? nil : name
    }
}
