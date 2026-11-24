import XCTest
@testable import SMSCore

/// Compile-time-ish guard that the Swift raw values equal the generated contract.
///
/// The JSON contract lives outside the package on purpose: it is produced by the
/// Python side, so the test proves the two languages agree rather than that the
/// package agrees with itself.
final class ContractsParityTests: XCTestCase {

    private struct Enums: Decodable {
        let intents: [String]
        let apple_actions: [String]
        let apple_subactions: [String]
        let retentions: [String]
        let ledger_treatments: [String]
        let entity_kinds: [String]
    }

    /// Repo-root/contracts/enums.json, resolved from this file's path so it works
    /// with `swift test`, SPM in Xcode, and CI checkouts alike.
    private func contractURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()          // Tests/SMSCoreTests
            .deletingLastPathComponent()          // Tests
            .deletingLastPathComponent()          // SMSCore
            .deletingLastPathComponent()          // Packages
            .deletingLastPathComponent()          // ios
            .deletingLastPathComponent()          // repo root
            .appendingPathComponent("contracts/enums.json")
    }

    private func loadEnums() throws -> Enums {
        let data = try Data(contentsOf: contractURL())
        return try JSONDecoder().decode(Enums.self, from: data)
    }

    func testContractFileIsReachable() throws {
        XCTAssertTrue(
            FileManager.default.fileExists(atPath: contractURL().path),
            "contracts/enums.json not found at \(contractURL().path)"
        )
    }

    func testIntentsMatchContract() throws {
        let enums = try loadEnums()
        XCTAssertEqual(Set(SMSEventIntent.allCases.map(\.rawValue)), Set(enums.intents))
    }

    func testAppleActionsMatchContract() throws {
        let enums = try loadEnums()
        XCTAssertEqual(Set(AppleAction.allCases.map(\.rawValue)), Set(enums.apple_actions))
    }

    func testSubActionsMatchContract() throws {
        let enums = try loadEnums()
        XCTAssertEqual(Set(AppleSubAction.allCases.map(\.rawValue)), Set(enums.apple_subactions))
    }

    func testRetentionsMatchContract() throws {
        let enums = try loadEnums()
        XCTAssertEqual(Set(Retention.allCases.map(\.rawValue)), Set(enums.retentions))
    }

    func testLedgerTreatmentsMatchContract() throws {
        let enums = try loadEnums()
        XCTAssertEqual(Set(LedgerTreatment.allCases.map(\.rawValue)), Set(enums.ledger_treatments))
    }

    func testEntityKindsMatchContract() throws {
        let enums = try loadEnums()
        XCTAssertEqual(Set(EntityKind.allCases.map(\.rawValue)), Set(enums.entity_kinds))
    }

    // Apple's hard limits, asserted locally so nobody "improves" the list in a
    // way that silently breaks folder rendering (FB15385442).
    func testDeclaredSubActionsRespectAppleLimits() {
        XCTAssertLessThanOrEqual(AppleSubAction.declaredTransactional.count, 5)
        XCTAssertLessThanOrEqual(AppleSubAction.declaredPromotional.count, 5)
        XCTAssertTrue(AppleSubAction.declaredTransactional.allSatisfy { $0.rawValue.hasPrefix("transactional") })
        XCTAssertTrue(AppleSubAction.declaredPromotional.allSatisfy { $0.rawValue.hasPrefix("promotional") })
        for subAction in AppleSubAction.allCases where subAction != .none {
            XCTAssertTrue(subAction.isDeclared, "\(subAction.rawValue) would be dropped by iOS")
        }
    }

    func testOnlyExpenseAndIncomeCountTowardTotals() {
        let counting = LedgerTreatment.allCases.filter(\.countsTowardSpend)
        XCTAssertEqual(counting, [.expense])
        XCTAssertEqual(LedgerTreatment.allCases.filter(\.countsTowardIncome), [.income])
        XCTAssertFalse(LedgerTreatment.internalTransfer.countsTowardSpend)
        XCTAssertFalse(LedgerTreatment.instrumentLoad.countsTowardSpend)
    }
}
