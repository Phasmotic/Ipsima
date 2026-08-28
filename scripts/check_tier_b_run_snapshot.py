#!/usr/bin/env python3
"""Validate a source-bound GitHub Tier B run/job snapshot fail closed."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import stat
import sys


PASS_PREFIX = "TIER B SNAPSHOT PASS:"
BLOCKED_MARKER = (
    "TIER B SNAPSHOT BLOCKED: evidence is missing, malformed, unavailable, "
    "or contradictory"
)
EXPECTED_JOBS = {
    "ios": "G7/G8/G10/G12 · iOS simulator",
    "watchos": "G9 · watch app + widgets + UI smoke",
    "archive": "G13 · unsigned archive links everything",
}
REPORTER_STEP = "Emit Tier B job status"
TOP_KEYS = frozenset(
    {
        "status",
        "conclusion",
        "databaseId",
        "headSha",
        "displayTitle",
        "url",
        "jobs",
    }
)
JOB_KEYS = frozenset(
    {
        "completedAt",
        "conclusion",
        "databaseId",
        "name",
        "startedAt",
        "status",
        "steps",
        "url",
    }
)
STEP_KEYS = frozenset(
    {"conclusion", "name", "number", "status"}
)
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
TITLE_PATTERN = re.compile(r"Talaria Tier B: talaria-[0-9a-f]{32}")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
POSITIVE_INTEGER_PATTERN = re.compile(r"[1-9][0-9]*")
EXIT_CODE_PATTERN = re.compile(r"0|[1-9][0-9]{0,2}")
TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?Z"
)
REQUIRED_OPTIONS = (
    "--snapshot",
    "--expected-run-id",
    "--expected-head-sha",
    "--expected-title",
    "--expected-repository",
    "--watch-rc",
    "--expected-attempt",
)


class EvidenceBlocked(RuntimeError):
    """Raised when the snapshot cannot support a source-bound verdict."""


class FailClosedArgumentParser(argparse.ArgumentParser):
    """Convert command-line errors into the generic BLOCKED result."""

    def error(self, message: str) -> None:
        del message
        raise EvidenceBlocked("invalid command line")


@dataclass(frozen=True)
class JobEvidence:
    key: str
    database_id: int
    conclusion: str


def _read_regular_utf8(path: Path) -> str:
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise EvidenceBlocked("snapshot is not a regular non-symlink file")
        payload = path.read_bytes()
    except EvidenceBlocked:
        raise
    except OSError as error:
        raise EvidenceBlocked("snapshot is unavailable") from error

    if b"\0" in payload:
        raise EvidenceBlocked("snapshot contains binary data")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EvidenceBlocked("snapshot is not valid UTF-8") from error


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceBlocked("snapshot contains a duplicate object key")
        result[key] = value
    return result


def _reject_nonfinite(token: str) -> None:
    del token
    raise EvidenceBlocked("snapshot contains a non-finite number")


def load_snapshot(path: Path) -> object:
    try:
        return json.loads(
            _read_regular_utf8(path),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except EvidenceBlocked:
        raise
    except (json.JSONDecodeError, RecursionError) as error:
        raise EvidenceBlocked("snapshot JSON is malformed") from error


def _exact_object(
    value: object, expected_keys: frozenset[str], label: str
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise EvidenceBlocked(f"{label} does not have the exact expected schema")
    return value


def _positive_integer(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise EvidenceBlocked(f"{label} is not a positive integer")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise EvidenceBlocked(f"{label} is not a UTC timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise EvidenceBlocked(f"{label} is not a valid timestamp") from error


def _interval(
    start_value: object, completed_value: object, label: str
) -> tuple[datetime, datetime]:
    started = _timestamp(start_value, f"{label} start")
    completed = _timestamp(completed_value, f"{label} completion")
    if completed < started:
        raise EvidenceBlocked(f"{label} completion precedes its start")
    return started, completed


def _validate_steps(
    value: object,
    job_conclusion: str,
) -> None:
    if not isinstance(value, list) or not value:
        raise EvidenceBlocked("job steps are missing or malformed")
    reporter_count = 0
    step_numbers: set[int] = set()
    has_failed_step = False
    for raw_step in value:
        step = _exact_object(raw_step, STEP_KEYS, "job step")
        name = step["name"]
        if (
            not isinstance(name, str)
            or not name
            or "\0" in name
            or "\n" in name
            or "\r" in name
        ):
            raise EvidenceBlocked("job step name is malformed")
        number = _positive_integer(step["number"], "job step number")
        if number in step_numbers:
            raise EvidenceBlocked("job step number is duplicated")
        step_numbers.add(number)
        if step["status"] != "completed":
            raise EvidenceBlocked("job step is not completed")
        conclusion = step["conclusion"]
        if conclusion not in {"success", "failure", "skipped"}:
            raise EvidenceBlocked("job step conclusion is indeterminate")
        if conclusion == "failure":
            has_failed_step = True
        if name == REPORTER_STEP:
            reporter_count += 1
            if conclusion != "success":
                raise EvidenceBlocked("job status reporter did not succeed")

    if reporter_count != 1:
        raise EvidenceBlocked("job status reporter inventory is ambiguous")
    if job_conclusion == "success" and has_failed_step:
        raise EvidenceBlocked("job success contradicts failed step evidence")
    if job_conclusion == "failure" and not has_failed_step:
        raise EvidenceBlocked("job failure lacks a failed step")


def _run_base_url(repository: str, run_id: int) -> str:
    return "https" + f"://github.com/{repository}/actions/runs/{run_id}"


def _expected_run_url(repository: str, run_id: int, attempt: int) -> str:
    return f"{_run_base_url(repository, run_id)}/attempts/{attempt}"


def _validate_job(
    raw_job: object,
    key: str,
    expected_name: str,
    repository: str,
    run_id: int,
) -> JobEvidence:
    job = _exact_object(raw_job, JOB_KEYS, "job")
    if job["name"] != expected_name:
        raise EvidenceBlocked("job name does not match its expected identity")
    database_id = _positive_integer(job["databaseId"], "job database ID")
    expected_url = f"{_run_base_url(repository, run_id)}/job/{database_id}"
    if job["url"] != expected_url:
        raise EvidenceBlocked("job URL does not bind the selected run and job")
    if job["status"] != "completed":
        raise EvidenceBlocked("job is not completed")
    conclusion = job["conclusion"]
    if conclusion not in {"success", "failure"}:
        raise EvidenceBlocked("job conclusion is indeterminate")
    _interval(job["startedAt"], job["completedAt"], "job")
    _validate_steps(job["steps"], conclusion)
    return JobEvidence(key, database_id, conclusion)


def validate_snapshot(
    value: object,
    *,
    expected_run_id: int,
    expected_head_sha: str,
    expected_title: str,
    expected_repository: str,
    watch_rc: int,
    expected_attempt: int,
) -> tuple[JobEvidence, ...]:
    snapshot = _exact_object(value, TOP_KEYS, "snapshot")
    if _positive_integer(snapshot["databaseId"], "run database ID") != expected_run_id:
        raise EvidenceBlocked("run database ID does not match")
    if snapshot["status"] != "completed":
        raise EvidenceBlocked("run is not completed")
    run_conclusion = snapshot["conclusion"]
    if run_conclusion not in {"success", "failure"}:
        raise EvidenceBlocked("run conclusion is indeterminate")
    if snapshot["headSha"] != expected_head_sha:
        raise EvidenceBlocked("run head SHA does not match")
    if snapshot["displayTitle"] != expected_title:
        raise EvidenceBlocked("run display title does not match")
    if snapshot["url"] != _expected_run_url(
        expected_repository, expected_run_id, expected_attempt
    ):
        raise EvidenceBlocked("run URL does not match")
    if (run_conclusion == "success" and watch_rc != 0) or (
        run_conclusion == "failure" and watch_rc == 0
    ):
        raise EvidenceBlocked("run conclusion contradicts watch status")

    raw_jobs = snapshot["jobs"]
    if not isinstance(raw_jobs, list) or len(raw_jobs) != len(EXPECTED_JOBS):
        raise EvidenceBlocked("job inventory is missing or has extra entries")
    raw_by_name: dict[str, object] = {}
    for raw_job in raw_jobs:
        if not isinstance(raw_job, dict):
            raise EvidenceBlocked("job inventory entry is malformed")
        name = raw_job.get("name")
        if not isinstance(name, str) or name in raw_by_name:
            raise EvidenceBlocked("job inventory name is malformed or duplicated")
        raw_by_name[name] = raw_job
    if set(raw_by_name) != set(EXPECTED_JOBS.values()):
        raise EvidenceBlocked("job inventory names do not match")

    jobs = tuple(
        _validate_job(
            raw_by_name[expected_name],
            key,
            expected_name,
            expected_repository,
            expected_run_id,
        )
        for key, expected_name in EXPECTED_JOBS.items()
    )
    if len({job.database_id for job in jobs}) != len(jobs):
        raise EvidenceBlocked("job database IDs are duplicated")
    any_failure = any(job.conclusion == "failure" for job in jobs)
    if (run_conclusion == "success" and any_failure) or (
        run_conclusion == "failure" and not any_failure
    ):
        raise EvidenceBlocked("job conclusions contradict the run conclusion")
    return jobs


def canonical_snapshot_digest(value: object) -> str:
    """Hash every validated value with only list/object ordering normalized."""
    if not isinstance(value, dict):
        raise EvidenceBlocked("validated snapshot is not an object")
    jobs = value.get("jobs")
    if not isinstance(jobs, list):
        raise EvidenceBlocked("validated snapshot jobs are unavailable")
    by_name: dict[str, dict[str, object]] = {}
    for raw_job in jobs:
        if not isinstance(raw_job, dict) or not isinstance(raw_job.get("name"), str):
            raise EvidenceBlocked("validated snapshot job is malformed")
        by_name[raw_job["name"]] = raw_job

    normalized_jobs: list[dict[str, object]] = []
    for expected_name in EXPECTED_JOBS.values():
        raw_job = by_name.get(expected_name)
        if raw_job is None:
            raise EvidenceBlocked("validated snapshot job is missing")
        raw_steps = raw_job.get("steps")
        if not isinstance(raw_steps, list):
            raise EvidenceBlocked("validated snapshot steps are unavailable")
        normalized_job = dict(raw_job)
        normalized_job["steps"] = sorted(
            raw_steps,
            key=lambda step: step["number"] if isinstance(step, dict) else -1,
        )
        normalized_jobs.append(normalized_job)

    normalized_snapshot = dict(value)
    normalized_snapshot["jobs"] = normalized_jobs
    canonical_json = json.dumps(
        normalized_snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical_json).hexdigest()


def _parse_positive_decimal(value: str, label: str) -> int:
    if POSITIVE_INTEGER_PATTERN.fullmatch(value) is None:
        raise EvidenceBlocked(f"{label} is malformed")
    return int(value)


def _parse_exit_code(value: str) -> int:
    if EXIT_CODE_PATTERN.fullmatch(value) is None:
        raise EvidenceBlocked("watch status is malformed")
    result = int(value)
    if result > 255:
        raise EvidenceBlocked("watch status is out of range")
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    if len(argv) != len(REQUIRED_OPTIONS) * 2 or any(
        argv.count(option) != 1 for option in REQUIRED_OPTIONS
    ):
        raise EvidenceBlocked("invalid command-line arity")
    parser = FailClosedArgumentParser(add_help=False)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-head-sha", required=True)
    parser.add_argument("--expected-title", required=True)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--watch-rc", required=True)
    parser.add_argument("--expected-attempt", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = parse_args(sys.argv[1:] if argv is None else argv)
        expected_run_id = _parse_positive_decimal(
            arguments.expected_run_id, "expected run ID"
        )
        expected_attempt = _parse_positive_decimal(
            arguments.expected_attempt, "expected run attempt"
        )
        if SHA_PATTERN.fullmatch(arguments.expected_head_sha) is None:
            raise EvidenceBlocked("expected head SHA is malformed")
        if TITLE_PATTERN.fullmatch(arguments.expected_title) is None:
            raise EvidenceBlocked("expected title is malformed")
        if REPOSITORY_PATTERN.fullmatch(arguments.expected_repository) is None:
            raise EvidenceBlocked("expected repository is malformed")
        watch_rc = _parse_exit_code(arguments.watch_rc)
        snapshot = load_snapshot(arguments.snapshot)
        jobs = validate_snapshot(
            snapshot,
            expected_run_id=expected_run_id,
            expected_head_sha=arguments.expected_head_sha,
            expected_title=arguments.expected_title,
            expected_repository=arguments.expected_repository,
            watch_rc=watch_rc,
            expected_attempt=expected_attempt,
        )
        digest = canonical_snapshot_digest(snapshot)
    except EvidenceBlocked:
        print(BLOCKED_MARKER, file=sys.stderr)
        return 2

    by_key = {job.key: job for job in jobs}
    print(
        f"{PASS_PREFIX} "
        f"ios={by_key['ios'].database_id}/{by_key['ios'].conclusion} "
        f"watchos={by_key['watchos'].database_id}/{by_key['watchos'].conclusion} "
        f"archive={by_key['archive'].database_id}/{by_key['archive'].conclusion} "
        f"conclusion="
        f"{'failure' if any(job.conclusion == 'failure' for job in jobs) else 'success'} "
        f"digest={digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
