import XCTest

/// G10 — accessibility audit on every primary screen.
/// P0 has exactly one primary screen; the audit grows with the surface count
/// (one audit call per screen, zero critical findings allowed).
final class AccessibilityAuditUITests: XCTestCase {
    func testRootScreenPassesAccessibilityAudit() throws {
        guard #available(iOS 17.0, *) else {
            throw XCTSkip("performAccessibilityAudit requires iOS 17+")
        }
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.staticTexts["Talaria"].waitForExistence(timeout: 10))

        try app.performAccessibilityAudit()
    }
}
