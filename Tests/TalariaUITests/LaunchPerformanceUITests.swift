import XCTest

/// G12 — cold-launch budget, enforced twice:
///   1. XCTApplicationLaunchMetric records the official baseline into the
///      result bundle.
///   2. A wall-clock assertion gives the gate teeth: cold start must beat
///      3.0 s measured from `launch()` to first asserted element. This is a
///      harness-side proxy (includes simulator overhead); the streaming
///      main-thread clause arms in P2 when live streams exist.
final class LaunchPerformanceUITests: XCTestCase {
    @MainActor
    func testLaunchMetricBaselineRecorded() {
        let app = XCUIApplication()
        let rootElement = app.staticTexts["Talaria"]
        self.terminateAndAssertNotRunning(app)

        let metric = XCTApplicationLaunchMetric()
        measure(metrics: [metric]) {
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
    func testColdLaunchWithinBudget() {
        let app = XCUIApplication()
        let rootElement = app.staticTexts["Talaria"]
        self.terminateAndAssertNotRunning(app)

        let clock = ContinuousClock()
        let elapsed = clock.measure {
            XCTAssertEqual(app.state, .notRunning, "cold-launch timer must start with the app terminated")
            app.launch()
            XCTAssertTrue(
                rootElement.waitForExistence(timeout: 10),
                "expected root element did not appear before cold-launch timing stopped"
            )
        }

        XCTAssertLessThan(elapsed, .seconds(3), "cold launch exceeded the 3 s budget")
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
