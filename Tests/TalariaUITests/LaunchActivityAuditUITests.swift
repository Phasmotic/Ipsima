import XCTest

final class LaunchActivityAuditUITests: XCTestCase {
    @MainActor
    func testFirstDrawHasNoTalariaOwnedLaunchActivity() {
        let app = XCUIApplication()
        app.launchEnvironment["TALARIA_LAUNCH_AUDIT"] = "1"
        app.launch()

        let probe = app.otherElements["launch-audit-frame"]
        XCTAssertTrue(
            probe.waitForExistence(timeout: 10),
            "the UIKit first-draw probe never became observable"
        )

        let clear = NSPredicate(format: "value == %@", "clear")
        let clearExpectation = XCTNSPredicateExpectation(predicate: clear, object: probe)
        XCTAssertEqual(
            XCTWaiter.wait(for: [clearExpectation], timeout: 10),
            .completed,
            "Talaria-owned URLSession, reachability, or timer activity preceded the first frame"
        )
    }
}
