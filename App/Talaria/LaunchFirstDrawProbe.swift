import Foundation
import SwiftUI
import UIKit

struct LaunchFirstDrawProbe: UIViewRepresentable {
    static let accessibilityEnvironmentKey = "TALARIA_LAUNCH_AUDIT"
    static let accessibilityIdentifier = "launch-audit-frame"
    static let clearAccessibilityValue = "clear"
    static let pendingAccessibilityValue = "pending"
    static let violationAccessibilityValue = "violation"

    let audit: LaunchActivityAudit

    init(audit: LaunchActivityAudit = .shared) {
        self.audit = audit
    }

    func makeUIView(context _: Context) -> LaunchFirstDrawProbeView {
        let view = LaunchFirstDrawProbeView(audit: self.audit)
        let exposesDiagnostic = ProcessInfo.processInfo.environment[
            Self.accessibilityEnvironmentKey
        ] == "1"
        view.isAccessibilityElement = exposesDiagnostic
        view.accessibilityElementsHidden = !exposesDiagnostic
        if exposesDiagnostic {
            view.accessibilityIdentifier = Self.accessibilityIdentifier
            view.accessibilityLabel = "Talaria-owned launch activity before first frame"
            view.accessibilityValue = Self.pendingAccessibilityValue
        }
        return view
    }

    func updateUIView(_ uiView: LaunchFirstDrawProbeView, context _: Context) {
        uiView.setNeedsDisplay()
    }
}

final class LaunchFirstDrawProbeView: UIView {
    private let audit: LaunchActivityAudit
    private var recordedFirstDraw = false

    init(audit: LaunchActivityAudit) {
        self.audit = audit
        super.init(frame: .zero)
        self.backgroundColor = .clear
        self.contentMode = .redraw
        self.isOpaque = false
    }

    @available(*, unavailable)
    required init?(coder _: NSCoder) {
        fatalError("init(coder:) is unavailable")
    }

    override func draw(_ rect: CGRect) {
        super.draw(rect)
        guard !self.recordedFirstDraw else { return }
        self.recordedFirstDraw = true

        let snapshot = self.audit.recordFirstFrame()
        self.accessibilityValue = snapshot.isClearAtFirstFrame
            ? LaunchFirstDrawProbe.clearAccessibilityValue
            : LaunchFirstDrawProbe.violationAccessibilityValue
    }
}
