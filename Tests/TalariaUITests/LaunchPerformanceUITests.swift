import XCTest

/// G12 — record exactly five official cold-launch samples in the result
/// bundle. The fail-closed Tier B checker enforces the absolute 3.0 s budget
/// over these samples; the streaming main-thread clause arms in P2.
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
