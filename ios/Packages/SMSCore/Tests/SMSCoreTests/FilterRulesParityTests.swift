import XCTest
@testable import SMSCore

/// Replays the same fixtures the Python suite replays.
///
/// `tests/fixtures/filter_rules/cases.json` is generated from the authoring file
/// and records what `rules.py` answers for each (sender, body, kind). If this
/// file and that one disagree, the extension would route a message differently
/// from the server — the one failure this pairing exists to make impossible.
///
/// Status: **written, not run** here (no Swift toolchain). `make swift-test` on a
/// machine with Swift is the gate.
final class FilterRulesParityTests: XCTestCase {

    private struct CaseFile: Decodable {
        let cases: [Case]
    }

    private struct Case: Decodable {
        let id: String
        let sender: String
        let senderKind: String
        let body: String
        let expect: Expectation

        enum CodingKeys: String, CodingKey {
            case id
            case sender
            case senderKind = "sender_kind"
            case body
            case expect
        }
    }

    private struct Expectation: Decodable {
        let matched: Bool
        let ruleID: String?
        let action: String
        let subAction: String

        enum CodingKeys: String, CodingKey {
            case matched
            case ruleID = "rule_id"
            case action
            case subAction = "sub_action"
        }
    }

    private func repoFile(_ relative: String) -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // Tests/SMSCoreTests
            .deletingLastPathComponent()   // Tests
            .deletingLastPathComponent()   // SMSCore
            .deletingLastPathComponent()   // Packages
            .deletingLastPathComponent()   // ios
            .deletingLastPathComponent()   // repo root
            .appendingPathComponent(relative)
    }

    private func ruleSet() throws -> FilterRuleSet {
        let url = repoFile("contracts/filter_rules.json")
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(FilterRuleSet.self, from: data)
    }

    private func cases() throws -> [Case] {
        let data = try Data(contentsOf: repoFile("tests/fixtures/filter_rules/cases.json"))
        return try JSONDecoder().decode(CaseFile.self, from: data).cases
    }

    func testTheCompiledFileDecodes() throws {
        let set = try ruleSet()
        XCTAssertEqual(set.syntax, FilterRuleSet.syntaxName)
        XCTAssertFalse(set.rules.isEmpty)
        XCTAssertEqual(set.declaredSubActions?.count, 8)
    }

    func testEveryFixtureCaseAgreesWithPython() throws {
        let set = try ruleSet()
        for testCase in try cases() {
            let verdict = set.verdict(
                sender: testCase.sender,
                body: testCase.body,
                senderKind: testCase.senderKind
            )
            if testCase.expect.matched {
                XCTAssertNotNil(verdict, "no rule matched \(testCase.id), Python matched \(testCase.expect.ruleID ?? "-")")
            } else {
                XCTAssertNil(verdict, "\(testCase.id) matched \(verdict?.ruleID ?? "-"), Python allowed it")
            }
            guard let verdict else { continue }
            XCTAssertEqual(verdict.ruleID, testCase.expect.ruleID, testCase.id)
            XCTAssertEqual(verdict.action.rawValue, testCase.expect.action, testCase.id)
            XCTAssertEqual(verdict.subAction.rawValue, testCase.expect.subAction, testCase.id)
        }
    }

    func testSenderPrefixMatchesTheServer() {
        // The same examples that are asserted on the Python side.
        XCTAssertEqual(FilterRuleSet.senderPrefix("AD-HDFCBK-T"), "HDFCBK")
        XCTAssertEqual(FilterRuleSet.senderPrefix("AD-HDFCBK"), "HDFCBK")
        XCTAssertEqual(FilterRuleSet.senderPrefix("SBICRD-T"), "SBICRD")
        XCTAssertEqual(FilterRuleSet.senderPrefix("AX-SBICRD"), "SBICRD")
        XCTAssertEqual(FilterRuleSet.senderPrefix("AD-HDFCBK-S"), "HDFCBK")
        XCTAssertEqual(FilterRuleSet.senderPrefix("57575"), "57575")
        XCTAssertEqual(FilterRuleSet.senderPrefix("919876543210"), "919876543210")
    }

    func testNormalisationCollapsesWhitespace() {
        XCTAssertEqual(FilterRuleSet.normalise("  Easy\tEMI \n offer "), "easy emi offer")
        XCTAssertTrue(FilterRuleSet.normalise("PRE-APPROVED").hasSuffix("pre-approved"))
    }

    func testLinkDetectionMatchesThePythonDefault() {
        XCTAssertTrue(FilterRuleSet.containsLink("click https://x.example/a"))
        XCTAssertTrue(FilterRuleSet.containsLink("see www.x.example"))
        XCTAssertTrue(FilterRuleSet.containsLink("HTTP://X.EXAMPLE"))
        XCTAssertFalse(FilterRuleSet.containsLink("awww.example is not a link"))
        XCTAssertFalse(FilterRuleSet.containsLink("no links here"))
    }

    func testAMalformedFileFailsOpen() throws {
        let broken = FileManager.default.temporaryDirectory.appendingPathComponent("broken-rules.json")
        try "{ not json }".write(to: broken, atomically: true, encoding: .utf8)
        XCTAssertNil(FilterRuleSet.load(at: broken), "a broken file must not take the extension down")
        XCTAssertNil(FilterRuleSet.load(at: nil))
    }

    func testAnUnknownActionInARuleDegradesToAllow() throws {
        let json = """
        {
          "syntax": "sms-wiz.filter/v1",
          "rules": [
            {
              "id": "test.future_action",
              "when": { "any": ["hello"] },
              "then": { "action": "quarantine", "sub_action": "none", "confidence": 0.9, "reason": "unknown action" }
            }
          ]
        }
        """
        let set = try JSONDecoder().decode(FilterRuleSet.self, from: Data(json.utf8))
        let verdict = set.verdict(sender: "9876543210", body: "hello")
        XCTAssertEqual(verdict?.action, .allow)
    }
}

/// Sender kinds are what several security rules key on: if the extension calls a
/// bank header "unknown", a genuine KYC reminder is one rule away from Junk.
/// The map ships as data (`contracts/sender_kinds.json`) precisely so this test
/// can compare it with the server's registry.
final class SenderKindTests: XCTestCase {

    private func map() throws -> SenderKind.Map {
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("contracts/sender_kinds.json")
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(SenderKind.Map.self, from: data)
    }

    func testTheMapDecodesAndCarriesTheRegistry() throws {
        let map = try map()
        XCTAssertEqual(map.syntax, SenderKind.syntaxName)
        XCTAssertGreaterThan(map.codes.count, 300, "one entry per unique registry code")
        XCTAssertEqual(map.codes["HDFCBK"], "bank")
        XCTAssertEqual(map.defaultKind, "unknown")
    }

    func testHeadersResolveLikeTheServer() throws {
        let map = try map()
        XCTAssertEqual(SenderKind.resolve("AD-HDFCBK-T", using: map), "bank")
        XCTAssertEqual(SenderKind.resolve("VM-HDFCBK", using: map), "bank")
        XCTAssertEqual(SenderKind.resolve("AD-AMAZON-S", using: map), "brand")
        XCTAssertEqual(SenderKind.resolve("AD-PAYTM-S", using: map), "fintech")
        // An alias with a space in it ("HDFC BANK") must not be treated as a
        // header: the server calls that sender unknown, and so must this.
        XCTAssertEqual(SenderKind.resolve("AD-HDFC BANK-S", using: map), "unknown")
        XCTAssertEqual(SenderKind.resolve("AD-NOSUCHCODE-S", using: map), "unknown")
    }

    func testNumbersResolveByShape() throws {
        let map = try map()
        XCTAssertEqual(SenderKind.resolve("9876543210", using: map), "personal_mobile")
        XCTAssertEqual(SenderKind.resolve("+91 98765 43210", using: map), "personal_mobile")
        XCTAssertEqual(SenderKind.resolve("57575", using: map), "shortcode")
        XCTAssertEqual(SenderKind.resolve("1909", using: map), "shortcode")
    }

    func testWithoutAMapEverySenderIsUnknown() {
        XCTAssertEqual(SenderKind.resolve("AD-HDFCBK-T", using: nil), "unknown")
        XCTAssertEqual(SenderKind.resolve("9876543210", using: nil), "personal_mobile",
                       "shape is still knowable without the map")
    }
}
