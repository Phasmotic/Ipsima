from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parent.parent
LAUNCH_TEST = REPO / "Tests" / "TalariaUITests" / "LaunchPerformanceUITests.swift"
CONTENT_VIEW = REPO / "App" / "Talaria" / "ContentView.swift"
WORKFLOW = REPO / ".github" / "workflows" / "tier-b.yml"
CHECKER = REPO / "scripts" / "check_launch_metrics.py"
BRIEF = REPO / "docs" / "BRIEF.md"
GOVERNANCE = REPO / "docs" / "GOVERNANCE.md"


class G12ContractTests(unittest.TestCase):
    def test_launch_test_records_exactly_five_official_samples(self) -> None:
        source = LAUNCH_TEST.read_text(encoding="utf-8")

        self.assertEqual(source.count("func testLaunchMetricBaselineRecorded()"), 1)
        self.assertIn("options.iterationCount = 5", source)
        self.assertIn("XCTApplicationLaunchMetric()", source)
        self.assertNotIn("ContinuousClock", source)
        self.assertNotIn("testColdLaunchWithinBudget", source)

    def test_each_launch_sample_is_cold_and_reaches_the_root(self) -> None:
        source = LAUNCH_TEST.read_text(encoding="utf-8")
        measured = source.split(
            "measure(metrics: [XCTApplicationLaunchMetric()], options: options) {",
            maxsplit=1,
        )[1].split("\n        }", maxsplit=1)[0]

        self.assertEqual(source.count("self.terminateAndAssertNotRunning(app)"), 2)
        self.assertEqual(measured.count("XCTAssertEqual(app.state, .notRunning"), 1)
        self.assertEqual(measured.count("app.launch()"), 1)
        self.assertEqual(
            measured.count("rootElement.waitForExistence(timeout: 10)"), 1
        )
        self.assertEqual(measured.count("self.terminateAndAssertNotRunning(app)"), 1)
        self.assertLess(
            measured.index("XCTAssertEqual(app.state, .notRunning"),
            measured.index("app.launch()"),
        )
        self.assertLess(
            measured.index("app.launch()"),
            measured.index("rootElement.waitForExistence(timeout: 10)"),
        )
        self.assertLess(
            measured.index("rootElement.waitForExistence(timeout: 10)"),
            measured.index("self.terminateAndAssertNotRunning(app)"),
        )
        helper = source.split(
            "private func terminateAndAssertNotRunning", maxsplit=1
        )[1]
        self.assertIn("app.terminate()", helper)
        self.assertIn("app.wait(for: .notRunning, timeout: 10)", helper)

    def test_xcresult_expectation_tracks_the_single_metric_test(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            "--expect 'TalariaUITests/LaunchPerformanceUITests/"
            "testLaunchMetricBaselineRecorded()'",
            workflow,
        )
        self.assertIn(
            "--expect-count 'TalariaUITests/LaunchPerformanceUITests=1'",
            workflow,
        )
        self.assertNotIn("testColdLaunchWithinBudget", workflow)

    def test_workflow_checks_exact_pinned_structured_metric(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        g12 = workflow.index("Verify G12 cold-launch budget")
        g11 = workflow.index(
            "G11 — N/A (no real streaming chat surface to capture)", g12
        )
        section = workflow[g12:g11]

        self.assertLess(g12, g11)
        self.assertEqual(section.count("xcresulttool get test-results metrics"), 2)
        self.assertEqual(section.count("--schema-version 0.1.0"), 2)
        self.assertIn("--path .gauntlet/ui.xcresult", section)
        self.assertIn(
            "G12_TEST_ID='test://com.apple.xcode/Talaria/TalariaUITests/"
            "LaunchPerformanceUITests/testLaunchMetricBaselineRecorded'",
            section,
        )
        self.assertIn('--test-id "$G12_TEST_ID"', section)
        self.assertIn("g12_blocked", section)
        self.assertIn("test -s .gauntlet/g12-metrics-schema.json", section)
        self.assertIn("test ! -s .gauntlet/g12-metrics-schema.stderr", section)
        self.assertIn("shasum -a 256 .gauntlet/g12-metrics-schema.json", section)
        self.assertIn(
            "55401dc6d98f6f89f82e05c971c3a29b8511a698d962af5a04c684c6fe46d8bf",
            section,
        )
        self.assertIn("test -s .gauntlet/g12-metrics.json", section)
        self.assertIn("test ! -s .gauntlet/g12-metrics.stderr", section)
        self.assertIn("python3 -B scripts/check_launch_metrics.py", section)
        self.assertIn("--schema-json .gauntlet/g12-metrics-schema.json", section)
        self.assertIn("--metrics-json .gauntlet/g12-metrics.json", section)
        self.assertNotIn("G12 BLOCKED", section)
        for marker in (
            "G12 COLD-LAUNCH PASS: ",
            "G12 COLD-LAUNCH FAIL: ",
            "G12 COLD-LAUNCH BLOCKED: ",
        ):
            self.assertIn(marker, section)
        self.assertGreaterEqual(section.count("exit 2"), 2)
        self.assertNotIn("placeholder", section.lower())
        self.assertNotIn("tee .gauntlet/g12-metrics.json", section)
        self.assertNotIn("tee .gauntlet/g12-metrics-schema.json", section)

    def test_tier_b_self_tests_the_launch_metrics_checker(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("scripts.test_check_launch_metrics", workflow)

    def test_streaming_clause_records_orchestrator_n_a_without_claiming_pass(
        self,
    ) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        ios = workflow[
            workflow.index("  ios:") : workflow.index("  watchos:")
        ]
        g4 = workflow.index("Verify formatter parity and lint (G4, authoritative)")
        step_header = (
            "      - name: G12 streaming responsiveness — N/A "
            "(no streaming surface until P2)"
        )
        reporter_header = "      - name: Emit Tier B job status"
        self.assertEqual(ios.count("      - name: G12 streaming responsiveness"), 1)
        not_applicable = workflow.index(step_header)
        reporter = workflow.index(reporter_header, not_applicable)
        section = workflow[not_applicable:reporter]
        expected_section = (
            f"{step_header}\n"
            "        run: |\n"
            '          status="G12 streaming responsiveness — N/A '
            "(no streaming surface until P2; arms with G11 and G14 on the first "
            'real streaming chat surface)"\n'
            "          printf '%s\\n' \"$status\" | tee "
            ".gauntlet/g12-streaming-status.txt\n"
            "          printf '### %s\\n' \"$status\" >> "
            '"$GITHUB_STEP_SUMMARY"\n'
            "\n"
        )

        self.assertGreater(not_applicable, g4)
        self.assertEqual(section, expected_section)
        self.assertNotIn("PASS", section)
        self.assertNotIn("BLOCKED", section)
        self.assertNotIn("exit ", section)
        self.assertNotIn("synthetic", ios.lower())
        self.assertNotIn("benchmark", ios.lower())

        watchos = workflow.index("  watchos:", reporter)
        self.assertEqual(workflow[reporter:watchos].count("      - name:"), 1)

    def test_phase_contract_rearms_dormant_gates_and_rebuilds_g11(self) -> None:
        brief = BRIEF.read_text(encoding="utf-8")
        governance = GOVERNANCE.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")

        for source in (brief, governance):
            self.assertIn("G12 streaming", source)
            self.assertIn("G11", source)
            self.assertIn("G14", source)
            self.assertIn("first real streaming chat surface", source)
            self.assertIn("ScreenshotMatrixUITests.swift", source)
            self.assertIn("rebuild", source.lower())
            self.assertIn("P2 is not complete", source)
            self.assertIn(
                "N/A (no real streaming chat surface to capture)", source
            )
            self.assertIn(
                "N/A (no G11 images to review before that arm point)", source
            )

        self.assertIn(
            "enumerate every N/A gate by name and reason",
            " ".join(governance.split()),
        )
        self.assertIn(
            "G11 — N/A (no real streaming chat surface to capture", workflow
        )
        self.assertIn("ScreenshotMatrixUITests.swift must be rebuilt", workflow)

    def test_checker_pins_schema_version_and_strict_budget(self) -> None:
        source = CHECKER.read_text(encoding="utf-8")

        self.assertIn('EXPECTED_SCHEMA_VERSION = "0.1.0"', source)
        self.assertIn('LAUNCH_BUDGET_SECONDS = Decimal("3")', source)
        self.assertIn("self.mean_seconds < LAUNCH_BUDGET_SECONDS", source)

    def test_phase_zero_footnote_uses_primary_contrast(self) -> None:
        source = CONTENT_VIEW.read_text(encoding="utf-8")

        self.assertIn('Text("Hermes Agent client — scaffold")', source)
        self.assertIn(".foregroundStyle(.primary)", source)
        self.assertNotIn(".foregroundStyle(.secondary)", source)


if __name__ == "__main__":
    unittest.main()
