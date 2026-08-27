extension ProtocolConformanceTests {
    func testM_ApprovalPending() throws {
        try self.assertRoundTrip("method", name: "approval.pending", id: .int(1))
    }

    func testM_BillingCharge() throws {
        try self.assertRoundTrip("method", name: "billing.charge", id: .int(1))
    }

    func testM_Bot_relayDeliver() throws {
        try self.assertRoundTrip("method", name: "bot_relay.deliver", id: .int(1))
    }

    func testM_BrowserControllerDetach() throws {
        try self.assertRoundTrip("method", name: "browser.controller.detach", id: .int(1))
    }

    func testM_BrowserManage() throws {
        try self.assertRoundTrip("method", name: "browser.manage", id: .int(1))
    }

    func testM_CommandDispatch() throws {
        try self.assertRoundTrip("method", name: "command.dispatch", id: .int(1))
    }

    func testM_CompleteSlash() throws {
        try self.assertRoundTrip("method", name: "complete.slash", id: .int(1))
    }

    func testM_CronManage() throws {
        try self.assertRoundTrip("method", name: "cron.manage", id: .int(1))
    }

    func testM_FileAttach() throws {
        try self.assertRoundTrip("method", name: "file.attach", id: .int(1))
    }

    func testM_ImageAttach() throws {
        try self.assertRoundTrip("method", name: "image.attach", id: .int(1))
    }

    func testM_InputDetect_drop() throws {
        try self.assertRoundTrip("method", name: "input.detect_drop", id: .int(1))
    }

    func testM_LearningEdit() throws {
        try self.assertRoundTrip("method", name: "learning.edit", id: .int(1))
    }

    func testM_McpServersAdd() throws {
        try self.assertRoundTrip("method", name: "mcp.servers.add", id: .int(1))
    }

    func testM_McpServersRemove() throws {
        try self.assertRoundTrip("method", name: "mcp.servers.remove", id: .int(1))
    }

    func testM_MessageReact() throws {
        try self.assertRoundTrip("method", name: "message.react", id: .int(1))
    }

    func testM_PasteCollapse() throws {
        try self.assertRoundTrip("method", name: "paste.collapse", id: .int(1))
    }

    func testM_PetDisable() throws {
        try self.assertRoundTrip("method", name: "pet.disable", id: .int(1))
    }

    func testM_PetGenerateStatus() throws {
        try self.assertRoundTrip("method", name: "pet.generate.status", id: .int(1))
    }

    func testM_PetRemove() throws {
        try self.assertRoundTrip("method", name: "pet.remove", id: .int(1))
    }

    func testM_PetThumb() throws {
        try self.assertRoundTrip("method", name: "pet.thumb", id: .int(1))
    }

    func testM_PreviewActRespond() throws {
        try self.assertRoundTrip("method", name: "preview.act.respond", id: .int(1))
    }

    func testM_ProcessList() throws {
        try self.assertRoundTrip("method", name: "process.list", id: .int(1))
    }

    func testM_ProfilesDescribe() throws {
        try self.assertRoundTrip("method", name: "profiles.describe", id: .int(1))
    }

    func testM_ProjectFacts() throws {
        try self.assertRoundTrip("method", name: "project.facts", id: .int(1))
    }

    func testM_ProjectsTree() throws {
        try self.assertRoundTrip("method", name: "projects.tree", id: .int(1))
    }

    func testM_ReloadMcp() throws {
        try self.assertRoundTrip("method", name: "reload.mcp", id: .int(1))
    }

    func testM_SecretRespond() throws {
        try self.assertRoundTrip("method", name: "secret.respond", id: .int(1))
    }

    func testM_SessionClose() throws {
        try self.assertRoundTrip("method", name: "session.close", id: .int(1))
    }

    func testM_SessionCwdSet() throws {
        try self.assertRoundTrip("method", name: "session.cwd.set", id: .int(1))
    }

    func testM_SessionHistory() throws {
        try self.assertRoundTrip("method", name: "session.history", id: .int(1))
    }

    func testM_SessionRedirect() throws {
        try self.assertRoundTrip("method", name: "session.redirect", id: .int(1))
    }

    func testM_SessionStatus() throws {
        try self.assertRoundTrip("method", name: "session.status", id: .int(1))
    }

    func testM_SessionUsage() throws {
        try self.assertRoundTrip("method", name: "session.usage", id: .int(1))
    }

    func testM_ShellExec() throws {
        try self.assertRoundTrip("method", name: "shell.exec", id: .int(1))
    }

    func testM_Spawn_treeList() throws {
        try self.assertRoundTrip("method", name: "spawn_tree.list", id: .int(1))
    }

    func testM_SubagentSteer() throws {
        try self.assertRoundTrip("method", name: "subagent.steer", id: .int(1))
    }

    func testM_SubscriptionState() throws {
        try self.assertRoundTrip("method", name: "subscription.state", id: .int(1))
    }

    func testM_TerminalReadRespond() throws {
        try self.assertRoundTrip("method", name: "terminal.read.respond", id: .int(1))
    }

    func testM_ToolsShow() throws {
        try self.assertRoundTrip("method", name: "tools.show", id: .int(1))
    }

    func testM_VerificationStatus() throws {
        try self.assertRoundTrip("method", name: "verification.status", id: .int(1))
    }

    func testM_WakeFeed() throws {
        try self.assertRoundTrip("method", name: "wake.feed", id: .int(1))
    }

    func testM_WakeStatus() throws {
        try self.assertRoundTrip("method", name: "wake.status", id: .int(1))
    }
}
