from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "verify_apple_toolchain.sh"
EXPECTED_SWIFT = (
    "swift-driver version: 1.148.6 Apple Swift version 6.3.3 "
    "(swiftlang-6.3.3.1.3 clang-2100.1.1.101)"
)


class AppleToolchainVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="talaria-apple-toolchain-")
        self.addCleanup(self.temporary.cleanup)
        self.bin = Path(self.temporary.name)

    def _write_tool(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n" + textwrap.dedent(body),
            encoding="utf-8",
            newline="\n",
        )
        path.chmod(0o755)

    def run_checker(
        self,
        *,
        xcode_version: str = "Xcode 26.6",
        xcode_build: str = "Build version 17F113",
        swift_version: str = EXPECTED_SWIFT,
    ) -> subprocess.CompletedProcess[str]:
        self._write_tool(
            "xcodebuild",
            f"""
            printf '%s\\n%s\\n' {xcode_version!r} {xcode_build!r}
            """,
        )
        self._write_tool(
            "xcrun",
            f"""
            [ "${{1:-}}" = "swift" ]
            [ "${{2:-}}" = "--version" ]
            printf '%s\\n' {swift_version!r}
            """,
        )
        environment = dict(os.environ)
        environment["PATH"] = str(self.bin) + os.pathsep + environment["PATH"]
        return subprocess.run(
            ["bash", str(CHECKER)],
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_xcode_26_6_driver_prefixed_swift_6_3_3_passes(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(EXPECTED_SWIFT, result.stdout)

    def test_wrong_swift_minor_fails(self) -> None:
        result = self.run_checker(
            swift_version="swift-driver version: 1.0 Apple Swift version 6.3.2"
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_lookalike_swift_version_fails(self) -> None:
        result = self.run_checker(
            swift_version="swift-driver version: 1.0 Apple Swift version 6.3.30"
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_regex_metacharacter_lookalike_fails(self) -> None:
        result = self.run_checker(
            swift_version="swift-driver version: 1.0 Apple Swift version 6x3y3"
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_wrong_xcode_build_fails(self) -> None:
        result = self.run_checker(xcode_build="Build version 17F999")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
