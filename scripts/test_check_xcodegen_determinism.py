from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "check_xcodegen_determinism.py"


@unittest.skipIf(os.name == "nt", "executable-script probes run in Tier A and Tier B")
class XcodeGenDeterminismTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="talaria-g6-test-")
        self.repository = Path(self.temporary.name)
        (self.repository / "project.yml").write_text("name: Talaria\n", encoding="utf-8")
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

                project = Path.cwd() / "Talaria.xcodeproj"
                project.mkdir()
                content = "stable"
                if mode == "different":
                    content = Path.cwd().name
                (project / "project.pbxproj").write_text(content, encoding="utf-8")
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
        self.assertEqual(before, after)
        self.assertFalse((self.repository / "Talaria.xcodeproj").exists())

    def test_different_generation_bytes_fail(self) -> None:
        result = self.run_checker("different")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("output is not deterministic", result.stderr)
        self.assertIn("project.pbxproj", result.stderr)

    def test_generator_rejection_is_a_gate_failure(self) -> None:
        result = self.run_checker("fail")

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("generation 1 failed with exit code 7", result.stderr)

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
