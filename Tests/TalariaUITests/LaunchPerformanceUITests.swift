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
    func testLaunchMetricBaselineRecorded() throws {
        let app = XCUIApplication()
        app.launch()

        let metric = XCTApplicationLaunchMetric()
        measure(metrics: [metric]) {
            app.terminate()
            app.launch()
        }
    }

    @MainActor
    func testColdLaunchWithinBudget() throws {
        let app = XCUIApplication()

        let clock = ContinuousClock()
        let elapsed = try clock.measure {
            app.launch()
            _ = app.staticTexts["Talaria"].waitForExistence(timeout: 10)
        }

        XCTAssertLessThan(elapsed, .seconds(3), "cold launch exceeded the 3 s budget")
    }
}
