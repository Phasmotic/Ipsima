from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parent.parent
CANARY = REPO / "scripts" / "check_gitleaks_canary.sh"


class GitleaksCanaryTests(unittest.TestCase):
    def run_canary(self, history_rc: int, worktree_rc: int) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="talaria-g5-fake-") as temporary:
            fake = Path(temporary) / "fake-gitleaks"
            fake.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    case "${1-}" in
                        dir) exit "${FAKE_WORKTREE_RC}" ;;
                        git) exit "${FAKE_HISTORY_RC}" ;;
                        *) exit 99 ;;
                    esac
                    """
                ),
                encoding="utf-8",
                newline="\n",
            )
            fake.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                FAKE_HISTORY_RC=str(history_rc),
                FAKE_WORKTREE_RC=str(worktree_rc),
            )
            return subprocess.run(
                ["bash", str(CANARY), str(fake), "42"],
                cwd=REPO,
                env=environment,
                capture_output=True,
                text=True,
            )

    def test_both_detection_modes_must_return_reserved_findings_status(self) -> None:
        result = self.run_canary(42, 42)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS (history + worktree", result.stdout)

    def test_history_operational_error_blocks(self) -> None:
        result = self.run_canary(1, 42)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("history probe returned rc=1", result.stderr)

    def test_worktree_operational_error_blocks(self) -> None:
        result = self.run_canary(42, 17)

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("worktree probe returned rc=17", result.stderr)


if __name__ == "__main__":
    unittest.main()
