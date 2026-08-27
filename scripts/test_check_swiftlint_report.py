from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.check_swiftlint_report import InvalidReport, violation_count


VALID_VIOLATION = {
    "character": 1,
    "file": "Sources/Example.swift",
    "line": 1,
    "reason": "Example violation",
    "rule_id": "example_rule",
    "severity": "Error",
    "type": "Example Rule",
}


class SwiftLintReportTests(unittest.TestCase):
    def parse(self, value: object) -> int:
        with tempfile.TemporaryDirectory(prefix="talaria-swiftlint-report-") as temp_dir:
            report = Path(temp_dir) / "report.json"
            report.write_text(json.dumps(value), encoding="utf-8")
            return violation_count(report)

    def test_empty_array_is_valid_clean_evidence(self) -> None:
        self.assertEqual(self.parse([]), 0)

    def test_structured_violation_is_counted(self) -> None:
        self.assertEqual(self.parse([VALID_VIOLATION]), 1)

    def test_non_array_report_is_rejected(self) -> None:
        with self.assertRaises(InvalidReport):
            self.parse({"violations": []})

    def test_malformed_violation_is_rejected(self) -> None:
        invalid_cases = (
            "not-an-object",
            {**VALID_VIOLATION, "rule_id": ""},
            {**VALID_VIOLATION, "severity": "Warning"},
            {**VALID_VIOLATION, "severity": "Fatal"},
            {**VALID_VIOLATION, "line": True},
            {**VALID_VIOLATION, "character": -1},
        )
        for violation in invalid_cases:
            with self.subTest(violation=violation), self.assertRaises(InvalidReport):
                self.parse([violation])


if __name__ == "__main__":
    unittest.main()
