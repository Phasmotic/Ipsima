#!/usr/bin/env python3
"""Reject warning diagnostics and require any declared build-success markers."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


WARNING = re.compile(r"(?:^|\s)warning:", re.IGNORECASE)


def check(text: str, success_markers: list[str]) -> list[str]:
    failures: list[str] = []
    for success_marker in success_markers:
        if success_marker not in text:
            failures.append(f"missing success marker: {success_marker}")
    warning_lines = [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if WARNING.search(line)
    ]
    if warning_lines:
        failures.append(
            "warning diagnostics at log lines "
            + ", ".join(str(number) for number in warning_lines)
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--success-marker", action="append", default=[])
    args = parser.parse_args()
    try:
        text = args.log.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as error:
        print(f"Build log check BLOCKED: {type(error).__name__}", file=sys.stderr)
        return 2
    if not text.strip():
        print("Build log check BLOCKED: log is empty", file=sys.stderr)
        return 2
    failures = check(text, args.success_marker)
    if failures:
        for failure in failures:
            print(f"Build log FAIL: {failure}", file=sys.stderr)
        return 1
    marker_detail = (
        f"{len(args.success_marker)} required success marker(s) present; "
        if args.success_marker
        else ""
    )
    print(f"Build log PASS: {marker_detail}zero warning diagnostics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
