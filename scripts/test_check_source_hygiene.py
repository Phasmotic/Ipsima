from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "check_source_hygiene.py"


class SourceHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="talaria-g5-test-")
        self.repository = Path(self.temporary.name)
        for directory in ("Packages", "App", "Tests"):
            (self.repository / directory).mkdir()
        self.source = self.repository / "App" / "Probe.swift"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_checker(self, source: str) -> subprocess.CompletedProcess[str]:
        self.source.write_text(source, encoding="utf-8", newline="\n")
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(CHECKER),
                "--repository",
                str(self.repository),
            ],
            capture_output=True,
            text=True,
        )

    def assert_fails_without_value(self, source: str, forbidden_value: str) -> None:
        result = self.run_checker(source)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertNotIn(forbidden_value, output)

    def test_harmless_source_and_exact_allowed_hosts_pass(self) -> None:
        result = self.run_checker(
            'let local = "http://localhost/path"\n'
            'let loopback = "ws://127.0.0.1/socket"\n'
            'let docs = "https://api.example.com/reference"\n'
            'let host = "localhost"\n'
            'Client(host: "api.example.com")\n'
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_allowed_and_forbidden_urls_on_same_line_fail(self) -> None:
        result = self.run_checker(
            'let urls = ["http://localhost", "https://forbidden.invalid"]\n'
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_lookalike_loopback_hosts_fail(self) -> None:
        result = self.run_checker(
            'let first = "https://localhost.evil"\n'
            'let second = "https://127.0.0.1.evil"\n'
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual((result.stdout + result.stderr).count("hardcoded"), 2)

    def test_token_named_assignment_fails_with_value_redacted(self) -> None:
        value = "synthetic_value_for_gate_probe"
        self.assert_fails_without_value(f'let token = "{value}"\n', value)

    def test_password_named_assignment_fails_with_value_redacted(self) -> None:
        value = "synthetic_password_for_probe"
        self.assert_fails_without_value(f'let password = "{value}"\n', value)

    def test_typed_camel_case_token_assignment_fails(self) -> None:
        value = "short"
        self.assert_fails_without_value(
            f'let accessToken: String = "{value}"\n', value
        )

    def test_dictionary_token_literal_fails(self) -> None:
        value = "brief"
        self.assert_fails_without_value(f'let headers = ["token": "{value}"]\n', value)

    def test_host_named_assignment_without_scheme_fails(self) -> None:
        value = "prod.invalid"
        self.assert_fails_without_value(f'let host: String = "{value}"\n', value)

    def test_allowed_and_forbidden_host_assignments_on_same_line_fail(self) -> None:
        value = "prod.invalid"
        self.assert_fails_without_value(
            f'let host = "localhost"; let endpoint = "{value}"\n', value
        )

    def test_components_host_assignment_fails(self) -> None:
        value = "prod.invalid"
        self.assert_fails_without_value(f'components.host = "{value}"\n', value)

    def test_host_argument_label_fails(self) -> None:
        value = "prod.invalid"
        self.assert_fails_without_value(f'Client(host: "{value}")\n', value)

    def test_token_argument_label_fails(self) -> None:
        value = "brief"
        self.assert_fails_without_value(f'Client(token: "{value}")\n', value)

    def test_non_loopback_ip_literal_fails_even_without_host_name(self) -> None:
        value = "192.0.2.44"
        self.assert_fails_without_value(f'let address = "{value}"\n', value)

    def test_generic_long_token_shaped_assignment_fails_with_value_redacted(self) -> None:
        value = "abcdefghijklmnopqrstuvwxyz012345"
        self.assert_fails_without_value(f'let opaque = "{value}"\n', value)


if __name__ == "__main__":
    unittest.main()
