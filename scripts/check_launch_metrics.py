#!/usr/bin/env python3
"""Fail-closed, offline validation for G12 cold-launch evidence.

The caller must export both documents with the pinned Xcode toolchain and
schema version before invoking this checker::

    xcrun xcresulttool get test-results metrics \
        --schema --schema-version 0.1.0 --path RESULT --compact > schema.json
    xcrun xcresulttool get test-results metrics \
        --schema-version 0.1.0 --path RESULT \
        --test-id TEST_IDENTIFIER_URL --compact > metrics.json

This module deliberately does not invoke ``xcresulttool``.  Tool exit status,
stderr, and output-file creation remain the workflow wrapper's operational
evidence.  The checked-in schema digest binds the checker to the reviewed
Xcode 26.6 schema bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import Any


EXPECTED_SCHEMA_VERSION = "0.1.0"
SCHEMA_SHA256_PLACEHOLDER = "PLACEHOLDER_XCODE_26_6_METRICS_SCHEMA_SHA256"
EXPECTED_SCHEMA_SHA256 = (
    "55401dc6d98f6f89f82e05c971c3a29b8511a698d962af5a04c684c6fe46d8bf"
)

EXPECTED_TEST_IDENTIFIER = (
    "LaunchPerformanceUITests/testLaunchMetricBaselineRecorded()"
)
EXPECTED_TEST_IDENTIFIER_URL = (
    "test://com.apple.xcode/Talaria/TalariaUITests/"
    "LaunchPerformanceUITests/testLaunchMetricBaselineRecorded"
)
EXPECTED_METRIC_IDENTIFIER = (
    "com.apple.dt.XCTMetric_ApplicationLaunch-AppLaunch.duration"
)
EXPECTED_METRIC_DISPLAY_NAME = "Duration (AppLaunch)"
EXPECTED_METRIC_POLARITY = "prefers smaller"
EXPECTED_METRIC_UNIT = "s"
EXPECTED_CONFIGURATION_ID = "1"
EXPECTED_CONFIGURATION_NAME = "Test Scheme Action"
EXPECTED_MEASUREMENT_COUNT = 5
LAUNCH_BUDGET_SECONDS = Decimal("3")

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class EvidenceBlocked(RuntimeError):
    """The supplied evidence cannot produce a trustworthy cold-launch verdict."""


class DuplicateJSONKeyError(ValueError):
    """A JSON object contained the same key more than once."""


@dataclass(frozen=True)
class LaunchMetricEvidence:
    """Validated application-launch samples and their arithmetic mean."""

    measurements: tuple[Decimal, ...]
    mean_seconds: Decimal
    device_id: str
    device_name: str

    @property
    def within_budget(self) -> bool:
        return self.mean_seconds < LAUNCH_BUDGET_SECONDS


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKeyError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite_json_number(token: str) -> Decimal:
    raise EvidenceBlocked(f"metrics JSON contains non-finite number {token!r}")


def parse_metrics_json(raw: bytes) -> object:
    """Decode metrics JSON strictly, preserving every number as ``Decimal``."""

    if not isinstance(raw, bytes):
        raise EvidenceBlocked("metrics JSON input must be bytes")
    if not raw:
        raise EvidenceBlocked("metrics JSON is empty")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceBlocked("metrics JSON is not valid UTF-8") from error
    if not text.strip():
        raise EvidenceBlocked("metrics JSON contains only whitespace")

    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_reject_nonfinite_json_number,
        )
    except EvidenceBlocked:
        raise
    except DuplicateJSONKeyError as error:
        raise EvidenceBlocked(str(error)) from error
    except (json.JSONDecodeError, DecimalException, ValueError) as error:
        raise EvidenceBlocked(
            f"metrics JSON is invalid ({type(error).__name__})"
        ) from error


def verify_schema_bytes(
    schema_bytes: bytes, expected_sha256: str | None = None
) -> str:
    """Require the reviewed Xcode 26.6 metrics-schema byte digest.

    ``expected_sha256`` is injectable for offline tests. Production callers
    omit it and therefore use the reviewed, checked-in digest. Retaining the
    placeholder branch makes an unfinished or accidentally reverted pin fail
    closed.
    """

    expected = EXPECTED_SCHEMA_SHA256 if expected_sha256 is None else expected_sha256
    if expected == SCHEMA_SHA256_PLACEHOLDER:
        raise EvidenceBlocked(
            "Xcode 26.6 metrics schema SHA-256 is still a placeholder; "
            f"review and pin schema version {EXPECTED_SCHEMA_VERSION}"
        )
    if not isinstance(expected, str) or _SHA256_PATTERN.fullmatch(expected) is None:
        raise EvidenceBlocked("expected metrics-schema SHA-256 is not 64 lowercase hex")
    if not isinstance(schema_bytes, bytes):
        raise EvidenceBlocked("metrics schema input must be bytes")
    if not schema_bytes:
        raise EvidenceBlocked("metrics schema is empty")

    actual = hashlib.sha256(schema_bytes).hexdigest()
    if actual != expected:
        raise EvidenceBlocked(
            "metrics-schema SHA-256 mismatch for version "
            f"{EXPECTED_SCHEMA_VERSION}: expected {expected}, found {actual}"
        )
    return actual


def _object_with_exact_keys(
    value: object, expected_keys: frozenset[str], label: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceBlocked(f"{label} must be an object")
    actual_keys = frozenset(value)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        unexpected = sorted(actual_keys - expected_keys)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(repr(key) for key in missing))
        if unexpected:
            details.append(
                "unexpected " + ", ".join(repr(key) for key in unexpected)
            )
        raise EvidenceBlocked(f"{label} keys are ambiguous: {'; '.join(details)}")
    return value


def _singleton_list(value: object, label: str) -> object:
    if not isinstance(value, list):
        raise EvidenceBlocked(f"{label} must be an array")
    if len(value) != 1:
        raise EvidenceBlocked(
            f"{label} must contain exactly one item, found {len(value)}"
        )
    return value[0]


def _exact_string(value: object, expected: str, label: str) -> str:
    if not isinstance(value, str) or value != expected:
        raise EvidenceBlocked(f"{label} must equal {expected!r}")
    return value


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceBlocked(f"{label} must be a non-empty string")
    if value != value.strip() or any(ord(character) < 32 for character in value):
        raise EvidenceBlocked(
            f"{label} contains surrounding whitespace or control characters"
        )
    return value


def _exact_number(value: object, expected: Decimal, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise EvidenceBlocked(f"{label} must be a JSON number")
    if not value.is_finite() or value != expected:
        raise EvidenceBlocked(f"{label} must equal {expected}")
    return value


def verify_metrics_document(document: object) -> LaunchMetricEvidence:
    """Validate the exact Xcode 26.6 G12 document and calculate its mean."""

    test_entry = _singleton_list(document, "metrics JSON root")
    test = _object_with_exact_keys(
        test_entry,
        frozenset({"testIdentifier", "testIdentifierURL", "testRuns"}),
        "metrics test entry",
    )
    _exact_string(
        test["testIdentifier"], EXPECTED_TEST_IDENTIFIER, "testIdentifier"
    )
    _exact_string(
        test["testIdentifierURL"],
        EXPECTED_TEST_IDENTIFIER_URL,
        "testIdentifierURL",
    )

    test_run_value = _singleton_list(test["testRuns"], "testRuns")
    test_run = _object_with_exact_keys(
        test_run_value,
        frozenset({"device", "metrics", "testPlanConfiguration"}),
        "test run",
    )

    device = _object_with_exact_keys(
        test_run["device"],
        frozenset({"deviceId", "deviceName"}),
        "test-run device",
    )
    device_id = _nonempty_string(device["deviceId"], "deviceId")
    device_name = _nonempty_string(device["deviceName"], "deviceName")

    configuration = _object_with_exact_keys(
        test_run["testPlanConfiguration"],
        frozenset({"configurationId", "configurationName"}),
        "test-plan configuration",
    )
    _exact_string(
        configuration["configurationId"],
        EXPECTED_CONFIGURATION_ID,
        "configurationId",
    )
    _exact_string(
        configuration["configurationName"],
        EXPECTED_CONFIGURATION_NAME,
        "configurationName",
    )

    metric_value = _singleton_list(test_run["metrics"], "test-run metrics")
    metric = _object_with_exact_keys(
        metric_value,
        frozenset(
            {
                "baselineAverage",
                "baselineName",
                "displayName",
                "identifier",
                "maxPercentRegression",
                "maxPercentRelativeStandardDeviation",
                "maxRegression",
                "maxStandardDeviation",
                "measurements",
                "polarity",
                "unitOfMeasurement",
            }
        ),
        "launch metric",
    )
    _exact_number(metric["baselineAverage"], Decimal("0"), "baselineAverage")
    _exact_string(metric["baselineName"], "", "baselineName")
    _exact_string(
        metric["displayName"], EXPECTED_METRIC_DISPLAY_NAME, "metric displayName"
    )
    _exact_string(
        metric["identifier"], EXPECTED_METRIC_IDENTIFIER, "metric identifier"
    )
    _exact_number(
        metric["maxPercentRegression"], Decimal("10"), "maxPercentRegression"
    )
    _exact_number(
        metric["maxPercentRelativeStandardDeviation"],
        Decimal("10"),
        "maxPercentRelativeStandardDeviation",
    )
    _exact_number(metric["maxRegression"], Decimal("0"), "maxRegression")
    _exact_number(
        metric["maxStandardDeviation"], Decimal("10"), "maxStandardDeviation"
    )
    _exact_string(
        metric["polarity"], EXPECTED_METRIC_POLARITY, "metric polarity"
    )
    _exact_string(
        metric["unitOfMeasurement"], EXPECTED_METRIC_UNIT, "metric unit"
    )

    raw_measurements = metric["measurements"]
    if not isinstance(raw_measurements, list):
        raise EvidenceBlocked("measurements must be an array")
    if len(raw_measurements) != EXPECTED_MEASUREMENT_COUNT:
        raise EvidenceBlocked(
            f"measurements must contain exactly {EXPECTED_MEASUREMENT_COUNT} samples, "
            f"found {len(raw_measurements)}"
        )

    measurements: list[Decimal] = []
    for index, value in enumerate(raw_measurements):
        if isinstance(value, bool) or not isinstance(value, Decimal):
            raise EvidenceBlocked(f"measurement {index} must be a JSON number")
        if not value.is_finite():
            raise EvidenceBlocked(f"measurement {index} must be finite")
        if value <= 0:
            raise EvidenceBlocked(f"measurement {index} must be positive")
        measurements.append(value)

    try:
        mean_seconds = sum(measurements, Decimal("0")) / Decimal(
            EXPECTED_MEASUREMENT_COUNT
        )
    except DecimalException as error:
        raise EvidenceBlocked("measurements cannot produce a finite mean") from error
    if not mean_seconds.is_finite() or mean_seconds <= 0:
        raise EvidenceBlocked("measurements cannot produce a finite positive mean")

    return LaunchMetricEvidence(
        tuple(measurements), mean_seconds, device_id, device_name
    )


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        if not path.is_file():
            raise EvidenceBlocked(f"{label} file does not exist")
        return path.read_bytes()
    except EvidenceBlocked:
        raise
    except OSError as error:
        raise EvidenceBlocked(
            f"{label} file could not be read ({type(error).__name__})"
        ) from error


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics-json", type=Path, required=True)
    parser.add_argument("--schema-json", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        schema_bytes = _read_bytes(arguments.schema_json, "metrics schema")
        verify_schema_bytes(schema_bytes)
        metrics_bytes = _read_bytes(arguments.metrics_json, "metrics JSON")
        evidence = verify_metrics_document(parse_metrics_json(metrics_bytes))
    except EvidenceBlocked as error:
        print(f"G12 COLD-LAUNCH BLOCKED: {error}", file=sys.stderr)
        return 2

    if not evidence.within_budget:
        print(
            "G12 COLD-LAUNCH FAIL: XCTApplicationLaunchMetric mean "
            f"{evidence.mean_seconds} s is not below {LAUNCH_BUDGET_SECONDS} s",
            file=sys.stderr,
        )
        return 1

    print(
        "G12 COLD-LAUNCH PASS: XCTApplicationLaunchMetric mean "
        f"{evidence.mean_seconds} s is below {LAUNCH_BUDGET_SECONDS} s "
        f"({EXPECTED_MEASUREMENT_COUNT} samples)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
