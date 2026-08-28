from __future__ import annotations

import hashlib
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import analyze_launch_ab as analyzer
from scripts import check_launch_metrics as checker


SCHEMA_BYTES = b'{"title":"Xcode 26.6 fixture schema"}\n'
DEVICE_ID = "fixture-device"
DEVICE_NAME = "iPhone 16e"


def metrics_bytes(
    sample: str,
    *,
    device_id: str = DEVICE_ID,
    device_name: str = DEVICE_NAME,
    test_identifier: str = checker.EXPECTED_AB_TEST_IDENTIFIER,
    test_identifier_url: str = checker.EXPECTED_AB_TEST_IDENTIFIER_URL,
    samples: list[object] | None = None,
) -> bytes:
    document = [
        {
            "testIdentifier": test_identifier,
            "testIdentifierURL": test_identifier_url,
            "testRuns": [
                {
                    "device": {
                        "deviceId": device_id,
                        "deviceName": device_name,
                    },
                    "metrics": [
                        {
                            "baselineAverage": 0,
                            "baselineName": "",
                            "displayName": "Duration (AppLaunch)",
                            "identifier": (
                                "com.apple.dt.XCTMetric_ApplicationLaunch-"
                                "AppLaunch.duration"
                            ),
                            "maxPercentRegression": 10,
                            "maxPercentRelativeStandardDeviation": 10,
                            "maxRegression": 0,
                            "maxStandardDeviation": 10,
                            "measurements": (
                                [json.loads(sample)] if samples is None else samples
                            ),
                            "polarity": "prefers smaller",
                            "unitOfMeasurement": "s",
                        }
                    ],
                    "testPlanConfiguration": {
                        "configurationId": "1",
                        "configurationName": "Test Scheme Action",
                    },
                }
            ],
        }
    ]
    return (json.dumps(document, separators=(",", ":")) + "\n").encode("utf-8")


def order_document() -> dict[str, object]:
    return {
        "pairs": [
            {
                "pair": pair,
                "order": (
                    ["control", "linked"]
                    if pair % 2 == 1
                    else ["linked", "control"]
                ),
            }
            for pair in range(1, analyzer.PAIR_COUNT + 1)
        ]
    }


def write_valid_fixture(root: Path) -> dict[str, Path]:
    evidence_dir = root / "pairs"
    evidence_dir.mkdir()
    # Deltas are -1.0 through -0.2, then 0.2 through 1.0. Their median and
    # mean are zero and their MAD is 0.6.
    linked_samples = (
        "1.0",
        "1.2",
        "1.4",
        "1.6",
        "1.8",
        "2.2",
        "2.4",
        "2.6",
        "2.8",
        "3.0",
    )
    for pair, linked_sample in enumerate(linked_samples, start=1):
        (evidence_dir / f"pair-{pair:02d}-control.json").write_bytes(
            metrics_bytes("2.0")
        )
        (evidence_dir / f"pair-{pair:02d}-linked.json").write_bytes(
            metrics_bytes(linked_sample)
        )

    order_path = evidence_dir / "order.json"
    order_path.write_text(
        json.dumps(order_document(), separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    schema_path = root / "schema.json"
    schema_path.write_bytes(SCHEMA_BYTES)
    control_map = root / "control-LinkMap.txt"
    control_map.write_text(
        "# Object files:\n[ 1] TalariaApp.o\n",
        encoding="utf-8",
    )
    linked_map = root / "linked-LinkMap.txt"
    linked_map.write_text(
        "# Object files:\n"
        "[ 1] libHermesKit.a(HermesTransport.o)\n"
        "[ 2] libHermesKit.a(WebSocketNetworking.o)\n"
        "[ 3] libHermesKit.a(WebSocketHermesTransport.o)\n",
        encoding="utf-8",
    )
    return {
        "evidence_dir": evidence_dir,
        "order": order_path,
        "schema": schema_path,
        "control_map": control_map,
        "linked_map": linked_map,
        "output": root / "analysis.json",
    }


def argv(paths: dict[str, Path]) -> list[str]:
    return [
        "--evidence-dir",
        str(paths["evidence_dir"]),
        "--order-json",
        str(paths["order"]),
        "--schema-json",
        str(paths["schema"]),
        "--output-json",
        str(paths["output"]),
        "--control-link-map",
        str(paths["control_map"]),
        "--linked-link-map",
        str(paths["linked_map"]),
    ]


class OrderEvidenceTests(unittest.TestCase):
    def test_exact_alternating_order_is_accepted(self) -> None:
        raw = json.dumps(order_document()).encode("utf-8")
        orders = analyzer._parse_order_document(raw)
        self.assertEqual(len(orders), 10)
        self.assertEqual(orders[0], ("control", "linked"))
        self.assertEqual(orders[1], ("linked", "control"))

    def test_wrong_order_pair_number_count_or_shape_blocks(self) -> None:
        mutations: list[dict[str, object]] = []

        wrong_order = order_document()
        wrong_order["pairs"][4]["order"] = ["linked", "control"]  # type: ignore[index]
        mutations.append(wrong_order)

        wrong_number = order_document()
        wrong_number["pairs"][4]["pair"] = 6  # type: ignore[index]
        mutations.append(wrong_number)

        boolean_number = order_document()
        boolean_number["pairs"][0]["pair"] = True  # type: ignore[index]
        mutations.append(boolean_number)

        wrong_count = order_document()
        wrong_count["pairs"] = wrong_count["pairs"][:-1]  # type: ignore[index]
        mutations.append(wrong_count)

        extra_key = order_document()
        extra_key["unexpected"] = True
        mutations.append(extra_key)

        for document in mutations:
            with self.subTest(document=document), self.assertRaises(
                analyzer.AnalysisBlocked
            ):
                analyzer._parse_order_document(json.dumps(document).encode("utf-8"))

    def test_duplicate_json_key_blocks(self) -> None:
        with self.assertRaisesRegex(analyzer.AnalysisBlocked, "duplicate JSON key"):
            analyzer._parse_order_document(b'{"pairs":[],"pairs":[]}')


class LinkMapTests(unittest.TestCase):
    VALID_LINKED = (
        b"# Object files:\n"
        b"[1] libHermesKit.a(HermesTransport.o)\n"
        b"[2] libHermesKit.a(WebSocketNetworking.o)\n"
        b"[3] libHermesKit.a(WebSocketHermesTransport.o)\n"
        b"# Sections:\n"
    )

    def test_expected_object_presence_and_absence_are_accepted(self) -> None:
        analyzer.verify_link_maps(
            b"# Object files:\n[1] TalariaApp.o\n# Sections:\n",
            self.VALID_LINKED,
        )

    def test_mock_gateway_is_neither_required_nor_forbidden(self) -> None:
        analyzer.verify_link_maps(
            b"# Object files:\n[1] MockGateway.o\n# Sections:\n",
            self.VALID_LINKED.replace(
                b"# Sections:\n", b"[4] MockGateway.o\n# Sections:\n"
            ),
        )

    def test_transport_object_in_control_blocks(self) -> None:
        with self.assertRaisesRegex(analyzer.AnalysisBlocked, "unexpectedly"):
            analyzer.verify_link_maps(
                b"# Object files:\n[1] HermesTransport.o\n# Sections:\n",
                self.VALID_LINKED,
            )

    def test_each_missing_linked_object_blocks(self) -> None:
        for object_name in analyzer.TRANSPORT_OBJECT_NAMES:
            linked = self.VALID_LINKED.replace(
                f"{object_name}.o".encode("utf-8"), b"OtherObject.o"
            )
            with self.subTest(object_name=object_name), self.assertRaisesRegex(
                analyzer.AnalysisBlocked, "does not contain"
            ):
                analyzer.verify_link_maps(
                    b"# Object files:\n[1] TalariaApp.o\n# Sections:\n",
                    linked,
                )

    def test_substrings_do_not_count_as_object_names(self) -> None:
        deceptive = self.VALID_LINKED.replace(
            b"HermesTransport.o", b"NotHermesTransport.oops"
        )
        with self.assertRaises(analyzer.AnalysisBlocked):
            analyzer.verify_link_maps(
                b"# Object files:\n[1] TalariaApp.o\n# Sections:\n",
                deceptive,
            )

    def test_names_outside_object_section_do_not_count(self) -> None:
        deceptive = (
            b"# Object files:\n[1] TalariaApp.o\n# Sections:\n"
            b"# HermesTransport.o WebSocketNetworking.o "
            b"WebSocketHermesTransport.o\n"
        )
        with self.assertRaises(analyzer.AnalysisBlocked):
            analyzer.verify_link_maps(
                b"# Object files:\n[1] TalariaApp.o\n# Sections:\n",
                deceptive,
            )

    def test_empty_invalid_utf8_and_nul_maps_block(self) -> None:
        for raw in (b"", b" \n", b"\xff", b"map\x00text"):
            with self.subTest(raw=raw), self.assertRaises(analyzer.AnalysisBlocked):
                analyzer.verify_link_maps(raw, self.VALID_LINKED)


class PairedEvidenceTests(unittest.TestCase):
    def test_exact_ten_pairs_produce_expected_verdict_free_statistics(self) -> None:
        with TemporaryDirectory() as directory:
            paths = write_valid_fixture(Path(directory))
            orders = analyzer._parse_order_document(paths["order"].read_bytes())
            observations = analyzer.load_observations(paths["evidence_dir"], orders)
            result = analyzer.summarize(observations)

        self.assertEqual(result["pair_count"], 10)
        self.assertEqual(
            result["delta_definition"], "linked_seconds_minus_control_seconds"
        )
        summary = result["summary"]
        self.assertEqual(summary["median_delta_seconds"], "0.0")  # type: ignore[index]
        self.assertEqual(summary["mad_delta_seconds"], "0.6")  # type: ignore[index]
        self.assertEqual(summary["mean_delta_seconds"], "0.0")  # type: ignore[index]
        self.assertEqual(  # type: ignore[index]
            summary["sign_count"], {"negative": 5, "positive": 5, "zero": 0}
        )
        pairs = result["pairs"]
        self.assertEqual(pairs[0]["control_seconds"], "2.0")  # type: ignore[index]
        self.assertEqual(pairs[0]["linked_seconds"], "1.0")  # type: ignore[index]
        self.assertNotIn("pass", json.dumps(result).lower())
        self.assertNotIn("threshold", json.dumps(result).lower())

    def test_missing_or_unexpected_pair_file_blocks(self) -> None:
        for mutation in ("missing", "unexpected"):
            with TemporaryDirectory() as directory:
                paths = write_valid_fixture(Path(directory))
                evidence_dir = paths["evidence_dir"]
                if mutation == "missing":
                    (evidence_dir / "pair-10-linked.json").unlink()
                else:
                    (evidence_dir / "pair-11-control.json").write_bytes(
                        metrics_bytes("2.0")
                    )
                orders = analyzer._parse_order_document(paths["order"].read_bytes())
                with self.subTest(mutation=mutation), self.assertRaises(
                    analyzer.AnalysisBlocked
                ):
                    analyzer.load_observations(evidence_dir, orders)

    def test_wrong_identity_or_sample_count_blocks(self) -> None:
        mutations = (
            metrics_bytes(
                "2.0", test_identifier="OtherTests/testOneLaunchObservation()"
            ),
            metrics_bytes("2.0", samples=[2.0, 2.1]),
        )
        for raw in mutations:
            with TemporaryDirectory() as directory:
                paths = write_valid_fixture(Path(directory))
                (paths["evidence_dir"] / "pair-03-control.json").write_bytes(raw)
                orders = analyzer._parse_order_document(paths["order"].read_bytes())
                with self.subTest(raw=raw), self.assertRaises(
                    analyzer.AnalysisBlocked
                ):
                    analyzer.load_observations(paths["evidence_dir"], orders)

    def test_device_mismatch_blocks_without_disclosing_identity(self) -> None:
        with TemporaryDirectory() as directory:
            paths = write_valid_fixture(Path(directory))
            private_device = "private-device"
            (paths["evidence_dir"] / "pair-07-linked.json").write_bytes(
                metrics_bytes("2.4", device_id=private_device)
            )
            orders = analyzer._parse_order_document(paths["order"].read_bytes())
            with self.assertRaises(analyzer.AnalysisBlocked) as context:
                analyzer.load_observations(paths["evidence_dir"], orders)
            self.assertNotIn(private_device, str(context.exception))

    def test_statistics_reject_wrong_pair_count_and_nonfinite_delta(self) -> None:
        observation = analyzer.PairObservation(
            1, ("control", "linked"), Decimal("1"), Decimal("2")
        )
        with self.assertRaises(analyzer.AnalysisBlocked):
            analyzer.summarize((observation,))

        invalid = tuple(
            analyzer.PairObservation(
                pair,
                ("control", "linked"),
                Decimal("1"),
                Decimal("Infinity") if pair == 5 else Decimal("2"),
            )
            for pair in range(1, 11)
        )
        with self.assertRaises(analyzer.AnalysisBlocked):
            analyzer.summarize(invalid)


class CommandLineTests(unittest.TestCase):
    def run_main(
        self, paths: dict[str, Path], *, digest: str | None = None
    ) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        expected_digest = digest or hashlib.sha256(SCHEMA_BYTES).hexdigest()
        with (
            patch.object(checker, "EXPECTED_SCHEMA_SHA256", expected_digest),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            status = analyzer.main(argv(paths))
        return status, stdout.getvalue(), stderr.getvalue()

    def test_valid_analysis_writes_sanitized_observation_without_verdict(self) -> None:
        with TemporaryDirectory() as directory:
            paths = write_valid_fixture(Path(directory))
            status, stdout, stderr = self.run_main(paths)
            output = paths["output"].read_text(encoding="utf-8")

        self.assertEqual(status, 0)
        self.assertIn("G12 LINK A/B OBSERVED:", stdout)
        self.assertNotIn("PASS", stdout)
        self.assertNotIn("FAIL", stdout)
        self.assertNotIn("GREEN", stdout)
        self.assertEqual(stderr, "")
        self.assertNotIn(DEVICE_ID, output)
        self.assertNotIn(DEVICE_NAME, output)
        parsed = json.loads(output)
        self.assertEqual(len(parsed["pairs"]), 10)
        self.assertEqual(parsed["pairs"][0]["control_seconds"], "2.0")
        self.assertEqual(parsed["pairs"][0]["linked_seconds"], "1.0")
        self.assertEqual(parsed["pairs"][0]["delta_seconds"], "-1.0")

    def test_schema_mismatch_or_malformed_pair_blocks_and_writes_no_output(self) -> None:
        for mutation in ("schema", "metrics"):
            with TemporaryDirectory() as directory:
                paths = write_valid_fixture(Path(directory))
                digest = None
                if mutation == "schema":
                    digest = "0" * 64
                else:
                    (paths["evidence_dir"] / "pair-01-control.json").write_text(
                        "not JSON", encoding="utf-8"
                    )
                status, stdout, stderr = self.run_main(paths, digest=digest)
                with self.subTest(mutation=mutation):
                    self.assertEqual(status, 2)
                    self.assertEqual(stdout, "")
                    self.assertIn("G12 LINK A/B BLOCKED:", stderr)
                    self.assertFalse(paths["output"].exists())

    def test_unwritable_output_blocks(self) -> None:
        with TemporaryDirectory() as directory:
            paths = write_valid_fixture(Path(directory))
            paths["output"].mkdir()
            status, stdout, stderr = self.run_main(paths)
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertIn("could not be written", stderr)

    def test_explicit_empty_argv_is_not_replaced_with_process_arguments(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as context:
            analyzer.main([])
        self.assertEqual(context.exception.code, 2)
        self.assertIn("--evidence-dir", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
