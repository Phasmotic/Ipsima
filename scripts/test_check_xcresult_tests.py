from __future__ import annotations

import copy
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from scripts.check_xcresult_tests import (
    ConfigurationError,
    SchemaError,
    collect_evidence,
    main,
    parse_expectations,
    parse_json,
    verify_document,
)


def test_case(suite: str, name: str, result: str = "Passed") -> dict[str, object]:
    return {
        "duration": "0.001s",
        "name": name,
        "nodeIdentifier": f"{suite}/{name}",
        "nodeIdentifierURL": f"test://example/{suite}/{name}",
        "nodeType": "Test Case",
        "result": result,
    }


def suite(
    name: str, cases: list[dict[str, object]], result: str = "Passed"
) -> dict[str, object]:
    return {
        "children": cases,
        "name": name,
        "nodeIdentifier": name,
        "nodeIdentifierURL": f"test://example/{name}",
        "nodeType": "Test Suite",
        "result": result,
    }


def bundle(
    name: str,
    suites: list[dict[str, object]],
    node_type: str = "Unit test bundle",
    result: str = "Passed",
) -> dict[str, object]:
    return {
        "children": suites,
        "name": name,
        "nodeIdentifier": name,
        "nodeIdentifierURL": f"test://example/{name}",
        "nodeType": node_type,
        "result": result,
    }


VALID_DOCUMENT = {
    "devices": [
        {
            "deviceId": "fixture-device",
            "deviceName": "Fixture Device",
            "platform": "iOS Simulator",
        }
    ],
    "testEnvironmentDescription": "fixture",
    "testNodes": [
        bundle(
            "TalariaUITests",
            [
                suite(
                    "SmokeUITests",
                    [
                        test_case("SmokeUITests", "testLaunchShowsShell()"),
                        test_case("SmokeUITests", "testSecondFlow()"),
                    ],
                ),
                suite(
                    "AccessibilityAuditUITests",
                    [
                        test_case(
                            "AccessibilityAuditUITests",
                            "testRootScreenPassesAccessibilityAudit()",
                        )
                    ],
                ),
            ],
            node_type="UI test bundle",
        )
    ],
}


RAW_IDENTIFIERS = [
    "TalariaUITests/SmokeUITests/testLaunchShowsShell()",
    "TalariaUITests/SmokeUITests/testSecondFlow()",
    (
        "TalariaUITests/AccessibilityAuditUITests/"
        "testRootScreenPassesAccessibilityAudit()"
    ),
]
RAW_COUNTS = [
    "TalariaUITests/SmokeUITests=2",
    "TalariaUITests/AccessibilityAuditUITests=1",
]


class XCTestEvidenceCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.expected = parse_expectations(RAW_IDENTIFIERS, RAW_COUNTS)

    def _verify(self, document: object):
        return verify_document(document, self.expected)

    def test_exact_expected_tests_pass(self) -> None:
        verification = self._verify(VALID_DOCUMENT)
        self.assertFalse(verification.failures)
        self.assertEqual(len(verification.summaries), 2)

    def test_test_plan_container_passes(self) -> None:
        document = {
            "testNodes": [
                {
                    "children": copy.deepcopy(VALID_DOCUMENT["testNodes"]),
                    "name": "Default Test Plan",
                    "nodeType": "Test Plan",
                    "result": "Passed",
                }
            ]
        }
        verification = self._verify(document)
        self.assertFalse(verification.failures)

    def test_zero_tests_fail(self) -> None:
        document = copy.deepcopy(VALID_DOCUMENT)
        document["testNodes"] = []
        verification = self._verify(document)
        self.assertTrue(
            any("zero test cases" in failure for failure in verification.failures),
            verification.failures,
        )

    def test_missing_expected_test_fails(self) -> None:
        document = copy.deepcopy(VALID_DOCUMENT)
        document["testNodes"][0]["children"][0]["children"].pop()
        verification = self._verify(document)
        self.assertTrue(
            any(
                "expected test is missing" in failure
                for failure in verification.failures
            ),
            verification.failures,
        )

    def test_unexpected_test_and_count_fail(self) -> None:
        document = copy.deepcopy(VALID_DOCUMENT)
        document["testNodes"][0]["children"][0]["children"].append(
            test_case("SmokeUITests", "testUnlistedFlow()")
        )
        verification = self._verify(document)
        self.assertTrue(
            any(
                "unexpected test is present" in failure
                for failure in verification.failures
            ),
            verification.failures,
        )
        self.assertTrue(
            any(
                "ran 3 tests; expected 2" in failure
                for failure in verification.failures
            ),
            verification.failures,
        )

    def test_unexpected_empty_bundle_fails(self) -> None:
        document = copy.deepcopy(VALID_DOCUMENT)
        document["testNodes"].append(bundle("DecoyTests", []))
        verification = self._verify(document)
        self.assertTrue(
            any(
                "unexpected test bundle is present: DecoyTests" in failure
                for failure in verification.failures
            ),
            verification.failures,
        )

    def test_failed_test_fails(self) -> None:
        document = copy.deepcopy(VALID_DOCUMENT)
        document["testNodes"][0]["children"][0]["children"][0]["result"] = (
            "Failed"
        )
        verification = self._verify(document)
        self.assertTrue(
            any("result is 'Failed'" in failure for failure in verification.failures),
            verification.failures,
        )

    def test_skipped_test_fails(self) -> None:
        document = copy.deepcopy(VALID_DOCUMENT)
        document["testNodes"][0]["children"][1]["children"][0]["result"] = (
            "Skipped"
        )
        verification = self._verify(document)
        self.assertTrue(
            any("result is 'Skipped'" in failure for failure in verification.failures),
            verification.failures,
        )

    def test_nonpassing_bundle_fails_even_when_cases_pass(self) -> None:
        document = copy.deepcopy(VALID_DOCUMENT)
        document["testNodes"][0]["result"] = "Failed"
        verification = self._verify(document)
        self.assertTrue(
            any("test bundle" in failure for failure in verification.failures),
            verification.failures,
        )

    def test_unknown_node_type_blocks(self) -> None:
        document = copy.deepcopy(VALID_DOCUMENT)
        document["testNodes"][0]["children"][0]["nodeType"] = "Future Test Group"
        with self.assertRaisesRegex(SchemaError, "unknown nodeType"):
            collect_evidence(document)

    def test_unknown_result_blocks(self) -> None:
        document = copy.deepcopy(VALID_DOCUMENT)
        document["testNodes"][0]["children"][0]["children"][0]["result"] = (
            "Almost Passed"
        )
        with self.assertRaisesRegex(SchemaError, "unknown result"):
            collect_evidence(document)

    def test_duplicate_case_identity_blocks(self) -> None:
        document = copy.deepcopy(VALID_DOCUMENT)
        duplicate = copy.deepcopy(
            document["testNodes"][0]["children"][0]["children"][0]
        )
        document["testNodes"][0]["children"][0]["children"].append(duplicate)
        with self.assertRaisesRegex(SchemaError, "occurs more than once"):
            collect_evidence(document)

    def test_case_without_bundle_suite_hierarchy_blocks(self) -> None:
        document = {
            "testNodes": [test_case("SmokeUITests", "testLoose()")]
        }
        with self.assertRaisesRegex(SchemaError, "no unambiguous bundle/suite parent"):
            collect_evidence(document)

    def test_mismatched_node_identifier_blocks(self) -> None:
        document = copy.deepcopy(VALID_DOCUMENT)
        document["testNodes"][0]["children"][0]["children"][0][
            "nodeIdentifier"
        ] = "DifferentSuite/testLaunchShowsShell()"
        with self.assertRaisesRegex(SchemaError, "nodeIdentifier does not end"):
            collect_evidence(document)

    def test_duplicate_json_object_key_blocks(self) -> None:
        with self.assertRaisesRegex(SchemaError, "duplicate JSON key"):
            parse_json('{"testNodes": [], "testNodes": []}')

    def test_declared_count_must_match_identifier_set(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "declares count 1 but lists 2"):
            parse_expectations(RAW_IDENTIFIERS, [
                "TalariaUITests/SmokeUITests=1",
                "TalariaUITests/AccessibilityAuditUITests=1",
            ])

    def test_cli_accepts_json_fixture(self) -> None:
        arguments = ["--json", "-"]
        for identifier in RAW_IDENTIFIERS:
            arguments.extend(["--expect", identifier])
        for count in RAW_COUNTS:
            arguments.extend(["--expect-count", count])
        stdout = StringIO()
        stderr = StringIO()
        with (
            patch("sys.stdin", StringIO(json.dumps(VALID_DOCUMENT))),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = main(arguments)
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertIn("XCTEST PASS", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
