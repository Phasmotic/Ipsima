from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts import check_conformance as checker


REPO = pathlib.Path(__file__).resolve().parent.parent
GAUNTLET = REPO / "scripts" / "gauntlet.sh"


class CheckConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="talaria-g3-test-")
        self.repo = pathlib.Path(self.temporary.name)
        scripts = self.repo / "scripts"
        protocol = self.repo / "protocol"
        tests = self.repo / "Packages" / "HermesKit" / "Tests" / "HermesKitTests"
        self.fixtures = tests / "Fixtures"
        scripts.mkdir(parents=True)
        protocol.mkdir(parents=True)
        self.fixtures.mkdir(parents=True)

        shutil.copy2(REPO / "scripts" / "check_conformance.py", scripts)
        shutil.copy2(REPO / "scripts" / "gen_conformance_tests.py", scripts)
        generator_path = scripts / "gen_conformance_tests.py"
        generator_source = generator_path.read_text(encoding="utf-8")
        decoded_line = '        "        let decoded = try codec.decode(data)",\n'
        equality_line = (
            '        "        XCTAssertEqual(decoded, envelope, '
            '\\"\\\\(kind) \\\\(name) changed on decode\\", '
            'file: file, line: line)",\n'
        )
        if decoded_line not in generator_source:
            raise AssertionError("generator fixture shape changed")
        generator_path.write_text(
            generator_source.replace(decoded_line, decoded_line + equality_line, 1),
            encoding="utf-8",
        )
        self.valid_catalog_text = (
            '{"requests":[{"name":"ping"}],"events":[]}\n'
        )
        self.catalog = protocol / "methods.json"
        self.catalog.write_text(self.valid_catalog_text, encoding="utf-8")
        self.generated = tests / "ProtocolConformanceTests.swift"
        generated = subprocess.run(
            [
                sys.executable,
                "-B",
                str(generator_path),
                "--output",
                str(self.generated),
            ],
            capture_output=True,
            text=True,
            cwd=self.repo,
        )
        if generated.returncode != 0:
            raise AssertionError(generated.stdout + generated.stderr)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_checker(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(self.repo / "scripts" / "check_conformance.py")],
            capture_output=True,
            text=True,
            cwd=self.repo,
        )

    def write_fixture(self, line: str) -> None:
        (self.fixtures / "golden.jsonl").write_text(line + "\n", encoding="utf-8")

    def test_valid_baseline_passes(self) -> None:
        self.write_fixture('{"jsonrpc":"2.0","method":"ping"}')
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "G3: PASS")

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
            '{"jsonrpc":"2.0","method":"ping","method":"pong"}',
            '{"jsonrpc":"2.0","method":"ping","params":NaN}',
            '{"jsonrpc":"2.0","method":"ping","params":Infinity}',
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
                '{"jsonrpc":"2.0","method":"ping","params":' + params + "}"
            )
            with self.subTest(params=params):
                result = self.run_checker()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("params must be an object or array", result.stdout)

    def test_request_event_name_collision_requires_both_test_identities(self) -> None:
        catalog = self.repo / "protocol" / "methods.json"
        catalog.write_text(
            '{"requests":[{"name":"session.title"}],'
            '"events":[{"name":"session.title"}]}',
            encoding="utf-8",
        )
        self.generated.write_text(
            "    func testM_SessionTitle() throws {\n",
            encoding="utf-8",
        )
        self.write_fixture('{"jsonrpc":"2.0","method":"ping"}')

        result = self.run_checker()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("catalog entries : 2", result.stdout)
        self.assertIn("testE_SessionTitle", result.stdout)

    def test_bare_event_method_generator_fails_real_envelope_contract(self) -> None:
        self.catalog.write_text(
            '{"requests":[],"events":[{"name":"session.title"}]}',
            encoding="utf-8",
        )
        regenerated = subprocess.run(
            [
                sys.executable,
                "-B",
                str(self.repo / "scripts" / "gen_conformance_tests.py"),
                "--output",
                str(self.generated),
            ],
            capture_output=True,
            text=True,
            cwd=self.repo,
        )
        self.assertEqual(regenerated.returncode, 0, regenerated.stdout + regenerated.stderr)
        self.write_fixture('{"jsonrpc":"2.0","method":"ping"}')

        result = self.run_checker()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("real-event-envelope round-trip helper", result.stdout)
        self.assertIn("do not call the real-envelope helper", result.stdout)

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
            (
                checker.REQUEST_HELPER_TEMPLATE,
                "XCTAssertEqual(again, decoded,",
            ),
            (
                checker.EVENT_HELPER_TEMPLATE,
                "XCTAssertEqual(decoded.params, params,",
            ),
        ):
            mutated = template.replace(fragment, "let removedAssertion =")
            with self.subTest(fragment=fragment):
                self.assertIsNotNone(
                    checker.helper_contract_problem(mutated, template, "mutated")
                )

    def test_noop_request_test_body_fails_round_trip_contract(self) -> None:
        source = self.generated.read_text(encoding="utf-8")
        self.generated.write_text(
            source.replace(
                'try assertRoundTrip("method", name: "ping", id: .int(1))',
                "XCTAssertTrue(true)",
            ),
            encoding="utf-8",
        )
        self.write_fixture('{"jsonrpc":"2.0","method":"ping"}')

        result = self.run_checker()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("request tests do not call", result.stdout)

    def test_drift_fails_without_mutating_generated(self) -> None:
        self.write_fixture('{"jsonrpc":"2.0","method":"ping"}')
        with self.generated.open("ab") as generated:
            generated.write(b"// injected drift\n")
        before = self.generated.read_bytes()

        result = self.run_checker()

        self.assertEqual(result.returncode, 1)
        self.assertIn("committed file is stale", result.stdout + result.stderr)
        self.assertEqual(self.generated.read_bytes(), before)

    def test_missing_or_invalid_catalog_blocks(self) -> None:
        catalog = self.catalog
        for replacement in (
            None,
            "not JSON",
            "[]",
            '{"requests":[],"events":{}}',
            '{"requests":[],"requests":[],"events":[]}',
            '{"requests":[{"name":"ping","value":NaN}],"events":[]}',
        ):
            catalog.write_text(self.valid_catalog_text, encoding="utf-8")
            if replacement is None:
                catalog.unlink()
            else:
                catalog.write_text(replacement, encoding="utf-8")
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

    def test_generator_failure_or_missing_candidate_blocks(self) -> None:
        generator = self.repo / "scripts" / "gen_conformance_tests.py"
        cases = (
            "import sys\nsys.exit(7)\n",
            "# succeeds without producing the requested output\n",
        )
        for source in cases:
            shutil.copy2(REPO / "scripts" / "gen_conformance_tests.py", generator)
            generator.write_text(source, encoding="utf-8")
            self.write_fixture('{"jsonrpc":"2.0","method":"ping"}')
            with self.subTest(source=source):
                result = self.run_checker()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stderr.splitlines()[-1], "G3: BLOCKED")

    def test_gauntlet_preserves_fail_vs_blocked_statuses(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        g3 = script.split("g3() {", maxsplit=1)[1].split(
            "# ---- G4", maxsplit=1
        )[0]
        self.assertIn('"0|G3: PASS") record G3 PASS', g3)
        self.assertIn('"1|G3: FAIL") record G3 FAIL', g3)
        self.assertIn('"2|G3: BLOCKED") record G3 BLOCKED', g3)
        self.assertIn("*) record G3 BLOCKED", g3)


if __name__ == "__main__":
    unittest.main()
