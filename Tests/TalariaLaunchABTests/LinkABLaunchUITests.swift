import XCTest

/// Diagnostic-only single-sample observer used by the interleaved link A/B study.
/// It has no threshold and cannot produce a G12 verdict.
final class LinkABLaunchUITests: XCTestCase {
    @MainActor
    func testOneLaunchObservation() {
        let app = XCUIApplication()
        let rootElement = app.staticTexts["Talaria"]
        self.terminateAndAssertNotRunning(app)

        let options = XCTMeasureOptions()
        options.iterationCount = 1
        measure(metrics: [XCTApplicationLaunchMetric()], options: options) {
            XCTAssertEqual(app.state, .notRunning)
            app.launch()
            XCTAssertTrue(rootElement.waitForExistence(timeout: 10))
            self.terminateAndAssertNotRunning(app)
        }
    }

    @MainActor
    private func terminateAndAssertNotRunning(_ app: XCUIApplication) {
        app.terminate()
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 10))
    }
}
