from pathlib import Path
import re
import unittest


REPO = Path(__file__).resolve().parent.parent
LAUNCH_TEST = REPO / "Tests" / "TalariaUITests" / "LaunchPerformanceUITests.swift"
LAUNCH_AUDIT_TEST = REPO / "Tests" / "TalariaUITests" / "LaunchActivityAuditUITests.swift"
LINK_AB_TEST = REPO / "Tests" / "TalariaLaunchABTests" / "LinkABLaunchUITests.swift"
CONTENT_VIEW = REPO / "App" / "Talaria" / "ContentView.swift"
LINK_ANCHOR = REPO / "App" / "Talaria" / "LaunchLinkAnchor.swift"
RESOURCE_FACTORY = REPO / "App" / "Talaria" / "AuditedLaunchResourceFactory.swift"
WORKFLOW = REPO / ".github" / "workflows" / "tier-b.yml"
OBSERVER = REPO / "scripts" / "check_launch_metrics.py"
STRUCTURE = REPO / "scripts" / "check_launch_structure.py"
SOURCE_AUDIT = REPO / "scripts" / "check_launch_activity_sources.py"
ANALYZER = REPO / "scripts" / "analyze_launch_ab.py"
BASELINE = REPO / "scripts" / "baselines" / "launch-structure-xcode-26.6-arm64.json"
PROJECT = REPO / "project.yml"
BRIEF = REPO / "docs" / "BRIEF.md"
GOVERNANCE = REPO / "docs" / "GOVERNANCE.md"


def swift_source_without_transport_guarded_branches(source: str) -> str:
    """Return lines that can remain when TALARIA_LINK_TRANSPORT is absent."""
    guarded_branches: list[bool] = []
    retained: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#if "):
            expression = stripped.removeprefix("#if ").strip()
            if "TALARIA_LINK_TRANSPORT" in expression:
                if expression != "TALARIA_LINK_TRANSPORT":
                    raise AssertionError(
                        "transport linkage must use the exact positive compilation guard"
                    )
                guarded_branches.append(True)
            else:
                guarded_branches.append(False)
            continue
        if stripped.startswith("#elseif "):
            if not guarded_branches:
                raise AssertionError("unbalanced Swift conditional compilation directive")
            expression = stripped.removeprefix("#elseif ").strip()
            if "TALARIA_LINK_TRANSPORT" in expression:
                if expression != "TALARIA_LINK_TRANSPORT":
                    raise AssertionError(
                        "transport linkage must use the exact positive compilation guard"
                    )
                guarded_branches[-1] = True
            else:
                guarded_branches[-1] = False
            continue
        if stripped == "#else":
            if not guarded_branches:
                raise AssertionError("unbalanced Swift conditional compilation directive")
            guarded_branches[-1] = False
            continue
        if stripped == "#endif":
            if not guarded_branches:
                raise AssertionError("unbalanced Swift conditional compilation directive")
            guarded_branches.pop()
            continue
        if not any(guarded_branches):
            retained.append(line)
    if guarded_branches:
        raise AssertionError("unbalanced Swift conditional compilation directive")
    return "\n".join(retained)


class G12ContractTests(unittest.TestCase):
    def test_stood_down_observer_retains_five_official_samples_without_verdict(self) -> None:
        test_source = LAUNCH_TEST.read_text(encoding="utf-8")
        observer = OBSERVER.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertEqual(test_source.count("func testLaunchMetricBaselineRecorded()"), 1)
        self.assertIn("options.iterationCount = 5", test_source)
        self.assertIn("XCTApplicationLaunchMetric()", test_source)
        self.assertIn("stood down", test_source)
        self.assertNotIn("ContinuousClock", test_source)

        self.assertIn("EXPECTED_MEASUREMENT_COUNT = 5", observer)
        self.assertIn('"samples_seconds"', observer)
        self.assertIn('"status": "stood_down"', observer)
        self.assertIn("G12 COLD-LAUNCH STOOD DOWN:", observer)
        self.assertNotIn("LAUNCH_BUDGET_SECONDS", observer)
        self.assertNotIn("G12 COLD-LAUNCH PASS", observer)
        self.assertNotIn("G12 COLD-LAUNCH FAIL", observer)

        start = workflow.index("Preserve G12 launch observations — STOOD DOWN, no verdict")
        end = workflow.index("Upload sanitized G12 per-iteration observation", start)
        section = workflow[start:end]
        self.assertEqual(section.count("xcresulttool get test-results metrics"), 2)
        self.assertEqual(section.count("--schema-version 0.1.0"), 2)
        self.assertIn("--evidence-json .gauntlet/g12-launch-observation.json", section)
        self.assertIn("G12 COLD-LAUNCH STOOD DOWN: ", section)
        self.assertIn("G12 COLD-LAUNCH BLOCKED: ", section)
        self.assertNotIn("G12 COLD-LAUNCH PASS", section)
        self.assertNotIn("G12 COLD-LAUNCH FAIL", section)
        self.assertIn("Upload sanitized G12 per-iteration observation", workflow)

    def test_each_observer_sample_launches_from_not_running_and_reaches_root(self) -> None:
        source = LAUNCH_TEST.read_text(encoding="utf-8")
        measured = source.split(
            "measure(metrics: [XCTApplicationLaunchMetric()], options: options) {",
            maxsplit=1,
        )[1].split("\n        }", maxsplit=1)[0]

        self.assertEqual(source.count("self.terminateAndAssertNotRunning(app)"), 2)
        for statement in (
            "XCTAssertEqual(app.state, .notRunning",
            "app.launch()",
            "rootElement.waitForExistence(timeout: 10)",
            "self.terminateAndAssertNotRunning(app)",
        ):
            self.assertEqual(measured.count(statement), 1)
        self.assertLess(measured.index("app.launch()"), measured.index("rootElement.waitForExistence"))

    def test_deterministic_replacement_is_enforced_without_wall_clock_verdict(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        checker = STRUCTURE.read_text(encoding="utf-8")

        self.assertIn("SIMCTL_CHILD_DYLD_PRINT_STATISTICS=1", workflow)
        self.assertIn("for index in 01 02 03 04 05 06 07 08 09 10", workflow)
        self.assertIn("check_launch_structure.py collect", workflow)
        self.assertIn("check_launch_structure.py check", workflow)
        self.assertIn("launch-structure-xcode-26.6-arm64.json", workflow)
        self.assertTrue(BASELINE.is_file())

        self.assertIn("EXPECTED_SAMPLE_COUNT = 10", checker)
        self.assertIn('EXPECTED_APP_EXECUTABLE = "Talaria"', checker)
        self.assertIn('EXPECTED_DEBUG_DYLIB = "Talaria.debug.dylib"', checker)
        self.assertIn("__mod_init_func", checker)
        self.assertIn("LC_*DYLIB closure changed", checker)
        self.assertIn("byte_ceiling", checker)
        self.assertIn("rebase_binding_ms", checker)
        self.assertIn("initializer_ms", checker)
        self.assertNotIn("XCTApplicationLaunchMetric", checker)

    def test_first_frame_contract_covers_talaria_owned_launch_resources(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        audit_test = LAUNCH_AUDIT_TEST.read_text(encoding="utf-8")
        source_checker = SOURCE_AUDIT.read_text(encoding="utf-8")

        self.assertIn("check_launch_activity_sources.py", workflow)
        self.assertIn("LaunchActivityAuditUITests/testFirstDrawHasNoTalariaOwnedLaunchActivity()", workflow)
        self.assertIn('app.launchEnvironment["TALARIA_LAUNCH_AUDIT"] = "1"', audit_test)
        self.assertIn('value == %@', audit_test)
        for construction in (
            "URLSession constructor",
            "URLSession.shared",
            "WebSocketHermesTransport constructor",
            "NWPathMonitor constructor",
            "SCNetworkReachabilityCreateWithName",
            "Timer constructor",
            "Timer.scheduledTimer",
            "DispatchSource timer constructor",
        ):
            self.assertIn(construction, source_checker)
        self.assertIn("bypasses AuditedLaunchResourceFactory", source_checker)

    def test_transport_link_study_is_paired_interleaved_and_verdict_free(self) -> None:
        analyzer = ANALYZER.read_text(encoding="utf-8")
        launch_test = LINK_AB_TEST.read_text(encoding="utf-8")
        link_anchor = LINK_ANCHOR.read_text(encoding="utf-8")
        resource_factory = RESOURCE_FACTORY.read_text(encoding="utf-8")
        project = PROJECT.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("PAIR_COUNT = 10", analyzer)
        self.assertIn('["control", "linked"] if index % 2 == 1', analyzer)
        self.assertIn("linked_seconds_minus_control_seconds", analyzer)
        self.assertIn('"control_seconds"', analyzer)
        self.assertIn('"linked_seconds"', analyzer)
        self.assertIn("median_delta_seconds", analyzer)
        self.assertIn("mad_delta_seconds", analyzer)
        self.assertIn("one device", analyzer)
        self.assertNotIn("G12 LINK A/B PASS", analyzer)
        self.assertNotIn("G12 LINK A/B FAIL", analyzer)
        self.assertIn("options.iterationCount = 1", launch_test)
        self.assertIn("It has no threshold", launch_test)
        self.assertIn("Talaria-LinkAB:", project)
        self.assertIn(
            'SWIFT_ACTIVE_COMPILATION_CONDITIONS: "$(inherited) TALARIA_LINK_TRANSPORT"',
            project,
        )
        self.assertNotIn("Talaria-LinkMap", project)
        self.assertEqual(workflow.count("conditions='DEBUG'"), 2)
        self.assertEqual(
            workflow.count("conditions='DEBUG TALARIA_LINK_TRANSPORT'"), 2
        )
        self.assertEqual(
            workflow.count('"SWIFT_ACTIVE_COMPILATION_CONDITIONS=$conditions"'), 2
        )
        for source_path in sorted((REPO / "App" / "Talaria").rglob("*.swift")):
            control_source = swift_source_without_transport_guarded_branches(
                source_path.read_text(encoding="utf-8")
            )
            uncommented_control_source = "\n".join(
                line.split("//", maxsplit=1)[0]
                for line in control_source.splitlines()
            )
            self.assertIsNone(
                re.search(
                    r"\b(?:Hermes[A-Z]\w*|WebSocketHermesTransport|WireCodec)\b",
                    uncommented_control_source,
                ),
                f"control source references HermesKit product code: {source_path.name}",
            )
        self.assertIn("private enum LaunchLinkControlMarker {}", link_anchor)
        self.assertIn(
            "unsafeBitCast(LaunchLinkControlMarker.self, to: UnsafeRawPointer.self)",
            link_anchor,
        )
        self.assertNotIn("WireCodec.self", link_anchor)
        self.assertIn('@_cdecl("talaria_transport_factory_link_anchor")', link_anchor)
        self.assertIn("@inline(never)", link_anchor)
        self.assertIn("AuditedLaunchResourceFactory.webSocketTransport", link_anchor)
        self.assertIn("Unmanaged.passRetained(transport).toOpaque()", link_anchor)
        self.assertIn(
            "talariaTransportFactoryLinkAnchor as TransportFactoryLinkFunction",
            link_anchor,
        )
        self.assertIn("import HermesKit", resource_factory)
        self.assertIn("return WebSocketHermesTransport(", resource_factory)
        observation_upload = workflow.index("Upload sanitized G12 per-iteration observation")
        build = workflow.index(
            "Build transport-link A/B variants and collect semantic evidence"
        )
        preflight_render = workflow.index(
            "Render sanitized transport-link preflight evidence"
        )
        preflight_upload = workflow.index(
            "Upload sanitized transport-link preflight evidence"
        )
        preflight_enforce = workflow.index(
            "Enforce retained transport-link preflight evidence"
        )
        observations = workflow.index(
            "Collect interleaved transport-link A/B observations"
        )
        render = workflow.index("Render sanitized transport-link A/B evidence")
        study_upload = workflow.index("Upload sanitized transport-link A/B evidence")
        enforce = workflow.index("Enforce retained transport-link A/B evidence")
        structure = workflow.index("Collect deterministic launch-structure replacement evidence")
        build_section = workflow[build:preflight_render]
        preflight_flow = workflow[preflight_render:observations]
        observation_section = workflow[observations:render]
        full_analysis_flow = workflow[render:structure]
        evidence_flow = workflow[preflight_render:structure]
        self.assertLess(observation_upload, build)
        self.assertLess(build, preflight_render)
        self.assertLess(preflight_render, preflight_upload)
        self.assertLess(preflight_upload, preflight_enforce)
        self.assertLess(preflight_enforce, observations)
        self.assertLess(observations, render)
        self.assertLess(render, study_upload)
        self.assertLess(study_upload, enforce)
        self.assertLess(enforce, structure)
        self.assertIn("xcrun nm -arch arm64 -U -j", build_section)
        self.assertIn("xcrun swift-demangle --compact", build_section)
        self.assertIn("link-collection-status.txt", build_section)
        self.assertIn("symbol_inventory_failed", build_section)
        self.assertIn("exit 0", build_section)
        self.assertNotIn("exit 2", build_section)
        self.assertNotIn("test-without-building", build_section)
        self.assertIn("analyze_launch_ab.py render-link", preflight_flow)
        self.assertIn("analyze_launch_ab.py enforce-link", preflight_flow)
        self.assertIn("--collection-status", preflight_flow)
        self.assertIn("if-no-files-found: error", preflight_flow)
        self.assertNotIn("test-without-building", preflight_flow)
        self.assertIn("test-without-building", observation_section)
        self.assertIn("measurement-collection-status.txt", observation_section)
        self.assertIn("metrics_export_failed", observation_section)
        self.assertIn("exit 0", observation_section)
        self.assertNotIn("exit 2", observation_section)
        self.assertIn(
            '--success-marker "** TEST EXECUTE SUCCEEDED **"',
            observation_section,
        )
        self.assertNotIn(
            '--success-marker "** TEST SUCCEEDED **"', observation_section
        )
        self.assertIn("--link-collection-status", full_analysis_flow)
        self.assertIn("--measurement-collection-status", full_analysis_flow)
        for bypass in ("always()", "!cancelled()", "continue-on-error", "if: failure()"):
            self.assertNotIn(bypass, evidence_flow)

    def test_rearm_contract_is_complete_and_blocks_p2_closure(self) -> None:
        for path in (BRIEF, GOVERNANCE):
            source = " ".join(path.read_text(encoding="utf-8").split())
            self.assertIn("stood down", source.lower())
            self.assertTrue(
                "never PASS or N/A" in source or "neither PASS nor N/A" in source
            )
            self.assertIn("detection floor", source.lower())
            self.assertIn("confidence", source.lower())
            self.assertIn("pinned known-good reference binary", source)
            self.assertIn("interleaved", source)
            self.assertIn("median delta or ratio", source)
            self.assertIn("at least ten", source.lower())
            self.assertIn("false-fail budget", source.lower())
            self.assertIn("1%", source)
            self.assertIn("30 runner instances", source)
            self.assertIn("seven days", source)
            self.assertIn("MAD", source)
            self.assertIn("P95", source)
            self.assertTrue(
                "P2 cannot close" in source or "P2 also cannot close" in source
            )

    def test_governance_freezes_subject_and_governs_red_rulings_and_warmup(self) -> None:
        source = " ".join(GOVERNANCE.read_text(encoding="utf-8").split())

        self.assertIn("subject is frozen", source)
        self.assertIn("N/A requires demonstrated absence", source)
        self.assertIn("independent review", source)
        self.assertIn("uniform warmup applied to every run", source)
        self.assertIn("outcome-dependent intervention", source)
        self.assertIn("best-of-two sample discarding", source)
        self.assertIn("discards a warm-up iteration", source)

    def test_streaming_clause_remains_named_n_a_until_real_surface(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        ios = workflow[workflow.index("  ios:") : workflow.index("  watchos:")]
        self.assertEqual(ios.count("      - name: G12 streaming responsiveness"), 1)
        start = ios.index("      - name: G12 streaming responsiveness")
        end = ios.index("      - name: Emit Tier B job status", start)
        section = ios[start:end]
        self.assertIn("N/A (no live stream or streaming UI to measure)", section)
        self.assertNotIn("PASS", section)
        self.assertNotIn("BLOCKED", section)

    def test_phase_zero_footnote_uses_primary_contrast(self) -> None:
        source = CONTENT_VIEW.read_text(encoding="utf-8")

        self.assertIn('Text("Hermes Agent client — scaffold")', source)
        self.assertIn(".foregroundStyle(.primary)", source)
        self.assertNotIn(".foregroundStyle(.secondary)", source)


if __name__ == "__main__":
    unittest.main()
