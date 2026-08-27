from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = pathlib.Path(__file__).resolve().parent.parent


class CheckConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="talaria-g3-test-")
        self.repo = pathlib.Path(self.temporary.name)
        scripts = self.repo / "scripts"
        protocol = self.repo / "protocol"
        tests = self.repo / "Packages" / "HermesKit" / "Tests" / "HermesKitTests"
        self.fixtures = tests / "Fixtures"
        scripts.mkdir(parents=True)
        protocol.mkdir(parents=True)
        self.fixtures.mkdir(parents=True)

        shutil.copy2(REPO / "scripts" / "check_conformance.py", scripts)
        shutil.copy2(REPO / "scripts" / "gen_conformance_tests.py", scripts)
        shutil.copy2(REPO / "protocol" / "methods.json", protocol)
        shutil.copy2(
            REPO
            / "Packages"
            / "HermesKit"
            / "Tests"
            / "HermesKitTests"
            / "ProtocolConformanceTests.swift",
            tests,
        )
        self.generated = tests / "ProtocolConformanceTests.swift"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_checker(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(self.repo / "scripts" / "check_conformance.py")],
            capture_output=True,
            text=True,
            cwd=self.repo,
        )

    def write_fixture(self, line: str) -> None:
        (self.fixtures / "golden.jsonl").write_text(line + "\n", encoding="utf-8")

    def test_valid_baseline_passes(self) -> None:
        self.write_fixture('{"jsonrpc":"2.0","method":"ping"}')
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_zero_frames_fail_for_zero_reason(self) -> None:
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no nonblank golden JSON-RPC frames", result.stdout + result.stderr)

    def test_non_envelope_fails_for_envelope_reason(self) -> None:
        self.write_fixture('{"jsonrpc":"2.0"}')
        result = self.run_checker()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "not a JSON-RPC envelope (response must contain exactly one of result or error)",
            result.stdout + result.stderr,
        )

    def test_drift_fails_without_mutating_generated(self) -> None:
        self.write_fixture('{"jsonrpc":"2.0","method":"ping"}')
        with self.generated.open("ab") as generated:
            generated.write(b"// injected drift\n")
        before = self.generated.read_bytes()

        result = self.run_checker()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("committed file is stale", result.stdout + result.stderr)
        self.assertEqual(self.generated.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
