from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
import stat
import unittest
from unittest.mock import patch
import uuid

from scripts import check_tier_b_status_log as checker


REPO = Path(__file__).resolve().parent.parent
CORRELATION = "talaria-" + "0123456789abcdef0123456789abcdef"
OTHER_CORRELATION = "talaria-" + "fedcba9876543210fedcba9876543210"


def record(job: str, status: str, token: str = CORRELATION) -> str:
    return f"{checker.RECORD_PREFIX}{token}|{job}|{status}"


def complete_log(
    ios: str = "PASS",
    watchos: str = "PASS",
    archive: str = "PASS",
    *,
    token: str = CORRELATION,
) -> str:
    return "\n".join(
        (
            record("ios", ios, token),
            record("watchos", watchos, token),
            record("archive", archive, token),
        )
    )


class TierBStatusLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scratch_root = REPO / ".gauntlet" / "tier-b-status-log-tests"
        cls.scratch_root.mkdir(parents=True, exist_ok=True)

    def setUp(self) -> None:
        self.scratch = self.scratch_root / uuid.uuid4().hex
        self.scratch.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.scratch_root.rmdir()
        except OSError:
            pass

    def _invoke(
        self,
        text: str | None = None,
        *,
        raw: bytes | None = None,
        token: str = CORRELATION,
        conclusion: str = "success",
        log_path: Path | None = None,
        argv: list[str] | None = None,
    ) -> tuple[int, str, str]:
        if argv is None:
            if log_path is None:
                log_path = self.scratch / "tier-b.log"
                payload = raw if raw is not None else (text or "").encode("utf-8")
                log_path.write_bytes(payload)
            argv = [
                "--log",
                str(log_path),
                "--correlation",
                token,
                "--conclusion",
                conclusion,
            ]
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = checker.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_verdict(
        self,
        invocation: tuple[int, str, str],
        code: int,
        status: str,
    ) -> str:
        actual_code, stdout, stderr = invocation
        self.assertEqual(actual_code, code, (stdout, stderr))
        expected_stream = stdout if code == 0 else stderr
        other_stream = stderr if code == 0 else stdout
        self.assertEqual(other_stream, "")
        lines = expected_stream.splitlines()
        self.assertEqual(len(lines), 1, (stdout, stderr))
        self.assertTrue(lines[0].startswith(f"TIER B EVIDENCE {status}: "))
        self.assertNotIn(CORRELATION, stdout + stderr)
        self.assertNotIn(OTHER_CORRELATION, stdout + stderr)
        return lines[0]

    def assert_blocked(self, invocation: tuple[int, str, str]) -> str:
        return self.assert_verdict(invocation, 2, "BLOCKED")

    def test_all_pass_with_successful_conclusion_passes(self) -> None:
        self.assert_verdict(self._invoke(complete_log()), 0, "PASS")

    def test_github_line_prefixes_crlf_noise_and_job_order_are_accepted(self) -> None:
        text = "\r\n".join(
            (
                "unrelated GitHub runtime output",
                "archive\tstep\t2026-01-01Z " + record("archive", "PASS"),
                "ios\tstep\t2026-01-01Z " + record("ios", "PASS"),
                "watch\tstep\t2026-01-01Z " + record("watchos", "PASS"),
                "unrelated trailer",
            )
        )
        self.assert_verdict(self._invoke(text), 0, "PASS")

    def test_each_job_fail_status_produces_fail(self) -> None:
        for job in checker.EXPECTED_JOBS:
            with self.subTest(job=job):
                statuses = {key: "PASS" for key in checker.EXPECTED_JOBS}
                statuses[job] = "FAIL"
                text = complete_log(
                    statuses["ios"], statuses["watchos"], statuses["archive"]
                )
                self.assert_verdict(
                    self._invoke(text, conclusion="failure"), 1, "FAIL"
                )

    def test_multiple_fail_statuses_still_produce_fail(self) -> None:
        text = complete_log("FAIL", "PASS", "FAIL")
        self.assert_verdict(self._invoke(text, conclusion="failure"), 1, "FAIL")

    def test_blocked_dominates_fail_and_pass(self) -> None:
        variants = (
            ("BLOCKED", "PASS", "PASS"),
            ("FAIL", "BLOCKED", "PASS"),
            ("FAIL", "FAIL", "BLOCKED"),
            ("BLOCKED", "BLOCKED", "BLOCKED"),
        )
        for statuses in variants:
            with self.subTest(statuses=statuses):
                self.assert_verdict(
                    self._invoke(complete_log(*statuses), conclusion="failure"),
                    2,
                    "BLOCKED",
                )

    def test_conclusion_contradictions_block(self) -> None:
        variants = (
            (complete_log(), "failure"),
            (complete_log("FAIL", "PASS", "PASS"), "success"),
            (complete_log("BLOCKED", "PASS", "PASS"), "success"),
        )
        for text, conclusion in variants:
            with self.subTest(conclusion=conclusion):
                marker = self.assert_blocked(
                    self._invoke(text, conclusion=conclusion)
                )
                self.assertIn("contradict", marker)

    def test_invalid_expected_correlation_values_block_without_disclosure(self) -> None:
        invalid_tokens = (
            "",
            "talaria-0123",
            "talaria-0123456789abcdef0123456789abcdeg",
            "Talaria-0123456789abcdef0123456789abcdef",
            CORRELATION + "0",
            " talaria-0123456789abcdef0123456789abcdef",
        )
        for invalid_token in invalid_tokens:
            with self.subTest(token=invalid_token):
                code, stdout, stderr = self._invoke(
                    complete_log(), token=invalid_token
                )
                self.assertEqual(code, 2)
                self.assertTrue(
                    stderr.startswith("TIER B EVIDENCE BLOCKED: "), stderr
                )
                self.assertEqual(stdout, "")
                if invalid_token:
                    self.assertNotIn(invalid_token, stdout + stderr)

    def test_mismatched_record_correlation_blocks_without_disclosure(self) -> None:
        marker = self.assert_blocked(
            self._invoke(complete_log(token=OTHER_CORRELATION))
        )
        self.assertNotIn("correlation", marker.lower())

    def test_missing_extra_duplicate_and_unknown_jobs_block(self) -> None:
        variants = {
            "missing": "\n".join((record("ios", "PASS"), record("archive", "PASS"))),
            "extra": complete_log() + "\n" + record("decoy", "PASS"),
            "duplicate": complete_log() + "\n" + record("ios", "PASS"),
            "unknown only": "\n".join(
                (record("ios", "PASS"), record("watchos", "PASS"), record("phone", "PASS"))
            ),
            "empty": "unrelated output only",
        }
        for label, text in variants.items():
            with self.subTest(case=label):
                self.assert_blocked(self._invoke(text))

    def test_every_prefix_bearing_line_must_be_exactly_valid(self) -> None:
        malformed = (
            checker.RECORD_PREFIX,
            checker.RECORD_PREFIX + CORRELATION,
            checker.RECORD_PREFIX + f"{CORRELATION}|ios",
            checker.RECORD_PREFIX + f"{CORRELATION}|ios|PASS|extra",
            checker.RECORD_PREFIX + f"{CORRELATION}|ios|pass",
            checker.RECORD_PREFIX + f"{CORRELATION}|ios|UNKNOWN",
            checker.RECORD_PREFIX + f"{CORRELATION}|ios |PASS",
            checker.RECORD_PREFIX + f"{CORRELATION}|ios|PASS ",
            checker.RECORD_PREFIX + f"{CORRELATION} |ios|PASS",
            checker.RECORD_PREFIX + f"{OTHER_CORRELATION}|ios|PASS",
            checker.RECORD_PREFIX
            + f"{CORRELATION}|ios|PASS"
            + checker.RECORD_PREFIX,
        )
        for bad_line in malformed:
            with self.subTest(record=bad_line):
                self.assert_blocked(self._invoke(complete_log() + "\n" + bad_line))

    def test_prefix_must_be_contiguous_and_record_must_consume_line_end(self) -> None:
        split_prefix = "TALARIA_TIER_B_" + " JOB_STATUS|"
        text = complete_log() + "\n" + split_prefix + f"{CORRELATION}|ios|FAIL"
        self.assert_verdict(self._invoke(text), 0, "PASS")

        trailing = complete_log().replace(
            record("archive", "PASS"), record("archive", "PASS") + " trailing"
        )
        self.assert_blocked(self._invoke(trailing))

    def test_malformed_record_token_blocks(self) -> None:
        malformed_tokens = (
            "talaria-0123",
            "talaria-0123456789ABCDEF0123456789ABCDEF",
            CORRELATION + "0",
        )
        for malformed_token in malformed_tokens:
            with self.subTest(token=malformed_token):
                text = complete_log().replace(
                    record("ios", "PASS"), record("ios", "PASS", malformed_token)
                )
                self.assert_blocked(self._invoke(text))

    def test_invalid_conclusion_and_command_lines_block(self) -> None:
        for conclusion in ("", "cancelled", "Success", " failure"):
            with self.subTest(conclusion=conclusion):
                self.assert_blocked(
                    self._invoke(complete_log(), conclusion=conclusion)
                )
        self.assert_blocked(self._invoke(argv=[]))
        self.assert_blocked(self._invoke(argv=["--unknown"]))

    def test_missing_directory_symlink_and_unreadable_log_block(self) -> None:
        missing = self.scratch / "missing.log"
        self.assert_blocked(self._invoke(log_path=missing))

        directory = self.scratch / "directory"
        directory.mkdir()
        self.assert_blocked(self._invoke(log_path=directory))

        regular = self.scratch / "regular.log"
        regular.write_text(complete_log(), encoding="utf-8")
        with patch.object(Path, "lstat") as mocked_lstat:
            mocked_lstat.return_value.st_mode = stat.S_IFLNK
            self.assert_blocked(self._invoke(log_path=regular))

        with patch.object(Path, "read_bytes", side_effect=PermissionError):
            self.assert_blocked(self._invoke(log_path=regular))

    def test_non_utf8_nul_and_exotic_line_terminator_records_block(self) -> None:
        self.assert_blocked(self._invoke(raw=b"\xff"))
        self.assert_blocked(self._invoke(raw=complete_log().encode() + b"\0"))
        exotic = complete_log().replace(
            record("ios", "PASS"), record("ios", "PASS") + "\u2028"
        )
        self.assert_blocked(self._invoke(exotic))


if __name__ == "__main__":
    unittest.main()
