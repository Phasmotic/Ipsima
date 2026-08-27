extension ProtocolConformanceTests {
    func testM_ApprovalReceived() throws {
        try self.assertRoundTrip("method", name: "approval.received", id: .int(1))
    }

    func testM_BillingCharge_status() throws {
        try self.assertRoundTrip("method", name: "billing.charge_status", id: .int(1))
    }

    func testM_Bot_relayOutboxDrain() throws {
        try self.assertRoundTrip("method", name: "bot_relay.outbox.drain", id: .int(1))
    }

    func testM_BrowserControllerHeartbeat() throws {
        try self.assertRoundTrip("method", name: "browser.controller.heartbeat", id: .int(1))
    }

    func testM_ClarifyRespond() throws {
        try self.assertRoundTrip("method", name: "clarify.respond", id: .int(1))
    }

    func testM_CommandResolve() throws {
        try self.assertRoundTrip("method", name: "command.resolve", id: .int(1))
    }

    func testM_ConfigGet() throws {
        try self.assertRoundTrip("method", name: "config.get", id: .int(1))
    }

    func testM_DelegationPause() throws {
        try self.assertRoundTrip("method", name: "delegation.pause", id: .int(1))
    }

    func testM_HandoffFail() throws {
        try self.assertRoundTrip("method", name: "handoff.fail", id: .int(1))
    }

    func testM_ImageAttach_bytes() throws {
        try self.assertRoundTrip("method", name: "image.attach_bytes", id: .int(1))
    }

    func testM_InsightsGet() throws {
        try self.assertRoundTrip("method", name: "insights.get", id: .int(1))
    }

    func testM_LearningFrames() throws {
        try self.assertRoundTrip("method", name: "learning.frames", id: .int(1))
    }

    func testM_McpServersList() throws {
        try self.assertRoundTrip("method", name: "mcp.servers.list", id: .int(1))
    }

    func testM_McpServersSet_api_key() throws {
        try self.assertRoundTrip("method", name: "mcp.servers.set_api_key", id: .int(1))
    }

    func testM_ModelDisconnect() throws {
        try self.assertRoundTrip("method", name: "model.disconnect", id: .int(1))
    }

    func testM_PdfAttach() throws {
        try self.assertRoundTrip("method", name: "pdf.attach", id: .int(1))
    }

    func testM_PetExport() throws {
        try self.assertRoundTrip("method", name: "pet.export", id: .int(1))
    }

    func testM_PetHatch() throws {
        try self.assertRoundTrip("method", name: "pet.hatch", id: .int(1))
    }

    func testM_PetRename() throws {
        try self.assertRoundTrip("method", name: "pet.rename", id: .int(1))
    }

    func testM_Ping() throws {
        try self.assertRoundTrip("method", name: "ping", id: .int(1))
    }

    func testM_PreviewReadRespond() throws {
        try self.assertRoundTrip("method", name: "preview.read.respond", id: .int(1))
    }

    func testM_ProcessStop() throws {
        try self.assertRoundTrip("method", name: "process.stop", id: .int(1))
    }

    func testM_ProfilesGet_asset() throws {
        try self.assertRoundTrip("method", name: "profiles.get_asset", id: .int(1))
    }

    func testM_ProjectsDiscover_repos() throws {
        try self.assertRoundTrip("method", name: "projects.discover_repos", id: .int(1))
    }

    func testM_PromptBackground() throws {
        try self.assertRoundTrip("method", name: "prompt.background", id: .int(1))
    }

    func testM_RollbackDiff() throws {
        try self.assertRoundTrip("method", name: "rollback.diff", id: .int(1))
    }

    func testM_SessionActivate() throws {
        try self.assertRoundTrip("method", name: "session.activate", id: .int(1))
    }

    func testM_SessionCompress() throws {
        try self.assertRoundTrip("method", name: "session.compress", id: .int(1))
    }

    func testM_SessionDelete() throws {
        try self.assertRoundTrip("method", name: "session.delete", id: .int(1))
    }

    func testM_SessionInterrupt() throws {
        try self.assertRoundTrip("method", name: "session.interrupt", id: .int(1))
    }

    func testM_SessionResume() throws {
        try self.assertRoundTrip("method", name: "session.resume", id: .int(1))
    }

    func testM_SessionSteer() throws {
        try self.assertRoundTrip("method", name: "session.steer", id: .int(1))
    }

    func testM_SessionWorkspaceMove() throws {
        try self.assertRoundTrip("method", name: "session.workspace.move", id: .int(1))
    }

    func testM_SkillsManage() throws {
        try self.assertRoundTrip("method", name: "skills.manage", id: .int(1))
    }

    func testM_Spawn_treeLoad() throws {
        try self.assertRoundTrip("method", name: "spawn_tree.load", id: .int(1))
    }

    func testM_SubscriptionChange() throws {
        try self.assertRoundTrip("method", name: "subscription.change", id: .int(1))
    }

    func testM_SubscriptionUpgrade() throws {
        try self.assertRoundTrip("method", name: "subscription.upgrade", id: .int(1))
    }

    func testM_TerminalResize() throws {
        try self.assertRoundTrip("method", name: "terminal.resize", id: .int(1))
    }

    func testM_ToolsetsList() throws {
        try self.assertRoundTrip("method", name: "toolsets.list", id: .int(1))
    }

    func testM_VoiceRecord() throws {
        try self.assertRoundTrip("method", name: "voice.record", id: .int(1))
    }

    func testM_WakePause() throws {
        try self.assertRoundTrip("method", name: "wake.pause", id: .int(1))
    }

    func testM_WakeStop() throws {
        try self.assertRoundTrip("method", name: "wake.stop", id: .int(1))
    }
}
