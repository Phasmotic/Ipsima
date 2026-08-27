#!/usr/bin/env python3
"""Offline failure-mode tests for the Tier B SwiftLint installer."""
from __future__ import annotations

import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = REPO / "scripts" / "install_swiftlint_macos.sh"
TEST_ROOT = REPO / ".gauntlet" / "install-swiftlint-tests"


class SwiftLintInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="case-", dir=TEST_ROOT)
        self.root = pathlib.Path(self.temporary.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        shutil.copy2(INSTALLER, scripts / INSTALLER.name)
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.github_path = self.root / "github-path.txt"
        self._write_tool(
            "curl",
            """#!/usr/bin/python3
import pathlib
import sys

arguments = sys.argv[1:]
output = pathlib.Path(arguments[arguments.index("--output") + 1])
output.write_bytes(b"synthetic verified archive")
""",
        )
        self._write_tool(
            "shasum",
            """#!/usr/bin/env bash
exit "${TALARIA_FAKE_SHASUM_RC:-0}"
""",
        )
        self._write_tool(
            "unzip",
            """#!/usr/bin/python3
import os
import sys

if os.environ.get("TALARIA_FAKE_UNZIP_RC"):
    raise SystemExit(int(os.environ["TALARIA_FAKE_UNZIP_RC"]))
if os.environ.get("TALARIA_FAKE_UNZIP_EMPTY") == "1":
    raise SystemExit(0)
version = os.environ.get("TALARIA_FAKE_SWIFTLINT_VERSION", "0.65.0")
stderr = os.environ.get("TALARIA_FAKE_VERSION_STDERR", "")
sys.stdout.write(f'''#!/usr/bin/env bash
if [ "${{1-}}" = "version" ]; then
    printf '%s\\n' '{version}'
    if [ -n '{stderr}' ]; then printf '%s\\n' '{stderr}' >&2; fi
    exit 0
fi
exit 64
''')
""",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_tool(self, name: str, content: str) -> None:
        path = self.fake_bin / name
        path.write_text(content, encoding="utf-8", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _run(self, **environment: str) -> subprocess.CompletedProcess[str]:
        process_environment = os.environ.copy()
        process_environment.update(
            {
                "PATH": f"{self.fake_bin}:/usr/bin:/bin",
                "GITHUB_PATH": str(self.github_path),
                **environment,
            }
        )
        return subprocess.run(
            ["bash", f"scripts/{INSTALLER.name}"],
            cwd=self.root,
            env=process_environment,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_pins_official_0650_artifact_bundle_and_digest(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('SWIFTLINT_VERSION="0.65.0"', source)
        self.assertIn("SwiftLintBinary.artifactbundle.zip", source)
        self.assertIn(
            "eb333bd76dfb5f46d21fdf3615fe39bb938956ca0b8e94c241c4b2db6e696b90",
            source,
        )
        self.assertIn(
            'SWIFTLINT_MEMBER="SwiftLintBinary.artifactbundle/macos/swiftlint"',
            source,
        )

    def test_success_requires_exact_version_and_publishes_path(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("SwiftLint 0.65.0 sha256:eb333bd", result.stdout)
        installed = self.root / ".gauntlet" / "tools" / "swiftlint-macos-0.65.0"
        self.assertTrue(installed.is_file())
        self.assertTrue(os.access(installed, os.X_OK))
        self.assertEqual(
            self.github_path.read_text(encoding="utf-8").strip(),
            str(installed.parent),
        )

    def test_checksum_mismatch_blocks_before_install(self) -> None:
        result = self._run(TALARIA_FAKE_SHASUM_RC="9")
        self.assertEqual(result.returncode, 2)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertFalse(
            (self.root / ".gauntlet" / "tools" / "swiftlint-macos-0.65.0").exists()
        )

    def test_empty_extraction_blocks_before_promotion(self) -> None:
        result = self._run(TALARIA_FAKE_UNZIP_EMPTY="1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("empty executable", result.stderr)
        self.assertFalse(
            (self.root / ".gauntlet" / "tools" / "swiftlint-macos-0.65.0").exists()
        )

    def test_preexisting_version_spoof_is_replaced_from_verified_archive(self) -> None:
        installed = self.root / ".gauntlet" / "tools" / "swiftlint-macos-0.65.0"
        installed.parent.mkdir(parents=True)
        installed.write_text(
            "#!/usr/bin/env bash\nprintf '0.65.0\\n' # PRESEEDED_SHIM\n",
            encoding="utf-8",
            newline="\n",
        )
        installed.chmod(installed.stat().st_mode | stat.S_IXUSR)

        result = self._run()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("PRESEEDED_SHIM", installed.read_text(encoding="utf-8"))
        self.assertIn('if [ "${1-}" = "version" ]', installed.read_text(encoding="utf-8"))

    def test_wrong_version_and_version_stderr_both_block(self) -> None:
        cases = (
            ({"TALARIA_FAKE_SWIFTLINT_VERSION": "0.64.0"}, "expected version"),
            ({"TALARIA_FAKE_VERSION_STDERR": "unexpected"}, "version stderr"),
        )
        for environment, expected in cases:
            with self.subTest(expected=expected):
                result = self._run(**environment)
                self.assertEqual(result.returncode, 2)
                self.assertIn(expected, result.stderr)
                tools = self.root / ".gauntlet" / "tools"
                shutil.rmtree(tools, ignore_errors=True)
                self.github_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
