#!/usr/bin/env python3
"""Validate source-bound Tier B status records from three separate job logs."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import stat
import sys


RECORD_PREFIX = "TALARIA_TIER_B_JOB_STATUS|"
CORRELATION_PATTERN = re.compile(r"talaria-[0-9a-f]{32}\Z")
EXPECTED_JOBS = ("ios", "watchos", "archive")
VALID_STATUSES = frozenset({"PASS", "BLOCKED"})
JOB_CONCLUSION_STATUS = {"success": "PASS", "failure": "BLOCKED"}

PASS_MESSAGE = (
    "TIER B EVIDENCE PASS: all three jobs reported PASS and the workflow run "
    "succeeded"
)
DECISIVE_BLOCKED_MESSAGE = (
    "TIER B EVIDENCE BLOCKED: at least one job reported BLOCKED and the workflow "
    "run failed"
)
GENERIC_BLOCKED_MESSAGE = (
    "TIER B EVIDENCE BLOCKED: evidence is missing, malformed, or unavailable"
)

REQUIRED_OPTIONS = (
    "--ios-log",
    "--ios-conclusion",
    "--watchos-log",
    "--watchos-conclusion",
    "--archive-log",
    "--archive-conclusion",
    "--correlation",
    "--conclusion",
)


class EvidenceBlocked(RuntimeError):
    """Raised when the supplied evidence cannot support a decisive verdict."""


class FailClosedArgumentParser(argparse.ArgumentParser):
    """Turn command-line errors into the generic fail-closed verdict."""

    def error(self, message: str) -> None:
        del message
        raise EvidenceBlocked("invalid command line")


def _read_regular_utf8(path: Path) -> str:
    """Read one complete job log without following symlinks or accepting binary data."""

    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise EvidenceBlocked("job log is not a regular non-symlink file")
        payload = path.read_bytes()
    except EvidenceBlocked:
        raise
    except OSError as error:
        raise EvidenceBlocked("job log is unavailable") from error

    if b"\0" in payload:
        raise EvidenceBlocked("job log contains binary data")
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceBlocked("job log is not valid UTF-8") from error


def _runtime_lines(text: str) -> list[str]:
    """Split GitHub log text while accepting only ordinary LF or CRLF endings."""

    return [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]


def parse_job_record(
    text: str,
    expected_job: str,
    expected_correlation: str,
) -> str:
    """Return one exact status record bound to its originating job log."""

    records: list[str] = []
    for line in _runtime_lines(text):
        prefix_index = line.find(RECORD_PREFIX)
        if prefix_index < 0:
            continue

        record = line[prefix_index:]
        fields = record.split("|")
        if len(fields) != 4 or fields[0] != RECORD_PREFIX[:-1]:
            raise EvidenceBlocked("job status record is malformed")
        correlation, job, status = fields[1:]
        if correlation != expected_correlation:
            raise EvidenceBlocked("job status correlation does not match")
        if job != expected_job:
            raise EvidenceBlocked("job status record came from a different job")
        if status not in VALID_STATUSES:
            raise EvidenceBlocked("job status is unsupported")
        records.append(status)

    if len(records) != 1:
        raise EvidenceBlocked("job log must contain exactly one status record")
    return records[0]


def classify(
    statuses: dict[str, str],
    job_conclusions: dict[str, str],
    run_conclusion: str,
) -> tuple[int, str]:
    """Cross-check every job status and the aggregate workflow conclusion."""

    if set(statuses) != set(EXPECTED_JOBS) or set(job_conclusions) != set(
        EXPECTED_JOBS
    ):
        raise EvidenceBlocked("job evidence inventory is incomplete")

    for job in EXPECTED_JOBS:
        conclusion = job_conclusions[job]
        expected_status = JOB_CONCLUSION_STATUS.get(conclusion)
        if expected_status is None or statuses[job] != expected_status:
            raise EvidenceBlocked("job status contradicts its job conclusion")

    all_pass = all(statuses[job] == "PASS" for job in EXPECTED_JOBS)
    expected_run_conclusion = "success" if all_pass else "failure"
    if run_conclusion != expected_run_conclusion:
        raise EvidenceBlocked("job statuses contradict the workflow conclusion")

    if all_pass:
        return 0, PASS_MESSAGE
    return 2, DECISIVE_BLOCKED_MESSAGE


def parse_args(argv: list[str]) -> argparse.Namespace:
    if any(argv.count(option) != 1 for option in REQUIRED_OPTIONS):
        raise EvidenceBlocked("required command-line option is missing or duplicated")

    parser = FailClosedArgumentParser(add_help=False)
    parser.add_argument("--ios-log", type=Path, required=True)
    parser.add_argument("--ios-conclusion", required=True)
    parser.add_argument("--watchos-log", type=Path, required=True)
    parser.add_argument("--watchos-conclusion", required=True)
    parser.add_argument("--archive-log", type=Path, required=True)
    parser.add_argument("--archive-conclusion", required=True)
    parser.add_argument("--correlation", required=True)
    parser.add_argument("--conclusion", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = parse_args(sys.argv[1:] if argv is None else argv)
        if CORRELATION_PATTERN.fullmatch(arguments.correlation) is None:
            raise EvidenceBlocked("expected correlation is malformed")

        paths = {
            "ios": arguments.ios_log,
            "watchos": arguments.watchos_log,
            "archive": arguments.archive_log,
        }
        statuses = {
            job: parse_job_record(
                _read_regular_utf8(paths[job]), job, arguments.correlation
            )
            for job in EXPECTED_JOBS
        }
        job_conclusions = {
            "ios": arguments.ios_conclusion,
            "watchos": arguments.watchos_conclusion,
            "archive": arguments.archive_conclusion,
        }
        code, message = classify(statuses, job_conclusions, arguments.conclusion)
    except EvidenceBlocked:
        code, message = 2, GENERIC_BLOCKED_MESSAGE

    output = sys.stdout if code == 0 else sys.stderr
    print(message, file=output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
