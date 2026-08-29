#!/usr/bin/env python3
"""Fail-closed discovery and execution evidence for HermesKit XCTest.

Swift 6.3.3 does not emit xUnit XML for this XCTest-only package.  G2 instead
captures the Linux XCTest executable's ``--dump-tests-json`` output and the
combined output from the corresponding ``swift test`` run.  This checker ties
those artifacts to ``protocol/methods.json`` and proves that every discovered
test started and terminated exactly once.

Exit codes form a gate contract:

* 0: complete, internally consistent evidence and every test passed;
* 1: complete evidence proves a test or source-contract failure;
* 2: evidence is missing, malformed, contradictory, or otherwise unsafe to
  interpret.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


GENERATED_SUITE = "HermesKitTests.ProtocolConformanceTests"
EXPECTED_HANDWRITTEN_TESTS = frozenset(
    {
        "HermesKitTests.HermesTransportLifecycleTests/testCancellationDuringHandshakeClosesSocket",
        "HermesKitTests.HermesTransportTests/testConfigurationRequiresSecureOrLoopbackRootURL",
        "HermesKitTests.HermesTransportLifecycleTests/testErrorsNeverExposeCredentialMaterial",
        "HermesKitTests.HermesTransportTests/testEveryConnectionAttemptMintsAndConsumesFreshTicket",
        "HermesKitTests.HermesTransportTests/testGoldenHandshakeTicketRequestAndBidirectionalFraming",
        "HermesKitTests.HermesTransportLifecycleTests/testHandshakeRejectsAnythingExceptWrappedReadyEvent",
        "HermesKitTests.HermesTransportLifecycleTests/testLinuxURLSessionWebSocketAvailabilityFailsExplicitly",
        "HermesKitTests.HermesTransportLifecycleTests/testMockGatewayRejectsUnissuedTicketAndReportsSafeSnapshot",
        "HermesKitTests.HermesTransportLifecycleTests/testReadinessTimeoutClosesSocket",
        "HermesKitTests.HermesTransportLifecycleTests/testReceiveIsSingleConsumerAndInvalidFramesDisconnect",
        "HermesKitTests.HermesTransportLifecycleTests/testSendAndReceiveRequireReadyValidEnvelopes",
        "HermesKitTests.HermesTransportRaceTests/testHandshakeCancellationWinsAgainstQueuedReady",
        "HermesKitTests.HermesTransportRaceTests/testInFlightSendCancellationClosesAndPreservesCancellation",
        "HermesKitTests.HermesTransportRaceTests/testPostReadyReceiveCancellationClosesAndReleasesOwnership",
        "HermesKitTests.HermesTransportRaceTests/testPreCancelledSendWritesNothingAndKeepsReady",
        "HermesKitTests.HermesTransportRaceTests/testStaleSendCompletionDoesNotClearReplacementSendOwnership",
        "HermesKitTests.HermesTransportRaceTests/testSupersededConnectorFailureCannotMutateReplacementAttempt",
        "HermesKitTests.HermesTransportRaceTests/testSupersededConnectorSuccessCannotMutateReplacementAttempt",
        "HermesKitTests.HermesTransportRaceTests/testSupersededTicketFailureCannotMutateReplacementAttempt",
        "HermesKitTests.HermesTransportRaceTests/testSupersededTicketSuccessCannotMutateReplacementAttempt",
        "HermesKitTests.HermesTransportTests/testTicketFailureClassificationIsBoundedAndFresh",
        "HermesKitTests.WireCodecTests/testAllGoldenFixturesDecodeAndReEncodeIdentically",
        "HermesKitTests.WireCodecTests/testCanonicalFormIsSortedCompactAndStable",
        "HermesKitTests.WireCodecTests/testCodecFailurePathsExposeCausesAndRejectInvalidInputs",
        "HermesKitTests.WireCodecTests/testEnvelopeClassificationsAndProgrammaticErrorRoundTrip",
        "HermesKitTests.WireCodecTests/testErrorObjectWithUnknownMemberRoundTrips",
        "HermesKitTests.WireCodecTests/testEventWithUnknownTopLevelMembersSurvivesRoundTrip",
        "HermesKitTests.WireCodecTests/testIDVariantsRoundTrip",
        "HermesKitTests.WireCodecTests/testIntegerStaysIntegerAcrossRoundTrip",
        "HermesKitTests.WireCodecTests/testJSONValueDoubleAndAccessorsRoundTrip",
        "HermesKitTests.WireCodecTests/testSplitFramesHandlesBatchTrailingNewlineAndBlanks",
    }
)
EXPECTED_ROOT_SUITE = "All tests"
EXPECTED_BUNDLE_SUITE = "debug.xctest"

_SWIFT_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_DISCOVERED_TEST_NAME = re.compile(r"test[A-Za-z0-9_]+\Z")
_DISCOVERED_SUITE_NAME = re.compile(
    r"HermesKitTests\.[A-Za-z_][A-Za-z0-9_]*\Z"
)
_SWIFTPM_LIST_TEST_ID = re.compile(
    r"HermesKitTests\.[A-Za-z_][A-Za-z0-9_]*/test[A-Za-z0-9_]+\Z"
)
_CATALOG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")

_TEST_START = re.compile(r"^Test Case '([^']+)' started at .+$")
_TEST_TERMINAL = re.compile(
    r"^Test Case '([^']+)' (passed|failed|skipped) "
    r"\(([0-9]+(?:\.[0-9]+)?) seconds\)$"
)
_SUITE_START = re.compile(r"^Test Suite '([^']+)' started at .+$")
_SUITE_TERMINAL = re.compile(
    r"^Test Suite '([^']+)' (passed|failed) at .+$"
)
_SUITE_SUMMARY = re.compile(
    r"^\s*Executed ([0-9]+) tests?, with ([0-9]+) failures? "
    r"\(([0-9]+) unexpected\) in "
    r"([0-9]+(?:\.[0-9]+)?) \(([0-9]+(?:\.[0-9]+)?)\) seconds$"
)


class EvidenceBlocked(RuntimeError):
    """The supplied artifacts cannot support a trustworthy verdict."""


class VerificationFailed(RuntimeError):
    """Complete evidence proves that the G2 test contract was violated."""


class DuplicateJSONKeyError(ValueError):
    """A JSON object contained a duplicate key."""


@dataclass(frozen=True)
class DiscoveryEvidence:
    """Strictly validated XCTest discovery output."""

    test_ids: frozenset[str]
    tests_by_suite: dict[str, frozenset[str]]
    runtime_aliases: dict[str, str]
    root_suite: str
    bundle_suite: str


@dataclass(frozen=True)
class TestExecutionEvidence:
    """Counts derived from the catalog, discovery JSON, and execution log."""

    request_count: int
    event_count: int
    generated_count: int
    handwritten_count: int
    listed_count: int
    discovered_count: int
    executed_count: int


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJSONKeyError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_json_constant(token: str) -> None:
    raise EvidenceBlocked(f"JSON contains non-finite constant {token!r}")


def parse_json_bytes(raw: bytes, label: str) -> object:
    """Decode one UTF-8 JSON artifact while rejecting duplicate keys."""

    if not isinstance(raw, bytes):
        raise EvidenceBlocked(f"{label} input must be bytes")
    if not raw:
        raise EvidenceBlocked(f"{label} is empty")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceBlocked(f"{label} is not valid UTF-8") from error
    if not text.strip():
        raise EvidenceBlocked(f"{label} contains only whitespace")
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except EvidenceBlocked:
        raise
    except DuplicateJSONKeyError as error:
        raise EvidenceBlocked(str(error)) from error
    except (json.JSONDecodeError, ValueError) as error:
        raise EvidenceBlocked(f"{label} is invalid JSON") from error


def _exact_object(value: object, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceBlocked(f"{label} must be an object")
    actual = frozenset(value)
    if actual != keys:
        raise EvidenceBlocked(f"{label} has an unexpected schema")
    return value


def _nonempty_array(value: object, label: str) -> list[object]:
    if not isinstance(value, list) or not value:
        raise EvidenceBlocked(f"{label} must be a non-empty array")
    return value


def _exact_string(value: object, expected: str, label: str) -> str:
    if not isinstance(value, str) or value != expected:
        raise EvidenceBlocked(f"{label} must equal {expected!r}")
    return value


def _safe_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise EvidenceBlocked(f"{label} must be a non-empty trimmed string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise EvidenceBlocked(f"{label} contains a control character")
    return value


def parse_discovery_document(document: object) -> DiscoveryEvidence:
    """Validate Swift 6.3.3 Linux XCTest ``--dump-tests-json`` output."""

    root = _exact_object(document, frozenset({"name", "tests"}), "discovery root")
    root_name = _exact_string(root["name"], EXPECTED_ROOT_SUITE, "root suite")
    root_children = _nonempty_array(root["tests"], "root tests")
    if len(root_children) != 1:
        raise EvidenceBlocked(
            f"discovery root must contain one test bundle, found {len(root_children)}"
        )

    bundle = _exact_object(
        root_children[0], frozenset({"name", "tests"}), "test bundle"
    )
    bundle_name = _exact_string(
        bundle["name"], EXPECTED_BUNDLE_SUITE, "test bundle name"
    )
    suite_values = _nonempty_array(bundle["tests"], "test bundle suites")

    tests_by_suite: dict[str, frozenset[str]] = {}
    all_ids: set[str] = set()
    aliases: dict[str, str] = {}
    suite_short_names: set[str] = set()

    for suite_index, suite_value in enumerate(suite_values):
        suite = _exact_object(
            suite_value,
            frozenset({"name", "tests"}),
            f"test suite {suite_index}",
        )
        suite_name = _safe_string(suite["name"], f"test suite {suite_index} name")
        if _DISCOVERED_SUITE_NAME.fullmatch(suite_name) is None:
            raise EvidenceBlocked(f"test suite name {suite_name!r} is unsupported")
        if suite_name in tests_by_suite:
            raise EvidenceBlocked(f"discovery contains duplicate suite {suite_name!r}")
        short_suite = suite_name.rsplit(".", 1)[1]
        if short_suite in suite_short_names:
            raise EvidenceBlocked(
                f"runtime suite name {short_suite!r} is ambiguous"
            )
        suite_short_names.add(short_suite)

        leaf_values = _nonempty_array(suite["tests"], f"tests in {suite_name}")
        suite_ids: set[str] = set()
        for leaf_index, leaf_value in enumerate(leaf_values):
            leaf = _exact_object(
                leaf_value,
                frozenset({"name"}),
                f"test leaf {suite_name}[{leaf_index}]",
            )
            test_name = _safe_string(
                leaf["name"], f"test leaf {suite_name}[{leaf_index}] name"
            )
            if _DISCOVERED_TEST_NAME.fullmatch(test_name) is None:
                raise EvidenceBlocked(f"discovered test name {test_name!r} is unsupported")

            test_id = f"{suite_name}/{test_name}"
            runtime_alias = f"{short_suite}.{test_name}"
            if test_id in all_ids or test_id in suite_ids:
                raise EvidenceBlocked(f"discovery contains duplicate test {test_id!r}")
            if runtime_alias in aliases:
                raise EvidenceBlocked(
                    f"runtime test identity {runtime_alias!r} is ambiguous"
                )
            suite_ids.add(test_id)
            all_ids.add(test_id)
            aliases[runtime_alias] = test_id
        tests_by_suite[suite_name] = frozenset(suite_ids)

    if not all_ids:
        raise EvidenceBlocked("discovery contains no XCTest tests")
    return DiscoveryEvidence(
        test_ids=frozenset(all_ids),
        tests_by_suite=tests_by_suite,
        runtime_aliases=aliases,
        root_suite=root_name,
        bundle_suite=bundle_name,
    )


def parse_swiftpm_list(raw: bytes) -> frozenset[str]:
    """Validate the complete ``swift test list`` stdout test inventory.

    SwiftPM lists XCTest identities in the same module/suite/test form used by
    this checker's canonical discovery IDs.  An unfamiliar line is BLOCKED,
    including a future Swift Testing identity that this checker does not yet
    know how to reconcile with XCTest's discovery document.
    """

    if not isinstance(raw, bytes):
        raise EvidenceBlocked("SwiftPM test list input must be bytes")
    if not raw:
        raise EvidenceBlocked("SwiftPM test list is empty")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceBlocked("SwiftPM test list is not valid UTF-8") from error
    if not text.strip():
        raise EvidenceBlocked("SwiftPM test list contains only whitespace")

    listed: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line or line != line.strip():
            raise EvidenceBlocked(
                f"SwiftPM test list line {line_number} is empty or padded"
            )
        if _SWIFTPM_LIST_TEST_ID.fullmatch(line) is None:
            raise EvidenceBlocked(
                f"SwiftPM test list line {line_number} has an unsupported identity"
            )
        if line in listed:
            raise EvidenceBlocked("SwiftPM test list contains a duplicate identity")
        listed.add(line)
    if not listed:
        raise EvidenceBlocked("SwiftPM test list contains no tests")
    return frozenset(listed)


def verify_list_matches_discovery(
    listed: frozenset[str], discovery: DiscoveryEvidence
) -> None:
    """Require SwiftPM and the exact XCTest executable to inventory one set."""

    missing_from_discovery = listed - discovery.test_ids
    missing_from_list = discovery.test_ids - listed
    if missing_from_discovery or missing_from_list:
        details: list[str] = []
        if missing_from_discovery:
            details.append(
                "listed but absent from XCTest discovery "
                + _brief_ids(missing_from_discovery)
            )
        if missing_from_list:
            details.append(
                "discovered but absent from SwiftPM list "
                + _brief_ids(missing_from_list)
            )
        raise EvidenceBlocked(
            "SwiftPM and XCTest discovery inventories disagree: "
            + "; ".join(details)
        )


def _catalog_names(document: object, key: str) -> list[str]:
    if not isinstance(document, dict):
        raise EvidenceBlocked("protocol catalog root must be an object")
    values = document.get(key)
    if not isinstance(values, list):
        raise EvidenceBlocked(f"protocol catalog {key!r} must be an array")
    names: list[str] = []
    for index, entry in enumerate(values):
        if not isinstance(entry, dict):
            raise EvidenceBlocked(f"protocol catalog {key}[{index}] must be an object")
        name = _safe_string(entry.get("name"), f"protocol catalog {key}[{index}].name")
        if _CATALOG_NAME.fullmatch(name) is None:
            raise EvidenceBlocked(f"protocol catalog name {name!r} is unsupported")
        names.append(name)
    return names


def _swift_test_name(kind: str, name: str) -> str:
    """Independently reproduce the committed generator's kind-aware name."""

    tag = "M" if kind == "request" else "E"
    cleaned = "".join(
        part.capitalize() for part in name.replace("-", "_").split(".")
    )
    identifier = f"test{tag}_{cleaned}"
    if _SWIFT_IDENTIFIER.fullmatch(identifier) is None:
        raise EvidenceBlocked(
            f"catalog entry {kind} {name!r} does not form a supported Swift identifier"
        )
    return identifier


def expected_generated_tests(document: object) -> tuple[frozenset[str], int, int]:
    """Return exact generated IDs, rejecting duplicate or colliding entries."""

    requests = _catalog_names(document, "requests")
    events = _catalog_names(document, "events")
    if not requests and not events:
        raise EvidenceBlocked("protocol catalog contains no requests or events")

    expected: dict[str, tuple[str, str]] = {}
    for kind, names in (("request", requests), ("event", events)):
        seen_names: set[str] = set()
        for name in names:
            if name in seen_names:
                raise VerificationFailed(
                    f"protocol catalog contains duplicate {kind} name {name!r}"
                )
            seen_names.add(name)
            test_name = _swift_test_name(kind, name)
            test_id = f"{GENERATED_SUITE}/{test_name}"
            previous = expected.get(test_id)
            if previous is not None:
                previous_kind, previous_name = previous
                raise VerificationFailed(
                    "catalog entries collide on generated XCTest identity "
                    f"{test_name!r}: {previous_kind} {previous_name!r} and "
                    f"{kind} {name!r}"
                )
            expected[test_id] = (kind, name)

    expected_count = len(requests) + len(events)
    if len(expected) != expected_count:
        raise VerificationFailed("generated XCTest identities are not one-to-one")
    return frozenset(expected), len(requests), len(events)


def _brief_ids(values: set[str] | frozenset[str]) -> str:
    ordered = sorted(values)
    preview = ", ".join(repr(value) for value in ordered[:3])
    if len(ordered) > 3:
        preview += f", ... ({len(ordered)} total)"
    return preview


def verify_discovery_against_catalog(
    discovery: DiscoveryEvidence, catalog: object
) -> tuple[frozenset[str], frozenset[str], int, int]:
    expected, request_count, event_count = expected_generated_tests(catalog)
    discovered_generated = discovery.tests_by_suite.get(
        GENERATED_SUITE, frozenset()
    )
    missing = expected - discovered_generated
    extra = discovered_generated - expected
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing " + _brief_ids(missing))
        if extra:
            details.append("extra " + _brief_ids(extra))
        raise VerificationFailed(
            "generated conformance discovery does not match the catalog: "
            + "; ".join(details)
        )
    handwritten = discovery.test_ids - discovered_generated
    missing_handwritten = EXPECTED_HANDWRITTEN_TESTS - handwritten
    extra_handwritten = handwritten - EXPECTED_HANDWRITTEN_TESTS
    if missing_handwritten or extra_handwritten:
        details = []
        if missing_handwritten:
            details.append("missing " + _brief_ids(missing_handwritten))
        if extra_handwritten:
            details.append("extra " + _brief_ids(extra_handwritten))
        raise VerificationFailed(
            "handwritten test discovery does not match its explicit ratchet: "
            + "; ".join(details)
        )
    return discovered_generated, frozenset(handwritten), request_count, event_count


def parse_execution_log(
    raw: bytes, discovery: DiscoveryEvidence, test_process_rc: int
) -> int:
    """Require exact once-only execution and agreeing XCTest root summaries."""

    if not isinstance(raw, bytes):
        raise EvidenceBlocked("XCTest execution log input must be bytes")
    if not raw:
        raise EvidenceBlocked("XCTest execution log is empty")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceBlocked("XCTest execution log is not valid UTF-8") from error
    if not text.strip():
        raise EvidenceBlocked("XCTest execution log contains only whitespace")
    if type(test_process_rc) is not int or test_process_rc < 0:
        raise EvidenceBlocked("test process exit status is invalid")

    lines = text.splitlines()
    started: dict[str, int] = {}
    terminal: dict[str, list[str]] = {}
    required_suites = frozenset({discovery.root_suite, discovery.bundle_suite})
    suite_starts: dict[str, int] = {name: 0 for name in required_suites}
    suite_summaries: dict[str, list[tuple[str, int, int, int]]] = {
        name: [] for name in required_suites
    }
    failures: list[str] = []
    blockers: list[str] = []
    observed_test_failure = False

    for index, line in enumerate(lines):
        if line.startswith("Test Case "):
            start_match = _TEST_START.fullmatch(line)
            terminal_match = _TEST_TERMINAL.fullmatch(line)
            if start_match is None and terminal_match is None:
                blockers.append("XCTest test-case log format was not recognized")
                continue

            runtime_name = (
                start_match.group(1)
                if start_match is not None
                else terminal_match.group(1)  # type: ignore[union-attr]
            )
            test_id = discovery.runtime_aliases.get(runtime_name)
            if test_id is None:
                failures.append(f"execution contained undiscovered test {runtime_name!r}")
                continue

            if start_match is not None:
                started[test_id] = started.get(test_id, 0) + 1
                if started[test_id] > 1:
                    failures.append(f"test started more than once: {test_id!r}")
                continue

            status = terminal_match.group(2)  # type: ignore[union-attr]
            terminal.setdefault(test_id, []).append(status)
            if len(terminal[test_id]) > 1:
                failures.append(f"test terminated more than once: {test_id!r}")
            if started.get(test_id, 0) != 1:
                failures.append(f"test terminated before exactly one start: {test_id!r}")
            if status != "passed":
                failures.append(f"test {status}: {test_id!r}")
                observed_test_failure = True
            continue

        if line.startswith("Test Suite "):
            start_match = _SUITE_START.fullmatch(line)
            terminal_match = _SUITE_TERMINAL.fullmatch(line)
            if start_match is None and terminal_match is None:
                blockers.append("XCTest suite log format was not recognized")
                continue
            if start_match is not None:
                suite_name = start_match.group(1)
                if suite_name in required_suites:
                    suite_starts[suite_name] += 1
                continue

            suite_name = terminal_match.group(1)  # type: ignore[union-attr]
            if suite_name not in required_suites:
                continue
            if index + 1 >= len(lines):
                blockers.append(f"suite {suite_name!r} has no execution summary")
                continue
            summary_match = _SUITE_SUMMARY.fullmatch(lines[index + 1])
            if summary_match is None:
                blockers.append(f"suite {suite_name!r} summary format was not recognized")
                continue
            suite_summaries[suite_name].append(
                (
                    terminal_match.group(2),  # type: ignore[union-attr]
                    int(summary_match.group(1)),
                    int(summary_match.group(2)),
                    int(summary_match.group(3)),
                )
            )

    complete_roots = True
    expected_count = len(discovery.test_ids)
    for suite_name in sorted(required_suites):
        if suite_starts[suite_name] != 1:
            blockers.append(
                f"suite {suite_name!r} must start exactly once, found "
                f"{suite_starts[suite_name]}"
            )
            complete_roots = False
        summaries = suite_summaries[suite_name]
        if len(summaries) != 1:
            blockers.append(
                f"suite {suite_name!r} must have exactly one terminal summary, "
                f"found {len(summaries)}"
            )
            complete_roots = False
            continue
        status, executed, failure_count, unexpected_count = summaries[0]
        if status != "passed":
            failures.append(f"suite {suite_name!r} reported {status}")
            observed_test_failure = True
        if executed != expected_count:
            failures.append(
                f"suite {suite_name!r} executed {executed}, expected {expected_count}"
            )
        if failure_count != 0 or unexpected_count != 0:
            failures.append(
                f"suite {suite_name!r} reported {failure_count} failures "
                f"({unexpected_count} unexpected)"
            )
            observed_test_failure = True

    missing_starts = discovery.test_ids - frozenset(started)
    missing_terminals = discovery.test_ids - frozenset(terminal)
    if missing_starts:
        message = "discovered tests did not start: " + _brief_ids(missing_starts)
        (failures if complete_roots else blockers).append(message)
    if missing_terminals:
        message = "discovered tests did not terminate: " + _brief_ids(missing_terminals)
        (failures if complete_roots else blockers).append(message)

    for test_id in discovery.test_ids & frozenset(terminal):
        statuses = terminal[test_id]
        if len(statuses) == 1 and statuses[0] == "passed" and started.get(test_id) == 1:
            continue
        # Specific violations were recorded while parsing.  This fallback
        # ensures no anomalous state can accidentally pass.
        if not any(test_id in failure for failure in failures):
            failures.append(f"test did not execute exactly once and pass: {test_id!r}")

    if test_process_rc != 0 and not failures:
        blockers.append(
            f"test process exited {test_process_rc} without determinate XCTest failure evidence"
        )

    # Malformed or incomplete evidence is BLOCKED unless an explicit XCTest
    # failure/skip already makes a non-pass verdict independently decisive.
    if blockers and not observed_test_failure:
        raise EvidenceBlocked("; ".join(dict.fromkeys(blockers)))
    if failures:
        raise VerificationFailed("; ".join(dict.fromkeys(failures)))
    if blockers:
        raise EvidenceBlocked("; ".join(dict.fromkeys(blockers)))
    if test_process_rc != 0:
        raise EvidenceBlocked(f"test process exited {test_process_rc}")
    return expected_count


def verify(
    discovery_json: bytes,
    swiftpm_list: bytes,
    execution_log: bytes,
    catalog_json: bytes,
    test_process_rc: int,
) -> TestExecutionEvidence:
    discovery = parse_discovery_document(
        parse_json_bytes(discovery_json, "XCTest discovery JSON")
    )
    listed = parse_swiftpm_list(swiftpm_list)
    verify_list_matches_discovery(listed, discovery)
    catalog = parse_json_bytes(catalog_json, "protocol catalog JSON")
    generated, handwritten, request_count, event_count = (
        verify_discovery_against_catalog(discovery, catalog)
    )
    executed_count = parse_execution_log(execution_log, discovery, test_process_rc)
    return TestExecutionEvidence(
        request_count=request_count,
        event_count=event_count,
        generated_count=len(generated),
        handwritten_count=len(handwritten),
        listed_count=len(listed),
        discovered_count=len(discovery.test_ids),
        executed_count=executed_count,
    )


def _read_artifact(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise EvidenceBlocked(f"could not read {label}") from error


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery-json", type=Path, required=True)
    parser.add_argument("--swiftpm-list", type=Path, required=True)
    parser.add_argument("--execution-log", type=Path, required=True)
    parser.add_argument("--catalog-json", type=Path, required=True)
    parser.add_argument("--test-rc", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        evidence = verify(
            _read_artifact(arguments.discovery_json, "XCTest discovery JSON"),
            _read_artifact(arguments.swiftpm_list, "SwiftPM test list"),
            _read_artifact(arguments.execution_log, "XCTest execution log"),
            _read_artifact(arguments.catalog_json, "protocol catalog JSON"),
            arguments.test_rc,
        )
    except VerificationFailed as error:
        print(f"G2 TEST FAIL: {error}", file=sys.stderr)
        return 1
    except EvidenceBlocked as error:
        print(f"G2 TEST BLOCKED: {error}", file=sys.stderr)
        return 2

    print(
        "G2 TEST PASS: "
        f"catalog={evidence.request_count} requests+{evidence.event_count} events; "
        f"generated={evidence.generated_count}; "
        f"handwritten={evidence.handwritten_count}; "
        f"listed={evidence.listed_count}; "
        f"discovered={evidence.discovered_count}; "
        f"executed={evidence.executed_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
