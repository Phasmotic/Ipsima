from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unittest
import uuid

from scripts import check_source_hygiene as hygiene


REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "check_source_hygiene.py"
TEST_TEMP_ROOT = REPO / ".gauntlet" / "g5-tests"


def network_url(scheme: str, remainder: str) -> str:
    # Keep negative fixtures out of this test module's own literal surface.
    return scheme + "://" + remainder


def assignment(name: str, value: str, prefix: str = "let ") -> str:
    return prefix + name + ' = "' + value + '"\n'


def remove_test_tree(path: Path) -> None:
    def make_writable_and_retry(function, target, _exception_info) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onerror=make_writable_and_retry)


class SourceHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.repository = TEST_TEMP_ROOT / f"repo-{uuid.uuid4().hex}"
        self.repository.mkdir()
        initialized = subprocess.run(
            ["git", "-C", str(self.repository), "init", "--quiet"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.source = Path("App/Probe.swift")

    def tearDown(self) -> None:
        if self.repository.parent == TEST_TEMP_ROOT and self.repository.name.startswith(
            "repo-"
        ):
            remove_test_tree(self.repository)

    def git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            capture_output=True,
            text=True,
        )

    def write_text(self, relative: str | Path, content: str) -> Path:
        path = self.repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return path

    def write_bytes(self, relative: str | Path, content: bytes) -> Path:
        path = self.repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def run_checker(
        self,
        source: str | None = "let harmless = true\n",
        *,
        repository: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if source is not None:
            self.write_text(self.source, source)
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(CHECKER),
                "--repository",
                str(repository or self.repository),
            ],
            capture_output=True,
            text=True,
            env=environment,
        )

    def assert_fails_without_value(self, source: str, forbidden_value: str) -> None:
        result = self.run_checker(source)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertNotIn(forbidden_value, output)

    def assert_path_fails(self, relative: str, content: str, value: str) -> None:
        target = self.write_text(relative, content)
        try:
            result = self.run_checker()
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 1, output)
            self.assertIn(relative, output)
            self.assertNotIn(value, output)
        finally:
            target.unlink(missing_ok=True)

    def test_harmless_source_and_exact_safe_hosts_pass(self) -> None:
        result = self.run_checker(
            assignment("docsURL", network_url("https", "api.example.com/reference"))
            + assignment("socketURL", network_url("ws", "127.42.0.1/socket"))
            + assignment("v6SocketURL", network_url("ws", "[::1]/socket"))
            + assignment("host", "localhost")
            + 'Client(host: "api.example.com")\n'
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_insecure_http_is_rejected_even_for_loopback(self) -> None:
        value = network_url("http", "localhost/path")
        self.assert_fails_without_value(assignment("localURL", value), value)

    def test_insecure_websocket_requires_loopback_not_example(self) -> None:
        value = network_url("ws", "api.example.com/socket")
        self.assert_fails_without_value(assignment("socketURL", value), value)

    def test_bare_network_prefixes_cannot_evade_url_validation(self) -> None:
        for scheme in ("http", "ws", "https", "wss"):
            with self.subTest(scheme=scheme):
                value = network_url(scheme, "")
                self.assert_fails_without_value(assignment("endpoint", value), value)

    def test_allowed_and_forbidden_urls_on_same_line_fail(self) -> None:
        allowed = network_url("https", "api.example.com")
        forbidden = network_url("https", "forbidden.invalid")
        result = self.run_checker('let urls = ["' + allowed + '", "' + forbidden + '"]\n')
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertNotIn(forbidden, output)

    def test_lookalike_loopback_hosts_fail(self) -> None:
        first = network_url("https", "localhost.evil")
        second = network_url("https", "127.0.0.1.evil")
        result = self.run_checker(
            assignment("firstURL", first) + assignment("secondURL", second)
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertGreaterEqual(output.count("hardcoded"), 2)
        self.assertNotIn(first, output)
        self.assertNotIn(second, output)

    def test_token_named_assignment_fails_with_value_redacted(self) -> None:
        value = "synthetic_" + "value_for_gate_probe"
        self.assert_fails_without_value(assignment("token", value), value)

    def test_password_named_assignment_fails_with_value_redacted(self) -> None:
        value = "synthetic_" + "password_for_probe"
        self.assert_fails_without_value(assignment("password", value), value)

    def test_typed_camel_case_token_assignment_fails(self) -> None:
        value = "short"
        source = 'let access' + 'Token: String = "' + value + '"\n'
        self.assert_fails_without_value(source, value)

    def test_dictionary_token_literal_fails(self) -> None:
        value = "brief"
        source = 'let headers = ["to' + 'ken": "' + value + '"]\n'
        self.assert_fails_without_value(source, value)

    def test_host_named_assignment_without_scheme_fails(self) -> None:
        value = "prod.invalid"
        source = 'let ho' + 'st: String = "' + value + '"\n'
        self.assert_fails_without_value(source, value)

    def test_allowed_and_forbidden_host_assignments_on_same_line_fail(self) -> None:
        value = "prod.invalid"
        source = (
            'let ho' + 'st = "localhost"; let end' + 'point = "' + value + '"\n'
        )
        self.assert_fails_without_value(source, value)

    def test_components_host_assignment_fails(self) -> None:
        value = "prod.invalid"
        source = 'components.ho' + 'st = "' + value + '"\n'
        self.assert_fails_without_value(source, value)

    def test_host_argument_label_fails(self) -> None:
        value = "prod.invalid"
        source = 'Client(ho' + 'st: "' + value + '")\n'
        self.assert_fails_without_value(source, value)

    def test_token_argument_label_fails(self) -> None:
        value = "brief"
        source = 'Client(to' + 'ken: "' + value + '")\n'
        self.assert_fails_without_value(source, value)

    def test_non_loopback_ip_literal_fails_even_without_host_name(self) -> None:
        value = "192.0.2." + "44"
        self.assert_fails_without_value(assignment("address", value), value)

    def test_generic_long_token_shaped_assignment_fails_with_value_redacted(self) -> None:
        value = "abcdefghijklmnop" + "qrstuvwxyz012345"
        self.assert_fails_without_value(assignment("opaque", value), value)

    def test_public_integrity_digest_is_not_treated_as_a_token(self) -> None:
        result = self.run_checker(assignment("ARCHIVE_SHA256", "a" * 64, prefix=""))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exact_public_https_allowance_is_path_scoped(self) -> None:
        value = network_url("https", "github.com/NousResearch/hermes-agent")
        self.write_text("README.md", "[upstream](" + value + ")\n")
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.write_text("README.md", "documentation\n")
        self.assert_path_fails("scripts/copied_url.py", 'URL = "' + value + '"\n', value)

    def test_g12_ledger_link_is_allowed_only_in_governed_docs(self) -> None:
        value = network_url(
            "https",
            "github.com/Phasmotic/Talaria/issues/2"
            "#issuecomment-5457754926",
        )
        source = f"[ledger]({value})\n"

        for relative in ("docs/BRIEF.md", "docs/GOVERNANCE.md"):
            findings: list[str] = []
            hygiene.scan_text(relative, source, findings)
            self.assertEqual(findings, [])

        findings = []
        hygiene.scan_text("README.md", source, findings)
        self.assertEqual(len(findings), 1)
        self.assertIn("hardcoded non-example host literal", findings[0])
        self.assertNotIn(value, findings[0])

    def test_public_https_allowance_is_value_exact(self) -> None:
        value = network_url("https", "github.com/NousResearch/not-hermes")
        self.assert_path_fails("README.md", "[upstream](" + value + ")\n", value)

    def test_forbidden_literals_are_found_in_every_repository_surface(self) -> None:
        value = network_url("https", "prod.invalid/service")
        content = assignment("endpoint", value, prefix="")
        paths = (
            "scripts/probe.py",
            ".github/workflows/probe.yml",
            "project.yml",
            ".swiftlint.yml",
            "Packages/HermesKit/Tests/HermesKitTests/Fixtures/probe.jsonl",
        )
        for relative in paths:
            with self.subTest(relative=relative):
                self.assert_path_fails(relative, content, value)

    def test_tracked_and_untracked_paths_are_both_scanned(self) -> None:
        tracked_value = network_url("https", "tracked.invalid")
        tracked = self.write_text("scripts/tracked.py", assignment("endpoint", tracked_value))
        added = self.git("add", tracked.relative_to(self.repository).as_posix())
        self.assertEqual(added.returncode, 0, added.stderr)
        tracked_result = self.run_checker()
        self.assertEqual(tracked_result.returncode, 1, tracked_result.stdout + tracked_result.stderr)

        tracked.write_text("harmless = True\n", encoding="utf-8", newline="\n")
        untracked_value = network_url("https", "untracked.invalid")
        self.assert_path_fails(
            "scripts/untracked.py", assignment("endpoint", untracked_value), untracked_value
        )

    def test_tracked_shell_script_with_100755_index_mode_passes(self) -> None:
        self.write_text("scripts/probe.sh", "#!/bin/sh\nexit 0\n")
        added = self.git("add", "scripts/probe.sh")
        self.assertEqual(added.returncode, 0, added.stderr)
        marked = self.git("update-index", "--chmod=+x", "scripts/probe.sh")
        self.assertEqual(marked.returncode, 0, marked.stderr)

        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_tracked_shell_script_with_100644_index_mode_is_a_finding(self) -> None:
        self.write_text("scripts/probe.sh", "#!/bin/sh\nexit 0\n")
        added = self.git("add", "scripts/probe.sh")
        self.assertEqual(added.returncode, 0, added.stderr)
        marked = self.git("update-index", "--chmod=-x", "scripts/probe.sh")
        self.assertEqual(marked.returncode, 0, marked.stderr)

        result = self.run_checker()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1, output)
        self.assertIn("scripts/probe.sh", output)
        self.assertIn("100644", output)
        self.assertIn("expected 100755", output)
        self.assertNotIn("blocked", output.lower())

    def test_unmerged_tracked_shell_index_evidence_blocks(self) -> None:
        relative = "scripts/conflict.sh"
        configured = self.git("config", "core.autocrlf", "false")
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assertEqual(configured.stderr, "")
        self.write_text(relative, "#!/bin/sh\nexit 0\n")
        added = self.git("add", relative)
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(added.stderr, "")
        hashed = self.git("hash-object", "-w", relative)
        self.assertEqual(hashed.returncode, 0, hashed.stderr)
        self.assertEqual(hashed.stderr, "")
        object_id = hashed.stdout.strip()
        zero_id = "0" * len(object_id)
        injected = subprocess.run(
            ["git", "-C", str(self.repository), "update-index", "--index-info"],
            input=(
                f"0 {zero_id}\t{relative}\n"
                f"100755 {object_id} 1\t{relative}\n"
                f"100755 {object_id} 2\t{relative}\n"
                f"100755 {object_id} 3\t{relative}\n"
            ).encode("ascii"),
            capture_output=True,
        )
        self.assertEqual(injected.returncode, 0, injected.stderr)
        self.assertEqual(injected.stderr, b"")

        staged = self.git("ls-files", "--stage", "--", relative)
        self.assertEqual(staged.returncode, 0, staged.stderr)
        self.assertEqual(staged.stderr, "")
        stages = [line.split(maxsplit=3)[2] for line in staged.stdout.splitlines()]
        self.assertEqual(stages, ["1", "2", "3"])

        result = self.run_checker()
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 2, output)

    def test_adversarial_shell_index_records_block(self) -> None:
        object_id = "1" * 40
        valid = f"100755 {object_id} 0\tscripts/probe.sh\0".encode()
        cases = {
            "not NUL terminated": valid[:-1],
            "malformed": b"not an index record\0",
            "duplicate": valid + valid,
            "unmerged": f"100755 {object_id} 2\tscripts/probe.sh\0".encode(),
            "zero object": f"100755 {'0' * 40} 0\tscripts/probe.sh\0".encode(),
            "non-regular": f"120000 {object_id} 0\tscripts/probe.sh\0".encode(),
        }
        for name, raw_index in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(hygiene.ScanBlocked):
                    hygiene.parse_tracked_shell_modes(raw_index)

    def test_gitignored_path_is_the_only_kind_excluded(self) -> None:
        value = network_url("http", "forbidden.invalid")
        self.write_text(".gitignore", "ignored.txt\n")
        self.write_text("ignored.txt", value + "\n")
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_empty_git_enumeration_blocks(self) -> None:
        result = self.run_checker(source=None)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("zero files", result.stderr)

    def test_non_git_repository_blocks(self) -> None:
        root = TEST_TEMP_ROOT / f"nongit-{uuid.uuid4().hex}"
        root.mkdir()
        try:
            (root / "file.txt").write_text("safe\n", encoding="utf-8")
            result = self.run_checker(source=None, repository=root)
        finally:
            remove_test_tree(root)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("worktree root", result.stderr)

    def test_missing_git_executable_blocks(self) -> None:
        environment = dict(os.environ)
        environment["PATH"] = ""
        result = self.run_checker(environment=environment)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("could not execute", result.stderr)

    def test_tracked_but_missing_path_blocks(self) -> None:
        tracked = self.write_text("scripts/disappears.py", "safe = True\n")
        added = self.git("add", "scripts/disappears.py")
        self.assertEqual(added.returncode, 0, added.stderr)
        tracked.unlink()
        result = self.run_checker()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("unavailable", result.stderr)

    def test_non_utf8_non_binary_file_blocks_without_disclosing_bytes(self) -> None:
        payload = b"\xff\xfeinvalid"
        self.write_bytes("scripts/invalid.py", payload)
        result = self.run_checker()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("not valid UTF-8", output)
        self.assertNotIn("invalid", output)

    def test_nul_classified_binary_is_counted_and_skipped_as_text(self) -> None:
        self.write_bytes("Assets/probe.bin", b"\x89BIN\x00\xff\xfe")
        result = self.run_checker()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("1 NUL-classified binary files", output)

    def test_binary_still_enforces_global_network_prefix_policy(self) -> None:
        value = network_url("http", "binary.invalid")
        self.write_bytes("Assets/probe.bin", b"\x00prefix" + value.encode("ascii"))
        result = self.run_checker()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("Assets/probe.bin:binary", output)
        self.assertNotIn(value, output)

    def test_binary_bare_network_prefix_is_also_rejected(self) -> None:
        value = network_url("ws", "")
        self.write_bytes("Assets/probe.bin", b"\x00prefix" + value.encode("ascii"))
        result = self.run_checker()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("malformed network URL", output)
        self.assertNotIn(value, output)

    def test_symlink_repository_entry_blocks(self) -> None:
        target = self.write_text("target.txt", "safe\n")
        link = self.repository / "linked.txt"
        try:
            link.symlink_to(target)
        except OSError as error:
            self.skipTest(f"symlink creation is unavailable: {type(error).__name__}")
        result = self.run_checker()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("symlink", result.stderr)


if __name__ == "__main__":
    unittest.main()
