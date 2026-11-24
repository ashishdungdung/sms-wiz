// AppConfig.swift
//
// Where the server lives, and the one secret that reads it.
//
// Lives in SMSCore, not the app target, because **three** processes need the
// same answer: the app, the share extension and the Shortcuts intent. If each
// kept its own copy, the extension would keep posting to a stale address after
// the user changed it in Settings — and the failure would look like "sharing
// does nothing".
//
// Storage is the App Group's `UserDefaults`, never the app bundle: a token in
// the bundle is a token in every backup and every App Store binary.
//
// Status: written, not compiled (see ios/README.md).

import Foundation

/// Server location and token, shared by every target through the App Group.
public struct AppConfig: Sendable, Equatable {
    /// Change this in both entitlements files as well — see ios/README.md.
    public static let appGroupID = "group.com.example.smswiz"

    public var baseURL: String
    public var token: String

    public init(baseURL: String = "http://localhost:8000", token: String = "") {
        self.baseURL = baseURL
        self.token = token
    }

    public var isConfigured: Bool {
        URL(string: baseURL) != nil && !baseURL.isEmpty
    }

    // MARK: - App Group storage

    public static func sharedDefaults() -> UserDefaults? {
        UserDefaults(suiteName: appGroupID)
    }

    /// Injection point for tests. An App Group container does not exist in a
    /// plain `swift test` run (and on some Linux hosts `suiteName:` returns a
    /// store with no backing file), so tests point this at a scratch suite.
    /// Production never sets it.
    public static var storeOverride: UserDefaults?

    static func store() -> UserDefaults? { storeOverride ?? sharedDefaults() }

    public static func load() -> AppConfig {
        guard let defaults = store() else { return AppConfig() }
        return AppConfig(
            baseURL: defaults.string(forKey: Keys.baseURL) ?? AppConfig().baseURL,
            token: defaults.string(forKey: Keys.token) ?? ""
        )
    }

    public func save() {
        guard let defaults = Self.store() else { return }
        defaults.set(baseURL, forKey: Keys.baseURL)
        defaults.set(token, forKey: Keys.token)
    }

    /// Trim whitespace and a trailing slash: "http://host:8000/" + "/api/…"
    /// would otherwise ask for "//api/…", which FastAPI answers with a redirect
    /// that a share extension does not follow.
    public var normalisedBaseURL: String {
        var value = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        while value.hasSuffix("/") { value.removeLast() }
        return value
    }

    // MARK: - Offline queue

    /// Messages that could not be delivered are kept here, not dropped: the
    /// whole promise of the import path is that a message you gave the app is
    /// never lost because the server was asleep.
    public struct PendingMessage: Codable, Sendable, Equatable {
        public let sender: String
        public let body: String
        public let queuedAt: Date
        public let source: String

        public init(sender: String, body: String, queuedAt: Date = Date(), source: String = "manual") {
            self.sender = sender
            self.body = body
            self.queuedAt = queuedAt
            self.source = source
        }
    }

    public static func appendPending(_ message: PendingMessage) {
        var queue = pendingMessages()
        queue.append(message)
        // A queue that grows without bound is its own bug; keep the last 500.
        if queue.count > 500 { queue = Array(queue.suffix(500)) }
        writePending(queue)
    }

    /// Legacy call shape kept so the Shortcuts intent keeps working.
    public static func appendPending(messageBody: String, sender: String) {
        appendPending(PendingMessage(sender: sender, body: messageBody, source: "shortcut"))
    }

    public static func pendingMessages() -> [PendingMessage] {
        guard let defaults = store(),
              let data = defaults.data(forKey: Keys.pending) else { return [] }
        return (try? JSONDecoder().decode([PendingMessage].self, from: data)) ?? []
    }

    public static func writePending(_ messages: [PendingMessage]) {
        guard let defaults = store() else { return }
        if messages.isEmpty {
            defaults.removeObject(forKey: Keys.pending)
        } else if let data = try? JSONEncoder().encode(messages) {
            defaults.set(data, forKey: Keys.pending)
        }
    }

    enum Keys {
        static let baseURL = "serverBaseURL"
        static let token = "serverToken"
        static let pending = "pendingImports.v2"
    }
}
