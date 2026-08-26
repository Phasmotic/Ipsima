import XCTest

final class SmokeUITests: XCTestCase {
    /// Launch + first-element assertion; extended by G8/G10/G11 flows later.
    func testLaunchShowsShell() {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.staticTexts["Talaria"].waitForExistence(timeout: 10))
    }
}
