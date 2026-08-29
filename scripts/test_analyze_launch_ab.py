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
    control_symbols = root / "control-symbols.txt"
    control_symbols.write_text(
        "_$s9HermesKit9WireCodecVN\n_talaria_launch_link_anchor\n",
        encoding="utf-8",
    )
    linked_symbols = root / "linked-symbols.txt"
    linked_symbols.write_text(
        "HermesKit.WebSocketHermesTransport.__allocating_init(configuration: "
        "HermesKit.HermesWebSocketConfiguration, tokenProvider: "
        "HermesKit.HermesBearerTokenProvider) -> "
        "HermesKit.WebSocketHermesTransport\n"
        "protocol witness for HermesKit.HermesHTTPDataLoading.data(for: "
        "Foundation.URLRequest) in conformance HermesKit.URLSessionHTTPDataLoader\n"
        "protocol witness for HermesKit.HermesWebSocketTicketAcquiring."
        "acquireTicket() in conformance HermesKit.URLSessionWebSocketTicketAcquirer\n"
        "protocol witness for HermesKit.HermesWebSocketConnecting.connect(to: "
        "Foundation.URL) in conformance HermesKit.URLSessionWebSocketConnector\n"
        "_talaria_launch_link_anchor\n"
        "_talaria_transport_factory_link_anchor\n",
        encoding="utf-8",
    )
    link_collection_status = root / "link-collection-status.txt"
    link_collection_status.write_bytes(b"complete\n")
    measurement_collection_status = root / "measurement-collection-status.txt"
    measurement_collection_status.write_bytes(b"complete\n")
    return {
        "evidence_dir": evidence_dir,
        "link_collection_status": link_collection_status,
        "measurement_collection_status": measurement_collection_status,
        "order": order_path,
        "schema": schema_path,
        "control_symbols": control_symbols,
        "linked_symbols": linked_symbols,
        "link_output": root / "link-preflight.json",
        "output": root / "analysis.json",
    }


def argv(paths: dict[str, Path]) -> list[str]:
    return [
        "render",
        "--evidence-dir",
        str(paths["evidence_dir"]),
        "--link-collection-status",
        str(paths["link_collection_status"]),
        "--measurement-collection-status",
        str(paths["measurement_collection_status"]),
        "--order-json",
        str(paths["order"]),
        "--schema-json",
        str(paths["schema"]),
        "--output-json",
        str(paths["output"]),
        "--control-symbols",
        str(paths["control_symbols"]),
        "--linked-symbols",
        str(paths["linked_symbols"]),
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


class LinkSymbolTests(unittest.TestCase):
    def valid_symbols(self) -> tuple[bytes, bytes]:
        with TemporaryDirectory() as directory:
            paths = write_valid_fixture(Path(directory))
            return (
                paths["control_symbols"].read_bytes(),
                paths["linked_symbols"].read_bytes(),
            )

    def test_expected_semantic_presence_and_absence_are_accepted(self) -> None:
        control, linked = self.valid_symbols()
        result = analyzer.verify_link_symbols(control, linked)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(set(result["checks"].values()), {True})  # type: ignore[union-attr]
        match_counts = result["match_counts"]
        self.assertIsInstance(match_counts, dict)
        for name in analyzer.LINK_MATCH_COUNT_NAMES:
            expected_control = 1 if name == "launch_anchor" else 0
            self.assertEqual(match_counts[name]["control"], expected_control)  # type: ignore[index]
            self.assertEqual(match_counts[name]["linked"], 1)  # type: ignore[index]

    def test_present_in_both_variants_is_distinct_from_missing_in_linked(self) -> None:
        control, linked = self.valid_symbols()
        semantic_lines = [
            line
            for line in linked.splitlines(keepends=True)
            if any(
                line.decode("utf-8").startswith(prefix)
                and all(token in line.decode("utf-8") for token in tokens)
                for prefix, tokens in analyzer.SEMANTIC_SYMBOL_SPECS.values()
            )
        ]
        result = analyzer.summarize_link_symbols(control + b"".join(semantic_lines), linked)

        self.assertEqual(result["status"], "blocked")
        match_counts = result["match_counts"]
        for name in analyzer.SEMANTIC_SYMBOL_SPECS:
            self.assertEqual(match_counts[name], {"control": 1, "linked": 1})  # type: ignore[index]
            self.assertFalse(result["checks"][name])  # type: ignore[index]

    def test_each_required_linked_semantic_symbol_blocks_independently(self) -> None:
        control, linked = self.valid_symbols()
        for check_name, (prefix, _) in analyzer.SEMANTIC_SYMBOL_SPECS.items():
            mutated = linked.replace(prefix.encode("utf-8"), b"missing", 1)
            result = analyzer.summarize_link_symbols(control, mutated)
            self.assertEqual(
                result["match_counts"][check_name],  # type: ignore[index]
                {"control": 0, "linked": 0},
            )
            with self.subTest(check=check_name), self.assertRaises(
                analyzer.AnalysisBlocked
            ):
                analyzer.verify_link_symbols(control, mutated)

    def test_each_transport_only_symbol_in_control_blocks(self) -> None:
        control, linked = self.valid_symbols()
        linked_lines = linked.decode("utf-8").splitlines()
        transport_lines = [
            line
            for line in linked_lines
            if line == analyzer.TRANSPORT_FACTORY_SYMBOL
            or any(
                line.startswith(prefix) and all(token in line for token in tokens)
                for prefix, tokens in analyzer.SEMANTIC_SYMBOL_SPECS.values()
            )
        ]
        for line in transport_lines:
            mutated = control + line.encode("utf-8") + b"\n"
            result = analyzer.summarize_link_symbols(mutated, linked)
            if line == analyzer.TRANSPORT_FACTORY_SYMBOL:
                count_name = analyzer.LINK_MATCH_COUNT_NAMES[1]
            else:
                count_name = next(
                    name
                    for name, (prefix, tokens) in analyzer.SEMANTIC_SYMBOL_SPECS.items()
                    if line.startswith(prefix) and all(token in line for token in tokens)
                )
            self.assertEqual(
                result["match_counts"][count_name],  # type: ignore[index]
                {"control": 1, "linked": 1},
            )
            with self.subTest(line=line), self.assertRaises(analyzer.AnalysisBlocked):
                analyzer.verify_link_symbols(mutated, linked)

    def test_duplicate_primary_match_is_reported_as_ambiguous(self) -> None:
        control, linked = self.valid_symbols()
        linked_lines = linked.decode("utf-8").splitlines()
        primary_lines = {
            "launch_anchor": analyzer.LAUNCH_LINK_SYMBOL,
            "transport_factory_anchor": analyzer.TRANSPORT_FACTORY_SYMBOL,
        }
        for name, (prefix, tokens) in analyzer.SEMANTIC_SYMBOL_SPECS.items():
            primary_lines[name] = next(
                line
                for line in linked_lines
                if line.startswith(prefix) and all(token in line for token in tokens)
            )
        for name, line in primary_lines.items():
            mutated = linked + line.encode("utf-8") + b"\n"
            result = analyzer.summarize_link_symbols(control, mutated)
            with self.subTest(name=name):
                self.assertEqual(
                    result["match_counts"][name],  # type: ignore[index]
                    {
                        "control": 1 if name == "launch_anchor" else 0,
                        "linked": 2,
                    },
                )
                self.assertEqual(result["status"], "blocked")

    def test_c_marker_substring_does_not_count(self) -> None:
        control, linked = self.valid_symbols()
        deceptive = linked.replace(
            analyzer.TRANSPORT_FACTORY_SYMBOL.encode("utf-8"),
            (analyzer.TRANSPORT_FACTORY_SYMBOL + "_lookalike").encode("utf-8"),
        )
        with self.assertRaises(analyzer.AnalysisBlocked):
            analyzer.verify_link_symbols(control, deceptive)

    def test_metadata_or_concrete_method_does_not_count_as_executable_proof(self) -> None:
        control, linked = self.valid_symbols()
        metadata_only = linked.replace(b".__allocating_init(", b".type metadata (")
        concrete_method = linked.replace(b"protocol witness for", b"concrete method for")
        for mutated in (metadata_only, concrete_method):
            with self.subTest(mutated=mutated), self.assertRaises(
                analyzer.AnalysisBlocked
            ):
                analyzer.verify_link_symbols(control, mutated)

    def test_adapter_type_name_outside_conformance_clause_does_not_count(self) -> None:
        control, linked = self.valid_symbols()
        conformance_tokens = [
            tokens[0]
            for _, tokens in analyzer.SEMANTIC_SYMBOL_SPECS.values()
            if tokens[0].startswith("in conformance ")
        ]
        self.assertEqual(len(conformance_tokens), 3)
        for token in conformance_tokens:
            deceptive = token.replace("in conformance ", "mentions ", 1)
            mutated = linked.replace(
                token.encode("utf-8"), deceptive.encode("utf-8"), 1
            )
            with self.subTest(token=token), self.assertRaises(
                analyzer.AnalysisBlocked
            ):
                analyzer.verify_link_symbols(control, mutated)

    def test_descriptors_resume_partials_and_async_pointers_are_not_ambiguous(self) -> None:
        control, linked = self.valid_symbols()
        primary_lines = [
            line
            for line in linked.decode("utf-8").splitlines()
            if any(
                line.startswith(prefix) and all(token in line for token in tokens)
                for prefix, tokens in analyzer.SEMANTIC_SYMBOL_SPECS.values()
            )
        ]
        extras = []
        for line in primary_lines:
            extras.append("method descriptor for " + line)
            if line.startswith("protocol witness for "):
                extras.append("(1) await resume partial function for " + line)
                extras.append("async function pointer to " + line)
        with_extras = linked + ("\n".join(extras) + "\n").encode("utf-8")
        result = analyzer.verify_link_symbols(control, with_extras)
        self.assertEqual(result["status"], "verified")

        duplicate_primary = linked + (primary_lines[0] + "\n").encode("utf-8")
        with self.assertRaises(analyzer.AnalysisBlocked):
            analyzer.verify_link_symbols(control, duplicate_primary)

    def test_empty_invalid_utf8_and_nul_symbol_evidence_blocks(self) -> None:
        _, linked = self.valid_symbols()
        for raw in (b"", b" \n", b"\xff", b"symbol\x00name"):
            with self.subTest(raw=raw), self.assertRaises(analyzer.AnalysisBlocked):
                analyzer.verify_link_symbols(raw, linked)


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

    def run_enforce(self, path: Path) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = analyzer.main(["enforce", "--input-json", str(path)])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_preflight_retains_then_enforces_valid_and_blocked_symbols(self) -> None:
        with TemporaryDirectory() as directory:
            paths = write_valid_fixture(Path(directory))
            render_command = [
                "render-link",
                "--collection-status",
                str(paths["link_collection_status"]),
                "--control-symbols",
                str(paths["control_symbols"]),
                "--linked-symbols",
                str(paths["linked_symbols"]),
                "--output-json",
                str(paths["link_output"]),
            ]
            enforce_command = [
                "enforce-link",
                "--input-json",
                str(paths["link_output"]),
            ]
            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                render_status = analyzer.main(render_command)
                enforce_status = analyzer.main(enforce_command)
            valid_output = paths["link_output"].read_text(encoding="utf-8")

            paths["linked_symbols"].write_text(
                "_talaria_launch_link_anchor\n", encoding="utf-8"
            )
            blocked_stdout = StringIO()
            blocked_stderr = StringIO()
            with redirect_stdout(blocked_stdout), redirect_stderr(blocked_stderr):
                blocked_render_status = analyzer.main(render_command)
                blocked_enforce_status = analyzer.main(enforce_command)
            blocked_output = paths["link_output"].read_text(encoding="utf-8")

        self.assertEqual(render_status, 0)
        self.assertEqual(enforce_status, 0)
        self.assertIn("status=verified", stdout.getvalue())
        self.assertIn("LINKAGE VERIFIED", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn("HermesKit.", valid_output)
        self.assertNotIn("_talaria_", valid_output)
        self.assertEqual(blocked_render_status, 0)
        self.assertEqual(blocked_enforce_status, 2)
        self.assertIn("status=blocked", blocked_stdout.getvalue())
        self.assertIn("G12 LINK A/B BLOCKED:", blocked_stderr.getvalue())
        self.assertEqual(json.loads(blocked_output)["status"], "blocked")

    def test_link_collection_failure_is_sanitized_before_enforcement(self) -> None:
        with TemporaryDirectory() as directory:
            paths = write_valid_fixture(Path(directory))
            paths["link_collection_status"].write_bytes(
                b"control_symbol_inventory_failed\n"
            )
            render_command = [
                "render-link",
                "--collection-status",
                str(paths["link_collection_status"]),
                "--control-symbols",
                str(paths["control_symbols"]),
                "--linked-symbols",
                str(paths["linked_symbols"]),
                "--output-json",
                str(paths["link_output"]),
            ]
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                render_status = analyzer.main(render_command)
                enforce_status = analyzer.main(
                    ["enforce-link", "--input-json", str(paths["link_output"])]
                )
            document = json.loads(paths["link_output"].read_text(encoding="utf-8"))

        self.assertEqual(render_status, 0)
        self.assertEqual(enforce_status, 2)
        self.assertEqual(document["status"], "blocked")
        self.assertEqual(
            document["link_contrast"]["blocker_code"],
            "control_symbol_inventory_failed",
        )
        self.assertEqual(set(document["link_contrast"]["checks"].values()), {None})
        self.assertIsNone(document["link_contrast"]["match_counts"])

    def test_observation_failure_is_sanitized_before_enforcement(self) -> None:
        with TemporaryDirectory() as directory:
            paths = write_valid_fixture(Path(directory))
            paths["measurement_collection_status"].write_bytes(
                b"linked_metrics_export_failed:04\n"
            )
            status, stdout, stderr = self.run_main(paths)
            enforce_status, _, enforce_stderr = self.run_enforce(paths["output"])
            document = json.loads(paths["output"].read_text(encoding="utf-8"))

        self.assertEqual(status, 0)
        self.assertIn("status=blocked", stdout)
        self.assertEqual(stderr, "")
        self.assertEqual(enforce_status, 2)
        self.assertIn("G12 LINK A/B BLOCKED:", enforce_stderr)
        self.assertEqual(
            document["measurements"]["blocker_code"],
            "linked_metrics_export_failed",
        )
        self.assertEqual(document["measurements"]["blocker_pair"], 4)
        self.assertEqual(document["measurements"]["pairs"], [])

    def test_valid_analysis_writes_sanitized_observation_without_verdict(self) -> None:
        with TemporaryDirectory() as directory:
            paths = write_valid_fixture(Path(directory))
            status, stdout, stderr = self.run_main(paths)
            output = paths["output"].read_text(encoding="utf-8")
            enforce_status, enforce_stdout, enforce_stderr = self.run_enforce(
                paths["output"]
            )

        self.assertEqual(status, 0)
        self.assertIn("G12 LINK A/B EVIDENCE RETAINED: status=observed", stdout)
        self.assertNotIn("PASS", stdout)
        self.assertNotIn("FAIL", stdout)
        self.assertNotIn("GREEN", stdout)
        self.assertEqual(stderr, "")
        self.assertEqual(enforce_status, 0)
        self.assertIn("G12 LINK A/B OBSERVED:", enforce_stdout)
        self.assertEqual(enforce_stderr, "")
        self.assertNotIn(DEVICE_ID, output)
        self.assertNotIn(DEVICE_NAME, output)
        parsed = json.loads(output)
        self.assertEqual(parsed["status"], "observed")
        self.assertEqual(parsed["link_contrast"]["status"], "verified")
        self.assertEqual(set(parsed["link_contrast"]["checks"].values()), {True})
        self.assertEqual(
            parsed["link_contrast"]["match_counts"]["transport_factory_anchor"],
            {"control": 0, "linked": 1},
        )
        pairs = parsed["measurements"]["pairs"]
        self.assertEqual(len(pairs), 10)
        self.assertEqual(pairs[0]["control_seconds"], "2.0")
        self.assertEqual(pairs[0]["linked_seconds"], "1.0")
        self.assertEqual(pairs[0]["delta_seconds"], "-1.0")
        self.assertNotIn("HermesKit.", output)
        self.assertNotIn("_talaria_", output)

    def test_schema_mismatch_or_malformed_pair_retains_blocked_evidence(self) -> None:
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
                output = paths["output"].read_text(encoding="utf-8")
                enforce_status, enforce_stdout, enforce_stderr = self.run_enforce(
                    paths["output"]
                )
                with self.subTest(mutation=mutation):
                    self.assertEqual(status, 0)
                    self.assertIn("status=blocked", stdout)
                    self.assertEqual(stderr, "")
                    self.assertEqual(enforce_status, 2)
                    self.assertEqual(enforce_stdout, "")
                    self.assertIn("G12 LINK A/B BLOCKED:", enforce_stderr)
                    parsed = json.loads(output)
                    self.assertEqual(parsed["status"], "blocked")
                    self.assertEqual(parsed["measurements"]["pairs"], [])

    def test_link_failure_retains_all_measurements_before_enforcement_blocks(self) -> None:
        with TemporaryDirectory() as directory:
            paths = write_valid_fixture(Path(directory))
            paths["linked_symbols"].write_text(
                "_talaria_launch_link_anchor\n", encoding="utf-8"
            )
            status, stdout, stderr = self.run_main(paths)
            output = paths["output"].read_text(encoding="utf-8")
            enforce_status, _, enforce_stderr = self.run_enforce(paths["output"])

        self.assertEqual(status, 0)
        self.assertIn("status=blocked", stdout)
        self.assertEqual(stderr, "")
        parsed = json.loads(output)
        self.assertEqual(parsed["measurements"]["validated_pair_count"], 10)
        self.assertEqual(len(parsed["measurements"]["pairs"]), 10)
        self.assertEqual(parsed["link_contrast"]["status"], "blocked")
        self.assertEqual(enforce_status, 2)
        self.assertIn("G12 LINK A/B BLOCKED:", enforce_stderr)

    def test_tampered_or_unknown_analysis_evidence_blocks_enforcement(self) -> None:
        for mutation in (
            "old_schema",
            "root_status",
            "nested_status",
            "check_type",
            "match_count",
            "missing_match_name",
            "extra_variant",
            "negative_count",
            "boolean_count",
            "count_exceeds_inventory",
            "disjoint_sum_exceeds_inventory",
            "zero_inventories",
            "unknown_key",
        ):
            with TemporaryDirectory() as directory:
                paths = write_valid_fixture(Path(directory))
                status, _, _ = self.run_main(paths)
                self.assertEqual(status, 0)
                document = json.loads(paths["output"].read_text(encoding="utf-8"))
                if mutation == "old_schema":
                    document["schema_version"] = 1
                elif mutation == "root_status":
                    document["status"] = "blocked"
                elif mutation == "nested_status":
                    document["link_contrast"]["status"] = "blocked"
                elif mutation == "check_type":
                    document["link_contrast"]["checks"][
                        "control_has_one_launch_anchor"
                    ] = []
                elif mutation == "match_count":
                    document["link_contrast"]["match_counts"][
                        "transport_initializer_control_absent_linked_present"
                    ]["linked"] = 0
                elif mutation == "missing_match_name":
                    del document["link_contrast"]["match_counts"]["launch_anchor"]
                elif mutation == "extra_variant":
                    document["link_contrast"]["match_counts"]["launch_anchor"][
                        "unexpected"
                    ] = 0
                elif mutation == "negative_count":
                    document["link_contrast"]["match_counts"]["launch_anchor"][
                        "control"
                    ] = -1
                elif mutation == "boolean_count":
                    document["link_contrast"]["match_counts"]["launch_anchor"][
                        "control"
                    ] = True
                elif mutation == "count_exceeds_inventory":
                    document["link_contrast"]["match_counts"]["launch_anchor"][
                        "control"
                    ] = document["link_contrast"]["control_symbol_count"] + 1
                elif mutation == "disjoint_sum_exceeds_inventory":
                    document["link_contrast"]["linked_symbol_count"] = 1
                elif mutation == "zero_inventories":
                    document["status"] = "blocked"
                    contrast = document["link_contrast"]
                    contrast["status"] = "blocked"
                    contrast["blocker_code"] = "link_contrast_not_established"
                    contrast["control_symbol_count"] = 0
                    contrast["linked_symbol_count"] = 0
                    for counts in contrast["match_counts"].values():
                        counts["control"] = 0
                        counts["linked"] = 0
                    contrast["checks"] = {
                        "control_has_one_launch_anchor": False,
                        "linked_has_one_launch_anchor": False,
                        "control_omits_transport_factory_anchor": True,
                        "linked_has_one_transport_factory_anchor": False,
                        "transport_initializer_control_absent_linked_present": False,
                        "http_loader_witness_control_absent_linked_present": False,
                        "ticket_acquirer_witness_control_absent_linked_present": False,
                        "websocket_connector_witness_control_absent_linked_present": False,
                    }
                else:
                    document["unexpected"] = True
                paths["output"].write_text(
                    json.dumps(document) + "\n", encoding="utf-8"
                )
                enforce_status, enforce_stdout, enforce_stderr = self.run_enforce(
                    paths["output"]
                )
            with self.subTest(mutation=mutation):
                self.assertEqual(enforce_status, 2)
                self.assertEqual(enforce_stdout, "")
                self.assertIn("G12 LINK A/B BLOCKED:", enforce_stderr)

    def test_unwritable_output_blocks(self) -> None:
        with TemporaryDirectory() as directory:
            paths = write_valid_fixture(Path(directory))
            paths["output"].mkdir()
            status, stdout, stderr = self.run_main(paths)
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertIn("could not be retained", stderr)

    def test_explicit_empty_argv_is_not_replaced_with_process_arguments(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as context:
            analyzer.main([])
        self.assertEqual(context.exception.code, 2)
        self.assertIn("render-link", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
