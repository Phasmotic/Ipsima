from __future__ import annotations

import copy
import hashlib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import check_launch_metrics as checker


PASS_MEASUREMENTS_JSON = (
    b"[1.905808042,1.913853959,1.977160208,2.41402925,2.26780575]"
)
FAIL_MEASUREMENTS_JSON = b"[3.123634,2.574230,3.181742,4.552391,4.226221]"

VALID_METRICS_JSON = b"""[
  {
    "testIdentifier": "LaunchPerformanceUITests/testLaunchMetricBaselineRecorded()",
    "testIdentifierURL": "test://com.apple.xcode/Talaria/TalariaUITests/LaunchPerformanceUITests/testLaunchMetricBaselineRecorded",
    "testRuns": [
      {
        "device": {
          "deviceId": "00000000-0000-0000-0000-000000000001",
          "deviceName": "iPhone 16e"
        },
        "metrics": [
          {
            "baselineAverage": 0,
            "baselineName": "",
            "displayName": "Duration (AppLaunch)",
            "identifier": "com.apple.dt.XCTMetric_ApplicationLaunch-AppLaunch.duration",
            "maxPercentRegression": 10,
            "maxPercentRelativeStandardDeviation": 10,
            "maxRegression": 0,
            "maxStandardDeviation": 10,
            "measurements": [1.905808042,1.913853959,1.977160208,2.41402925,2.26780575],
            "polarity": "prefers smaller",
            "unitOfMeasurement": "s"
          }
        ],
        "testPlanConfiguration": {
          "configurationId": "1",
          "configurationName": "Test Scheme Action"
        }
      }
    ]
  }
]"""


def valid_document() -> list[object]:
    document = checker.parse_metrics_json(VALID_METRICS_JSON)
    if not isinstance(document, list):
        raise AssertionError("valid fixture did not parse to a list")
    return document


def test_entry(document: list[object]) -> dict[str, object]:
    return document[0]  # type: ignore[return-value]


def test_run(document: list[object]) -> dict[str, object]:
    return test_entry(document)["testRuns"][0]  # type: ignore[index,return-value]


def metric(document: list[object]) -> dict[str, object]:
    return test_run(document)["metrics"][0]  # type: ignore[index,return-value]


def device(document: list[object]) -> dict[str, object]:
    return test_run(document)["device"]  # type: ignore[return-value]


def configuration(document: list[object]) -> dict[str, object]:
    return test_run(document)["testPlanConfiguration"]  # type: ignore[return-value]


def metrics_json_with(measurements: bytes) -> bytes:
    return VALID_METRICS_JSON.replace(PASS_MEASUREMENTS_JSON, measurements)


class ParseMetricsJSONTests(unittest.TestCase):
    def test_numbers_are_decimal_and_fixture_preserves_observed_shape(self) -> None:
        document = valid_document()
        self.assertIsInstance(metric(document)["baselineAverage"], Decimal)
        self.assertTrue(
            all(
                isinstance(value, Decimal)
                for value in metric(document)[
                    "measurements"
                ]  # type: ignore[union-attr]
            )
        )

    def test_empty_or_whitespace_only_input_blocks(self) -> None:
        for raw in (b"", b" ", b"\r\n\t"):
            with self.subTest(raw=raw), self.assertRaises(checker.EvidenceBlocked):
                checker.parse_metrics_json(raw)

    def test_non_bytes_input_blocks(self) -> None:
        with self.assertRaises(checker.EvidenceBlocked):
            checker.parse_metrics_json("[]")  # type: ignore[arg-type]

    def test_invalid_utf8_and_utf8_bom_block(self) -> None:
        for raw in (b"[\xff]", b"\xef\xbb\xbf[]"):
            with self.subTest(raw=raw), self.assertRaises(checker.EvidenceBlocked):
                checker.parse_metrics_json(raw)

    def test_invalid_or_trailing_json_blocks(self) -> None:
        for raw in (b"[", b"[] trailing", b"{'not': 'json'}"):
            with self.subTest(raw=raw), self.assertRaises(checker.EvidenceBlocked):
                checker.parse_metrics_json(raw)

    def test_duplicate_key_at_any_object_level_blocks(self) -> None:
        raw = b'[{"testIdentifier":"first","testIdentifier":"second"}]'
        with self.assertRaisesRegex(checker.EvidenceBlocked, "duplicate JSON key"):
            checker.parse_metrics_json(raw)

    def test_nonfinite_json_constants_block(self) -> None:
        for token in (b"NaN", b"Infinity", b"-Infinity"):
            raw = metrics_json_with(
                PASS_MEASUREMENTS_JSON.replace(b"1.905808042", token, 1)
            )
            with self.subTest(token=token), self.assertRaises(
                checker.EvidenceBlocked
            ):
                checker.parse_metrics_json(raw)


class SchemaDigestTests(unittest.TestCase):
    def test_production_digest_is_pinned_lowercase_sha256(self) -> None:
        digest = checker.EXPECTED_SCHEMA_SHA256
        self.assertNotEqual(digest, checker.SCHEMA_SHA256_PLACEHOLDER)
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(character in "0123456789abcdef" for character in digest))

    def test_placeholder_is_an_unconditional_block(self) -> None:
        with self.assertRaisesRegex(checker.EvidenceBlocked, "placeholder"):
            checker.verify_schema_bytes(
                b"reviewed schema", checker.SCHEMA_SHA256_PLACEHOLDER
            )

    def test_matching_digest_passes_and_is_returned(self) -> None:
        raw = b'{"schema":"fixture"}\n'
        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual(checker.verify_schema_bytes(raw, digest), digest)

    def test_digest_mismatch_blocks(self) -> None:
        with self.assertRaisesRegex(checker.EvidenceBlocked, "mismatch"):
            checker.verify_schema_bytes(b"schema", "0" * 64)

    def test_malformed_expected_digest_blocks(self) -> None:
        for digest in ("", "0" * 63, "0" * 65, "A" * 64, "g" * 64):
            with self.subTest(digest=digest), self.assertRaises(
                checker.EvidenceBlocked
            ):
                checker.verify_schema_bytes(b"schema", digest)

    def test_empty_or_nonbytes_schema_blocks(self) -> None:
        digest = hashlib.sha256(b"schema").hexdigest()
        for raw in (b"", "schema"):
            with self.subTest(raw=raw), self.assertRaises(
                checker.EvidenceBlocked
            ):
                checker.verify_schema_bytes(raw, digest)  # type: ignore[arg-type]


class LaunchMetricVerdictTests(unittest.TestCase):
    def test_known_xcode_26_6_pass_mean(self) -> None:
        evidence = checker.verify_metrics_document(valid_document())
        self.assertEqual(evidence.mean_seconds, Decimal("2.0957314418"))
        self.assertTrue(evidence.within_budget)
        self.assertEqual(len(evidence.measurements), 5)

    def test_observed_floating_expansion_stays_below_budget(self) -> None:
        raw = VALID_METRICS_JSON.replace(b"1.977160208", b"1.9771602080000001")
        evidence = checker.verify_metrics_document(checker.parse_metrics_json(raw))
        self.assertEqual(evidence.mean_seconds, Decimal("2.09573144180000002"))
        self.assertTrue(evidence.within_budget)

    def test_known_failing_run_mean(self) -> None:
        document = checker.parse_metrics_json(
            metrics_json_with(FAIL_MEASUREMENTS_JSON)
        )
        evidence = checker.verify_metrics_document(document)
        self.assertEqual(evidence.mean_seconds, Decimal("3.5316436"))
        self.assertFalse(evidence.within_budget)

    def test_exact_budget_and_above_budget_fail(self) -> None:
        for samples in (
            b"[3,3,3,3,3]",
            b"[3.000000001,3,3,3,3]",
        ):
            document = checker.parse_metrics_json(metrics_json_with(samples))
            with self.subTest(samples=samples):
                self.assertFalse(
                    checker.verify_metrics_document(document).within_budget
                )

    def test_mean_not_worst_sample_controls_budget(self) -> None:
        document = checker.parse_metrics_json(
            metrics_json_with(b"[2,2,2,2,4]")
        )
        evidence = checker.verify_metrics_document(document)
        self.assertEqual(evidence.mean_seconds, Decimal("2.4"))
        self.assertTrue(evidence.within_budget)


class ExactShapeTests(unittest.TestCase):
    def assert_blocked(self, document: object, pattern: str | None = None) -> None:
        context = (
            self.assertRaisesRegex(checker.EvidenceBlocked, pattern)
            if pattern is not None
            else self.assertRaises(checker.EvidenceBlocked)
        )
        with context:
            checker.verify_metrics_document(document)

    def test_root_must_be_exact_singleton_array(self) -> None:
        for document in ({}, None, [], [test_entry(valid_document())] * 2):
            with self.subTest(document=document):
                self.assert_blocked(document)

    def test_test_entry_rejects_missing_and_unexpected_keys(self) -> None:
        for mutation in ("missing", "unexpected"):
            document = valid_document()
            entry = test_entry(document)
            if mutation == "missing":
                del entry["testIdentifierURL"]
            else:
                entry["unexpected"] = "field"
            with self.subTest(mutation=mutation):
                self.assert_blocked(document, "keys are ambiguous")

    def test_test_identity_and_url_are_exact(self) -> None:
        for field, value in (
            ("testIdentifier", "testLaunchMetricBaselineRecorded()"),
            (
                "testIdentifierURL",
                "test://com.apple.xcode/OtherTarget/testLaunchMetricBaselineRecorded",
            ),
            ("testIdentifier", None),
            ("testIdentifierURL", True),
        ):
            document = valid_document()
            test_entry(document)[field] = value
            with self.subTest(field=field, value=value):
                self.assert_blocked(document)

    def test_test_runs_must_be_exact_singleton_array(self) -> None:
        original_run = copy.deepcopy(test_run(valid_document()))
        for value in (None, {}, [], [original_run, copy.deepcopy(original_run)]):
            document = valid_document()
            test_entry(document)["testRuns"] = value
            with self.subTest(value=value):
                self.assert_blocked(document)

    def test_test_run_rejects_missing_and_unexpected_keys(self) -> None:
        for mutation in ("missing", "unexpected"):
            document = valid_document()
            run = test_run(document)
            if mutation == "missing":
                del run["device"]
            else:
                run["unexpected"] = "field"
            with self.subTest(mutation=mutation):
                self.assert_blocked(document, "keys are ambiguous")

    def test_device_object_shape_and_strings_are_strict(self) -> None:
        document = valid_document()
        test_run(document)["device"] = "iPhone 16e"
        self.assert_blocked(document)

        for mutation in ("missing", "unexpected"):
            document = valid_document()
            current = device(document)
            if mutation == "missing":
                del current["deviceId"]
            else:
                current["platform"] = "iOS Simulator"
            with self.subTest(mutation=mutation):
                self.assert_blocked(document, "keys are ambiguous")

        for field, value in (
            ("deviceId", ""),
            ("deviceId", " id "),
            ("deviceName", None),
            ("deviceName", "iPhone\n16e"),
        ):
            document = valid_document()
            device(document)[field] = value
            with self.subTest(field=field, value=value):
                self.assert_blocked(document)

    def test_configuration_shape_and_values_are_strict(self) -> None:
        document = valid_document()
        test_run(document)["testPlanConfiguration"] = []
        self.assert_blocked(document)

        for mutation in ("missing", "unexpected"):
            document = valid_document()
            current = configuration(document)
            if mutation == "missing":
                del current["configurationId"]
            else:
                current["extra"] = "value"
            with self.subTest(mutation=mutation):
                self.assert_blocked(document, "keys are ambiguous")

        for field, value in (
            ("configurationId", "2"),
            ("configurationId", 1),
            ("configurationName", "Other Configuration"),
            ("configurationName", None),
        ):
            document = valid_document()
            configuration(document)[field] = value
            with self.subTest(field=field, value=value):
                self.assert_blocked(document)

    def test_metrics_must_be_exact_singleton_array(self) -> None:
        original_metric = copy.deepcopy(metric(valid_document()))
        for value in (None, {}, [], [original_metric, copy.deepcopy(original_metric)]):
            document = valid_document()
            test_run(document)["metrics"] = value
            with self.subTest(value=value):
                self.assert_blocked(document)

    def test_metric_rejects_each_missing_field_and_any_extra_field(self) -> None:
        expected_fields = tuple(metric(valid_document()))
        for field in expected_fields:
            document = valid_document()
            del metric(document)[field]
            with self.subTest(field=field):
                self.assert_blocked(document, "keys are ambiguous")

        document = valid_document()
        metric(document)["average"] = Decimal("2.0957314418")
        self.assert_blocked(document, "keys are ambiguous")

    def test_metric_identity_unit_and_polarity_are_exact(self) -> None:
        cases = (
            ("identifier", "com.apple.dt.XCTMetric_WallClockTime"),
            ("displayName", "Launch Duration"),
            ("polarity", "prefers larger"),
            ("unitOfMeasurement", "ms"),
            ("identifier", None),
            ("unitOfMeasurement", True),
        )
        for field, value in cases:
            document = valid_document()
            metric(document)[field] = value
            with self.subTest(field=field, value=value):
                self.assert_blocked(document)

    def test_baseline_metadata_values_and_numeric_types_are_exact(self) -> None:
        cases = (
            ("baselineAverage", Decimal("1")),
            ("baselineAverage", False),
            ("baselineName", "CI baseline"),
            ("maxPercentRegression", Decimal("11")),
            ("maxPercentRelativeStandardDeviation", Decimal("9")),
            ("maxRegression", Decimal("1")),
            ("maxStandardDeviation", Decimal("0")),
            ("maxStandardDeviation", "10"),
        )
        for field, value in cases:
            document = valid_document()
            metric(document)[field] = value
            with self.subTest(field=field, value=value):
                self.assert_blocked(document)


class MeasurementValidationTests(unittest.TestCase):
    def assert_measurements_block(self, values: object) -> None:
        document = valid_document()
        metric(document)["measurements"] = values
        with self.assertRaises(checker.EvidenceBlocked):
            checker.verify_metrics_document(document)

    def test_measurements_must_be_array_of_exactly_five(self) -> None:
        for values in (
            None,
            {},
            (),
            [],
            [Decimal("1")] * 4,
            [Decimal("1")] * 6,
        ):
            with self.subTest(values=values):
                self.assert_measurements_block(values)

    def test_non_numeric_and_boolean_samples_block(self) -> None:
        for value in (True, False, "1", None, {}, []):
            values = [Decimal("1")] * 5
            values[2] = value  # type: ignore[list-item]
            with self.subTest(value=value):
                self.assert_measurements_block(values)

    def test_nonfinite_nonpositive_and_negative_zero_samples_block(self) -> None:
        for value in (
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
            Decimal("0"),
            Decimal("-0"),
            Decimal("-0.001"),
        ):
            values = [Decimal("1")] * 5
            values[3] = value
            with self.subTest(value=value):
                self.assert_measurements_block(values)

    def test_decimal_overflow_while_computing_mean_blocks(self) -> None:
        self.assert_measurements_block([Decimal("9E+999999")] * 5)


class CommandLineStatusTests(unittest.TestCase):
    SCHEMA_BYTES = b'{"title":"Xcode 26.6 fixture schema"}\n'

    def run_main(
        self,
        metrics_bytes: bytes = VALID_METRICS_JSON,
        *,
        expected_digest: str | None = None,
        create_schema: bool = True,
        create_metrics: bool = True,
    ) -> tuple[int, str, str]:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            schema_path = root / "schema.json"
            metrics_path = root / "metrics.json"
            if create_schema:
                schema_path.write_bytes(self.SCHEMA_BYTES)
            if create_metrics:
                metrics_path.write_bytes(metrics_bytes)

            stdout = StringIO()
            stderr = StringIO()
            digest = (
                hashlib.sha256(self.SCHEMA_BYTES).hexdigest()
                if expected_digest is None
                else expected_digest
            )
            with (
                patch.object(checker, "EXPECTED_SCHEMA_SHA256", digest),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                status = checker.main(
                    [
                        "--metrics-json",
                        str(metrics_path),
                        "--schema-json",
                        str(schema_path),
                    ]
                )
            return status, stdout.getvalue(), stderr.getvalue()

    def test_pass_status_is_zero(self) -> None:
        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0)
        self.assertIn("G12 COLD-LAUNCH PASS", stdout)
        self.assertIn("2.0957314418", stdout)
        self.assertEqual(stderr, "")

    def test_fail_status_is_one_for_above_and_equal_budget(self) -> None:
        for samples, expected_mean in (
            (FAIL_MEASUREMENTS_JSON, "3.5316436"),
            (b"[3,3,3,3,3]", "3"),
        ):
            status, stdout, stderr = self.run_main(metrics_json_with(samples))
            with self.subTest(samples=samples):
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertIn("G12 COLD-LAUNCH FAIL", stderr)
                self.assertIn(expected_mean, stderr)

    def test_placeholder_status_is_two(self) -> None:
        status, stdout, stderr = self.run_main(
            expected_digest=checker.SCHEMA_SHA256_PLACEHOLDER
        )
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertIn("G12 COLD-LAUNCH BLOCKED", stderr)
        self.assertIn("placeholder", stderr)

    def test_digest_mismatch_status_is_two(self) -> None:
        status, _, stderr = self.run_main(expected_digest="0" * 64)
        self.assertEqual(status, 2)
        self.assertIn("mismatch", stderr)

    def test_missing_schema_or_metrics_file_status_is_two(self) -> None:
        for create_schema, create_metrics in ((False, True), (True, False)):
            status, _, stderr = self.run_main(
                create_schema=create_schema, create_metrics=create_metrics
            )
            with self.subTest(
                create_schema=create_schema, create_metrics=create_metrics
            ):
                self.assertEqual(status, 2)
                self.assertIn("G12 COLD-LAUNCH BLOCKED", stderr)

    def test_malformed_metrics_status_is_two(self) -> None:
        status, stdout, stderr = self.run_main(b"not JSON")
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertIn("G12 COLD-LAUNCH BLOCKED", stderr)

    def test_explicit_empty_argv_is_not_replaced_with_process_arguments(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as context:
            checker.main([])
        self.assertEqual(context.exception.code, 2)
        self.assertIn("--metrics-json", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
