extension ProtocolConformanceTests {
    func testM_ApprovalRespond() throws {
        try self.assertRoundTrip("method", name: "approval.respond", id: .int(1))
    }

    func testM_BillingState() throws {
        try self.assertRoundTrip("method", name: "billing.state", id: .int(1))
    }

    func testM_Bot_relayReply() throws {
        try self.assertRoundTrip("method", name: "bot_relay.reply", id: .int(1))
    }

    func testM_BrowserControllerRegister() throws {
        try self.assertRoundTrip("method", name: "browser.controller.register", id: .int(1))
    }

    func testM_CliExec() throws {
        try self.assertRoundTrip("method", name: "cli.exec", id: .int(1))
    }

    func testM_CommandsCatalog() throws {
        try self.assertRoundTrip("method", name: "commands.catalog", id: .int(1))
    }

    func testM_ConfigSet() throws {
        try self.assertRoundTrip("method", name: "config.set", id: .int(1))
    }

    func testM_DelegationStatus() throws {
        try self.assertRoundTrip("method", name: "delegation.status", id: .int(1))
    }

    func testM_HandoffRequest() throws {
        try self.assertRoundTrip("method", name: "handoff.request", id: .int(1))
    }

    func testM_ImageDetach() throws {
        try self.assertRoundTrip("method", name: "image.detach", id: .int(1))
    }

    func testM_LearningDelete() throws {
        try self.assertRoundTrip("method", name: "learning.delete", id: .int(1))
    }

    func testM_LlmOneshot() throws {
        try self.assertRoundTrip("method", name: "llm.oneshot", id: .int(1))
    }

    func testM_McpServersOauthPoll() throws {
        try self.assertRoundTrip("method", name: "mcp.servers.oauth.poll", id: .int(1))
    }

    func testM_McpServersTest() throws {
        try self.assertRoundTrip("method", name: "mcp.servers.test", id: .int(1))
    }

    func testM_ModelOptions() throws {
        try self.assertRoundTrip("method", name: "model.options", id: .int(1))
    }

    func testM_PetCancel() throws {
        try self.assertRoundTrip("method", name: "pet.cancel", id: .int(1))
    }

    func testM_PetGallery() throws {
        try self.assertRoundTrip("method", name: "pet.gallery", id: .int(1))
    }

    func testM_PetInfo() throws {
        try self.assertRoundTrip("method", name: "pet.info", id: .int(1))
    }

    func testM_PetScale() throws {
        try self.assertRoundTrip("method", name: "pet.scale", id: .int(1))
    }

    func testM_PluginsList() throws {
        try self.assertRoundTrip("method", name: "plugins.list", id: .int(1))
    }

    func testM_PreviewRestart() throws {
        try self.assertRoundTrip("method", name: "preview.restart", id: .int(1))
    }

    func testM_ProfilesConfigure() throws {
        try self.assertRoundTrip("method", name: "profiles.configure", id: .int(1))
    }

    func testM_ProfilesList() throws {
        try self.assertRoundTrip("method", name: "profiles.list", id: .int(1))
    }

    func testM_ProjectsProject_sessions() throws {
        try self.assertRoundTrip("method", name: "projects.project_sessions", id: .int(1))
    }

    func testM_PromptSubmit() throws {
        try self.assertRoundTrip("method", name: "prompt.submit", id: .int(1))
    }

    func testM_RollbackList() throws {
        try self.assertRoundTrip("method", name: "rollback.list", id: .int(1))
    }

    func testM_SessionActive_list() throws {
        try self.assertRoundTrip("method", name: "session.active_list", id: .int(1))
    }

    func testM_SessionContext_breakdown() throws {
        try self.assertRoundTrip("method", name: "session.context_breakdown", id: .int(1))
    }

    func testM_SessionEventsSince() throws {
        try self.assertRoundTrip("method", name: "session.events.since", id: .int(1))
    }

    func testM_SessionList() throws {
        try self.assertRoundTrip("method", name: "session.list", id: .int(1))
    }

    func testM_SessionSave() throws {
        try self.assertRoundTrip("method", name: "session.save", id: .int(1))
    }

    func testM_SessionTitle() throws {
        try self.assertRoundTrip("method", name: "session.title", id: .int(1))
    }

    func testM_SetupRuntime_check() throws {
        try self.assertRoundTrip("method", name: "setup.runtime_check", id: .int(1))
    }

    func testM_SkillsReload() throws {
        try self.assertRoundTrip("method", name: "skills.reload", id: .int(1))
    }

    func testM_Spawn_treeSave() throws {
        try self.assertRoundTrip("method", name: "spawn_tree.save", id: .int(1))
    }

    func testM_SubscriptionPreview() throws {
        try self.assertRoundTrip("method", name: "subscription.preview", id: .int(1))
    }

    func testM_SudoRespond() throws {
        try self.assertRoundTrip("method", name: "sudo.respond", id: .int(1))
    }

    func testM_ToolsConfigure() throws {
        try self.assertRoundTrip("method", name: "tools.configure", id: .int(1))
    }

    func testM_TourRespond() throws {
        try self.assertRoundTrip("method", name: "tour.respond", id: .int(1))
    }

    func testM_VoiceToggle() throws {
        try self.assertRoundTrip("method", name: "voice.toggle", id: .int(1))
    }

    func testM_WakeResume() throws {
        try self.assertRoundTrip("method", name: "wake.resume", id: .int(1))
    }

    func testM_WindowReadRespond() throws {
        try self.assertRoundTrip("method", name: "window.read.respond", id: .int(1))
    }
}
