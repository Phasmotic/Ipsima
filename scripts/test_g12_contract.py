from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parent.parent
LAUNCH_TEST = REPO / "Tests" / "TalariaUITests" / "LaunchPerformanceUITests.swift"
CONTENT_VIEW = REPO / "App" / "Talaria" / "ContentView.swift"
WORKFLOW = REPO / ".github" / "workflows" / "tier-b.yml"


class G12DiagnosticContractTests(unittest.TestCase):
    def test_launch_test_records_exactly_five_official_samples(self) -> None:
        source = LAUNCH_TEST.read_text(encoding="utf-8")

        self.assertEqual(source.count("func testLaunchMetricBaselineRecorded()"), 1)
        self.assertIn("options.iterationCount = 5", source)
        self.assertIn("XCTApplicationLaunchMetric()", source)
        self.assertNotIn("ContinuousClock", source)
        self.assertNotIn("testColdLaunchWithinBudget", source)

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

    def test_diagnostic_uses_structured_metrics_and_remains_blocked(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        diagnostic = workflow.index(
            "Capture G12 structured metrics contract (diagnostic; fail closed)"
        )
        g4 = workflow.index("Verify formatter parity and lint (G4, authoritative)")
        section = workflow[diagnostic:g4]

        self.assertLess(diagnostic, g4)
        self.assertIn("xcresulttool help get test-results metrics", section)
        self.assertIn("xcresulttool get test-results metrics", section)
        self.assertIn("--path .gauntlet/ui.xcresult", section)
        self.assertIn("--compact", section)
        self.assertIn("test ! -s .gauntlet/g12-metrics.stderr", section)
        self.assertIn("G12 BLOCKED", section)
        self.assertIn("exit 2", section)

    def test_phase_zero_footnote_uses_primary_contrast(self) -> None:
        source = CONTENT_VIEW.read_text(encoding="utf-8")

        self.assertIn('Text("Hermes Agent client — scaffold")', source)
        self.assertIn(".foregroundStyle(.primary)", source)
        self.assertNotIn(".foregroundStyle(.secondary)", source)


if __name__ == "__main__":
    unittest.main()
