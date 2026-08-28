#!/usr/bin/env python3
"""Validate SwiftLint 0.65.0 JSON reporter evidence and print its finding count."""

from __future__ import annotations

import json
from pathlib import Path
import sys


REQUIRED_TEXT_FIELDS = ("file", "reason", "rule_id", "severity", "type")
EXPECTED_SEVERITY = "Error"


class InvalidReport(ValueError):
    """The reporter output is not decisive SwiftLint violation evidence."""


def violation_count(path: Path) -> int:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InvalidReport("report is missing, unreadable, or not valid UTF-8 JSON") from error

    if not isinstance(report, list):
        raise InvalidReport("top-level report must be a JSON array")

    for index, violation in enumerate(report):
        if not isinstance(violation, dict):
            raise InvalidReport(f"violation {index} must be a JSON object")
        for field in REQUIRED_TEXT_FIELDS:
            if not isinstance(violation.get(field), str) or not violation[field]:
                raise InvalidReport(f"violation {index} has an invalid {field}")
        if violation["severity"] != EXPECTED_SEVERITY:
            raise InvalidReport(
                f"violation {index} was not promoted to Error by --strict"
            )
        for field, minimum in (("line", 1), ("character", 0)):
            value = violation.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise InvalidReport(f"violation {index} has an invalid {field}")
    return len(report)


def main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        print("usage: check_swiftlint_report.py REPORT.json", file=sys.stderr)
        return 2
    try:
        count = violation_count(Path(arguments[0]))
    except InvalidReport as error:
        print(f"invalid SwiftLint JSON evidence: {error}", file=sys.stderr)
        return 2
    print(count)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
