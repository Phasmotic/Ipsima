extension ProtocolConformanceTests {
    func testM_AgentsList() throws {
        try self.assertRoundTrip("method", name: "agents.list", id: .int(1))
    }

    func testM_BillingAuto_reload() throws {
        try self.assertRoundTrip("method", name: "billing.auto_reload", id: .int(1))
    }

    func testM_BillingStep_up() throws {
        try self.assertRoundTrip("method", name: "billing.step_up", id: .int(1))
    }

    func testM_Bot_relayRosterSync() throws {
        try self.assertRoundTrip("method", name: "bot_relay.roster.sync", id: .int(1))
    }

    func testM_BrowserControllerResult() throws {
        try self.assertRoundTrip("method", name: "browser.controller.result", id: .int(1))
    }

    func testM_ClipboardPaste() throws {
        try self.assertRoundTrip("method", name: "clipboard.paste", id: .int(1))
    }

    func testM_CompletePath() throws {
        try self.assertRoundTrip("method", name: "complete.path", id: .int(1))
    }

    func testM_ConfigShow() throws {
        try self.assertRoundTrip("method", name: "config.show", id: .int(1))
    }

    func testM_DiagnosticsShare_nous() throws {
        try self.assertRoundTrip("method", name: "diagnostics.share_nous", id: .int(1))
    }

    func testM_HandoffState() throws {
        try self.assertRoundTrip("method", name: "handoff.state", id: .int(1))
    }

    func testM_ImageGenerate() throws {
        try self.assertRoundTrip("method", name: "image.generate", id: .int(1))
    }

    func testM_LearningDetail() throws {
        try self.assertRoundTrip("method", name: "learning.detail", id: .int(1))
    }

    func testM_McpCatalog() throws {
        try self.assertRoundTrip("method", name: "mcp.catalog", id: .int(1))
    }

    func testM_McpServersOauthStart() throws {
        try self.assertRoundTrip("method", name: "mcp.servers.oauth.start", id: .int(1))
    }

    func testM_McpSetupRespond() throws {
        try self.assertRoundTrip("method", name: "mcp.setup.respond", id: .int(1))
    }

    func testM_ModelSave_key() throws {
        try self.assertRoundTrip("method", name: "model.save_key", id: .int(1))
    }

    func testM_PetCells() throws {
        try self.assertRoundTrip("method", name: "pet.cells", id: .int(1))
    }

    func testM_PetGenerate() throws {
        try self.assertRoundTrip("method", name: "pet.generate", id: .int(1))
    }

    func testM_PetInfoMeta() throws {
        try self.assertRoundTrip("method", name: "pet.info.meta", id: .int(1))
    }

    func testM_PetSelect() throws {
        try self.assertRoundTrip("method", name: "pet.select", id: .int(1))
    }

    func testM_PluginsManage() throws {
        try self.assertRoundTrip("method", name: "plugins.manage", id: .int(1))
    }

    func testM_ProcessKill() throws {
        try self.assertRoundTrip("method", name: "process.kill", id: .int(1))
    }

    func testM_ProfilesCreate() throws {
        try self.assertRoundTrip("method", name: "profiles.create", id: .int(1))
    }

    func testM_ProfilesSet_asset() throws {
        try self.assertRoundTrip("method", name: "profiles.set_asset", id: .int(1))
    }

    func testM_ProjectsRecord_repos() throws {
        try self.assertRoundTrip("method", name: "projects.record_repos", id: .int(1))
    }

    func testM_ReloadEnv() throws {
        try self.assertRoundTrip("method", name: "reload.env", id: .int(1))
    }

    func testM_RollbackRestore() throws {
        try self.assertRoundTrip("method", name: "rollback.restore", id: .int(1))
    }

    func testM_SessionBranch() throws {
        try self.assertRoundTrip("method", name: "session.branch", id: .int(1))
    }

    func testM_SessionCreate() throws {
        try self.assertRoundTrip("method", name: "session.create", id: .int(1))
    }

    func testM_SessionEventsStats() throws {
        try self.assertRoundTrip("method", name: "session.events.stats", id: .int(1))
    }

    func testM_SessionMost_recent() throws {
        try self.assertRoundTrip("method", name: "session.most_recent", id: .int(1))
    }

    func testM_SessionSet_hidden() throws {
        try self.assertRoundTrip("method", name: "session.set_hidden", id: .int(1))
    }

    func testM_SessionUndo() throws {
        try self.assertRoundTrip("method", name: "session.undo", id: .int(1))
    }

    func testM_SetupStatus() throws {
        try self.assertRoundTrip("method", name: "setup.status", id: .int(1))
    }

    func testM_SlashExec() throws {
        try self.assertRoundTrip("method", name: "slash.exec", id: .int(1))
    }

    func testM_SubagentInterrupt() throws {
        try self.assertRoundTrip("method", name: "subagent.interrupt", id: .int(1))
    }

    func testM_SubscriptionResume() throws {
        try self.assertRoundTrip("method", name: "subscription.resume", id: .int(1))
    }

    func testM_SystemBattery() throws {
        try self.assertRoundTrip("method", name: "system.battery", id: .int(1))
    }

    func testM_ToolsList() throws {
        try self.assertRoundTrip("method", name: "tools.list", id: .int(1))
    }

    func testM_UsageBars() throws {
        try self.assertRoundTrip("method", name: "usage.bars", id: .int(1))
    }

    func testM_VoiceTts() throws {
        try self.assertRoundTrip("method", name: "voice.tts", id: .int(1))
    }

    func testM_WakeStart() throws {
        try self.assertRoundTrip("method", name: "wake.start", id: .int(1))
    }
}
