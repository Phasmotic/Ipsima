from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import re
import shutil
import stat
import unittest
from unittest.mock import patch
import uuid

from scripts import check_tier_b_run_snapshot as checker


REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_ID = 24681012
ATTEMPT = 1
HEAD_SHA = "a" * 40
CORRELATION = "talaria-" + "0123456789abcdef0123456789abcdef"
TITLE = f"Talaria Tier B: {CORRELATION}"
REPOSITORY = "Phasmotic/Ipsima"
JOB_IDS = {"ios": 101, "watchos": 102, "archive": 103}
JOB_STARTED = "2026-01-01T00:00:00Z"
JOB_COMPLETED = "2026-01-01T00:10:00Z"
STEP_STARTED = "2026-01-01T00:01:00Z"
STEP_COMPLETED = "2026-01-01T00:02:00Z"


def run_base_url(run_id: int = RUN_ID, repository: str = REPOSITORY) -> str:
    return "https" + f"://github.com/{repository}/actions/runs/{run_id}"


def run_url(
    run_id: int = RUN_ID,
    repository: str = REPOSITORY,
    attempt: int = ATTEMPT,
) -> str:
    return f"{run_base_url(run_id, repository)}/attempts/{attempt}"


def job_url(
    job_id: int,
    run_id: int = RUN_ID,
    repository: str = REPOSITORY,
) -> str:
    return f"{run_base_url(run_id, repository)}/job/{job_id}"


def step(name: str, number: int, conclusion: str = "success") -> dict[str, object]:
    return {
        "conclusion": conclusion,
        "name": name,
        "number": number,
        "status": "completed",
    }


def valid_snapshot(
    *,
    conclusion: str = "success",
    failed_jobs: tuple[str, ...] = (),
) -> dict[str, object]:
    jobs: list[dict[str, object]] = []
    for key, name in checker.EXPECTED_JOBS.items():
        job_conclusion = "failure" if key in failed_jobs else "success"
        steps = [step("Set up job", 1)]
        if job_conclusion == "failure":
            steps.append(step("Build and test", 2, "failure"))
        steps.append(step(checker.REPORTER_STEP, len(steps) + 1))
        steps.append(step("Complete job", len(steps) + 1))
        database_id = JOB_IDS[key]
        jobs.append(
            {
                "completedAt": JOB_COMPLETED,
                "conclusion": job_conclusion,
                "databaseId": database_id,
                "name": name,
                "startedAt": JOB_STARTED,
                "status": "completed",
                "steps": steps,
                "url": job_url(database_id),
            }
        )
    return {
        "status": "completed",
        "conclusion": conclusion,
        "databaseId": RUN_ID,
        "headSha": HEAD_SHA,
        "displayTitle": TITLE,
        "url": run_url(),
        "jobs": jobs,
    }


class TierBRunSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scratch_root = REPO_ROOT / ".gauntlet" / "tier-b-snapshot-tests"
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

    def _job(self, snapshot: dict[str, object], key: str) -> dict[str, object]:
        name = checker.EXPECTED_JOBS[key]
        return next(
            job
            for job in snapshot["jobs"]
            if isinstance(job, dict) and job.get("name") == name
        )

    def _invoke(
        self,
        snapshot: object | None = None,
        *,
        raw: bytes | None = None,
        snapshot_path: Path | None = None,
        expected_run_id: str = str(RUN_ID),
        expected_head_sha: str = HEAD_SHA,
        expected_title: str = TITLE,
        expected_repository: str = REPOSITORY,
        watch_rc: str = "0",
        expected_attempt: str = str(ATTEMPT),
        argv: list[str] | None = None,
    ) -> tuple[int, str, str]:
        if argv is None:
            if snapshot_path is None:
                snapshot_path = self.scratch / "snapshot.json"
                payload = raw if raw is not None else json.dumps(snapshot).encode("utf-8")
                snapshot_path.write_bytes(payload)
            argv = [
                "--snapshot",
                str(snapshot_path),
                "--expected-run-id",
                expected_run_id,
                "--expected-head-sha",
                expected_head_sha,
                "--expected-title",
                expected_title,
                "--expected-repository",
                expected_repository,
                "--watch-rc",
                watch_rc,
                "--expected-attempt",
                expected_attempt,
            ]
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = checker.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_pass(
        self,
        invocation: tuple[int, str, str],
        *,
        ios: str = "success",
        watchos: str = "success",
        archive: str = "success",
        conclusion: str = "success",
    ) -> str:
        code, stdout, stderr = invocation
        self.assertEqual(code, 0, (stdout, stderr))
        self.assertEqual(stderr, "")
        expected_prefix = (
            f"{checker.PASS_PREFIX} "
            f"ios={JOB_IDS['ios']}/{ios} "
            f"watchos={JOB_IDS['watchos']}/{watchos} "
            f"archive={JOB_IDS['archive']}/{archive} "
            f"conclusion={conclusion} digest="
        )
        match = re.fullmatch(
            rf"{re.escape(expected_prefix)}([0-9a-f]{{64}})\n", stdout
        )
        self.assertIsNotNone(match, stdout)
        for secret_value in (HEAD_SHA, TITLE, CORRELATION, REPOSITORY):
            self.assertNotIn(secret_value, stdout)
        assert match is not None
        return match.group(1)

    def assert_blocked(self, invocation: tuple[int, str, str]) -> None:
        code, stdout, stderr = invocation
        self.assertEqual(code, 2, (stdout, stderr))
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, checker.BLOCKED_MARKER + "\n")
        for redacted_value in (HEAD_SHA, TITLE, CORRELATION, REPOSITORY):
            self.assertNotIn(redacted_value, stderr)

    def test_exact_success_snapshot_passes_with_redacted_output(self) -> None:
        self.assert_pass(self._invoke(valid_snapshot()))

    def test_exact_failure_snapshot_passes_with_normalized_job_order(self) -> None:
        snapshot = valid_snapshot(conclusion="failure", failed_jobs=("ios",))
        snapshot["jobs"].reverse()
        self.assert_pass(
            self._invoke(snapshot, watch_rc="1"),
            ios="failure",
            conclusion="failure",
        )

    def test_digest_normalizes_object_job_and_step_order(self) -> None:
        baseline = valid_snapshot()
        baseline_digest = self.assert_pass(self._invoke(baseline))

        reordered = copy.deepcopy(baseline)
        reordered["jobs"].reverse()
        for job in reordered["jobs"]:
            job["steps"].reverse()
            job["steps"] = [
                dict(reversed(tuple(item.items()))) for item in job["steps"]
            ]
        reordered["jobs"] = [
            dict(reversed(tuple(job.items()))) for job in reordered["jobs"]
        ]
        reordered = dict(reversed(tuple(reordered.items())))
        reordered_digest = self.assert_pass(self._invoke(reordered))
        self.assertEqual(reordered_digest, baseline_digest)

    def test_digest_changes_when_a_non_reporter_validated_field_changes(self) -> None:
        baseline = valid_snapshot()
        baseline_digest = self.assert_pass(self._invoke(baseline))

        changed = copy.deepcopy(baseline)
        setup_step = self._job(changed, "ios")["steps"][0]
        self.assertNotEqual(setup_step["name"], checker.REPORTER_STEP)
        setup_step["name"] = "Set up job with changed validated evidence"
        changed_digest = self.assert_pass(self._invoke(changed))
        self.assertNotEqual(changed_digest, baseline_digest)

    def test_matching_nondefault_attempt_uses_its_exact_url_suffix(self) -> None:
        snapshot = valid_snapshot()
        snapshot["url"] = run_url(attempt=2)
        self.assert_pass(self._invoke(snapshot, expected_attempt="2"))

    def test_exact_top_level_schema_is_required(self) -> None:
        variants = []
        missing = valid_snapshot()
        missing.pop("url")
        variants.append(("missing", missing))
        extra = valid_snapshot()
        extra["unexpected"] = True
        variants.append(("extra", extra))
        self_asserted_attempt = valid_snapshot()
        self_asserted_attempt["attempt"] = ATTEMPT
        variants.append(("self-asserted attempt", self_asserted_attempt))
        not_object: object = []
        variants.append(("array", not_object))
        for label, snapshot in variants:
            with self.subTest(case=label):
                self.assert_blocked(self._invoke(snapshot))

    def test_run_database_id_must_be_an_exact_positive_integer(self) -> None:
        mutations = (
            ("run mismatch", "databaseId", RUN_ID + 1),
            ("run zero", "databaseId", 0),
            ("run bool", "databaseId", True),
            ("run float", "databaseId", float(RUN_ID)),
        )
        for label, field, value in mutations:
            with self.subTest(case=label):
                snapshot = valid_snapshot()
                snapshot[field] = value
                self.assert_blocked(self._invoke(snapshot))

    def test_head_sha_title_and_run_url_are_exactly_bound(self) -> None:
        variants = []
        wrong_sha = valid_snapshot()
        wrong_sha["headSha"] = "b" * 40
        variants.append(("SHA", wrong_sha))
        wrong_title = valid_snapshot()
        wrong_title["displayTitle"] = TITLE + " changed"
        variants.append(("title", wrong_title))
        for label, url in (
            ("run", run_url(RUN_ID + 1)),
            ("repository", run_url(repository="Phasmotic/Other")),
            ("scheme", "http" + run_url()[5:]),
            ("missing attempt", run_base_url()),
            ("wrong attempt", run_url(attempt=2)),
            ("trailing slash", run_url() + "/"),
            ("query", run_url() + "?attempt=1"),
        ):
            snapshot = valid_snapshot()
            snapshot["url"] = url
            variants.append((label, snapshot))
        for label, snapshot in variants:
            with self.subTest(case=label):
                self.assert_blocked(self._invoke(snapshot))

    def test_run_state_conclusion_and_watch_status_must_agree(self) -> None:
        variants = []
        incomplete = valid_snapshot()
        incomplete["status"] = "in_progress"
        variants.append(("incomplete", incomplete, "0"))
        cancelled = valid_snapshot()
        cancelled["conclusion"] = "cancelled"
        variants.append(("cancelled", cancelled, "1"))
        success_watch_red = valid_snapshot()
        variants.append(("success with red watch", success_watch_red, "1"))
        failure_watch_green = valid_snapshot(
            conclusion="failure", failed_jobs=("archive",)
        )
        variants.append(("failure with green watch", failure_watch_green, "0"))
        for label, snapshot, watch_rc in variants:
            with self.subTest(case=label):
                self.assert_blocked(self._invoke(snapshot, watch_rc=watch_rc))

    def test_job_inventory_requires_exact_three_unique_names(self) -> None:
        variants = []
        missing = valid_snapshot()
        missing["jobs"].pop()
        variants.append(("missing", missing))
        extra = valid_snapshot()
        decoy = copy.deepcopy(extra["jobs"][0])
        decoy["name"] = "unexpected"
        decoy["databaseId"] = 999
        decoy["url"] = job_url(999)
        extra["jobs"].append(decoy)
        variants.append(("extra", extra))
        duplicate = valid_snapshot()
        duplicate["jobs"][1] = copy.deepcopy(duplicate["jobs"][0])
        variants.append(("duplicate", duplicate))
        unknown = valid_snapshot()
        unknown["jobs"][0]["name"] = "unexpected"
        variants.append(("unknown", unknown))
        wrong_type = valid_snapshot()
        wrong_type["jobs"] = {}
        variants.append(("wrong type", wrong_type))
        for label, snapshot in variants:
            with self.subTest(case=label):
                self.assert_blocked(self._invoke(snapshot))

    def test_job_schema_ids_urls_and_state_are_exact(self) -> None:
        variants = []
        extra_key = valid_snapshot()
        self._job(extra_key, "ios")["unexpected"] = True
        variants.append(("extra key", extra_key))
        for label, value in (
            ("zero ID", 0),
            ("boolean ID", True),
            ("string ID", "101"),
        ):
            snapshot = valid_snapshot()
            self._job(snapshot, "ios")["databaseId"] = value
            variants.append((label, snapshot))
        duplicate_id = valid_snapshot()
        watch = self._job(duplicate_id, "watchos")
        watch["databaseId"] = JOB_IDS["ios"]
        watch["url"] = job_url(JOB_IDS["ios"])
        variants.append(("duplicate ID", duplicate_id))
        for label, url in (
            ("wrong run", job_url(JOB_IDS["ios"], RUN_ID + 1)),
            ("wrong job", job_url(999)),
            ("wrong repository", job_url(JOB_IDS["ios"], repository="other/repo")),
            ("attempt suffix", f"{run_url()}/job/{JOB_IDS['ios']}"),
            ("trailing slash", job_url(JOB_IDS["ios"]) + "/"),
        ):
            snapshot = valid_snapshot()
            self._job(snapshot, "ios")["url"] = url
            variants.append((label, snapshot))
        incomplete = valid_snapshot()
        self._job(incomplete, "archive")["status"] = "queued"
        variants.append(("incomplete", incomplete))
        cancelled = valid_snapshot()
        self._job(cancelled, "watchos")["conclusion"] = "cancelled"
        variants.append(("cancelled", cancelled))
        for label, snapshot in variants:
            with self.subTest(case=label):
                self.assert_blocked(self._invoke(snapshot))

    def test_job_conclusions_must_aggregate_to_run_conclusion(self) -> None:
        success_with_failure = valid_snapshot()
        ios = self._job(success_with_failure, "ios")
        ios["conclusion"] = "failure"
        ios["steps"].insert(1, step("Build and test", 9, "failure"))
        self.assert_blocked(self._invoke(success_with_failure))

        failure_without_failure = valid_snapshot()
        failure_without_failure["conclusion"] = "failure"
        self.assert_blocked(self._invoke(failure_without_failure, watch_rc="1"))

    def test_reporter_step_must_exist_exactly_once_and_succeed(self) -> None:
        variants = []
        missing = valid_snapshot()
        job_steps = self._job(missing, "ios")["steps"]
        job_steps[:] = [item for item in job_steps if item["name"] != checker.REPORTER_STEP]
        variants.append(("missing", missing))
        duplicate = valid_snapshot()
        reporter = copy.deepcopy(
            next(
                item
                for item in self._job(duplicate, "watchos")["steps"]
                if item["name"] == checker.REPORTER_STEP
            )
        )
        reporter["number"] = 99
        self._job(duplicate, "watchos")["steps"].append(reporter)
        variants.append(("duplicate", duplicate))
        failed = valid_snapshot()
        reporter = next(
            item
            for item in self._job(failed, "archive")["steps"]
            if item["name"] == checker.REPORTER_STEP
        )
        reporter["conclusion"] = "failure"
        variants.append(("failed", failed))
        skipped = valid_snapshot()
        reporter = next(
            item
            for item in self._job(skipped, "ios")["steps"]
            if item["name"] == checker.REPORTER_STEP
        )
        reporter["conclusion"] = "skipped"
        variants.append(("skipped", skipped))
        pending = valid_snapshot()
        reporter = next(
            item
            for item in self._job(pending, "watchos")["steps"]
            if item["name"] == checker.REPORTER_STEP
        )
        reporter["status"] = "in_progress"
        variants.append(("pending", pending))
        for label, snapshot in variants:
            with self.subTest(case=label):
                self.assert_blocked(self._invoke(snapshot))

    def test_step_schema_numbers_names_and_states_are_fail_closed(self) -> None:
        variants = []
        wrong_steps_type = valid_snapshot()
        self._job(wrong_steps_type, "ios")["steps"] = {}
        variants.append(("wrong steps type", wrong_steps_type))
        empty = valid_snapshot()
        self._job(empty, "ios")["steps"] = []
        variants.append(("empty", empty))
        extra_key = valid_snapshot()
        self._job(extra_key, "ios")["steps"][0]["unexpected"] = True
        variants.append(("extra key", extra_key))
        self_asserted_started = valid_snapshot()
        self._job(self_asserted_started, "ios")["steps"][0][
            "startedAt"
        ] = STEP_STARTED
        variants.append(("self-asserted start timestamp", self_asserted_started))
        self_asserted_completed = valid_snapshot()
        self._job(self_asserted_completed, "ios")["steps"][0][
            "completedAt"
        ] = STEP_COMPLETED
        variants.append(("self-asserted completion timestamp", self_asserted_completed))
        duplicate_number = valid_snapshot()
        steps = self._job(duplicate_number, "watchos")["steps"]
        steps[1]["number"] = steps[0]["number"]
        variants.append(("duplicate number", duplicate_number))
        boolean_number = valid_snapshot()
        self._job(boolean_number, "archive")["steps"][0]["number"] = True
        variants.append(("boolean number", boolean_number))
        invalid_name = valid_snapshot()
        self._job(invalid_name, "ios")["steps"][0]["name"] = "bad\nname"
        variants.append(("invalid name", invalid_name))
        incomplete = valid_snapshot()
        self._job(incomplete, "watchos")["steps"][0]["status"] = "pending"
        variants.append(("incomplete", incomplete))
        neutral = valid_snapshot()
        self._job(neutral, "archive")["steps"][0]["conclusion"] = "neutral"
        variants.append(("neutral", neutral))
        for label, snapshot in variants:
            with self.subTest(case=label):
                self.assert_blocked(self._invoke(snapshot))

    def test_job_and_step_conclusions_must_be_coherent(self) -> None:
        success_with_failed_step = valid_snapshot()
        self._job(success_with_failed_step, "ios")["steps"][0][
            "conclusion"
        ] = "failure"
        self.assert_blocked(self._invoke(success_with_failed_step))

        failure_without_failed_step = valid_snapshot(
            conclusion="failure", failed_jobs=("archive",)
        )
        archive_steps = self._job(failure_without_failed_step, "archive")["steps"]
        for item in archive_steps:
            item["conclusion"] = "success"
        self.assert_blocked(
            self._invoke(failure_without_failed_step, watch_rc="1")
        )

    def test_job_timestamps_must_be_valid_intervals(self) -> None:
        variants = []
        invalid_job = valid_snapshot()
        self._job(invalid_job, "ios")["completedAt"] = "2026-99-99T00:00:00Z"
        variants.append(("invalid job", invalid_job))
        reversed_job = valid_snapshot()
        job = self._job(reversed_job, "watchos")
        job["startedAt"], job["completedAt"] = job["completedAt"], job["startedAt"]
        variants.append(("reversed job", reversed_job))
        for label, snapshot in variants:
            with self.subTest(case=label):
                self.assert_blocked(self._invoke(snapshot))

    def test_malformed_duplicate_nonfinite_and_non_utf8_json_block(self) -> None:
        payloads = {
            "malformed": b'{"attempt":',
            "duplicate top key": b'{"attempt": 1, "attempt": 1}',
            "duplicate nested key": b'{"jobs": [{"name": "a", "name": "b"}]}',
            "NaN": b'{"attempt": NaN}',
            "infinity": b'{"attempt": Infinity}',
            "NUL": b'{"attempt": 1}\0',
            "non-UTF-8": b'\xff',
        }
        for label, payload in payloads.items():
            with self.subTest(case=label):
                self.assert_blocked(self._invoke(raw=payload))

    def test_expected_argument_formats_and_values_are_fail_closed(self) -> None:
        variants = (
            {"expected_run_id": "0"},
            {"expected_run_id": "01"},
            {"expected_run_id": "not-a-run"},
            {"expected_head_sha": "A" * 40},
            {"expected_head_sha": "a" * 39},
            {"expected_title": TITLE + " changed"},
            {"expected_repository": "missing-slash"},
            {"expected_repository": "owner/repo/extra"},
            {"watch_rc": "-1"},
            {"watch_rc": "01"},
            {"watch_rc": "256"},
            {"watch_rc": "failure"},
            {"expected_attempt": "0"},
            {"expected_attempt": "01"},
            {"expected_attempt": "latest"},
        )
        for arguments in variants:
            with self.subTest(arguments=arguments):
                self.assert_blocked(self._invoke(valid_snapshot(), **arguments))

    def test_mismatched_expected_bindings_block_without_disclosure(self) -> None:
        variants = (
            {"expected_run_id": str(RUN_ID + 1)},
            {"expected_head_sha": "b" * 40},
            {
                "expected_title": "Talaria Tier B: talaria-"
                + "fedcba9876543210fedcba9876543210"
            },
            {"expected_repository": "Phasmotic/Other"},
            {"expected_attempt": "2"},
        )
        for arguments in variants:
            with self.subTest(arguments=arguments):
                self.assert_blocked(self._invoke(valid_snapshot(), **arguments))

    def test_missing_directory_symlink_and_unreadable_paths_block(self) -> None:
        missing = self.scratch / "missing.json"
        self.assert_blocked(self._invoke(snapshot_path=missing))

        directory = self.scratch / "directory"
        directory.mkdir()
        self.assert_blocked(self._invoke(snapshot_path=directory))

        regular = self.scratch / "regular.json"
        regular.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
        with patch.object(Path, "lstat") as mocked_lstat:
            mocked_lstat.return_value.st_mode = stat.S_IFLNK
            self.assert_blocked(self._invoke(snapshot_path=regular))

        with patch.object(Path, "read_bytes", side_effect=PermissionError):
            self.assert_blocked(self._invoke(snapshot_path=regular))

    def test_wrong_cli_arity_duplicate_options_and_unknown_options_block(self) -> None:
        valid_path = self.scratch / "valid.json"
        valid_path.write_text(json.dumps(valid_snapshot()), encoding="utf-8")
        valid_argv = [
            "--snapshot",
            str(valid_path),
            "--expected-run-id",
            str(RUN_ID),
            "--expected-head-sha",
            HEAD_SHA,
            "--expected-title",
            TITLE,
            "--expected-repository",
            REPOSITORY,
            "--watch-rc",
            "0",
            "--expected-attempt",
            "1",
        ]
        variants = (
            [],
            valid_argv[:-2],
            valid_argv + ["extra"],
            valid_argv + ["--unknown", "value"],
            valid_argv[:-2] + ["--watch-rc", "1"],
        )
        for argv in variants:
            with self.subTest(argv=argv):
                self.assert_blocked(self._invoke(argv=argv))


if __name__ == "__main__":
    unittest.main()
