#!/usr/bin/env python3
"""Classify fail-closed Tier B status records from a GitHub runtime log."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import stat
import sys


RECORD_PREFIX = "TALARIA_TIER_B_JOB_STATUS|"
CORRELATION_PATTERN = re.compile(r"talaria-[0-9a-f]{32}")
EXPECTED_JOBS = frozenset({"ios", "watchos", "archive"})
VALID_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED"})


class EvidenceBlocked(RuntimeError):
    """Raised when the supplied evidence cannot support a decisive verdict."""


class FailClosedArgumentParser(argparse.ArgumentParser):
    """Turn command-line errors into the same fail-closed evidence result."""

    def error(self, message: str) -> None:
        del message
        raise EvidenceBlocked("invalid command line")


@dataclass(frozen=True)
class Verdict:
    code: int
    status: str
    detail: str


def _read_regular_utf8(path: Path) -> str:
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise EvidenceBlocked("log is not a regular non-symlink file")
        payload = path.read_bytes()
    except EvidenceBlocked:
        raise
    except OSError as error:
        raise EvidenceBlocked("log is unavailable") from error

    if b"\0" in payload:
        raise EvidenceBlocked("log contains binary data")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EvidenceBlocked("log is not valid UTF-8") from error


def _runtime_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.split("\n"):
        lines.append(raw_line[:-1] if raw_line.endswith("\r") else raw_line)
    return lines


def parse_records(text: str, expected_correlation: str) -> dict[str, str]:
    records: dict[str, str] = {}
    for line in _runtime_lines(text):
        prefix_index = line.find(RECORD_PREFIX)
        if prefix_index < 0:
            continue

        record = line[prefix_index:]
        fields = record.split("|")
        if len(fields) != 4 or fields[0] != RECORD_PREFIX[:-1]:
            raise EvidenceBlocked("status record is malformed")
        correlation, job, status = fields[1:]
        if CORRELATION_PATTERN.fullmatch(correlation) is None:
            raise EvidenceBlocked("status record correlation is malformed")
        if correlation != expected_correlation:
            raise EvidenceBlocked("status record correlation does not match")
        if job not in EXPECTED_JOBS:
            raise EvidenceBlocked("status record has an unknown job")
        if status not in VALID_STATUSES:
            raise EvidenceBlocked("status record has an unknown status")
        if job in records:
            raise EvidenceBlocked("status record is duplicated")
        records[job] = status

    if set(records) != EXPECTED_JOBS:
        raise EvidenceBlocked("status record inventory is incomplete")
    return records


def classify(records: dict[str, str], conclusion: str) -> Verdict:
    statuses = set(records.values())
    if "BLOCKED" in statuses:
        aggregate = "BLOCKED"
    elif "FAIL" in statuses:
        aggregate = "FAIL"
    else:
        aggregate = "PASS"

    expected_conclusion = "success" if aggregate == "PASS" else "failure"
    if conclusion != expected_conclusion:
        return Verdict(
            2,
            "BLOCKED",
            "job records contradict the workflow-run conclusion",
        )
    if aggregate == "PASS":
        return Verdict(
            0,
            "PASS",
            "all three jobs reported PASS and the workflow run succeeded",
        )
    if aggregate == "FAIL":
        return Verdict(
            1,
            "FAIL",
            "at least one job reported FAIL and the workflow run failed",
        )
    return Verdict(
        2,
        "BLOCKED",
        "at least one job reported BLOCKED and the workflow run failed",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = FailClosedArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--correlation", required=True)
    parser.add_argument("--conclusion", required=True)
    return parser.parse_args(argv)


def _blocked_verdict() -> Verdict:
    return Verdict(2, "BLOCKED", "evidence is missing, malformed, or unavailable")


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = parse_args(sys.argv[1:] if argv is None else argv)
        if CORRELATION_PATTERN.fullmatch(arguments.correlation) is None:
            raise EvidenceBlocked("invalid expected correlation")
        if arguments.conclusion not in {"success", "failure"}:
            raise EvidenceBlocked("invalid workflow-run conclusion")
        log = _read_regular_utf8(arguments.log)
        records = parse_records(log, arguments.correlation)
        verdict = classify(records, arguments.conclusion)
    except EvidenceBlocked:
        verdict = _blocked_verdict()

    marker = f"TIER B EVIDENCE {verdict.status}: {verdict.detail}"
    output = sys.stdout if verdict.code == 0 else sys.stderr
    print(marker, file=output)
    return verdict.code


if __name__ == "__main__":
    raise SystemExit(main())
