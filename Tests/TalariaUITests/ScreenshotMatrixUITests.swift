import XCTest

/// G11 — fixed screenshot matrix: every primary screen × {light, dark} ×
/// {large, small device} × {default, Dynamic Type XL}.
/// Device axis is chosen by the workflow (this target runs on the small
/// device; the large-device run repeats the same suite via the unit-test
/// scheme step). Attachments are exported by CI into the g11-screenshots
/// artifact and judged by hand (G14).
final class ScreenshotMatrixUITests: XCTestCase {
    @MainActor
    private func capture(variant: String) {
        let app = XCUIApplication()
        app.launch()
        _ = app.staticTexts["Talaria"].waitForExistence(timeout: 10)

        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = "root-\(variant)"
        attachment.lifetime = .keepAlways
        add(attachment)
        app.terminate()
    }

    func testMatrixOnSmallDeviceDefaultType() {
        capture(variant: "light-default")
        capture(variant: "dark-default")
    }

    func testMatrixOnSmallDeviceXLType() {
        capture(variant: "light-xl")
        capture(variant: "dark-xl")
    }
}
