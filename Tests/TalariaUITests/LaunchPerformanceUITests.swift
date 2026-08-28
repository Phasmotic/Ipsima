import XCTest

/// Retain exactly five application-launch observations while G12 cold launch is
/// stood down for re-specification. The fail-closed Tier B checker validates and
/// preserves every sample but cannot emit a performance verdict.
final class LaunchPerformanceUITests: XCTestCase {
    @MainActor
    func testLaunchMetricBaselineRecorded() {
        let app = XCUIApplication()
        let rootElement = app.staticTexts["Talaria"]
        self.terminateAndAssertNotRunning(app)

        let options = XCTMeasureOptions()
        options.iterationCount = 5
        measure(metrics: [XCTApplicationLaunchMetric()], options: options) {
            XCTAssertEqual(app.state, .notRunning, "launch metric must start with the app terminated")
            app.launch()
            XCTAssertTrue(
                rootElement.waitForExistence(timeout: 10),
                "expected root element did not appear during the measured launch"
            )
            self.terminateAndAssertNotRunning(app)
        }
    }

    @MainActor
    private func terminateAndAssertNotRunning(_ app: XCUIApplication) {
        app.terminate()
        XCTAssertTrue(
            app.wait(for: .notRunning, timeout: 10),
            "application did not reach the terminated state"
        )
    }
}
