from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "check_xcode_log.py"


class XcodeLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="talaria-xcode-log-")
        self.log = Path(self.temporary.name) / "xcode.log"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_checker(
        self,
        text: str,
        markers: tuple[str, ...] = ("** BUILD SUCCEEDED **",),
    ) -> subprocess.CompletedProcess[str]:
        self.log.write_text(text, encoding="utf-8", newline="\n")
        marker_arguments = [
            argument
            for marker in markers
            for argument in ("--success-marker", marker)
        ]
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(CHECKER),
                "--log",
                str(self.log),
                *marker_arguments,
            ],
            capture_output=True,
            text=True,
        )

    def test_success_marker_without_warnings_passes(self) -> None:
        result = self.run_checker("CompileSwift\n** BUILD SUCCEEDED **\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_warning_with_success_marker_fails(self) -> None:
        result = self.run_checker("source.swift:1:1: warning: probe\n** BUILD SUCCEEDED **\n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("warning diagnostics", result.stderr)

    def test_missing_success_marker_fails(self) -> None:
        result = self.run_checker("CompileSwift\n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("missing success marker", result.stderr)

    def test_every_declared_success_marker_is_required(self) -> None:
        result = self.run_checker(
            "--- debug ---\n",
            markers=("--- debug ---", "--- release ---"),
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("--- release ---", result.stderr)

    def test_warning_scan_can_run_without_a_success_marker(self) -> None:
        result = self.run_checker("tool warning: probe\n", markers=())
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("warning diagnostics", result.stderr)

    def test_empty_log_is_blocked(self) -> None:
        result = self.run_checker("")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
