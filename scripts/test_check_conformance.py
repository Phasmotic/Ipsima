from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts import check_conformance as checker


REPO = pathlib.Path(__file__).resolve().parent.parent
GAUNTLET = REPO / "scripts" / "gauntlet.sh"
GENERATOR = REPO / "scripts" / "gen_conformance_tests.py"
OUTPUT_NAMES = (
    "ProtocolConformanceTests.swift",
    "ProtocolRequestConformanceTests1.swift",
    "ProtocolRequestConformanceTests2.swift",
    "ProtocolRequestConformanceTests3.swift",
    "ProtocolRequestConformanceTests4.swift",
    "ProtocolEventConformanceTests.swift",
)
VALID_SANITIZED_REQUEST = (
    '{"id":1,"jsonrpc":"2.0","method":"session.list"}'
)


class CheckConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="talaria-g3-test-")
        self.repo = pathlib.Path(self.temporary.name)
        scripts = self.repo / "scripts"
        protocol = self.repo / "protocol"
        self.tests = (
            self.repo / "Packages" / "HermesKit" / "Tests" / "HermesKitTests"
        )
        self.fixtures = self.tests / "Fixtures"
        scripts.mkdir(parents=True)
        protocol.mkdir(parents=True)
        self.fixtures.mkdir(parents=True)

        shutil.copy2(REPO / "scripts" / "check_conformance.py", scripts)
        shutil.copy2(REPO / "scripts" / "derive_protocol.py", scripts)
        shutil.copy2(GENERATOR, scripts)
        self.valid_catalog_text = (REPO / "protocol" / "methods.json").read_text(
            encoding="utf-8"
        )
        self.catalog = protocol / "methods.json"
        self.catalog.write_text(self.valid_catalog_text, encoding="utf-8")
        self.generated_request = self.tests / OUTPUT_NAMES[0]
        self.generated_request_test = self.tests / OUTPUT_NAMES[1]
        self.generated_event = self.tests / OUTPUT_NAMES[-1]
        generated = self.run_generator()
        if generated.returncode != 0:
            raise AssertionError(generated.stdout + generated.stderr)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_generator(
        self,
        *,
        catalog: pathlib.Path | None = None,
        output_directory: pathlib.Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(self.repo / "scripts" / "gen_conformance_tests.py"),
                "--catalog",
                str(catalog or self.catalog),
                "--output-directory",
                str(output_directory or self.tests),
            ],
            capture_output=True,
            text=True,
            cwd=self.repo,
        )

    def run_checker(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(self.repo / "scripts" / "check_conformance.py")],
            capture_output=True,
            text=True,
            cwd=self.repo,
        )

    def write_fixture(self, line: str) -> None:
        self.write_fixtures(line)

    def write_fixtures(self, *lines: str) -> None:
        (self.fixtures / "golden.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def write_fixture_objects(self, *frames: object) -> None:
        self.write_fixtures(
            *(
                json.dumps(
                    frame,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                for frame in frames
            )
        )

    def test_valid_baseline_and_cross_namespace_collision_pass(self) -> None:
        self.write_fixture(VALID_SANITIZED_REQUEST)
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("catalog requests: 168", result.stdout)
        self.assertIn("catalog events  : 56", result.stdout)
        self.assertIn("generated tests : 224", result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], "G3: PASS")

    def test_canonical_sanitized_request_event_and_response_pass(self) -> None:
        self.write_fixture_objects(
            {
                "id": 1,
                "jsonrpc": "2.0",
                "method": "session.list",
                "params": {
                    "field_001": "<redacted>",
                    "field_002": 0,
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "payload": {
                        "field_001": "<redacted>",
                        "field_002": [False, 0, 0.5, None],
                    },
                    "type": "message.delta",
                },
            },
            {
                "id": 1,
                "jsonrpc": "2.0",
                "result": {
                    "field_001": "<redacted>",
                    "field_002": 0.5,
                },
            },
        )

        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("golden frames   : 3", result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], "G3: PASS")

    def test_manually_placed_unknown_members_fail_residual_safety_contract(self) -> None:
        cases = (
            (
                {
                    "jsonrpc": "2.0",
                    "method": "session.list",
                    "operator_member": "<redacted>",
                },
                "unknown top-level members were not removed",
            ),
            (
                {
                    "jsonrpc": "2.0",
                    "method": "session.list",
                    "params": {"operator_member": "<redacted>"},
                },
                "object field names were not normalized",
            ),
            (
                {
                    "error": {
                        "code": -32000,
                        "message": "<redacted>",
                        "operator_member": "<redacted>",
                    },
                    "id": 1,
                    "jsonrpc": "2.0",
                },
                "unknown error members were not removed",
            ),
        )
        for frame, reason in cases:
            with self.subTest(reason=reason):
                self.write_fixture_objects(frame)
                result = self.run_checker()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("unsafe golden fixture", result.stdout)
                self.assertIn(reason, result.stdout)
                self.assertEqual(result.stdout.splitlines()[-1], "G3: FAIL")

    def test_manually_placed_unsanitized_and_non_normalized_leaves_fail(self) -> None:
        cases = (
            ("operator text", "text payload was not redacted"),
            (True, "boolean payload was not normalized"),
            (7, "integer payload was not normalized"),
            (1.25, "floating payload was not normalized"),
        )
        for value, reason in cases:
            with self.subTest(reason=reason):
                self.write_fixture_objects(
                    {
                        "jsonrpc": "2.0",
                        "method": "session.list",
                        "params": {"field_001": value},
                    }
                )
                result = self.run_checker()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("unsafe golden fixture", result.stdout)
                self.assertIn(reason, result.stdout)
                self.assertEqual(result.stdout.splitlines()[-1], "G3: FAIL")

    def test_manual_unknown_method_and_event_type_fail_catalog_contract(self) -> None:
        cases = (
            (
                {"jsonrpc": "2.0", "method": "operator.private"},
                "request method is outside the pinned catalog",
            ),
            (
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {
                        "payload": {"field_001": "<redacted>"},
                        "type": "operator.private",
                    },
                },
                "server event type is outside the pinned catalog",
            ),
        )
        for frame, reason in cases:
            with self.subTest(reason=reason):
                self.write_fixture_objects(frame)
                result = self.run_checker()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("unsafe golden fixture", result.stdout)
                self.assertIn(reason, result.stdout)

    def test_zero_frames_fail_for_zero_reason(self) -> None:
        result = self.run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no nonblank golden JSON-RPC frames", result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "G3: FAIL")

    def test_non_envelope_fails_for_envelope_reason(self) -> None:
        self.write_fixture('{"jsonrpc":"2.0"}')
        result = self.run_checker()
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "not a JSON-RPC envelope (response must contain exactly one of result or error)",
            result.stdout + result.stderr,
        )

    def test_duplicate_keys_and_nonfinite_numbers_are_invalid_json(self) -> None:
        for frame in (
            '{"jsonrpc":"2.0","method":"session.list","method":"session.create"}',
            '{"jsonrpc":"2.0","method":"session.list","params":NaN}',
            '{"jsonrpc":"2.0","method":"session.list","params":Infinity}',
        ):
            self.write_fixture(frame)
            with self.subTest(frame=frame):
                result = self.run_checker()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("invalid JSON", result.stdout + result.stderr)
                self.assertEqual(result.stdout.splitlines()[-1], "G3: FAIL")

    def test_scalar_or_null_params_fail_json_rpc_shape(self) -> None:
        for params in ("null", "true", "1", '"value"'):
            self.write_fixture(
                '{"jsonrpc":"2.0","method":"session.list","params":'
                + params
                + "}"
            )
            with self.subTest(params=params):
                result = self.run_checker()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("params must be an object or array", result.stdout)

    def test_request_event_collision_requires_both_test_identities(self) -> None:
        source = self.generated_event.read_text(encoding="utf-8")
        self.generated_event.write_text(
            source.replace(
                "    func testE_SessionTitle() throws {\n"
                '        try self.assertEventRoundTrip(type: "session.title")\n'
                "    }\n",
                "",
            ),
            encoding="utf-8",
        )
        self.write_fixture(VALID_SANITIZED_REQUEST)

        result = self.run_checker()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("testE_SessionTitle", result.stdout)

    def test_bare_event_method_fails_real_envelope_contract(self) -> None:
        source = self.generated_event.read_text(encoding="utf-8")
        self.generated_event.write_text(
            source.replace(
                'JSONRPCEnvelope(method: "event", params: params)',
                "JSONRPCEnvelope(method: type, params: params)",
            ),
            encoding="utf-8",
        )
        self.write_fixture(VALID_SANITIZED_REQUEST)

        result = self.run_checker()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("real-event-envelope round-trip helper", result.stdout)

    def test_event_helper_must_prove_id_is_absent(self) -> None:
        source = self.generated_event.read_text(encoding="utf-8")
        self.generated_event.write_text(
            source.replace(
                '        XCTAssertFalse(object.keys.contains("id"), '
                '"event \\(type) encoded an id", file: file, line: line)\n',
                "",
            ),
            encoding="utf-8",
        )
        self.write_fixture(VALID_SANITIZED_REQUEST)

        result = self.run_checker()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("real-event-envelope round-trip helper", result.stdout)

    def test_helper_contracts_are_exact_and_assertions_are_load_bearing(self) -> None:
        self.assertIsNone(
            checker.helper_contract_problem(
                checker.REQUEST_HELPER_TEMPLATE,
                checker.REQUEST_HELPER_TEMPLATE,
                "request",
            )
        )
        self.assertIsNone(
            checker.helper_contract_problem(
                checker.EVENT_HELPER_TEMPLATE,
                checker.EVENT_HELPER_TEMPLATE,
                "event",
            )
        )
        for template, fragment in (
            (checker.REQUEST_HELPER_TEMPLATE, "XCTAssertEqual(again, decoded,"),
            (checker.EVENT_HELPER_TEMPLATE, 'object.keys.contains("id")'),
            (checker.EVENT_HELPER_TEMPLATE, "XCTAssertEqual(decoded.params, params,"),
        ):
            mutated = template.replace(fragment, "let removedAssertion =")
            with self.subTest(fragment=fragment):
                self.assertIsNotNone(
                    checker.helper_contract_problem(mutated, template, "mutated")
                )

    def test_noop_request_test_body_fails_round_trip_contract(self) -> None:
        source = self.generated_request_test.read_text(encoding="utf-8")
        self.generated_request_test.write_text(
            source.replace(
                'try self.assertRoundTrip("method", name: "agents.list", id: .int(1))',
                "XCTAssertTrue(true)",
            ),
            encoding="utf-8",
        )
        self.write_fixture(VALID_SANITIZED_REQUEST)

        result = self.run_checker()

        self.assertEqual(result.returncode, 1)
        self.assertIn("request tests do not call", result.stdout)

    def test_drift_fails_without_mutating_generated(self) -> None:
        self.write_fixture(VALID_SANITIZED_REQUEST)
        with self.generated_request.open("ab") as generated:
            generated.write(b"// injected drift\n")
        before = self.generated_request.read_bytes()

        result = self.run_checker()

        self.assertEqual(result.returncode, 1)
        self.assertIn("committed files are stale", result.stdout + result.stderr)
        self.assertEqual(self.generated_request.read_bytes(), before)

    def test_duplicate_catalog_entry_is_fail_not_blocked(self) -> None:
        document = json.loads(self.valid_catalog_text)
        document["requests"].append(document["requests"][-1])
        self.catalog.write_text(json.dumps(document), encoding="utf-8")
        self.write_fixture(VALID_SANITIZED_REQUEST)
        result = self.run_checker()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("duplicate request/event entry", result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], "G3: FAIL")

    def test_provenance_drift_fails_with_generated_names_unchanged(self) -> None:
        document = json.loads(self.valid_catalog_text)
        document["source"]["commit"] = "0" * 40
        self.catalog.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.write_fixture(VALID_SANITIZED_REQUEST)

        result = self.run_checker()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "protocol catalog bytes do not match the pinned Hermes derivation",
            result.stdout,
        )
        self.assertEqual(result.stdout.splitlines()[-1], "G3: FAIL")

    def test_missing_or_invalid_catalog_blocks(self) -> None:
        for replacement in (
            None,
            "not JSON",
            "[]",
            '{"requests":[],"events":{}}',
            '{"requests":[],"requests":[],"events":[]}',
            '{"requests":[{"name":"ping","value":NaN}],"events":[]}',
        ):
            self.catalog.write_text(self.valid_catalog_text, encoding="utf-8")
            if replacement is None:
                self.catalog.unlink()
            else:
                self.catalog.write_text(replacement, encoding="utf-8")
            with self.subTest(replacement=replacement):
                result = self.run_checker()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("G3: BLOCKED", result.stderr)
                self.assertEqual(result.stderr.splitlines()[-1], "G3: BLOCKED")

    def test_missing_generator_blocks(self) -> None:
        (self.repo / "scripts" / "gen_conformance_tests.py").unlink()
        result = self.run_checker()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("conformance generator is missing", result.stderr)

    def test_unreadable_fixture_shape_blocks(self) -> None:
        (self.fixtures / "golden.jsonl").mkdir()
        result = self.run_checker()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("G3: BLOCKED", result.stderr)

    def test_generator_failure_or_missing_outputs_blocks(self) -> None:
        generator = self.repo / "scripts" / "gen_conformance_tests.py"
        cases = (
            "import sys\nsys.exit(7)\n",
            "# succeeds without producing the requested outputs\n",
        )
        for source in cases:
            generator.write_text(source, encoding="utf-8")
            self.write_fixture(VALID_SANITIZED_REQUEST)
            with self.subTest(source=source):
                result = self.run_checker()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stderr.splitlines()[-1], "G3: BLOCKED")
            shutil.copy2(GENERATOR, generator)

    def test_gauntlet_preserves_fail_vs_blocked_statuses(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        g3 = script.split("g3() {", maxsplit=1)[1].split(
            "# ---- G4", maxsplit=1
        )[0]
        self.assertIn('"0|G3: PASS") record G3 PASS', g3)
        self.assertIn('"1|G3: FAIL") record G3 FAIL', g3)
        self.assertIn('"2|G3: BLOCKED") record G3 BLOCKED', g3)
        self.assertIn("*) record G3 BLOCKED", g3)


class ConformanceGeneratorTests(unittest.TestCase):
    def run_generator(
        self, catalog: pathlib.Path, output: pathlib.Path
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(GENERATOR),
                "--catalog",
                str(catalog),
                "--output-directory",
                str(output),
            ],
            capture_output=True,
            text=True,
            cwd=REPO,
        )

    def test_full_catalog_ratchets_224_kind_aware_tests(self) -> None:
        with tempfile.TemporaryDirectory(prefix="talaria-gen-full-") as temporary:
            output = pathlib.Path(temporary)
            result = self.run_generator(REPO / "protocol" / "methods.json", output)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            request_source = "\n".join(
                (output / filename).read_text(encoding="utf-8")
                for filename in OUTPUT_NAMES[1:-1]
            )
            event_source = (output / OUTPUT_NAMES[-1]).read_text(encoding="utf-8")
            request_count = request_source.count("    func testM_")
            event_count = event_source.count("    func testE_")
            self.assertEqual((request_count, event_count), (168, 56))
            self.assertEqual(request_count + event_count, 224)
            for stem in ("SessionTitle", "SessionUsage"):
                self.assertIn(f"testM_{stem}", request_source)
                self.assertIn(f"testE_{stem}", event_source)
            self.assertNotIn('assertRoundTrip("event"', event_source)
            self.assertEqual(event_source.count("try self.assertEventRoundTrip("), 56)

    def test_two_directories_and_reordered_catalog_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="talaria-gen-determinism-") as temporary:
            root = pathlib.Path(temporary)
            original = json.loads(
                (REPO / "protocol" / "methods.json").read_text(encoding="utf-8")
            )
            original["requests"].reverse()
            original["events"].reverse()
            reordered = root / "reordered.json"
            reordered.write_bytes(
                (json.dumps(original, ensure_ascii=False) + "\n").encode("utf-8")
            )
            outputs = [root / name for name in ("one", "two", "reordered")]
            for catalog, output in (
                (REPO / "protocol" / "methods.json", outputs[0]),
                (REPO / "protocol" / "methods.json", outputs[1]),
                (reordered, outputs[2]),
            ):
                result = self.run_generator(catalog, output)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for filename in OUTPUT_NAMES:
                values = [(output / filename).read_bytes() for output in outputs]
                self.assertEqual(values[0], values[1])
                self.assertEqual(values[0], values[2])
                self.assertNotIn(b"\r", values[0])
                self.assertFalse(values[0].startswith(b"\xef\xbb\xbf"))
            self.assertIn(
                "→".encode("utf-8"),
                (outputs[0] / OUTPUT_NAMES[0]).read_bytes(),
            )

    def test_duplicate_and_normalized_identity_collision_fail_without_output(self) -> None:
        cases = (
            {
                "requests": [{"name": "ping"}, {"name": "ping"}],
                "events": [{"name": "event.ok"}],
            },
            {
                "requests": [{"name": "foo.bar"}, {"name": "foo.Bar"}],
                "events": [{"name": "event.ok"}],
            },
        )
        with tempfile.TemporaryDirectory(prefix="talaria-gen-invalid-") as temporary:
            root = pathlib.Path(temporary)
            for index, document in enumerate(cases):
                catalog = root / f"catalog-{index}.json"
                output = root / f"output-{index}"
                catalog.write_text(json.dumps(document), encoding="utf-8")
                output.mkdir()
                seeded = {
                    filename: b"seeded output\n" for filename in OUTPUT_NAMES
                }
                for filename, content in seeded.items():
                    (output / filename).write_bytes(content)
                with self.subTest(document=document):
                    result = self.run_generator(catalog, output)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertEqual(
                        {
                            filename: (output / filename).read_bytes()
                            for filename in OUTPUT_NAMES
                        },
                        seeded,
                    )

    def test_missing_catalog_is_blocked_without_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="talaria-gen-missing-") as temporary:
            root = pathlib.Path(temporary)
            output = root / "output"
            result = self.run_generator(root / "missing.json", output)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertFalse(output.exists())

    def test_invalid_utf8_catalog_is_blocked_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="talaria-gen-utf8-") as temporary:
            root = pathlib.Path(temporary)
            catalog = root / "invalid.json"
            catalog.write_bytes(b'{"requests":[]}' + bytes([0xFF]))
            output = root / "output"
            output.mkdir()
            before = {}
            for filename in OUTPUT_NAMES:
                content = f"seed {filename}\n".encode("utf-8")
                (output / filename).write_bytes(content)
                before[filename] = content

            result = self.run_generator(catalog, output)

            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(
                {
                    filename: (output / filename).read_bytes()
                    for filename in OUTPUT_NAMES
                },
                before,
            )


if __name__ == "__main__":
    unittest.main()
