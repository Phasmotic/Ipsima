import Foundation
@testable import HermesKit
import XCTest

extension ProtocolConformanceTests {
    private func assertEventRoundTrip(
        type: String, file: StaticString = #filePath, line: UInt = #line
    ) throws {
        let codec = WireCodec()
        let payload: JSONValue = .object(["__probe": .string(type)])
        let params: JSONValue = .object([
            "payload": payload,
            "type": .string(type),
        ])
        let envelope = JSONRPCEnvelope(method: "event", params: params)
        let data = try codec.encode(envelope)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertFalse(object.keys.contains("id"), "event \(type) encoded an id", file: file, line: line)
        let decoded = try codec.decode(data)
        XCTAssertEqual(decoded, envelope, "event \(type) changed on decode", file: file, line: line)
        XCTAssertEqual(decoded.method, "event", file: file, line: line)
        XCTAssertNil(decoded.id, file: file, line: line)
        XCTAssertEqual(decoded.params, params, file: file, line: line)
        let again = try codec.decode(codec.encode(decoded))
        XCTAssertEqual(again, decoded, "event \(type) not a fixed point", file: file, line: line)
    }

    func testE_AgentTerminalOutput() throws {
        try self.assertEventRoundTrip(type: "agent.terminal.output")
    }

    func testE_ApprovalRequest() throws {
        try self.assertEventRoundTrip(type: "approval.request")
    }

    func testE_BackgroundComplete() throws {
        try self.assertEventRoundTrip(type: "background.complete")
    }

    func testE_BillingStep_upVerification() throws {
        try self.assertEventRoundTrip(type: "billing.step_up.verification")
    }

    func testE_BrowserProgress() throws {
        try self.assertEventRoundTrip(type: "browser.progress")
    }

    func testE_ClarifyExpire() throws {
        try self.assertEventRoundTrip(type: "clarify.expire")
    }

    func testE_ClarifyRequest() throws {
        try self.assertEventRoundTrip(type: "clarify.request")
    }

    func testE_Error() throws {
        try self.assertEventRoundTrip(type: "error")
    }

    func testE_GatewayReady() throws {
        try self.assertEventRoundTrip(type: "gateway.ready")
    }

    func testE_McpSetupExpire() throws {
        try self.assertEventRoundTrip(type: "mcp.setup.expire")
    }

    func testE_McpSetupRequest() throws {
        try self.assertEventRoundTrip(type: "mcp.setup.request")
    }

    func testE_MessageComplete() throws {
        try self.assertEventRoundTrip(type: "message.complete")
    }

    func testE_MessageDelta() throws {
        try self.assertEventRoundTrip(type: "message.delta")
    }

    func testE_MessageInterim() throws {
        try self.assertEventRoundTrip(type: "message.interim")
    }

    func testE_MessageStart() throws {
        try self.assertEventRoundTrip(type: "message.start")
    }

    func testE_MoaAggregating() throws {
        try self.assertEventRoundTrip(type: "moa.aggregating")
    }

    func testE_MoaPhase() throws {
        try self.assertEventRoundTrip(type: "moa.phase")
    }

    func testE_MoaProgress() throws {
        try self.assertEventRoundTrip(type: "moa.progress")
    }

    func testE_MoaReference() throws {
        try self.assertEventRoundTrip(type: "moa.reference")
    }

    func testE_Notice() throws {
        try self.assertEventRoundTrip(type: "notice")
    }

    func testE_NotificationClear() throws {
        try self.assertEventRoundTrip(type: "notification.clear")
    }

    func testE_NotificationShow() throws {
        try self.assertEventRoundTrip(type: "notification.show")
    }

    func testE_PetGenerateProgress() throws {
        try self.assertEventRoundTrip(type: "pet.generate.progress")
    }

    func testE_PetHatchProgress() throws {
        try self.assertEventRoundTrip(type: "pet.hatch.progress")
    }

    func testE_PreviewActExpire() throws {
        try self.assertEventRoundTrip(type: "preview.act.expire")
    }

    func testE_PreviewActRequest() throws {
        try self.assertEventRoundTrip(type: "preview.act.request")
    }

    func testE_PreviewReadExpire() throws {
        try self.assertEventRoundTrip(type: "preview.read.expire")
    }

    func testE_PreviewReadRequest() throws {
        try self.assertEventRoundTrip(type: "preview.read.request")
    }

    func testE_PreviewRestartComplete() throws {
        try self.assertEventRoundTrip(type: "preview.restart.complete")
    }

    func testE_PreviewRestartProgress() throws {
        try self.assertEventRoundTrip(type: "preview.restart.progress")
    }

    func testE_Reaction() throws {
        try self.assertEventRoundTrip(type: "reaction")
    }

    func testE_ReasoningAvailable() throws {
        try self.assertEventRoundTrip(type: "reasoning.available")
    }

    func testE_ReasoningDelta() throws {
        try self.assertEventRoundTrip(type: "reasoning.delta")
    }

    func testE_ReviewSummary() throws {
        try self.assertEventRoundTrip(type: "review.summary")
    }

    func testE_SecretExpire() throws {
        try self.assertEventRoundTrip(type: "secret.expire")
    }

    func testE_SecretRequest() throws {
        try self.assertEventRoundTrip(type: "secret.request")
    }

    func testE_SessionInfo() throws {
        try self.assertEventRoundTrip(type: "session.info")
    }

    func testE_SessionResume_progress() throws {
        try self.assertEventRoundTrip(type: "session.resume_progress")
    }

    func testE_SessionTitle() throws {
        try self.assertEventRoundTrip(type: "session.title")
    }

    func testE_SessionUsage() throws {
        try self.assertEventRoundTrip(type: "session.usage")
    }

    func testE_StatusUpdate() throws {
        try self.assertEventRoundTrip(type: "status.update")
    }

    func testE_SudoExpire() throws {
        try self.assertEventRoundTrip(type: "sudo.expire")
    }

    func testE_SudoRequest() throws {
        try self.assertEventRoundTrip(type: "sudo.request")
    }

    func testE_TerminalClose() throws {
        try self.assertEventRoundTrip(type: "terminal.close")
    }

    func testE_TerminalReadExpire() throws {
        try self.assertEventRoundTrip(type: "terminal.read.expire")
    }

    func testE_TerminalReadRequest() throws {
        try self.assertEventRoundTrip(type: "terminal.read.request")
    }

    func testE_ThinkingDelta() throws {
        try self.assertEventRoundTrip(type: "thinking.delta")
    }

    func testE_ToolComplete() throws {
        try self.assertEventRoundTrip(type: "tool.complete")
    }

    func testE_ToolGenerating() throws {
        try self.assertEventRoundTrip(type: "tool.generating")
    }

    func testE_ToolOutput_risk() throws {
        try self.assertEventRoundTrip(type: "tool.output_risk")
    }

    func testE_ToolStart() throws {
        try self.assertEventRoundTrip(type: "tool.start")
    }

    func testE_TourExpire() throws {
        try self.assertEventRoundTrip(type: "tour.expire")
    }

    func testE_TourRequest() throws {
        try self.assertEventRoundTrip(type: "tour.request")
    }

    func testE_WakeDetected() throws {
        try self.assertEventRoundTrip(type: "wake.detected")
    }

    func testE_WindowReadExpire() throws {
        try self.assertEventRoundTrip(type: "window.read.expire")
    }

    func testE_WindowReadRequest() throws {
        try self.assertEventRoundTrip(type: "window.read.request")
    }
}
