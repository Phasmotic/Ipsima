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


def record(job: str, status: str, correlation: str = CORRELATION) -> str:
    return f"{checker.RECORD_PREFIX}{correlation}|{job}|{status}"


class TierBStatusLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scratch_root = REPO / ".gauntlet" / "tier-b-status-log-tests"
        cls.scratch_root.mkdir(parents=True, exist_ok=True)

    def setUp(self) -> None:
        self.scratch = self.scratch_root / uuid.uuid4().hex
        self.scratch.mkdir()
        self.paths = {
            job: self.scratch / f"{job}.log" for job in checker.EXPECTED_JOBS
        }
        for job in checker.EXPECTED_JOBS:
            self._write(job, record(job, "PASS"))

    def tearDown(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.scratch_root.rmdir()
        except OSError:
            pass

    def _write(
        self,
        job: str,
        text: str | None = None,
        *,
        raw: bytes | None = None,
    ) -> None:
        payload = raw if raw is not None else (text or "").encode("utf-8")
        self.paths[job].write_bytes(payload)

    def _argv(
        self,
        *,
        paths: dict[str, Path] | None = None,
        job_conclusions: dict[str, str] | None = None,
        correlation: str = CORRELATION,
        conclusion: str = "success",
    ) -> list[str]:
        selected_paths = dict(self.paths if paths is None else paths)
        conclusions = {job: "success" for job in checker.EXPECTED_JOBS}
        if job_conclusions is not None:
            conclusions.update(job_conclusions)
        return [
            "--ios-log",
            str(selected_paths["ios"]),
            "--ios-conclusion",
            conclusions["ios"],
            "--watchos-log",
            str(selected_paths["watchos"]),
            "--watchos-conclusion",
            conclusions["watchos"],
            "--archive-log",
            str(selected_paths["archive"]),
            "--archive-conclusion",
            conclusions["archive"],
            "--correlation",
            correlation,
            "--conclusion",
            conclusion,
        ]

    def _invoke(
        self,
        *,
        paths: dict[str, Path] | None = None,
        job_conclusions: dict[str, str] | None = None,
        correlation: str = CORRELATION,
        conclusion: str = "success",
        argv: list[str] | None = None,
    ) -> tuple[int, str, str]:
        arguments = (
            self._argv(
                paths=paths,
                job_conclusions=job_conclusions,
                correlation=correlation,
                conclusion=conclusion,
            )
            if argv is None
            else argv
        )
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = checker.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_exact(
        self,
        invocation: tuple[int, str, str],
        code: int,
        message: str,
    ) -> None:
        actual_code, stdout, stderr = invocation
        self.assertEqual(actual_code, code, (stdout, stderr))
        if code == 0:
            self.assertEqual(stdout, message + "\n")
            self.assertEqual(stderr, "")
        else:
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, message + "\n")
        combined = stdout + stderr
        self.assertNotIn(CORRELATION, combined)
        self.assertNotIn(OTHER_CORRELATION, combined)
        self.assertNotIn(checker.RECORD_PREFIX, combined)

    def assert_pass(self, invocation: tuple[int, str, str]) -> None:
        self.assert_exact(invocation, 0, checker.PASS_MESSAGE)

    def assert_decisive_blocked(self, invocation: tuple[int, str, str]) -> None:
        self.assert_exact(invocation, 2, checker.DECISIVE_BLOCKED_MESSAGE)

    def assert_generic_blocked(self, invocation: tuple[int, str, str]) -> None:
        self.assert_exact(invocation, 2, checker.GENERIC_BLOCKED_MESSAGE)

    def test_all_pass_with_matching_job_and_run_conclusions_passes(self) -> None:
        self.assert_pass(self._invoke())

    def test_each_blocked_job_and_all_blocked_are_decisive(self) -> None:
        variants = (
            frozenset({"ios"}),
            frozenset({"watchos"}),
            frozenset({"archive"}),
            frozenset(checker.EXPECTED_JOBS),
        )
        for blocked_jobs in variants:
            for job in checker.EXPECTED_JOBS:
                status = "BLOCKED" if job in blocked_jobs else "PASS"
                self._write(job, record(job, status))
            job_conclusions = {
                job: "failure" if job in blocked_jobs else "success"
                for job in checker.EXPECTED_JOBS
            }
            with self.subTest(blocked_jobs=blocked_jobs):
                self.assert_decisive_blocked(
                    self._invoke(
                        job_conclusions=job_conclusions,
                        conclusion="failure",
                    )
                )

    def test_github_prefixes_crlf_noise_and_separate_job_order_are_accepted(self) -> None:
        for job in reversed(checker.EXPECTED_JOBS):
            self._write(
                job,
                "\r\n".join(
                    (
                        "unrelated GitHub runtime output",
                        f"{job}\tstep\t2026-01-01Z " + record(job, "PASS"),
                        "unrelated trailer",
                    )
                ),
            )
        self.assert_pass(self._invoke())

    def test_swapping_job_logs_is_rejected(self) -> None:
        swapped = dict(self.paths)
        swapped["ios"], swapped["watchos"] = swapped["watchos"], swapped["ios"]
        self.assert_generic_blocked(self._invoke(paths=swapped))

    def test_fail_status_is_rejected_in_every_job(self) -> None:
        for target in checker.EXPECTED_JOBS:
            for job in checker.EXPECTED_JOBS:
                self._write(job, record(job, "FAIL" if job == target else "PASS"))
            with self.subTest(job=target):
                self.assert_generic_blocked(
                    self._invoke(
                        job_conclusions={target: "failure"},
                        conclusion="failure",
                    )
                )

    def test_job_status_and_conclusion_mismatches_are_generic(self) -> None:
        variants = (
            ("PASS", "failure"),
            ("BLOCKED", "success"),
            ("PASS", "cancelled"),
            ("PASS", "Success"),
            ("PASS", ""),
        )
        for status, job_conclusion in variants:
            for job in checker.EXPECTED_JOBS:
                self._write(job, record(job, "PASS"))
            self._write("ios", record("ios", status))
            top_conclusion = "failure" if status == "BLOCKED" else "success"
            with self.subTest(status=status, job_conclusion=job_conclusion):
                self.assert_generic_blocked(
                    self._invoke(
                        job_conclusions={"ios": job_conclusion},
                        conclusion=top_conclusion,
                    )
                )

    def test_top_level_conclusion_mismatches_are_generic(self) -> None:
        self.assert_generic_blocked(self._invoke(conclusion="failure"))

        self._write("ios", record("ios", "BLOCKED"))
        self.assert_generic_blocked(
            self._invoke(
                job_conclusions={"ios": "failure"},
                conclusion="success",
            )
        )

        self._write("ios", record("ios", "PASS"))
        for invalid in ("", "cancelled", "Success", " failure"):
            with self.subTest(conclusion=invalid):
                self.assert_generic_blocked(self._invoke(conclusion=invalid))

    def test_duplicate_and_conflicting_records_are_generic(self) -> None:
        variants = (
            record("ios", "PASS") + "\n" + record("ios", "PASS"),
            record("ios", "PASS") + "\n" + record("ios", "BLOCKED"),
            record("ios", "PASS") + "\n" + checker.RECORD_PREFIX,
        )
        for text in variants:
            self._write("ios", text)
            with self.subTest(text=text):
                self.assert_generic_blocked(self._invoke())

    def test_every_prefix_bearing_line_must_be_exact(self) -> None:
        malformed = (
            checker.RECORD_PREFIX,
            checker.RECORD_PREFIX + CORRELATION,
            checker.RECORD_PREFIX + f"{CORRELATION}|ios",
            checker.RECORD_PREFIX + f"{CORRELATION}|ios|PASS|extra",
            checker.RECORD_PREFIX + f"{CORRELATION}|ios|pass",
            checker.RECORD_PREFIX + f"{CORRELATION}|ios|UNKNOWN",
            checker.RECORD_PREFIX + f"{CORRELATION}|phone|PASS",
            checker.RECORD_PREFIX + f"{CORRELATION}|ios |PASS",
            checker.RECORD_PREFIX + f"{CORRELATION}|ios|PASS ",
            checker.RECORD_PREFIX + f"{CORRELATION} |ios|PASS",
            checker.RECORD_PREFIX + f"{OTHER_CORRELATION}|ios|PASS",
            checker.RECORD_PREFIX + "talaria-0123|ios|PASS",
            checker.RECORD_PREFIX
            + f"{CORRELATION}|ios|PASS"
            + checker.RECORD_PREFIX,
        )
        for bad_line in malformed:
            self._write("ios", bad_line)
            with self.subTest(record=bad_line):
                self.assert_generic_blocked(self._invoke())

    def test_empty_partial_noise_only_and_missing_record_are_generic(self) -> None:
        variants = (
            "",
            "unrelated output only",
            "TALARIA_TIER_B_JOB_",
            "TALARIA_TIER_B_ JOB_STATUS|" + f"{CORRELATION}|ios|PASS",
        )
        for text in variants:
            self._write("ios", text)
            with self.subTest(text=text):
                self.assert_generic_blocked(self._invoke())

    def test_invalid_or_mismatched_correlation_is_generic_and_never_disclosed(self) -> None:
        invalid_values = (
            "",
            "talaria-0123",
            "talaria-0123456789abcdef0123456789abcdeg",
            "Talaria-0123456789abcdef0123456789abcdef",
            CORRELATION + "0",
            " " + CORRELATION,
        )
        for invalid in invalid_values:
            with self.subTest(correlation=invalid):
                self.assert_generic_blocked(self._invoke(correlation=invalid))

        self._write("ios", record("ios", "PASS", OTHER_CORRELATION))
        self.assert_generic_blocked(self._invoke())

    def test_missing_unknown_duplicate_and_help_command_lines_are_generic(self) -> None:
        self.assert_generic_blocked(self._invoke(argv=[]))
        self.assert_generic_blocked(self._invoke(argv=["--unknown"]))
        self.assert_generic_blocked(self._invoke(argv=["--help"]))

        duplicated = self._argv() + ["--ios-log", str(self.paths["ios"])]
        self.assert_generic_blocked(self._invoke(argv=duplicated))

        equals_form = self._argv()
        equals_form[0:2] = [f"--ios-log={self.paths['ios']}"]
        self.assert_generic_blocked(self._invoke(argv=equals_form))

    def test_missing_directory_symlink_and_unreadable_files_are_generic(self) -> None:
        missing_paths = dict(self.paths)
        missing_paths["ios"] = self.scratch / "missing.log"
        self.assert_generic_blocked(self._invoke(paths=missing_paths))

        directory = self.scratch / "directory"
        directory.mkdir()
        directory_paths = dict(self.paths)
        directory_paths["watchos"] = directory
        self.assert_generic_blocked(self._invoke(paths=directory_paths))

        with patch.object(Path, "lstat") as mocked_lstat:
            mocked_lstat.return_value.st_mode = stat.S_IFLNK
            self.assert_generic_blocked(self._invoke())

        with patch.object(Path, "read_bytes", side_effect=PermissionError):
            self.assert_generic_blocked(self._invoke())

    def test_non_utf8_nul_and_exotic_line_terminator_files_are_generic(self) -> None:
        self._write("ios", raw=b"\xff")
        self.assert_generic_blocked(self._invoke())

        self._write("ios", raw=record("ios", "PASS").encode("utf-8") + b"\0")
        self.assert_generic_blocked(self._invoke())

        self._write("ios", record("ios", "PASS") + "\u2028")
        self.assert_generic_blocked(self._invoke())


if __name__ == "__main__":
    unittest.main()
