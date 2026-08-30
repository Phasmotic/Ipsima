#!/usr/bin/env python3
"""Tests for pinned, deterministic protocol derivation."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import derive_protocol as derive

REPO = pathlib.Path(__file__).resolve().parent.parent
TEST_TEMP_ROOT = REPO / ".gauntlet" / "derive-protocol-tests"


def _temporary_directory() -> tempfile.TemporaryDirectory[str]:
    TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(
        prefix="case-", dir=TEST_TEMP_ROOT
    )


def _source(path: str, text: str) -> derive.SourceFile:
    return derive.SourceFile.from_bytes(path, text.encode("utf-8"))


def _fake_files() -> dict[str, derive.SourceFile]:
    texts = {
        "tui_gateway/methods_alpha.py": '''
@method("alpha.request")
def alpha():
    pass
''',
        "tui_gateway/server.py": '''
def _event_frame(event, sid, payload=None):
    params = {"type": event}
    return {"jsonrpc": "2.0", "method": "event", "params": params}

_emit("alpha.event", "sid", {})
_block("sudo.request", "sid", {})
''',
        "tui_gateway/entry.py": '''
write_json({
    "jsonrpc": "2.0",
    "method": "event",
    "params": {"type": "gateway.ready", "payload": {}},
})
''',
        "tui_gateway/transport.py": '''
line = json.dumps(obj, ensure_ascii=False) + "\\n"
''',
        "tui_gateway/ws.py": '''
_TOKEN_COALESCE_S = 0.033
line = json.dumps(obj, ensure_ascii=False)
await self._ws.send_text(line)
raw = await ws.receive_text()
ready = {
    "jsonrpc": "2.0",
    "method": "event",
    "params": {"type": "gateway.ready", "payload": {}},
}
error = {"error": {"code": -32700}, "id": None}
if req_method == "gateway.ping":
    reply = {"result": {"ok": True}}
''',
        "gateway/platforms/api_server.py": '''
def _check_auth(auth_header):
    if auth_header.startswith("Bearer "):
        return True

def _http_route_table(self):
    routes = [
        ("GET", "/same", self.get_same),
        ("POST", "/same", self.post_same),
        ("GET", "/same", self.get_same),
    ]
    routes.append(("PATCH", "/other", self.patch_other))
    return routes

startup = "API_SERVER_KEY is required for the API server"
loopback = "including loopback-only binds"
''',
        "hermes_cli/dashboard_auth/ws_tickets.py": '''
TTL_SECONDS = 30
_tickets[ticket] = (int(time.time()) + TTL_SECONDS, info)
entry = _tickets.pop(ticket, None)
''',
        "hermes_cli/web_server.py": '''
_SESSION_HEADER_NAME = "X-Hermes-Session-Token"
expected = f"Bearer {_SESSION_TOKEN}"

@app.websocket("/api/ws")
async def gateway_ws(ws):
    """single-use, 30s-TTL ticket

    The legacy ``?token=`` path is unconditionally rejected in gated mode.
    """
    info = consume_ticket(ticket)

dashboard_only = ("GET", "/dashboard-only", handler)
''',
    }
    return {path: _source(path, text) for path, text in texts.items()}


class SourceFileTests(unittest.TestCase):
    def test_repository_attributes_pin_generated_contracts_to_lf(self) -> None:
        paths = (
            "protocol/methods.json",
            "scripts/derive_protocol.py",
            "Packages/HermesKit/Tests/HermesKitTests/ProtocolConformanceTests.swift",
        )
        result = subprocess.run(
            ["git", "check-attr", "eol", "--", *paths],
            cwd=REPO,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(": eol: lf\n"), len(paths))

    def test_strict_utf8_and_hash_are_bound_to_raw_blob(self) -> None:
        raw = "héllo\n".encode("utf-8")
        source = derive.SourceFile.from_bytes("tui_gateway/example.py", raw)
        self.assertEqual(source.text, "héllo\n")
        self.assertEqual(source.sha256, hashlib.sha256(raw).hexdigest())

    def test_invalid_utf8_blocks(self) -> None:
        with self.assertRaisesRegex(derive.DerivationBlocked, "strict UTF-8"):
            derive.SourceFile.from_bytes("tui_gateway/bad.py", b"\xff")

    def test_empty_python_blob_is_hashable_manifest_evidence(self) -> None:
        source = derive.SourceFile.from_bytes("tui_gateway/__init__.py", b"")
        self.assertEqual(source.text, "")
        self.assertEqual(source.sha256, hashlib.sha256(b"").hexdigest())


class CatalogDerivationTests(unittest.TestCase):
    def test_pinned_provenance_constants_are_exact(self) -> None:
        self.assertEqual(
            derive.SOURCE_REPOSITORY,
            "https://github.com/NousResearch/hermes-agent.git",
        )
        self.assertEqual(
            derive.SOURCE_COMMIT,
            "e3b5512b7b3f6cbcb23ba5fffdc66d5015eca246",
        )
        self.assertEqual(
            derive.PINNED_CATALOG_SHA256,
            hashlib.sha256((REPO / "protocol" / "methods.json").read_bytes()).hexdigest(),
        )

    def test_head_reverification_catalog_is_bound_to_exact_evidence(self) -> None:
        path = REPO / "protocol" / "methods-26350357.json"
        raw = path.read_bytes()
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            "8863412ba36e4b518da9aff635312e937d97a7a44c92ebf52c99c1694a15255b",
        )
        catalog = json.loads(raw)
        self.assertEqual(
            catalog["source"]["commit"],
            "26350357d76e4508c8df9304a3374bdc5a6f6220",
        )
        self.assertEqual(catalog["derived_at"], "2026-08-30")
        self.assertEqual(
            (
                len(catalog["requests"]),
                len(catalog["events"]),
                len(catalog["rest_routes"]),
            ),
            (170, 58, 42),
        )
        self.assertEqual(raw, derive.catalog_bytes(catalog))

    def test_catalog_manifest_hashes_every_declared_input(self) -> None:
        files = _fake_files()
        catalog = derive.derive_catalog(files, expected_counts=None)
        self.assertNotIn("$schema", catalog)
        manifest = catalog["source"]["inputs"]
        self.assertEqual(
            [item["path"] for item in manifest], sorted(files)
        )
        by_path = {item["path"]: item["sha256"] for item in manifest}
        self.assertEqual(
            by_path["hermes_cli/web_server.py"],
            files["hermes_cli/web_server.py"].sha256,
        )
        self.assertEqual(
            by_path["hermes_cli/dashboard_auth/ws_tickets.py"],
            files["hermes_cli/dashboard_auth/ws_tickets.py"].sha256,
        )
        self.assertEqual(
            catalog["source"]["repository"], derive.SOURCE_REPOSITORY
        )
        self.assertEqual(catalog["source"]["commit"], derive.SOURCE_COMMIT)

    def test_alternate_revision_and_date_are_explicit_catalog_provenance(self) -> None:
        revision = "a" * 40
        catalog = derive.derive_catalog(
            _fake_files(),
            expected_counts=None,
            source_commit=revision,
            derived_at="2026-08-30",
        )
        self.assertEqual(catalog["source"]["commit"], revision)
        self.assertEqual(catalog["derived_at"], "2026-08-30")

    def test_revision_and_date_must_be_immutable_canonical_values(self) -> None:
        with self.assertRaisesRegex(derive.DerivationBlocked, "full lowercase"):
            derive.derive_catalog(
                _fake_files(), expected_counts=None, source_commit="HEAD"
            )
        with self.assertRaisesRegex(derive.DerivationBlocked, "calendar date"):
            derive.derive_catalog(
                _fake_files(), expected_counts=None, derived_at="2026-02-30"
            )

    def test_transport_framing_distinguishes_websocket_messages_from_stdio_lines(
        self,
    ) -> None:
        catalog = derive.derive_catalog(_fake_files(), expected_counts=None)
        self.assertEqual(
            catalog["framing"]["encoding"],
            "one JSON-RPC 2.0 object per WebSocket text message, both "
            "directions; stdio is newline-delimited",
        )

    def test_unproved_websocket_framing_fact_blocks(self) -> None:
        mutations = (
            (
                "json.dumps(obj, ensure_ascii=False)",
                'json.dumps(obj, ensure_ascii=False) + "\\n"',
                "text-message serialization",
            ),
            ("self._ws.send_text(line)", "self._ws.send_bytes(line)", "outbound"),
            ("ws.receive_text()", "ws.receive_bytes()", "inbound"),
        )
        for old, new, label in mutations:
            with self.subTest(label=label):
                files = _fake_files()
                source = files["tui_gateway/ws.py"].text.replace(old, new)
                files["tui_gateway/ws.py"] = _source("tui_gateway/ws.py", source)
                with self.assertRaisesRegex(derive.DerivationBlocked, label):
                    derive.derive_catalog(files, expected_counts=None)

    def test_ticket_lifetime_and_single_use_are_code_proven(self) -> None:
        mutations = (
            ("TTL_SECONDS = 30", "TTL_SECONDS = 60", "30-second"),
            ("_tickets.pop(ticket, None)", "_tickets.get(ticket)", "single-use"),
        )
        for old, new, label in mutations:
            with self.subTest(label=label):
                files = _fake_files()
                path = "hermes_cli/dashboard_auth/ws_tickets.py"
                files[path] = _source(path, files[path].text.replace(old, new))
                with self.assertRaisesRegex(derive.DerivationBlocked, label):
                    derive.derive_catalog(files, expected_counts=None)

    def test_routes_dedupe_by_method_and_path(self) -> None:
        catalog = derive.derive_catalog(_fake_files(), expected_counts=None)
        self.assertEqual(
            catalog["rest_routes"],
            [
                {"method": "GET", "path": "/same"},
                {"method": "POST", "path": "/same"},
                {"method": "PATCH", "path": "/other"},
            ],
        )
        self.assertNotIn(
            "/dashboard-only",
            [route["path"] for route in catalog["rest_routes"]],
        )

    def test_direct_gateway_ready_locations_are_real_source_lines(self) -> None:
        catalog = derive.derive_catalog(_fake_files(), expected_counts=None)
        ready = next(
            event for event in catalog["events"]
            if event["name"] == "gateway.ready"
        )
        self.assertEqual(len(ready["emitted_at"]), 2)
        self.assertTrue(any(loc.startswith("entry.py:") for loc in ready["emitted_at"]))
        self.assertTrue(any(loc.startswith("ws.py:") for loc in ready["emitted_at"]))
        self.assertFalse(any("derived" in loc for loc in ready["emitted_at"]))

    def test_missing_declared_input_blocks(self) -> None:
        files = _fake_files()
        del files["hermes_cli/web_server.py"]
        with self.assertRaisesRegex(derive.DerivationBlocked, "missing required"):
            derive.derive_catalog(files, expected_counts=None)

    def test_unproved_hardcoded_fact_blocks(self) -> None:
        files = _fake_files()
        web = files["hermes_cli/web_server.py"].text.replace(
            "consume_ticket(ticket)", "accept_ticket(ticket)"
        )
        files["hermes_cli/web_server.py"] = _source(
            "hermes_cli/web_server.py", web
        )
        with self.assertRaisesRegex(derive.DerivationBlocked, "ticket consumption"):
            derive.derive_catalog(files, expected_counts=None)

    def test_count_contract_blocks_extractor_drift(self) -> None:
        with self.assertRaisesRegex(derive.DerivationBlocked, "counts disagree"):
            derive.derive_catalog(_fake_files(), expected_counts=(999, 999, 999))

    def test_canonical_bytes_are_deterministic_utf8_lf(self) -> None:
        catalog = derive.derive_catalog(_fake_files(), expected_counts=None)
        first = derive.catalog_bytes(catalog)
        second = derive.catalog_bytes(catalog)
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))
        self.assertNotIn(b"\r\n", first)
        self.assertIn("—".encode("utf-8"), first)
        self.assertNotIn(b"\\u2014", first)


class GitObjectSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = _temporary_directory()
        self.repo = pathlib.Path(self.temporary.name)
        self._git("init", "-q")
        self._git("config", "user.email", "tests@example.invalid")
        self._git("config", "user.name", "Talaria Tests")
        self._git("remote", "add", "origin", derive.SOURCE_REPOSITORY)
        files = {
            "tui_gateway/__init__.py": b"",
            "tui_gateway/entry.py": b"entry = True\n",
            "tui_gateway/server.py": b"server = True\n",
            "tui_gateway/transport.py": b"transport = True\n",
            "tui_gateway/ws.py": b"ws = True\n",
            "tui_gateway/methods_alpha.py": b'@method("old.method")\n',
            "gateway/platforms/api_server.py": b"api = True\n",
            "hermes_cli/dashboard_auth/ws_tickets.py": b"tickets = True\n",
            "hermes_cli/web_server.py": b"web = True\n",
        }
        for relative, content in files.items():
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self._git("add", ".")
        self._git("commit", "-q", "-m", "fixture")
        self.revision = self._git("rev-parse", "HEAD").strip()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return result.stdout

    def test_snapshot_reads_commit_objects_not_dirty_worktree(self) -> None:
        dirty = self.repo / "tui_gateway" / "methods_alpha.py"
        dirty.write_text('@method("dirty.method")\n', encoding="utf-8")
        source = derive.GitObjectSource(
            self.repo,
            revision=self.revision,
            expected_repository=derive.SOURCE_REPOSITORY,
        )
        snapshot = source.snapshot()
        self.assertIn("old.method", snapshot["tui_gateway/methods_alpha.py"].text)
        self.assertNotIn("dirty.method", snapshot["tui_gateway/methods_alpha.py"].text)
        self.assertEqual(snapshot["tui_gateway/__init__.py"].raw, b"")

    def test_snapshot_ignores_git_replace_objects(self) -> None:
        replacement_file = self.repo / "tui_gateway" / "methods_alpha.py"
        replacement_file.write_text(
            '@method("replacement.method")\n', encoding="utf-8"
        )
        self._git("add", ".")
        self._git("commit", "-q", "-m", "replacement")
        replacement = self._git("rev-parse", "HEAD").strip()
        self._git("replace", self.revision, replacement)

        source = derive.GitObjectSource(
            self.repo,
            revision=self.revision,
            expected_repository=derive.SOURCE_REPOSITORY,
        )
        snapshot = source.snapshot()
        method_text = snapshot["tui_gateway/methods_alpha.py"].text
        self.assertIn("old.method", method_text)
        self.assertNotIn("replacement.method", method_text)

    def test_noncanonical_origin_blocks(self) -> None:
        observed = "ssh" + "://private.example.invalid/operator/layout.git"
        self._git("remote", "set-url", "origin", observed)
        source = derive.GitObjectSource(
            self.repo,
            revision=self.revision,
            expected_repository=derive.SOURCE_REPOSITORY,
        )
        with self.assertRaisesRegex(
            derive.DerivationBlocked, "canonical repository"
        ) as caught:
            source.snapshot()
        self.assertNotIn(observed, str(caught.exception))
        self.assertNotIn("operator/layout", str(caught.exception))

    def test_git_failure_does_not_echo_raw_stderr(self) -> None:
        source = derive.GitObjectSource(
            self.repo,
            revision=self.revision,
            expected_repository=derive.SOURCE_REPOSITORY,
        )
        result = subprocess.CompletedProcess(
            args=["git"],
            returncode=9,
            stdout=b"",
            stderr=b"sensitive diagnostic must not escape",
        )
        with patch.object(derive.subprocess, "run", return_value=result):
            with self.assertRaises(derive.DerivationBlocked) as caught:
                source._git("rev-parse", "bad")
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertIn("exit 9", str(caught.exception))

    def test_missing_revision_blocks(self) -> None:
        source = derive.GitObjectSource(
            self.repo,
            revision="0" * 40,
            expected_repository=derive.SOURCE_REPOSITORY,
        )
        with self.assertRaisesRegex(derive.DerivationBlocked, "Git command failed"):
            source.snapshot()

    def test_non_utf8_blob_blocks(self) -> None:
        target = self.repo / "tui_gateway" / "methods_bad.py"
        target.write_bytes(b"\xff")
        self._git("add", ".")
        self._git("commit", "-q", "-m", "invalid utf8")
        revision = self._git("rev-parse", "HEAD").strip()
        source = derive.GitObjectSource(
            self.repo,
            revision=revision,
            expected_repository=derive.SOURCE_REPOSITORY,
        )
        with self.assertRaisesRegex(derive.DerivationBlocked, "strict UTF-8"):
            source.snapshot()


class OutputContractTests(unittest.TestCase):
    def test_alternate_revision_cannot_replace_the_pinned_default_catalog(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            status = derive.main([".", "--revision", "a" * 40])
        self.assertEqual(status, 2)
        self.assertIn("explicit non-default output", error.getvalue())

    def test_default_catalog_rejects_changed_provenance_profile(self) -> None:
        cases = (
            ["--derived-at", "2026-08-30"],
            ["--expected-counts", "169", "56", "42"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    status = derive.main([".", *arguments])
                self.assertEqual(status, 2)
                self.assertIn("complete pinned provenance profile", error.getvalue())

    def test_alternate_revision_requires_explicit_date_and_counts(self) -> None:
        with _temporary_directory() as temporary:
            output = pathlib.Path(temporary) / "alternate.json"
            cases = (
                ["--derived-at", "2026-08-30"],
                ["--expected-counts", "170", "58", "42"],
            )
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    error = io.StringIO()
                    with contextlib.redirect_stderr(error):
                        status = derive.main(
                            [
                                ".",
                                "--revision",
                                "a" * 40,
                                "--output",
                                str(output),
                                *arguments,
                            ]
                        )
                    self.assertEqual(status, 2)
                    self.assertIn(
                        "explicit derived-at and expected counts", error.getvalue()
                    )
                    self.assertFalse(output.exists())

    def test_atomic_write_replaces_and_leaves_no_temporary_file(self) -> None:
        with _temporary_directory() as temporary:
            target = pathlib.Path(temporary) / "nested" / "methods.json"
            derive.atomic_write(target, b"first\n")
            derive.atomic_write(target, b"second\n")
            self.assertEqual(target.read_bytes(), b"second\n")
            self.assertEqual(list(target.parent.glob(".methods.json.*.tmp")), [])

    def test_check_pass_and_drift_have_distinct_statuses(self) -> None:
        catalog = {"requests": [], "events": [], "rest_routes": []}
        expected = derive.catalog_bytes(catalog)
        with _temporary_directory() as temporary:
            target = pathlib.Path(temporary) / "methods.json"
            target.write_bytes(expected)
            with patch.object(
                derive.GitObjectSource, "snapshot", return_value={}
            ), patch.object(derive, "derive_catalog", return_value=catalog):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(
                        derive.run(pathlib.Path(temporary), target, check=True), 0
                    )
                self.assertEqual(output.getvalue().splitlines()[-1], "DERIVATION: PASS")

                target.write_bytes(b"drift\n")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(
                        derive.run(pathlib.Path(temporary), target, check=True), 1
                    )
                self.assertEqual(output.getvalue().splitlines()[-1], "DERIVATION: FAIL")

    def test_generation_status_does_not_disclose_output_parent(self) -> None:
        catalog = {"requests": [], "events": [], "rest_routes": []}
        with _temporary_directory() as temporary:
            target = pathlib.Path(temporary) / "nested" / "methods.json"
            with patch.object(
                derive.GitObjectSource, "snapshot", return_value={}
            ), patch.object(derive, "derive_catalog", return_value=catalog):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = derive.run(pathlib.Path(temporary), target, check=False)
        self.assertEqual(status, 0)
        self.assertIn("wrote methods.json", output.getvalue())
        self.assertNotIn(str(target.parent), output.getvalue())

    def test_main_translates_indeterminate_input_to_blocked(self) -> None:
        with patch.object(
            derive.GitObjectSource,
            "snapshot",
            side_effect=derive.DerivationBlocked("synthetic indeterminate input"),
        ):
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                status = derive.main([".", "--check"])
        self.assertEqual(status, 2)
        self.assertEqual(error.getvalue().splitlines()[-1], "DERIVATION: BLOCKED")

    def test_main_redacts_unexpected_exception_detail(self) -> None:
        with patch.object(
            derive.GitObjectSource,
            "snapshot",
            side_effect=RuntimeError("sensitive diagnostic must not escape"),
        ):
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                status = derive.main([".", "--check"])
        self.assertEqual(status, 2)
        self.assertIn("unexpected RuntimeError", error.getvalue())
        self.assertNotIn("sensitive", error.getvalue())


if __name__ == "__main__":
    unittest.main()
