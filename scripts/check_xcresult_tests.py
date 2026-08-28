#!/usr/bin/env python3
"""Fail-closed validation for XCTest evidence exported from an xcresult.

The supported document is the simplified Xcode 26 JSON emitted by:

    xcrun xcresulttool get test-results tests \
        --path RESULT.xcresult --format json

Expectations deliberately contain both an exact test-identifier set and an
explicit count for every bundle/suite.  The redundancy makes an accidentally
truncated command line a configuration error instead of a smaller green gate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BUNDLE_TYPES = frozenset({"Unit test bundle", "UI test bundle"})
CONTAINER_TYPES = frozenset(
    {
        "Test Plan",
        "Test Plan Configuration",
        "Test Destination",
        "Test Target",
    }
)
SUITE_TYPE = "Test Suite"
CASE_TYPE = "Test Case"
BAD_RESULTS = frozenset(
    {
        "Cancelled",
        "Expected Failure",
        "Failed",
        "Not Run",
        "Skipped",
        "Timed Out",
    }
)


class ConfigurationError(RuntimeError):
    """The expectation declaration cannot produce a decidable check."""


class SchemaError(RuntimeError):
    """The evidence does not match the supported Xcode 26 JSON schema."""


class DuplicateJSONKeyError(ValueError):
    """A JSON object contains a duplicate key and is therefore ambiguous."""


@dataclass(frozen=True, order=True)
class Group:
    bundle: str
    suite: str

    def display(self) -> str:
        return f"{self.bundle}/{self.suite}"


@dataclass(frozen=True, order=True)
class TestIdentity:
    bundle: str
    suite: str
    identifier: str

    @property
    def group(self) -> Group:
        return Group(self.bundle, self.suite)

    def display(self) -> str:
        return f"{self.bundle}/{self.suite}/{self.identifier}"


@dataclass(frozen=True)
class Expectations:
    identifiers: dict[Group, frozenset[str]]
    counts: dict[Group, int]


@dataclass
class Evidence:
    tests: dict[TestIdentity, str] = field(default_factory=dict)
    bundles_seen: set[str] = field(default_factory=set)
    suites_seen: set[Group] = field(default_factory=set)
    failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Verification:
    summaries: tuple[str, ...]
    failures: tuple[str, ...]


def _validate_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{label} must be a non-empty string")
    if value != value.strip() or any(ord(character) < 32 for character in value):
        raise SchemaError(
            f"{label} contains surrounding whitespace or control characters"
        )
    return value


def _parse_group(raw: str, label: str) -> Group:
    if raw.count("/") != 1:
        raise ConfigurationError(f"{label} must have the form BUNDLE/SUITE")
    bundle, suite = raw.split("/", 1)
    try:
        return Group(
            _validate_name(bundle, f"{label} bundle"),
            _validate_name(suite, f"{label} suite"),
        )
    except SchemaError as error:
        raise ConfigurationError(str(error)) from error


def parse_expectations(
    raw_identifiers: list[str], raw_counts: list[str]
) -> Expectations:
    if not raw_identifiers:
        raise ConfigurationError("at least one --expect identifier is required")
    if not raw_counts:
        raise ConfigurationError("at least one --expect-count declaration is required")

    identifiers: dict[Group, set[str]] = {}
    for raw in raw_identifiers:
        first_separator = raw.find("/")
        second_separator = raw.find("/", first_separator + 1)
        if first_separator <= 0 or second_separator <= first_separator + 1:
            raise ConfigurationError(
                "--expect must have the form BUNDLE/SUITE/TEST_IDENTIFIER"
            )
        group = _parse_group(raw[:second_separator], "--expect group")
        try:
            identifier = _validate_name(
                raw[second_separator + 1 :], "--expect test identifier"
            )
        except SchemaError as error:
            raise ConfigurationError(str(error)) from error
        group_identifiers = identifiers.setdefault(group, set())
        if identifier in group_identifiers:
            raise ConfigurationError(f"duplicate --expect: {raw}")
        group_identifiers.add(identifier)

    counts: dict[Group, int] = {}
    for raw in raw_counts:
        group_raw, separator, count_raw = raw.rpartition("=")
        if not separator:
            raise ConfigurationError(
                "--expect-count must have the form BUNDLE/SUITE=COUNT"
            )
        group = _parse_group(group_raw, "--expect-count group")
        if group in counts:
            raise ConfigurationError(
                f"duplicate --expect-count for {group.display()}"
            )
        try:
            count = int(count_raw)
        except ValueError as error:
            raise ConfigurationError(
                f"--expect-count for {group.display()} is not an integer"
            ) from error
        if count <= 0:
            raise ConfigurationError(
                f"--expect-count for {group.display()} must be positive"
            )
        counts[group] = count

    identifier_groups = set(identifiers)
    count_groups = set(counts)
    if identifier_groups != count_groups:
        missing_counts = sorted(identifier_groups - count_groups)
        missing_identifiers = sorted(count_groups - identifier_groups)
        details: list[str] = []
        if missing_counts:
            details.append(
                "missing counts for "
                + ", ".join(group.display() for group in missing_counts)
            )
        if missing_identifiers:
            details.append(
                "missing identifiers for "
                + ", ".join(group.display() for group in missing_identifiers)
            )
        raise ConfigurationError("; ".join(details))

    for group, group_identifiers in identifiers.items():
        if len(group_identifiers) != counts[group]:
            raise ConfigurationError(
                f"{group.display()} declares count {counts[group]} but lists "
                f"{len(group_identifiers)} unique identifiers"
            )

    return Expectations(
        identifiers={
            group: frozenset(group_identifiers)
            for group, group_identifiers in identifiers.items()
        },
        counts=counts,
    )


def _children(node: dict[str, Any], node_label: str, required: bool) -> list[Any]:
    if "children" not in node:
        if required:
            raise SchemaError(f"{node_label} has no children array")
        return []
    children = node["children"]
    if not isinstance(children, list):
        raise SchemaError(f"{node_label} children is not an array")
    return children


def _result(node: dict[str, Any], node_label: str, required: bool) -> str | None:
    if "result" not in node:
        if required:
            raise SchemaError(f"{node_label} has no result")
        return None
    result = node["result"]
    if not isinstance(result, str) or not result:
        raise SchemaError(f"{node_label} result is not a non-empty string")
    if result == "Passed":
        return result
    if result in BAD_RESULTS:
        return result
    raise SchemaError(f"{node_label} has unknown result {result!r}")


def _record_nonpassing(evidence: Evidence, label: str, result: str | None) -> None:
    if result is not None and result != "Passed":
        evidence.failures.append(f"{label} result is {result!r}, expected 'Passed'")


def _walk_node(
    raw_node: object,
    evidence: Evidence,
    bundle: str | None = None,
    suite: str | None = None,
) -> None:
    if not isinstance(raw_node, dict):
        raise SchemaError("testNodes contains a node that is not an object")
    node_type = _validate_name(raw_node.get("nodeType"), "nodeType")
    name = _validate_name(raw_node.get("name"), f"{node_type} name")

    if node_type in CONTAINER_TYPES:
        if bundle is not None or suite is not None:
            raise SchemaError(f"{node_type} is nested inside a bundle or suite")
        result = _result(raw_node, node_type, required=False)
        _record_nonpassing(evidence, node_type, result)
        for child in _children(raw_node, node_type, required=True):
            _walk_node(child, evidence)
        return

    if node_type in BUNDLE_TYPES:
        if bundle is not None or suite is not None:
            raise SchemaError(
                f"test bundle {name!r} is nested inside another test node"
            )
        if name in evidence.bundles_seen:
            raise SchemaError(f"test bundle {name!r} occurs more than once")
        evidence.bundles_seen.add(name)
        result = _result(raw_node, f"test bundle {name!r}", required=True)
        _record_nonpassing(evidence, f"test bundle {name!r}", result)
        for child in _children(raw_node, f"test bundle {name!r}", required=True):
            _walk_node(child, evidence, bundle=name)
        return

    if node_type == SUITE_TYPE:
        if bundle is None:
            raise SchemaError(f"test suite {name!r} has no test-bundle parent")
        if suite is not None:
            raise SchemaError(
                f"test suite {name!r} is nested inside test suite {suite!r}"
            )
        group = Group(bundle, name)
        if group in evidence.suites_seen:
            raise SchemaError(f"test suite {group.display()} occurs more than once")
        evidence.suites_seen.add(group)
        result = _result(raw_node, f"test suite {group.display()}", required=True)
        _record_nonpassing(evidence, f"test suite {group.display()}", result)
        for child in _children(
            raw_node, f"test suite {group.display()}", required=True
        ):
            _walk_node(child, evidence, bundle=bundle, suite=name)
        return

    if node_type == CASE_TYPE:
        if bundle is None or suite is None:
            raise SchemaError(
                f"test case {name!r} has no unambiguous bundle/suite parent"
            )
        if _children(raw_node, f"test case {name!r}", required=False):
            raise SchemaError(f"test case {name!r} unexpectedly contains child nodes")
        node_identifier = _validate_name(
            raw_node.get("nodeIdentifier"), f"test case {name!r} nodeIdentifier"
        )
        identifier_parts = node_identifier.split("/")
        if len(identifier_parts) < 2 or identifier_parts[-2:] != [suite, name]:
            raise SchemaError(
                f"test case {name!r} nodeIdentifier does not end with "
                f"{suite}/{name}"
            )
        identity = TestIdentity(bundle, suite, name)
        if identity in evidence.tests:
            raise SchemaError(f"test case {identity.display()} occurs more than once")
        result = _result(raw_node, f"test case {identity.display()}", required=True)
        evidence.tests[identity] = result or ""
        _record_nonpassing(evidence, f"test case {identity.display()}", result)
        return

    raise SchemaError(f"unknown nodeType {node_type!r}")


def collect_evidence(document: object) -> Evidence:
    if not isinstance(document, dict):
        raise SchemaError("JSON root is not an object")
    if "testNodes" not in document:
        raise SchemaError("JSON root has no testNodes array")
    nodes = document["testNodes"]
    if not isinstance(nodes, list):
        raise SchemaError("JSON root testNodes is not an array")

    evidence = Evidence()
    for node in nodes:
        _walk_node(node, evidence)
    return evidence


def verify_document(document: object, expected: Expectations) -> Verification:
    evidence = collect_evidence(document)
    failures = list(evidence.failures)

    if not evidence.tests:
        failures.append("xcresult contains zero test cases")

    expected_identities = {
        TestIdentity(group.bundle, group.suite, identifier)
        for group, identifiers in expected.identifiers.items()
        for identifier in identifiers
    }
    actual_identities = set(evidence.tests)

    for identity in sorted(expected_identities - actual_identities):
        failures.append(f"expected test is missing: {identity.display()}")
    for identity in sorted(actual_identities - expected_identities):
        failures.append(f"unexpected test is present: {identity.display()}")

    expected_groups = set(expected.identifiers)
    expected_bundles = {group.bundle for group in expected_groups}
    for bundle_name in sorted(expected_bundles - evidence.bundles_seen):
        failures.append(f"expected test bundle is missing: {bundle_name}")
    for bundle_name in sorted(evidence.bundles_seen - expected_bundles):
        failures.append(f"unexpected test bundle is present: {bundle_name}")

    for group in sorted(expected_groups - evidence.suites_seen):
        failures.append(f"expected test suite is missing: {group.display()}")
    for group in sorted(evidence.suites_seen - expected_groups):
        failures.append(f"unexpected test suite is present: {group.display()}")

    summaries: list[str] = []
    for group in sorted(expected_groups):
        actual_count = sum(identity.group == group for identity in actual_identities)
        expected_count = expected.counts[group]
        if actual_count != expected_count:
            failures.append(
                f"{group.display()} ran {actual_count} tests; expected {expected_count}"
            )
        else:
            summaries.append(
                f"{group.display()}: {actual_count}/{expected_count} "
                "expected tests present"
            )

    return Verification(tuple(summaries), tuple(dict.fromkeys(failures)))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKeyError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def parse_json(raw: str) -> object:
    if not raw.strip():
        raise SchemaError("test-results JSON is empty")
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except DuplicateJSONKeyError as error:
        raise SchemaError(str(error)) from error
    except json.JSONDecodeError as error:
        raise SchemaError(
            f"test-results JSON is invalid at line {error.lineno}, column {error.colno}"
        ) from error


def _load_json_file(path: str) -> object:
    if path == "-":
        raw = sys.stdin.read()
    else:
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise SchemaError(
                f"JSON input could not be read ({type(error).__name__})"
            ) from error
    return parse_json(raw)


def _load_xcresult(path: Path) -> object:
    if not path.is_dir():
        raise SchemaError("xcresult input directory does not exist")
    try:
        process = subprocess.run(
            [
                "xcrun",
                "xcresulttool",
                "get",
                "test-results",
                "tests",
                "--path",
                str(path),
                "--format",
                "json",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, UnicodeError) as error:
        raise SchemaError(
            f"xcresulttool could not be executed ({type(error).__name__})"
        ) from error
    if process.returncode != 0:
        raise SchemaError(
            f"xcresulttool export failed with exit code {process.returncode}"
        )
    return parse_json(process.stdout)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--xcresult",
        type=Path,
        help="xcresult directory to export with Xcode 26 xcresulttool",
    )
    source.add_argument(
        "--json",
        help="already-exported test-results JSON file, or '-' for stdin",
    )
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="BUNDLE/SUITE/TEST_IDENTIFIER",
    )
    parser.add_argument(
        "--expect-count",
        action="append",
        default=[],
        metavar="BUNDLE/SUITE=COUNT",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv or sys.argv[1:])
    try:
        expected = parse_expectations(arguments.expect, arguments.expect_count)
        document = (
            _load_xcresult(arguments.xcresult)
            if arguments.xcresult is not None
            else _load_json_file(arguments.json)
        )
        verification = verify_document(document, expected)
    except (ConfigurationError, SchemaError) as error:
        print(f"XCTEST BLOCKED: {error}", file=sys.stderr)
        return 2

    for summary in verification.summaries:
        print(summary)
    if verification.failures:
        for failure in verification.failures:
            print(f"XCTEST FAIL: {failure}", file=sys.stderr)
        return 1

    total = sum(expected.counts.values())
    print(
        f"XCTEST PASS: exact XCTest evidence verified "
        f"({total} tests in {len(expected.counts)} suites)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
