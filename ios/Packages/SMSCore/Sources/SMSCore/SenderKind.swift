// SenderKind.swift
//
// "Who is this message from?" — offline.
//
// Several filter rules only fire for particular senders (`security.kyc_link_
// from_personal_number` must not junk a genuine bank KYC reminder, and
// `fraud.guaranteed_returns` only matters from numbers that are not banks). The
// server answers that question with `registry.py`; the extension has no Python,
// so `contracts/sender_kinds.json` carries the same answer as data.
//
// Deriving the kind from the header's *shape* instead would be a guess, and the
// extension and the server would then disagree about the same message — the one
// thing this package exists to prevent.
//
// Status: written, not compiled. `SenderKindTests` pins the numbers.

import Foundation

public enum SenderKind {
    public static let syntaxName = "sms-wiz.sender_kinds/v1"

    /// The generated map. `codes` is entity code → `EntityKind` raw value.
    public struct Map: Decodable, Sendable, Equatable {
        public let syntax: String?
        public let defaultKind: String
        public let codes: [String: String]

        enum CodingKeys: String, CodingKey {
            case syntax
            case defaultKind = "default"
            case codes
        }
    }

    /// Fail-open loader: no map means every sender is "unknown", which makes the
    /// kind-scoped rules simply not fire. That is the safe direction.
    public static func load(at url: URL?) -> Map? {
        guard let url, let data = try? Data(contentsOf: url) else { return nil }
        guard let map = try? JSONDecoder().decode(Map.self, from: data) else { return nil }
        if let syntax = map.syntax, syntax != syntaxName { return nil }
        return map
    }

    public static func bundled(in bundle: Bundle = .main) -> Map? {
        load(at: bundle.url(forResource: "sender_kinds", withExtension: "json"))
    }

    /// The same answer `extract.parse_sender` gives, for the cases that matter
    /// to a folder decision.
    ///
    /// Order is not arbitrary — it is Python's: a personal number is checked
    /// before anything else, then a short code, then a DLT header. A ten-digit
    /// number that happens to start with "AD" is still a phone number.
    public static func resolve(_ sender: String, using map: Map?) -> String {
        let unknown = map?.defaultKind ?? "unknown"
        let compact = sender.filter { !$0.isWhitespace && $0 != "-" && $0 != "(" && $0 != ")" }
        if isMobile(compact) { return "personal_mobile" }
        if isShortCode(compact) { return "shortcode" }
        // Mirror `DLT_SENDER_RE`: an optional two-letter operator, an entity code
        // of 2–9 alphanumerics, an optional category suffix. Anything else —
        // including a header with a space in it, which the alias table uses for
        // matching message text — is not a DLT sender, and the server says so
        // too. Without this guard the two sides disagree.
        guard isDLTShaped(sender.trimmingCharacters(in: .whitespaces).uppercased()) else { return unknown }
        guard let prefix = FilterRuleSet.senderPrefix(sender) else { return unknown }
        return map?.codes[prefix.uppercased()] ?? unknown
    }

    static func isDLTShaped(_ header: String) -> Bool {
        let pattern = "^(?:[A-Z]{2}-)?[A-Z0-9]{2,9}(?:-[PSTG])?$"
        return header.range(of: pattern, options: .regularExpression) != nil
    }

    /// `+919876543210`, `919876543210`, `9876543210`.
    static func isMobile(_ compact: String) -> Bool {
        var digits = compact
        if digits.hasPrefix("+") { digits.removeFirst() }
        guard digits.count == 10 || (digits.count == 12 && digits.hasPrefix("91")) else { return false }
        let national = String(digits.suffix(10))
        guard let first = national.first else { return false }
        return "6789".contains(first) && national.allSatisfy(\.isNumber)
    }

    /// `1909`, `56161` — operator short codes. Three to six digits, exactly the
    /// server's `SHORTCODE_RE`; a wider range here would classify a seven-digit
    /// sender differently from `parse_sender`.
    static func isShortCode(_ compact: String) -> Bool {
        (3...6).contains(compact.count) && compact.allSatisfy(\.isNumber)
    }
}
