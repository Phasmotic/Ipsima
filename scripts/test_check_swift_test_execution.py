from __future__ import annotations

import copy
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import check_swift_test_execution as checker


REQUEST_TEST = "testM_SessionTitle"
EVENT_TEST = "testE_SessionTitle"
MANUAL_TESTS = tuple(
    sorted(test_id.rsplit("/", 1)[1] for test_id in checker.EXPECTED_HANDWRITTEN_TESTS)
)
TOTAL_TESTS = 2 + len(MANUAL_TESTS)


def valid_catalog() -> dict[str, object]:
    return {
        "title": "fixture",
        "requests": [{"name": "session.title"}],
        "events": [{"name": "session.title"}],
    }


def valid_discovery() -> dict[str, object]:
    return {
        "name": "All tests",
        "tests": [
            {
                "name": "debug.xctest",
                "tests": [
                    {
                        "name": "HermesKitTests.ProtocolConformanceTests",
                        "tests": [
                            {"name": REQUEST_TEST},
                            {"name": EVENT_TEST},
                        ],
                    },
                    {
                        "name": "HermesKitTests.WireCodecTests",
                        "tests": [{"name": name} for name in MANUAL_TESTS],
                    },
                ],
            }
        ],
    }


def encoded(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def runtime_names() -> list[str]:
    return [
        f"ProtocolConformanceTests.{REQUEST_TEST}",
        f"ProtocolConformanceTests.{EVENT_TEST}",
        *(f"WireCodecTests.{name}" for name in MANUAL_TESTS),
    ]


def valid_log(names: list[str] | None = None) -> bytes:
    selected = runtime_names() if names is None else names
    lines = [
        "Test Suite 'All tests' started at 2026-08-27 01:00:00.000",
        "Test Suite 'debug.xctest' started at 2026-08-27 01:00:00.001",
    ]
    for index, name in enumerate(selected):
        lines.append(
            f"Test Case '{name}' started at 2026-08-27 01:00:00.{index + 2:03d}"
        )
        lines.append(f"Test Case '{name}' passed (0.001 seconds)")
    count = len(selected)
    lines.extend(
        [
            "Test Suite 'debug.xctest' passed at 2026-08-27 01:00:01.000",
            f"\t Executed {count} tests, with 0 failures (0 unexpected) in 0.1 (0.1) seconds",
            "Test Suite 'All tests' passed at 2026-08-27 01:00:01.001",
            f"\t Executed {count} tests, with 0 failures (0 unexpected) in 0.1 (0.1) seconds",
        ]
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def swiftpm_list_for_discovery(discovery: dict[str, object]) -> bytes:
    suites = discovery["tests"][0]["tests"]  # type: ignore[index]
    identities = [
        f"{suite['name']}/{leaf['name']}"
        for suite in suites  # type: ignore[union-attr]
        for leaf in suite["tests"]
    ]
    return ("\n".join(sorted(identities)) + "\n").encode("utf-8")


def valid_swiftpm_list() -> bytes:
    return swiftpm_list_for_discovery(valid_discovery())


def valid_inputs() -> tuple[bytes, bytes, bytes, bytes, int]:
    return (
        encoded(valid_discovery()),
        valid_swiftpm_list(),
        valid_log(),
        encoded(valid_catalog()),
        0,
    )


class HappyPathTests(unittest.TestCase):
    def test_exact_kind_aware_split_and_execution_passes(self) -> None:
        evidence = checker.verify(*valid_inputs())
        self.assertEqual(evidence.request_count, 1)
        self.assertEqual(evidence.event_count, 1)
        self.assertEqual(evidence.generated_count, 2)
        self.assertEqual(evidence.handwritten_count, len(MANUAL_TESTS))
        self.assertEqual(evidence.listed_count, TOTAL_TESTS)
        self.assertEqual(evidence.discovered_count, TOTAL_TESTS)
        self.assertEqual(evidence.executed_count, TOTAL_TESTS)

    def test_cross_namespace_name_collision_remains_two_tests(self) -> None:
        expected, request_count, event_count = checker.expected_generated_tests(
            valid_catalog()
        )
        self.assertEqual(request_count, 1)
        self.assertEqual(event_count, 1)
        self.assertEqual(
            expected,
            frozenset(
                {
                    f"{checker.GENERATED_SUITE}/{REQUEST_TEST}",
                    f"{checker.GENERATED_SUITE}/{EVENT_TEST}",
                }
            ),
        )

    def test_no_handwritten_tests_fails_the_explicit_ratchet(self) -> None:
        discovery = valid_discovery()
        suites = discovery["tests"][0]["tests"]  # type: ignore[index]
        suites.pop()  # type: ignore[union-attr]
        names = runtime_names()[:2]
        with self.assertRaisesRegex(
            checker.VerificationFailed, "handwritten test discovery"
        ):
            checker.verify(
                encoded(discovery),
                swiftpm_list_for_discovery(discovery),
                valid_log(names),
                encoded(valid_catalog()),
                0,
            )


class SwiftPMListBlockingTests(unittest.TestCase):
    def test_empty_whitespace_invalid_utf8_and_unsupported_lines_block(self) -> None:
        discovery, _, log, catalog, rc = valid_inputs()
        for raw in (b"", b" \r\n", b"\xff", b"not-a-test\n", b" Suite/test\n"):
            with self.subTest(raw=raw), self.assertRaises(checker.EvidenceBlocked):
                checker.verify(discovery, raw, log, catalog, rc)

    def test_duplicate_identity_blocks(self) -> None:
        discovery, listed, log, catalog, rc = valid_inputs()
        duplicate = listed + listed.splitlines(keepends=True)[0]
        with self.assertRaisesRegex(checker.EvidenceBlocked, "duplicate"):
            checker.verify(discovery, duplicate, log, catalog, rc)

    def test_list_and_xctest_discovery_disagreement_blocks(self) -> None:
        discovery, listed, log, catalog, rc = valid_inputs()
        shortened = b"\n".join(listed.splitlines()[:-1]) + b"\n"
        with self.assertRaisesRegex(checker.EvidenceBlocked, "disagree"):
            checker.verify(discovery, shortened, log, catalog, rc)


class JSONAndDiscoveryBlockingTests(unittest.TestCase):
    def assert_discovery_blocked(self, raw: bytes) -> None:
        _, listed, log, catalog, rc = valid_inputs()
        with self.assertRaises(checker.EvidenceBlocked):
            checker.verify(raw, listed, log, catalog, rc)

    def test_empty_whitespace_invalid_utf8_and_invalid_json_block(self) -> None:
        for raw in (b"", b" \r\n", b"\xff", b"{", b"[] trailing"):
            with self.subTest(raw=raw):
                self.assert_discovery_blocked(raw)

    def test_duplicate_json_key_blocks(self) -> None:
        self.assert_discovery_blocked(b'{"name":"first","name":"second"}')

    def test_wrong_root_name_or_schema_blocks(self) -> None:
        for mutation in ("name", "extra", "no_tests"):
            discovery = valid_discovery()
            if mutation == "name":
                discovery["name"] = "Everything"
            elif mutation == "extra":
                discovery["unexpected"] = True
            else:
                del discovery["tests"]
            with self.subTest(mutation=mutation):
                self.assert_discovery_blocked(encoded(discovery))

    def test_zero_or_multiple_bundles_block(self) -> None:
        for count in (0, 2):
            discovery = valid_discovery()
            bundle = copy.deepcopy(discovery["tests"][0])  # type: ignore[index]
            discovery["tests"] = [copy.deepcopy(bundle) for _ in range(count)]
            with self.subTest(count=count):
                self.assert_discovery_blocked(encoded(discovery))

    def test_wrong_bundle_name_blocks(self) -> None:
        discovery = valid_discovery()
        discovery["tests"][0]["name"] = "release.xctest"  # type: ignore[index]
        self.assert_discovery_blocked(encoded(discovery))

    def test_empty_suite_and_leaf_arrays_block(self) -> None:
        empty_bundle = valid_discovery()
        empty_bundle["tests"][0]["tests"] = []  # type: ignore[index]
        empty_suite = valid_discovery()
        empty_suite["tests"][0]["tests"][0]["tests"] = []  # type: ignore[index]
        for discovery in (empty_bundle, empty_suite):
            with self.subTest(discovery=discovery):
                self.assert_discovery_blocked(encoded(discovery))

    def test_duplicate_suite_or_test_blocks(self) -> None:
        duplicate_suite = valid_discovery()
        suites = duplicate_suite["tests"][0]["tests"]  # type: ignore[index]
        suites.append(copy.deepcopy(suites[0]))  # type: ignore[union-attr,index]

        duplicate_test = valid_discovery()
        leaves = duplicate_test["tests"][0]["tests"][0]["tests"]  # type: ignore[index]
        leaves.append(copy.deepcopy(leaves[0]))  # type: ignore[union-attr,index]

        for discovery in (duplicate_suite, duplicate_test):
            with self.subTest(discovery=discovery):
                self.assert_discovery_blocked(encoded(discovery))

    def test_unsupported_suite_or_test_name_blocks(self) -> None:
        bad_suite = valid_discovery()
        bad_suite["tests"][0]["tests"][0]["name"] = "OtherModule.Tests"  # type: ignore[index]
        bad_test = valid_discovery()
        bad_test["tests"][0]["tests"][0]["tests"][0]["name"] = "not-a-test"  # type: ignore[index]
        for discovery in (bad_suite, bad_test):
            with self.subTest(discovery=discovery):
                self.assert_discovery_blocked(encoded(discovery))


class CatalogContractTests(unittest.TestCase):
    def verify_catalog(self, catalog: bytes) -> checker.TestExecutionEvidence:
        discovery, listed, log, _, rc = valid_inputs()
        return checker.verify(discovery, listed, log, catalog, rc)

    def test_malformed_catalog_inputs_block(self) -> None:
        malformed_values = [
            b"",
            b"\xff",
            b"[]",
            b'{"requests":[],"events":[],"events":[]}',
            encoded({"requests": "not-an-array", "events": []}),
            encoded({"requests": [{}], "events": []}),
            encoded({"requests": [{"name": 7}], "events": []}),
            encoded({"requests": [{"name": "bad name"}], "events": []}),
            encoded({"requests": [], "events": []}),
        ]
        for raw in malformed_values:
            with self.subTest(raw=raw), self.assertRaises(checker.EvidenceBlocked):
                self.verify_catalog(raw)

    def test_duplicate_name_within_one_namespace_fails(self) -> None:
        catalog = valid_catalog()
        catalog["requests"] = [
            {"name": "session.title"},
            {"name": "session.title"},
        ]
        with self.assertRaisesRegex(checker.VerificationFailed, "duplicate request"):
            self.verify_catalog(encoded(catalog))

    def test_distinct_names_that_collapse_to_one_identifier_fail(self) -> None:
        catalog = {
            "requests": [{"name": "foo.bar"}, {"name": "foo.Bar"}],
            "events": [],
        }
        with self.assertRaisesRegex(checker.VerificationFailed, "collide"):
            checker.expected_generated_tests(catalog)

    def test_missing_generated_case_fails(self) -> None:
        discovery = valid_discovery()
        leaves = discovery["tests"][0]["tests"][0]["tests"]  # type: ignore[index]
        leaves.pop()  # type: ignore[union-attr]
        with self.assertRaisesRegex(checker.VerificationFailed, "missing"):
            checker.verify(
                encoded(discovery), swiftpm_list_for_discovery(discovery), valid_log(), encoded(valid_catalog()), 0
            )

    def test_extra_generated_case_fails(self) -> None:
        discovery = valid_discovery()
        leaves = discovery["tests"][0]["tests"][0]["tests"]  # type: ignore[index]
        leaves.append({"name": "testM_Unexpected"})  # type: ignore[union-attr]
        with self.assertRaisesRegex(checker.VerificationFailed, "extra"):
            checker.verify(
                encoded(discovery), swiftpm_list_for_discovery(discovery), valid_log(), encoded(valid_catalog()), 0
            )

    def test_missing_or_extra_handwritten_case_fails(self) -> None:
        for mutation in ("missing", "extra"):
            discovery = valid_discovery()
            leaves = discovery["tests"][0]["tests"][1]["tests"]  # type: ignore[index]
            if mutation == "missing":
                leaves.pop()  # type: ignore[union-attr]
            else:
                leaves.append({"name": "testUnexpectedManual"})  # type: ignore[union-attr]
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                checker.VerificationFailed, "handwritten test discovery"
            ):
                checker.verify(
                    encoded(discovery), swiftpm_list_for_discovery(discovery), valid_log(), encoded(valid_catalog()), 0
                )

    def test_event_cannot_be_substituted_with_request_shaped_identity(self) -> None:
        discovery = valid_discovery()
        leaves = discovery["tests"][0]["tests"][0]["tests"]  # type: ignore[index]
        leaves[1] = {"name": "testM_SessionUsage"}  # type: ignore[index]
        with self.assertRaisesRegex(checker.VerificationFailed, "missing"):
            checker.verify(
                encoded(discovery), swiftpm_list_for_discovery(discovery), valid_log(), encoded(valid_catalog()), 0
            )


class ExecutionEvidenceTests(unittest.TestCase):
    def discovery(self) -> checker.DiscoveryEvidence:
        return checker.parse_discovery_document(valid_discovery())

    def assert_execution_fails(self, raw: bytes, rc: int = 0) -> None:
        with self.assertRaises(checker.VerificationFailed):
            checker.parse_execution_log(raw, self.discovery(), rc)

    def assert_execution_blocks(self, raw: bytes, rc: int = 0) -> None:
        with self.assertRaises(checker.EvidenceBlocked):
            checker.parse_execution_log(raw, self.discovery(), rc)

    def test_empty_whitespace_invalid_utf8_and_invalid_rc_block(self) -> None:
        for raw, rc in (
            (b"", 0),
            (b" \r\n", 0),
            (b"\xff", 0),
            (valid_log(), -1),
            (valid_log(), True),
        ):
            with self.subTest(raw=raw, rc=rc):
                self.assert_execution_blocks(raw, rc)  # type: ignore[arg-type]

    def test_missing_start_or_terminal_fails_with_complete_roots(self) -> None:
        name = runtime_names()[0]
        for removed in (
            f"Test Case '{name}' started at 2026-08-27 01:00:00.002\n",
            f"Test Case '{name}' passed (0.001 seconds)\n",
        ):
            log = valid_log().replace(removed.encode(), b"")
            with self.subTest(removed=removed):
                self.assert_execution_fails(log)

    def test_duplicate_start_or_terminal_fails(self) -> None:
        name = runtime_names()[0]
        start = f"Test Case '{name}' started at 2026-08-27 01:00:00.002\n".encode()
        terminal = f"Test Case '{name}' passed (0.001 seconds)\n".encode()
        for repeated in (start, terminal):
            log = valid_log().replace(repeated, repeated + repeated, 1)
            with self.subTest(repeated=repeated):
                self.assert_execution_fails(log)

    def test_terminal_before_start_fails(self) -> None:
        name = runtime_names()[0]
        start = f"Test Case '{name}' started at 2026-08-27 01:00:00.002\n".encode()
        terminal = f"Test Case '{name}' passed (0.001 seconds)\n".encode()
        log = valid_log().replace(start + terminal, terminal + start)
        self.assert_execution_fails(log)

    def test_unknown_execution_identity_fails(self) -> None:
        log = valid_log().replace(
            runtime_names()[0].encode(), b"ProtocolConformanceTests.testM_Unknown"
        )
        self.assert_execution_fails(log)

    def test_failed_or_skipped_test_fails(self) -> None:
        name = runtime_names()[0]
        passing = f"Test Case '{name}' passed (0.001 seconds)".encode()
        for status in ("failed", "skipped"):
            log = valid_log().replace(
                passing,
                f"Test Case '{name}' {status} (0.001 seconds)".encode(),
            )
            with self.subTest(status=status):
                self.assert_execution_fails(log, 1 if status == "failed" else 0)

    def test_malformed_test_case_line_blocks(self) -> None:
        name = runtime_names()[0]
        passing = f"Test Case '{name}' passed (0.001 seconds)".encode()
        log = valid_log().replace(passing, f"Test Case '{name}' completed".encode())
        self.assert_execution_blocks(log)

    def test_missing_or_malformed_root_summary_blocks(self) -> None:
        summary = (
            "Test Suite 'All tests' passed at 2026-08-27 01:00:01.001\n"
            f"\t Executed {TOTAL_TESTS} tests, with 0 failures (0 unexpected) "
            "in 0.1 (0.1) seconds\n"
        ).encode()
        missing = valid_log().replace(summary, b"")
        malformed = valid_log().replace(
            f"Executed {TOTAL_TESTS} tests".encode(), b"Ran tests", 1
        )
        for log in (missing, malformed):
            with self.subTest(log=log):
                self.assert_execution_blocks(log)

    def test_missing_root_start_blocks(self) -> None:
        log = valid_log().replace(
            b"Test Suite 'All tests' started at 2026-08-27 01:00:00.000\n", b""
        )
        self.assert_execution_blocks(log)

    def test_wrong_summary_total_or_failure_count_fails(self) -> None:
        for old, new in (
            (
                f"Executed {TOTAL_TESTS} tests".encode(),
                f"Executed {TOTAL_TESTS - 1} tests".encode(),
            ),
            (b"0 failures (0 unexpected)", b"1 failure (1 unexpected)"),
            (b"Suite 'debug.xctest' passed", b"Suite 'debug.xctest' failed"),
        ):
            log = valid_log().replace(old, new, 1)
            with self.subTest(old=old, new=new):
                self.assert_execution_fails(log, 1)

    def test_nonzero_rc_without_failure_evidence_blocks(self) -> None:
        self.assert_execution_blocks(valid_log(), 70)

    def test_nonzero_rc_with_explicit_failure_still_fails(self) -> None:
        name = runtime_names()[0]
        log = valid_log().replace(
            f"Test Case '{name}' passed (0.001 seconds)".encode(),
            f"Test Case '{name}' failed (0.001 seconds)".encode(),
        )
        self.assert_execution_fails(log, 1)


class CLITests(unittest.TestCase):
    def test_cli_pass_fail_and_block_classification(self) -> None:
        discovery, listed, log, catalog, _ = valid_inputs()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            discovery_path = root / "discovery.json"
            list_path = root / "swiftpm-list.txt"
            log_path = root / "execution.log"
            catalog_path = root / "catalog.json"
            discovery_path.write_bytes(discovery)
            list_path.write_bytes(listed)
            log_path.write_bytes(log)
            catalog_path.write_bytes(catalog)
            arguments = [
                "--discovery-json",
                str(discovery_path),
                "--swiftpm-list",
                str(list_path),
                "--execution-log",
                str(log_path),
                "--catalog-json",
                str(catalog_path),
                "--test-rc",
                "0",
            ]

            stdout = StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(checker.main(arguments), 0)
            self.assertIn("generated=2", stdout.getvalue())
            self.assertIn(f"handwritten={len(MANUAL_TESTS)}", stdout.getvalue())
            self.assertIn(f"listed={TOTAL_TESTS}", stdout.getvalue())
            self.assertIn(f"discovered={TOTAL_TESTS}", stdout.getvalue())
            self.assertIn(f"executed={TOTAL_TESTS}", stdout.getvalue())

            missing_generated = valid_discovery()
            leaves = missing_generated["tests"][0]["tests"][0]["tests"]  # type: ignore[index]
            leaves.pop()  # type: ignore[union-attr]
            discovery_path.write_bytes(encoded(missing_generated))
            list_path.write_bytes(swiftpm_list_for_discovery(missing_generated))
            stderr = StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(checker.main(arguments), 1)
            self.assertTrue(stderr.getvalue().startswith("G2 TEST FAIL:"))

            discovery_path.write_bytes(b"not json")
            stderr = StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(checker.main(arguments), 2)
            self.assertTrue(stderr.getvalue().startswith("G2 TEST BLOCKED:"))

    def test_unreadable_artifact_blocks_without_echoing_path(self) -> None:
        missing = "does-not-exist-private-layout.json"
        stderr = StringIO()
        with redirect_stderr(stderr):
            result = checker.main(
                [
                    "--discovery-json",
                    missing,
                    "--swiftpm-list",
                    missing,
                    "--execution-log",
                    missing,
                    "--catalog-json",
                    missing,
                    "--test-rc",
                    "0",
                ]
            )
        self.assertEqual(result, 2)
        self.assertNotIn(missing, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
