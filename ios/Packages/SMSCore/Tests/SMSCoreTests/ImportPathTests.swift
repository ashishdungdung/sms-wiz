import XCTest
@testable import SMSCore

/// The two import paths that actually work on a phone: paste, and share.
///
/// Both funnel into `PasteParser` + `APIClient`, so this file covers the parsing
/// rules and the request the client builds. Network behaviour is not tested
/// here; status: written, not run (no Swift toolchain in the authoring
/// environment — `make swift-test` on a Mac is the gate).
final class PasteParserTests: XCTestCase {

    func testAHeaderWithTheBodyOnTheSameLine() {
        let messages = PasteParser.parse("AD-HDFCBK-T: Rs.450.00 debited from A/c XX1234 at SWIGGY")
        XCTAssertEqual(messages.count, 1)
        XCTAssertEqual(messages[0].sender, "AD-HDFCBK-T")
        XCTAssertEqual(messages[0].body, "Rs.450.00 debited from A/c XX1234 at SWIGGY")
    }

    func testAHeaderOnItsOwnLine() {
        let messages = PasteParser.parse("VM-HDFCBK\nRs 99 credited to A/c XX1234")
        XCTAssertEqual(messages.count, 1)
        XCTAssertEqual(messages[0].sender, "VM-HDFCBK")
        XCTAssertEqual(messages[0].body, "Rs 99 credited to A/c XX1234")
    }

    func testManyBubblesWithBlankLinesBetweenThem() {
        let blob = """
        AD-HDFCBK-T: Rs 100 debited at CAFE

        AD-ICICIB-S: Rs 250 debited at SWIGGY

        """
        let messages = PasteParser.parse(blob)
        XCTAssertEqual(messages.count, 2)
        XCTAssertEqual(messages.map(\.sender), ["AD-HDFCBK-T", "AD-ICICIB-S"])
    }

    func testABodyWithoutAHeaderIsStillImported() {
        let messages = PasteParser.parse("Rs 1,299 spent on your SBI Card XX4433 at CROMA")
        XCTAssertEqual(messages.count, 1)
        XCTAssertEqual(messages[0].sender, "")
        XCTAssertFalse(messages[0].hasSender, "no header means no sender, not a guessed one")
    }

    func testAWrappedBankMessageStaysOneMessage() {
        let blob = """
        AD-HDFCBK-T: Rs.1,450.00 debited from A/c XX4092 on 25-02-25
        to VPA KIRANAKART@axisbank. UPI Ref 505612345678.
        Avl Bal Rs.5,000.00
        """
        let messages = PasteParser.parse(blob)
        XCTAssertEqual(messages.count, 1)
        XCTAssertTrue(messages[0].body.contains("UPI Ref 505612345678"))
        XCTAssertTrue(messages[0].body.contains("Avl Bal"))
    }

    func testSmsFromPrefix() {
        let messages = PasteParser.parse("SMS from VM-HDFCBK Rs 450 debited at ZOMATO")
        XCTAssertEqual(messages[0].sender, "VM-HDFCBK")
        XCTAssertTrue(messages[0].body.contains("Rs 450 debited"))
    }

    func testAMobileNumberIsAHeader() {
        XCTAssertEqual(PasteParser.sender(in: "9876543210: your parcel is out for delivery"), "9876543210")
        XCTAssertEqual(PasteParser.sender(in: "+91 98765 43210"), nil, "spaced numbers are prose, not a header")
    }

    func testAnAmountIsNotASender() {
        XCTAssertNil(PasteParser.sender(in: "2026 was a good year"))
        XCTAssertNil(PasteParser.sender(in: "Rs 500 debited"))
    }

    func testAnEmptyBlobImportsNothing() {
        XCTAssertTrue(PasteParser.parse("   \n\n  ").isEmpty)
    }

    func testShortCodeHeaders() {
        XCTAssertEqual(PasteParser.sender(in: "57575: OTP 123456 for your login"), "57575")
        XCTAssertNil(PasteParser.sender(in: "1234 is not a short code"))
    }
}

final class APIClientTests: XCTestCase {

    private func client(token: String = "s3cret") -> APIClient {
        APIClient(config: AppConfig(baseURL: "http://192.168.0.5:8000/", token: token))
    }

    func testBaseUrlTrailingSlashDoesNotDouble() throws {
        let request = try client().request(path: "/api/v1/events")
        XCTAssertEqual(request.url?.absoluteString, "http://192.168.0.5:8000/api/v1/events")
    }

    func testEveryRequestCarriesTheToken() throws {
        let request = try client().request(path: "/api/v1/events")
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-SMSWiz-Token"), "s3cret")
    }

    func testAnEmptyTokenSendsNoHeader() throws {
        let request = try client(token: "").request(path: "/health")
        XCTAssertNil(request.value(forHTTPHeaderField: "X-SMSWiz-Token"))
    }

    func testQueryItemsArePercentEncoded() throws {
        let request = try client().request(
            path: "/api/v1/events",
            query: [URLQueryItem(name: "month", value: "2026-11")]
        )
        XCTAssertEqual(request.url?.query, "month=2026-11")
    }

    func testAnUnconfiguredClientRefusesToBuildARequest() {
        XCTAssertThrowsError(try APIClient(config: AppConfig(baseURL: "", token: "")).request(path: "/health")) { error in
            XCTAssertEqual(error as? APIError, .notConfigured)
        }
    }

    func testUnauthorisedIsItsOwnError() {
        let response = HTTPURLResponse(url: URL(string: "http://x")!, statusCode: 401, httpVersion: nil, headerFields: nil)!
        XCTAssertThrowsError(try APIClient.validate(response, data: Data())) { error in
            XCTAssertEqual(error as? APIError, .unauthorised)
        }
    }

    func testFilenameComesFromContentDisposition() {
        XCTAssertEqual(
            APIClient.filename(fromContentDisposition: "attachment; filename=\"sms-wiz-2026-11-24.csv\""),
            "sms-wiz-2026-11-24.csv"
        )
        XCTAssertNil(APIClient.filename(fromContentDisposition: "attachment"))
    }
}

final class AppConfigTests: XCTestCase {

    override func setUp() {
        super.setUp()
        AppConfig.storeOverride = UserDefaults(suiteName: "sms-wiz.tests")
        AppConfig.writePending([])
    }

    override func tearDown() {
        AppConfig.writePending([])
        AppConfig.storeOverride = nil
        super.tearDown()
    }

    func testQueueSurvivesARoundTrip() {
        AppConfig.writePending([])
        AppConfig.appendPending(.init(sender: "AD-HDFCBK-T", body: "Rs 100 debited"))
        AppConfig.appendPending(messageBody: "Rs 200 debited", sender: "AD-HDFCBK-T")
        let queued = AppConfig.pendingMessages()
        XCTAssertEqual(queued.count, 2)
        XCTAssertEqual(queued.last?.body, "Rs 200 debited")
        AppConfig.writePending([])
        XCTAssertTrue(AppConfig.pendingMessages().isEmpty)
    }

    func testTheQueueIsCapped() {
        AppConfig.writePending([])
        for index in 0..<510 {
            AppConfig.appendPending(.init(sender: "X", body: "message \(index)"))
        }
        let queued = AppConfig.pendingMessages()
        XCTAssertEqual(queued.count, 500)
        XCTAssertEqual(queued.last?.body, "message 509")
        AppConfig.writePending([])
    }
}
