from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parent.parent
LAUNCH_TEST = REPO / "Tests" / "TalariaUITests" / "LaunchPerformanceUITests.swift"
CONTENT_VIEW = REPO / "App" / "Talaria" / "ContentView.swift"
WORKFLOW = REPO / ".github" / "workflows" / "tier-b.yml"
CHECKER = REPO / "scripts" / "check_launch_metrics.py"


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
        g11 = workflow.index("G11 — N/A (no UI surface yet)", g12)
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

    def test_overall_g12_cannot_green_without_streaming_authority(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        g4 = workflow.index("Verify formatter parity and lint (G4, authoritative)")
        blocked = workflow.index(
            "G12 streaming responsiveness — BLOCKED pending phase decision"
        )
        watchos = workflow.index("  watchos:", blocked)
        section = workflow[blocked:watchos]

        self.assertGreater(blocked, g4)
        self.assertIn("if: ${{ always() }}", section)
        self.assertIn("G12 BLOCKED — streaming responsiveness is not certified", section)
        self.assertIn("exit 2", section)

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
