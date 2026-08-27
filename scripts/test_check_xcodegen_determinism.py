from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml


REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "check_xcodegen_determinism.py"


class ProjectBundleVersionTests(unittest.TestCase):
    def test_parent_apps_and_extensions_share_project_versions(self) -> None:
        project = yaml.safe_load((REPO / "project.yml").read_text(encoding="utf-8"))
        base = project["settings"]["base"]
        self.assertEqual(base["CURRENT_PROJECT_VERSION"], "1")
        self.assertEqual(base["MARKETING_VERSION"], "1.0")

        for target_name in (
            "Talaria",
            "TalariaWidgets",
            "TalariaWatch",
            "TalariaWatchWidgets",
        ):
            target_base = project["targets"][target_name].get("settings", {}).get(
                "base", {}
            )
            with self.subTest(target=target_name):
                self.assertNotIn("CURRENT_PROJECT_VERSION", target_base)
                self.assertNotIn("MARKETING_VERSION", target_base)

        for target_name in ("TalariaWidgets", "TalariaWatchWidgets"):
            properties = project["targets"][target_name]["info"]["properties"]
            with self.subTest(widget=target_name):
                self.assertEqual(
                    properties["CFBundleVersion"], "$(CURRENT_PROJECT_VERSION)"
                )
                self.assertEqual(
                    properties["CFBundleShortVersionString"], "$(MARKETING_VERSION)"
                )


class ProjectMetadataContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project = yaml.safe_load(
            (REPO / "project.yml").read_text(encoding="utf-8")
        )

    def test_all_swift_test_bundles_link_app_intents(self) -> None:
        expected_dependencies = {
            "TalariaTests": [
                {"target": "Talaria"},
                {"sdk": "AppIntents.framework"},
            ],
            "TalariaUITests": [
                {"target": "Talaria"},
                {"sdk": "AppIntents.framework"},
            ],
            "TalariaWatchUITests": [
                {"target": "TalariaWatch"},
                {"sdk": "AppIntents.framework"},
            ],
        }

        for target_name, expected in expected_dependencies.items():
            dependencies = self.project["targets"][target_name]["dependencies"]
            with self.subTest(target=target_name):
                self.assertEqual(dependencies, expected)

    def test_watch_app_declares_companion_without_becoming_watch_only(self) -> None:
        settings = self.project["targets"]["TalariaWatch"]["settings"]["base"]

        self.assertEqual(
            settings["INFOPLIST_KEY_WKCompanionAppBundleIdentifier"],
            self.project["targets"]["Talaria"]["settings"]["base"][
                "PRODUCT_BUNDLE_IDENTIFIER"
            ],
        )
        self.assertIs(
            settings["INFOPLIST_KEY_WKRunsIndependentlyOfCompanionApp"], True
        )
        self.assertNotIn("INFOPLIST_KEY_WKWatchOnly", settings)
        self.assertNotIn(
            "INFOPLIST_KEY_WKWatchOnly", self.project["settings"]["base"]
        )


@unittest.skipIf(os.name == "nt", "executable-script probes run in Tier A and Tier B")
class XcodeGenDeterminismTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="talaria-g6-test-")
        self.repository = Path(self.temporary.name)
        (self.repository / "project.yml").write_text("name: Talaria\n", encoding="utf-8")
        (self.repository / "App").mkdir()
        self.mode = self.repository / "mode.txt"
        self.mode.write_text("stable\n", encoding="utf-8", newline="\n")
        self.fake = self.repository / "fake-xcodegen"
        self.fake.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                from pathlib import Path
                import sys

                mode = (Path(__file__).parent / "mode.txt").read_text(encoding="utf-8").strip()
                if mode == "fail":
                    raise SystemExit(7)
                if mode == "fail-path":
                    print(Path.cwd(), file=sys.stderr)
                    raise SystemExit(7)

                project = Path.cwd() / "Talaria.xcodeproj"
                project.mkdir()
                content = "stable"
                if mode == "different":
                    content = Path.cwd().name
                (project / "project.pbxproj").write_text(content, encoding="utf-8")

                generated = Path.cwd() / ".gauntlet" / "generated"
                generated.mkdir(parents=True)
                plist_names = (
                    "TalariaWatchWidgets-Info.plist",
                    "TalariaWidgets-Info.plist",
                )
                for name in plist_names:
                    if mode == "missing-plist" and name == plist_names[0]:
                        continue
                    plist_content = "stable"
                    if mode == "different-plist" and name == plist_names[0]:
                        plist_content = Path.cwd().name
                    (generated / name).write_text(plist_content, encoding="utf-8")

                if mode == "extra-plist":
                    (generated / "Unexpected-Info.plist").write_text(
                        "unexpected", encoding="utf-8"
                    )
                if mode == "extra-output":
                    (Path.cwd() / "App" / "Unexpected.plist").write_text(
                        "unexpected", encoding="utf-8"
                    )
                if mode == "extra":
                    (Path.cwd() / "Other.xcodeproj").mkdir()
                """
            ),
            encoding="utf-8",
            newline="\n",
        )
        self.fake.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_checker(self, mode: str, executable: Path | None = None) -> subprocess.CompletedProcess[str]:
        self.mode.write_text(mode + "\n", encoding="utf-8", newline="\n")
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(CHECKER),
                "--xcodegen",
                str(executable or self.fake),
            ],
            cwd=self.repository,
            capture_output=True,
            text=True,
        )

    def test_identical_generations_pass_without_mutating_source_tree(self) -> None:
        before = sorted(path.relative_to(self.repository) for path in self.repository.rglob("*"))
        result = self.run_checker("stable")
        after = sorted(path.relative_to(self.repository) for path in self.repository.rglob("*"))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("XcodeGen deterministic", result.stdout)
        self.assertIn("plus 2 generated plists", result.stdout)
        self.assertEqual(before, after)
        self.assertFalse((self.repository / "Talaria.xcodeproj").exists())

    def test_different_generation_bytes_fail(self) -> None:
        result = self.run_checker("different")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("output is not deterministic", result.stderr)
        self.assertIn("Talaria.xcodeproj/project.pbxproj", result.stderr)

    def test_different_generated_plist_bytes_fail(self) -> None:
        result = self.run_checker("different-plist")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("output is not deterministic", result.stderr)
        self.assertIn(
            ".gauntlet/generated/TalariaWatchWidgets-Info.plist", result.stderr
        )

    def test_missing_generated_plist_fails(self) -> None:
        result = self.run_checker("missing-plist")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unexpected generated plist set", result.stderr)
        self.assertIn(
            "missing: .gauntlet/generated/TalariaWatchWidgets-Info.plist",
            result.stderr,
        )

    def test_unexpected_generated_plist_fails(self) -> None:
        result = self.run_checker("extra-plist")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unexpected generated plist set", result.stderr)
        self.assertIn(
            "unexpected: .gauntlet/generated/Unexpected-Info.plist",
            result.stderr,
        )

    def test_generated_file_outside_exact_output_set_fails(self) -> None:
        result = self.run_checker("extra-output")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("changed paths outside the exact output set", result.stderr)
        self.assertIn("App/Unexpected.plist: added", result.stderr)
        self.assertNotIn(str(self.repository), result.stderr)

    def test_generator_rejection_is_a_gate_failure(self) -> None:
        result = self.run_checker("fail")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("generation 1 failed with exit code 7", result.stderr)

    def test_generator_diagnostic_sanitizes_temporary_checkout_path(self) -> None:
        result = self.run_checker("fail-path")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("<temporary-checkout>", result.stderr)
        self.assertNotIn("talaria-xcodegen-", result.stderr)

    def test_unavailable_executable_is_blocked(self) -> None:
        result = self.run_checker("stable", self.repository / "missing-xcodegen")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("executable is unavailable", result.stderr)

    def test_extra_generated_project_fails(self) -> None:
        result = self.run_checker("extra")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unexpected project set", result.stderr)


if __name__ == "__main__":
    unittest.main()
