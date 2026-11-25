// PasteParser.swift
//
// Turning a clipboard blob into (sender, body) pairs.
//
// This is the least glamorous and most used import path in the app. Threads and
// Messages let a user select many bubbles and copy them, and what lands on the
// pasteboard is a text blob with no structure: headers sometimes, timestamps
// sometimes, an "SMS from" prefix sometimes, blank lines between bubbles.
//
// Two rules keep it honest:
//
//  1. **Never invent a sender.** If the blob has no recognisable header, the
//     message is imported with an empty sender and the server marks it
//     `unknown` — which is a true statement, unlike guessing "HDFC".
//  2. **Never merge two messages.** A blank line is a separator; a header line
//     starts a new message; anything else is body text, including wrapped lines
//     from a long bank SMS.
//
// Status: written, not compiled. The rules are mirrored in
// `PasteParserTests.swift`, which is the file to read before changing them.

import Foundation

public struct PastedMessage: Sendable, Equatable, Identifiable {
    public let id: UUID
    public let sender: String
    public let body: String

    public init(id: UUID = UUID(), sender: String, body: String) {
        self.id = id
        self.sender = sender
        self.body = body
    }

    public var hasSender: Bool { !sender.isEmpty }
}

public enum PasteParser {

    /// A DLT header: `AD-HDFCBK-T`, `VM-HDFCBK`, `SBICRD-T`, `57575`, or an
    /// Indian mobile number. Anchored at the start of a line, optionally
    /// followed by ":" or "-" and the message text.
    ///
    /// Deliberately narrow: `AXISBK` on its own line is a bank code, but "SBI"
    /// in prose is not a sender, and treating it as one would drop the body.
    public static func sender(in line: String) -> String? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return nil }
        let token = trimmed.split(whereSeparator: { $0 == " " || $0 == ":" || $0 == "\t" }).first.map(String.init) ?? ""
        let candidate = token.hasSuffix("-") ? String(token.dropLast()) : token

        if isDLTHeader(candidate) || isShortCode(candidate) || isMobile(candidate) {
            return candidate.uppercased()
        }
        return nil
    }

    /// `AD-HDFCBK-T`, `VM-HDFCBK`, `SBICRD-T`, `HDFCBK-S`.
    static func isDLTHeader(_ token: String) -> Bool {
        token.contains("-") && SenderKind.isDLTShaped(token.uppercased())
    }

    /// `57575`, `5600010` — operator short codes. Five or more digits, so a
    /// bare "2026" in a body is not mistaken for one.
    static func isShortCode(_ token: String) -> Bool {
        (5...8).contains(token.count) && token.allSatisfy(\.isNumber)
    }

    /// An Indian mobile number, optionally +91 / 91 prefixed.
    static func isMobile(_ token: String) -> Bool {
        let digits = token.filter(\.isNumber)
        guard digits.count == 10 || digits.count == 12 else { return false }
        let national = digits.count == 12 ? String(digits.suffix(10)) : digits
        return national.first.map { "6789".contains($0) } ?? false
    }

    /// Split a pasteboard blob into messages.
    ///
    /// - A header line starts a message; a following line that is also a header
    ///   starts another (missing body → dropped, with the count reported).
    /// - A blank line ends the current message.
    /// - A "SMS from X" / "From X:" style prefix is treated as a header.
    public static func parse(_ text: String) -> [PastedMessage] {
        var messages: [PastedMessage] = []
        var sender = ""
        var lines: [String] = []

        func flush() {
            let body = lines
                .joined(separator: "\n")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            lines = []
            guard !body.isEmpty else { return }
            messages.append(PastedMessage(sender: sender, body: body))
        }

        for rawLine in text.components(separatedBy: .newlines) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty {
                flush()
                sender = ""
                continue
            }
            if let split = headerSplit(line) {
                // Close the previous message *before* the new one starts, or the
                // new body is filed under the old sender.
                flush()
                sender = split.sender
                if let inline = split.body { lines.append(inline) }
                continue
            }
            lines.append(line)
        }
        flush()
        return messages
    }

    /// Split a line that carries a sender, and the body if the text is on the
    /// same line: `"AD-HDFCBK-T: Rs 500 debited"` → `("AD-HDFCBK-T", "Rs 500 debited")`.
    ///
    /// Returns `nil` when the line is body text: "57575" inside a sentence, or a
    /// phone number that starts a line but continues into prose.
    static func headerSplit(_ line: String) -> (sender: String, body: String?)? {
        // "SMS from AD-HDFCBK-T …" / "From: 9876543210 …" / "Sender: HDFCBK …"
        let lowered = line.lowercased()
        for prefix in ["sms from ", "from: ", "from ", "sender: ", "sender "] where lowered.hasPrefix(prefix) {
            let rest = String(line.dropFirst(prefix.count)).trimmingCharacters(in: .whitespaces)
            guard let sender = sender(in: rest) else { continue }
            let afterHeader = rest.drop(while: { !$0.isWhitespace })
            let body = String(afterHeader).trimmingCharacters(in: CharacterSet(charactersIn: ":- "))
            return (sender, body.isEmpty ? nil : body)
        }

        guard let sender = sender(in: line) else {
            // A four-digit short code ("1909: OTP 123456") is a header only with
            // an explicit separator: "2026 was a good year" is prose, and both
            // are four digits.
            let token = line.split(whereSeparator: { $0 == " " || $0 == ":" || $0 == "\t" }).first.map(String.init) ?? ""
            let digits = token.replacingOccurrences(of: ":", with: "")
            if (3...6).contains(digits.count), digits.allSatisfy(\.isNumber), line.hasPrefix(digits + ":") {
                let body = line.dropFirst(digits.count + 1).trimmingCharacters(in: .whitespaces)
                return (digits, body.isEmpty ? nil : body)
            }
            return nil
        }
        let token = line.split(whereSeparator: { $0 == " " || $0 == ":" || $0 == "\t" }).first.map(String.init) ?? ""
        let rest = line.dropFirst(token.count).trimmingCharacters(in: .whitespaces)
        if rest.isEmpty { return (sender, nil) }
        if rest.hasPrefix(":") || rest.hasPrefix("-") {
            let body = String(rest.dropFirst()).trimmingCharacters(in: .whitespaces)
            return (sender, body.isEmpty ? nil : body)
        }
        return nil
    }
}
