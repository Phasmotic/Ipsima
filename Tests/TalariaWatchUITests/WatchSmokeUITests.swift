import XCTest

/// watchOS UI smoke — proves the watch app boots and renders its root view.
final class WatchSmokeUITests: XCTestCase {
    func testWatchAppLaunchesAndShowsRoot() {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.staticTexts["Talaria"].waitForExistence(timeout: 15))
    }
}
