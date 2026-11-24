// FilterRules.swift
//
// The offline half of the filter-rule DSL (`sms-wiz.filter/v1`).
//
// `rules/filter_rules.json` → `sms_wiz/rules.py` validates it → and this file
// evaluates `contracts/filter_rules.json` inside the Message Filter Extension,
// where there is one message, a few milliseconds, and no guaranteed network.
//
// Status: **written, not compiled.** There is no Swift toolchain in the
// environment this was authored in (see `ios/README.md`). What is proven is the
// contract (`ContractsParityTests`) and, by construction, agreement with the
// Python evaluator via `tests/fixtures/filter_rules/cases.json`, which
// `FilterRulesParityTests` replays through this file. Run `make swift-test` on a
// machine with Swift before trusting the extension.
//
// Two rules matter more than the syntax:
//
//  1. **First match wins**, in file order. The authoring file's order is the
//     priority order, so this type must never sort, dedupe or reorder rules.
//  2. **Fail open.** Any decode error, any unknown action, any missing field
//     ends in `allow`. A wrongly junked bank alert is worse than a leaky promo.
//
// Drift risks with `rules.py`, called out because they are the only places the
// two implementations could quietly disagree:
//
//  * whitespace normalisation — Python `re.sub(r"\s+", " ")`; here every
//    whitespace run becomes one space, then lowercased;
//  * the `has_link` default — Python `https?://|\bwww\.`, mirrored below;
//  * the sender prefix — `templates.sender_prefix`, mirrored in `senderPrefix`
//    with the same operator-code list.

import Foundation

/// One rule from the compiled file.
public struct FilterRule: Decodable, Sendable, Equatable {
    public let id: String
    public let when: FilterConditions
    public let then: FilterOutcome
}

/// The `when` block. Every field is optional: a rule constrains what it needs.
public struct FilterConditions: Decodable, Sendable, Equatable {
    public let senderPrefixes: [String]?
    public let senderKinds: [String]?
    public let any: [String]?
    public let all: [String]?
    public let none: [String]?
    public let hasLink: Bool?
    public let minAmount: Double?
    public let maxAmount: Double?

    enum CodingKeys: String, CodingKey {
        case senderPrefixes = "sender_prefixes"
        case senderKinds = "sender_kinds"
        case any
        case all
        case none
        case hasLink = "has_link"
        case minAmount = "min_amount"
        case maxAmount = "max_amount"
    }
}

/// The `then` block. Raw strings, converted to the contract enums on read so an
/// unknown value degrades to `allow` instead of failing the whole file.
public struct FilterOutcome: Decodable, Sendable, Equatable {
    public let action: String
    public let subAction: String
    public let confidence: Double
    public let reason: String

    enum CodingKeys: String, CodingKey {
        case action
        case subAction = "sub_action"
        case confidence
        case reason
    }
}

/// What the extension should answer for one message.
public struct FilterVerdict: Sendable, Equatable {
    public let ruleID: String
    public let action: AppleAction
    public let subAction: AppleSubAction
    public let confidence: Double
    public let reason: String

    public init(ruleID: String, action: AppleAction, subAction: AppleSubAction, confidence: Double, reason: String) {
        self.ruleID = ruleID
        self.action = action
        self.subAction = subAction
        self.confidence = confidence
        self.reason = reason
    }

    /// `allow` with no sub-action: what "no rule matched" means.
    public static let allow = FilterVerdict(
        ruleID: "-",
        action: .allow,
        subAction: .none,
        confidence: 0,
        reason: "no rule matched"
    )
}

/// The compiled rule file, ready to evaluate.
public struct FilterRuleSet: Decodable, Sendable, Equatable {
    public let version: Int?
    public let syntax: String?
    public let declaredSubActions: [String]?
    public let rules: [FilterRule]

    enum CodingKeys: String, CodingKey {
        case version
        case syntax
        case declaredSubActions = "declared_sub_actions"
        case rules
    }

    /// Decode from a file. Throws — callers that must not crash use `load`.
    public init(contentsOf url: URL) throws {
        let data = try Data(contentsOf: url)
        self = try JSONDecoder().decode(FilterRuleSet.self, from: data)
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        version = try container.decodeIfPresent(Int.self, forKey: .version)
        syntax = try container.decodeIfPresent(String.self, forKey: .syntax)
        declaredSubActions = try container.decodeIfPresent([String].self, forKey: .declaredSubActions)
        rules = try container.decode([FilterRule].self, forKey: .rules)
    }

    /// Fail-open loader for the extension: `nil` means "no local rules", and the
    /// caller then asks the server or allows the message.
    ///
    /// A malformed rules file must never take the extension down: the failure
    /// mode of that is every SMS landing in Junk until the next app update.
    public static func load(at url: URL?) -> FilterRuleSet? {
        guard let url else { return nil }
        guard let data = try? Data(contentsOf: url) else { return nil }
        guard let decoded = try? JSONDecoder().decode(FilterRuleSet.self, from: data) else { return nil }
        // A file from a different DSL version is not guessed at; it is ignored.
        if let syntax = decoded.syntax, syntax != FilterRuleSet.syntaxName { return nil }
        return decoded
    }

    /// The file shipped in the app bundle (`filter_rules.json` in Copy Bundle
    /// Resources). Absent or broken → `nil`, i.e. fail open.
    public static func bundled(in bundle: Bundle = .main) -> FilterRuleSet? {
        let url = bundle.url(forResource: "filter_rules", withExtension: "json")
        return load(at: url)
    }

    public static let syntaxName = "sms-wiz.filter/v1"

    /// First matching rule wins; `nil` means "no opinion, allow".
    public func verdict(
        sender: String,
        body: String,
        senderKind: String = "unknown",
        hasLink: Bool? = nil,
        amount: Double? = nil
    ) -> FilterVerdict? {
        let text = FilterRuleSet.normalise(body)
        let link = hasLink ?? FilterRuleSet.containsLink(body)
        let prefix = FilterRuleSet.senderPrefix(sender)
        for rule in rules {
            guard FilterRuleSet.matches(rule.when, text: text, prefix: prefix, senderKind: senderKind, hasLink: link, amount: amount) else {
                continue
            }
            // Unknown action/sub-action values degrade instead of throwing: a
            // rule that cannot be honoured must not decide the folder.
            guard let action = AppleAction(rawValue: rule.then.action) else { return FilterVerdict.allow }
            let subAction = AppleSubAction(rawValue: rule.then.subAction) ?? .none
            return FilterVerdict(
                ruleID: rule.id,
                action: action,
                subAction: subAction,
                confidence: rule.then.confidence,
                reason: rule.then.reason
            )
        }
        return nil
    }

    // MARK: - Matching

    static func matches(
        _ when: FilterConditions,
        text: String,
        prefix: String?,
        senderKind: String,
        hasLink: Bool,
        amount: Double?
    ) -> Bool {
        if let prefixes = when.senderPrefixes, !prefixes.isEmpty {
            guard let prefix, prefixes.contains(where: { $0.uppercased() == prefix }) else { return false }
        }
        if let kinds = when.senderKinds, !kinds.isEmpty {
            guard kinds.contains(where: { $0.lowercased() == senderKind.lowercased() }) else { return false }
        }
        if when.hasLink == true && !hasLink { return false }
        if when.hasLink == false && hasLink { return false }
        if let amount {
            if let minimum = when.minAmount, amount < minimum { return false }
            if let maximum = when.maxAmount, amount > maximum { return false }
        }
        for term in when.all ?? [] where !text.contains(term.lowercased()) {
            return false
        }
        for term in when.none ?? [] where text.contains(term.lowercased()) {
            return false
        }
        if let anyTerms = when.any, !anyTerms.isEmpty {
            guard anyTerms.contains(where: { text.contains($0.lowercased()) }) else { return false }
        }
        return true
    }

    /// Collapse whitespace runs and lowercase — the same text `rules.py` sees.
    static func normalise(_ body: String) -> String {
        var out = ""
        var pendingSpace = false
        for character in body.lowercased() {
            if character.isWhitespace {
                pendingSpace = !out.isEmpty
                continue
            }
            if pendingSpace {
                out.append(" ")
                pendingSpace = false
            }
            out.append(character)
        }
        return out
    }

    /// `\bwww\.` and `https?://`, case-insensitively — the Python default.
    static func containsLink(_ body: String) -> Bool {
        let lowered = body.lowercased()
        if lowered.contains("http://") || lowered.contains("https://") { return true }
        var searchRange = lowered.startIndex..<lowered.endIndex
        while let found = lowered.range(of: "www.", range: searchRange) {
            if found.lowerBound == lowered.startIndex { return true }
            // `\b` in Python is "one side is a word character": letters, digits
            // or underscore. "x.www." is a boundary; "awww." is not.
            let before = lowered[lowered.index(before: found.lowerBound)]
            if !before.isLetter && !before.isNumber && before != "_" { return true }
            searchRange = found.upperBound..<lowered.endIndex
        }
        return false
    }

    /// Mirror of `templates.sender_prefix`: `AD-HDFCBK-T` → `HDFCBK`.
    ///
    /// Two-part headers are the interesting case: `SBICRD-T` is the bank's own
    /// code, `AX-SBICRD` is an operator followed by it. A two-character first
    /// part is an operator; anything longer is the entity and must not be skipped.
    static func senderPrefix(_ sender: String) -> String? {
        let parts = sender
            .uppercased()
            .split(whereSeparator: { $0 == "-" || $0 == "_" || $0.isWhitespace })
            .map(String.init)
        guard let first = parts.first else { return nil }
        if parts.count >= 3 && first.count <= 3 { return parts[1] }
        if parts.count == 2 {
            let second = parts[1]
            return (operatorCodes.contains(first) || first.count <= 2) ? second : first
        }
        return first
    }

    /// `templates._OPERATOR_CODES`, kept in the same order for easy diffing.
    static let operatorCodes: Set<String> = [
        "AD", "AX", "VM", "VK", "TM", "BT", "BM", "BW", "JD", "JK", "GX", "TA", "TD", "CP",
    ]
}
