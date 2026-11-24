import XCTest
@testable import SMSCore

/// The shared fixtures in `contracts/fixtures/` are the app/backend handshake.
/// Python replays them through the real pipeline; here we only prove that the
/// values a fixture asserts are decodable by the Swift model. Decoding failure
/// means the backend will hand the app something it cannot read.
final class FixtureDecodeTests: XCTestCase {

    private func fixturesDirectory() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("contracts/fixtures")
    }

    private struct Fixture: Decodable {
        let id: String
        let expect: [String: AnyCodableValue]
    }

    /// Minimal dynamic JSON value so the test does not need to model every
    /// optional expectation field.
    enum AnyCodableValue: Decodable {
        case string(String), number(Double), bool(Bool), other

        init(from decoder: Decoder) throws {
            let container = try decoder.singleValueContainer()
            if let value = try? container.decode(String.self) { self = .string(value) }
            else if let value = try? container.decode(Bool.self) { self = .bool(value) }
            else if let value = try? container.decode(Double.self) { self = .number(value) }
            else { self = .other }
        }
    }

    func testEveryFixtureDecodesAndUsesKnownCases() throws {
        let urls = try FileManager.default
            .contentsOfDirectory(at: fixturesDirectory(), includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }

        XCTAssertFalse(urls.isEmpty, "no fixtures found in \(fixturesDirectory().path)")

        for url in urls {
            let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: url))

            if case let .string(intent)? = fixture.expect["intent"] {
                XCTAssertNotNil(SMSEventIntent(rawValue: intent), "\(fixture.id): unknown intent \(intent)")
            }
            if case let .string(action)? = fixture.expect["apple_action"] {
                XCTAssertNotNil(AppleAction(rawValue: action), "\(fixture.id): unknown action \(action)")
            }
            if case let .string(subAction)? = fixture.expect["apple_subaction"] {
                guard let parsed = AppleSubAction(rawValue: subAction) else {
                    return XCTFail("\(fixture.id): unknown sub-action \(subAction)")
                }
                XCTAssertTrue(parsed.isDeclared, "\(fixture.id): \(subAction) is not declared to iOS")
            }
            if case let .string(retention)? = fixture.expect["retention"] {
                XCTAssertNotNil(Retention(rawValue: retention), "\(fixture.id): unknown retention")
            }
            if case let .string(treatment)? = fixture.expect["ledger_treatment"] {
                XCTAssertNotNil(LedgerTreatment(rawValue: treatment), "\(fixture.id): unknown treatment")
            }
        }
    }
}
