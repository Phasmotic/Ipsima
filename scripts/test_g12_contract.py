from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parent.parent
CONTENT_VIEW = REPO / "App" / "Talaria" / "ContentView.swift"
WORKFLOW = REPO / ".github" / "workflows" / "tier-b.yml"
PROJECT = REPO / "project.yml"
BRIEF = REPO / "docs" / "BRIEF.md"
GOVERNANCE = REPO / "docs" / "GOVERNANCE.md"

DROPPED_INSTRUMENTS = (
    "App/Talaria/AuditedLaunchResourceFactory.swift",
    "App/Talaria/LaunchActivityAudit.swift",
    "App/Talaria/LaunchFirstDrawProbe.swift",
    "App/Talaria/LaunchLinkAnchor.swift",
    "Tests/TalariaLaunchABTests/LinkABLaunchUITests.swift",
    "Tests/TalariaUITests/LaunchActivityAuditUITests.swift",
    "Tests/TalariaUITests/LaunchPerformanceUITests.swift",
    "scripts/analyze_launch_ab.py",
    "scripts/check_launch_ab_builds.py",
    "scripts/check_launch_ab_project.py",
    "scripts/check_launch_activity_sources.py",
    "scripts/check_launch_metrics.py",
    "scripts/check_launch_structure.py",
    "scripts/baselines/launch-structure-xcode-26.6-arm64.json",
)


class G12ContractTests(unittest.TestCase):
    def test_cold_launch_is_stood_down_without_an_instrument(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        project = PROJECT.read_text(encoding="utf-8")
        header = "G12 cold launch — STOOD DOWN (gate-specification defect)"
        start = workflow.index(header)
        end = workflow.index("G11 — N/A", start)
        section = workflow[start:end]

        self.assertEqual(workflow.count(header), 1)
        self.assertIn("never PASS or N/A", section)
        self.assertIn("replacement coverage deferred to P2", section)
        self.assertIn("P2 cannot close before re-arm", section)
        self.assertNotIn("G12 COLD-LAUNCH PASS", workflow)
        self.assertNotIn("G12 COLD-LAUNCH FAIL", workflow)
        self.assertNotIn("G12 COLD-LAUNCH BLOCKED", workflow)
        self.assertNotIn("LaunchPerformanceUITests", workflow)
        self.assertNotIn("check_launch_metrics", workflow)
        self.assertNotIn("DYLD_PRINT_STATISTICS", workflow)
        self.assertNotIn("TalariaLaunchAB", project)
        self.assertIn("name: G7/G8/G10 · iOS simulator", workflow)

        for relative in DROPPED_INSTRUMENTS:
            with self.subTest(path=relative):
                self.assertFalse((REPO / relative).exists())

    def test_record_and_p2_rearm_contract_are_durable(self) -> None:
        for path in (BRIEF, GOVERNANCE):
            source = path.read_text(encoding="utf-8")
            normalized = " ".join(source.split())
            lower = normalized.lower()

            self.assertIn("G12 cold launch", source)
            self.assertIn("stood down", lower)
            self.assertIn("never PASS or N/A", source)
            self.assertIn("gate-specification defect", lower)
            self.assertIn("3.0482066832", source)
            self.assertIn("six failures", lower)
            self.assertIn("public audit issue #2", lower)
            self.assertIn("replacement coverage is deferred to p2", lower)
            self.assertIn("P2 cannot close", source)
            self.assertIn("detection floor", lower)
            self.assertIn("confidence", lower)
            self.assertIn("pinned known-good reference binary", lower)
            self.assertIn("interleaved", lower)
            self.assertIn("median delta or ratio", lower)
            self.assertIn("at least ten", lower)
            self.assertIn("false-fail budget", lower)
            self.assertIn("1%", source)
            self.assertIn("30 runner instances", lower)
            self.assertIn("seven days", lower)
            self.assertIn("MAD", source)
            self.assertIn("P95", source)
            self.assertIn("one-retry", lower)
            self.assertIn("warm-up", lower)

    def test_governance_prevents_relabeling_and_selective_measurement(self) -> None:
        source = " ".join(GOVERNANCE.read_text(encoding="utf-8").split())
        lower = source.lower()

        self.assertIn("gate's subject is frozen", lower)
        self.assertIn("N/A requires demonstrated absence", source)
        self.assertIn("defective-instrument finding", lower)
        self.assertIn("requires independent review", lower)
        self.assertIn("five independent reviews", lower)
        self.assertIn("uniform warmup", lower)
        self.assertIn(
            "no measurement-protocol change may be made while red to turn that red green",
            lower,
        )
        self.assertIn("selective warming", lower)

    def test_streaming_clause_remains_n_a_until_its_real_surface(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        brief = BRIEF.read_text(encoding="utf-8")
        governance = GOVERNANCE.read_text(encoding="utf-8")

        self.assertEqual(workflow.count("G12 streaming responsiveness — N/A"), 2)
        self.assertNotIn("G12 streaming responsiveness — PASS", workflow)
        for source in (brief, governance):
            normalized = " ".join(source.split())
            self.assertIn(
                "arms at the first real streaming chat surface (currently P3)",
                normalized,
            )
            self.assertIn("ScreenshotMatrixUITests.swift", source)

    def test_phase_zero_footnote_uses_primary_contrast(self) -> None:
        source = CONTENT_VIEW.read_text(encoding="utf-8")

        self.assertIn('Text("Hermes Agent client — scaffold")', source)
        self.assertIn(".foregroundStyle(.primary)", source)
        self.assertNotIn(".foregroundStyle(.secondary)", source)


if __name__ == "__main__":
    unittest.main()
