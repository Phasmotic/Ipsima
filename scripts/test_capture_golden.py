from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib import error as urlerror
from urllib import parse as urlparse
import uuid

from scripts import capture_golden as capture


def encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canary(label: str = "probe") -> str:
    return label + "-" + uuid.uuid4().hex


def catalog() -> capture.ProtocolCatalog:
    return capture.ProtocolCatalog(
        requests=frozenset({"ping", "session.list"}),
        events=frozenset({"gateway.ready", "session.update"}),
        source_commit="0" * 40,
        source_repository=canary("repository"),
        source_inputs=(),
    )


def ready_frame(payload: object | None = None) -> capture.CapturedFrame:
    return capture.CapturedFrame(
        "in",
        encoded(
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {
                    "type": "gateway.ready",
                    "payload": {} if payload is None else payload,
                },
            }
        ),
    )


def request_frame(identifier: object = 91) -> capture.CapturedFrame:
    return capture.CapturedFrame(
        "out", encoded({"jsonrpc": "2.0", "id": identifier, "method": "ping"})
    )


def response_frame(identifier: object = 91) -> capture.CapturedFrame:
    return capture.CapturedFrame(
        "in", encoded({"jsonrpc": "2.0", "id": identifier, "result": {"pong": True}})
    )


def complete_capture(identifier: object = 91) -> list[capture.CapturedFrame]:
    return [ready_frame(), request_frame(identifier), response_frame(identifier)]


class FakeWebSocket:
    def __init__(self, incoming: list[object], send_error: BaseException | None = None):
        self.incoming = list(incoming)
        self.sent: list[str] = []
        self.send_error = send_error

    async def recv(self) -> object:
        if not self.incoming:
            raise asyncio.TimeoutError
        value = self.incoming.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def send(self, text: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(text)


class FakeConnection:
    def __init__(self, websocket: FakeWebSocket):
        self.websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *_args: object) -> None:
        return None


class RecordingConnector:
    def __init__(self, websocket: FakeWebSocket):
        self.websocket = websocket
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, uri: str, **options: object) -> FakeConnection:
        self.calls.append((uri, options))
        return FakeConnection(self.websocket)


class FakeHTTPResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


class FakeHTTPOpener:
    def __init__(self, result: object):
        self.result = result
        self.requests: list[tuple[object, object]] = []

    def open(self, request: object, timeout: object = None) -> object:
        self.requests.append((request, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeProcess:
    def __init__(
        self,
        *,
        pid: int = 7001,
        poll_result: int | None = None,
        waits: list[object] | None = None,
    ) -> None:
        self.pid = pid
        self.poll_result = poll_result
        self.waits = list(waits or [0])
        self.wait_calls: list[object] = []
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        return self.poll_result

    def wait(self, timeout: object = None) -> int:
        self.wait_calls.append(timeout)
        value = self.waits.pop(0) if self.waits else 0
        if isinstance(value, BaseException):
            raise value
        self.poll_result = int(value)
        return self.poll_result

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1


class BlockedAssertions(unittest.TestCase):
    def assertBlocked(self, reason: str):  # noqa: N802 - mirrors unittest helpers
        return self.assertRaisesRegex(capture.CaptureBlocked, "^" + reason + "$")


class StrictJSONTests(BlockedAssertions):
    def test_valid_json_and_scalar_types_round_trip(self) -> None:
        value = {"array": [None, False, 7, 0.25, "text"], "object": {"ok": True}}
        self.assertEqual(capture.strict_json_loads(encoded(value)), value)

    def test_empty_non_string_duplicate_nonfinite_and_trailing_data_are_rejected(self) -> None:
        cases: tuple[object, ...] = (
            "",
            b"{}",
            '{"a":1,"a":2}',
            '{"value":NaN}',
            '{"value":Infinity}',
            '{}{}',
        )
        for value in cases:
            with self.subTest(value=type(value).__name__), self.assertBlocked(
                "raw-frame-invalid"
            ):
                capture.strict_json_loads(value)  # type: ignore[arg-type]

    def test_frame_size_is_measured_as_utf8_bytes(self) -> None:
        text = json.dumps("é", ensure_ascii=False)
        with self.assertBlocked("raw-frame-too-large"):
            capture.strict_json_loads(text, max_bytes=len(text))

    def test_depth_collection_integer_and_surrogate_limits_fail_closed(self) -> None:
        deep: object = 0
        for _ in range(4):
            deep = [deep]
        cases = (
            lambda: capture.strict_json_loads(encoded(deep), max_depth=2),
            lambda: capture.strict_json_loads("[0,0,0]", max_collection_items=2),
            lambda: capture.strict_json_loads("1" * 65),
            lambda: capture.strict_json_loads(json.dumps("\ud800")),
        )
        for operation in cases:
            with self.subTest(operation=operation), self.assertBlocked("raw-frame-invalid"):
                operation()

    def test_rpc_ids_are_type_exact_and_int64_bounded(self) -> None:
        self.assertEqual(capture._rpc_id_key(None), ("null", None))
        self.assertEqual(capture._rpc_id_key(0), ("int", 0))
        self.assertEqual(capture._rpc_id_key("0"), ("string", "0"))
        for value in (True, 0.5, 1 << 63, -(1 << 63) - 1, [], {}):
            with self.subTest(value=value), self.assertBlocked("capture-flow-invalid"):
                capture._rpc_id_key(value)


class SanitizerAndFlowTests(BlockedAssertions):
    def test_sanitizer_removes_names_values_unknown_members_and_aliases_ids(self) -> None:
        marker_one = canary("leaf")
        marker_two = canary("field")
        raw = {
            "jsonrpc": "2.0",
            "id": marker_one,
            "method": "session.list",
            "params": {
                marker_two: [marker_one, True, 813, 1.25, None],
                canary("nested-name"): {canary("inner-name"): marker_two},
            },
            "unknown": marker_one,
        }
        sanitized = capture.sanitize_envelope(raw, catalog(), capture.IDAliases())
        rendered = encoded(sanitized)
        self.assertNotIn(marker_one, rendered)
        self.assertNotIn(marker_two, rendered)
        self.assertNotIn("unknown", sanitized)
        self.assertEqual(sanitized["id"], "id-1")
        self.assertIsNone(
            capture.conformance.sanitized_fixture_problem(
                sanitized, catalog().requests, catalog().events
            )
        )

    def test_object_key_renaming_is_independent_of_captured_key_order_and_names(self) -> None:
        first = {canary("one"): "text", canary("two"): 7}
        second = {canary("three"): 7, canary("four"): "different"}
        self.assertEqual(capture._sanitize_generic(first), capture._sanitize_generic(second))

    def test_event_keeps_only_registered_type_and_sanitized_payload(self) -> None:
        marker = canary("payload")
        raw = {
            "jsonrpc": "2.0",
            "method": "event",
            "params": {
                "type": "session.update",
                "payload": {canary("name"): marker},
                "extra": marker,
            },
            "extra": marker,
        }
        value = capture.sanitize_envelope(raw, catalog(), capture.IDAliases())
        self.assertEqual(set(value), {"jsonrpc", "method", "params"})
        self.assertEqual(set(value["params"]), {"type", "payload"})  # type: ignore[arg-type]
        self.assertNotIn(marker, encoded(value))

    def test_error_is_normalized_without_retaining_code_message_or_keys(self) -> None:
        marker = canary("error")
        raw = {
            "jsonrpc": "2.0",
            "id": 99,
            "error": {
                "code": -32123,
                "message": marker,
                "data": {canary("detail"): marker},
                "unknown": marker,
            },
        }
        value = capture.sanitize_envelope(raw, catalog(), capture.IDAliases())
        self.assertEqual(value["id"], 1)
        self.assertEqual(value["error"]["code"], -32000)  # type: ignore[index]
        self.assertEqual(
            value["error"]["message"], capture.conformance.REDACTED_TEXT  # type: ignore[index]
        )
        self.assertNotIn(marker, encoded(value))
        self.assertNotIn("unknown", value["error"])  # type: ignore[operator]

    def test_id_aliases_are_stable_per_type_and_do_not_collide(self) -> None:
        aliases = capture.IDAliases()
        self.assertEqual(aliases.alias(42), 1)
        self.assertEqual(aliases.alias(7), 2)
        self.assertEqual(aliases.alias(42), 1)
        self.assertEqual(aliases.alias("42"), "id-1")
        self.assertEqual(aliases.alias("other"), "id-2")
        self.assertEqual(aliases.alias(None), None)

    def test_complete_minimal_capture_is_canonical_and_preserves_causal_order(self) -> None:
        lines = capture.sanitize_capture(complete_capture(777), catalog())
        self.assertEqual(len(lines), 3)
        values = [json.loads(line) for line in lines]
        self.assertEqual(values[0]["params"]["type"], "gateway.ready")
        self.assertEqual(values[1]["method"], "ping")
        self.assertEqual(values[1]["id"], values[2]["id"])
        for line, value in zip(lines, values, strict=True):
            self.assertEqual(line, capture.conformance.canonical(value))

    def test_capture_projection_is_independent_of_ids_and_captured_leaf_values(self) -> None:
        first = complete_capture(88)
        first[0] = ready_frame({canary("a"): canary("b")})
        second = complete_capture(999)
        second[0] = ready_frame({canary("c"): canary("d")})
        self.assertEqual(
            capture.sanitize_capture(first, catalog()),
            capture.sanitize_capture(second, catalog()),
        )

    def test_wrong_count_order_direction_or_method_is_incomplete(self) -> None:
        for frames in ([], complete_capture()[:2], complete_capture() + [response_frame()]):
            with self.subTest(count=len(frames)), self.assertBlocked("capture-incomplete"):
                capture.sanitize_capture(frames, catalog())

        incomplete = (
            [request_frame(), ready_frame(), response_frame()],
            [
                ready_frame(),
                capture.CapturedFrame(
                    "out",
                    encoded({"jsonrpc": "2.0", "id": 91, "method": "session.list"}),
                ),
                response_frame(),
            ],
        )
        for frames in incomplete:
            with self.subTest(frames=frames), self.assertBlocked("capture-incomplete"):
                capture.sanitize_capture(frames, catalog())
        wrong_direction = [
            ready_frame(),
            capture.CapturedFrame("in", request_frame().text),
            response_frame(),
        ]
        with self.assertBlocked("capture-flow-invalid"):
            capture.sanitize_capture(wrong_direction, catalog())

    def test_duplicate_request_id_unsolicited_response_and_id_type_mismatch_fail(self) -> None:
        pending: dict[tuple[str, object], None] = {}
        capture._classify_flow_frame(request_frame(1), {"method": "ping", "id": 1}, pending)
        with self.assertBlocked("capture-flow-invalid"):
            capture._classify_flow_frame(request_frame(1), {"method": "ping", "id": 1}, pending)
        with self.assertBlocked("capture-flow-invalid"):
            capture._classify_flow_frame(
                response_frame(2), {"jsonrpc": "2.0", "id": 2, "result": {}}, pending
            )
        mismatched = [ready_frame(), request_frame(1), response_frame("1")]
        with self.assertBlocked("capture-flow-invalid"):
            capture.sanitize_capture(mismatched, catalog())

    def test_unregistered_method_event_and_malformed_envelopes_fail_closed(self) -> None:
        marker = canary("unregistered")
        cases = (
            {"jsonrpc": "2.0", "id": 1, "method": marker},
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {"type": marker, "payload": {}},
            },
            {"jsonrpc": "2.0", "method": "event", "params": {"type": "gateway.ready"}},
            {"jsonrpc": "1.0", "id": 1, "method": "ping"},
        )
        for value in cases:
            with self.subTest(keys=tuple(value)), self.assertRaises(capture.CaptureBlocked):
                capture.sanitize_envelope(value, catalog(), capture.IDAliases())


class FixtureWriteTests(BlockedAssertions):
    def valid_lines(self) -> tuple[bytes, ...]:
        return capture.sanitize_capture(complete_capture(), catalog())

    def test_atomic_write_emits_valid_canonical_jsonl_with_final_newline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-fixture-test-") as temporary:
            path = Path(temporary) / "nested" / "golden.jsonl"
            lines = self.valid_lines()
            capture.write_fixture_atomic(path, lines, catalog())
            self.assertEqual(path.read_bytes(), b"\n".join(lines) + b"\n")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_validation_occurs_before_directory_or_temporary_file_creation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-preflight-test-") as temporary:
            parent = Path(temporary) / "must-not-exist"
            path = parent / "golden.jsonl"
            unsafe = (encoded({"jsonrpc": "2.0", "method": "ping", "params": canary()}).encode(),)
            with self.assertBlocked("fixture-invalid"):
                capture.write_fixture_atomic(path, unsafe, catalog())
            self.assertFalse(parent.exists())

    def test_replace_failure_preserves_existing_fixture_and_removes_temporary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-replace-test-") as temporary:
            path = Path(temporary) / "golden.jsonl"
            before = b"existing\n"
            path.write_bytes(before)
            with mock.patch.object(capture.os, "replace", side_effect=OSError):
                with self.assertBlocked("fixture-write"):
                    capture.write_fixture_atomic(path, self.valid_lines(), catalog())
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual([item.name for item in path.parent.iterdir()], [path.name])

class RuntimeAndEnvironmentTests(BlockedAssertions):
    def locked_runtime(self, root: Path) -> tuple[capture.Runtime, Path, Path]:
        checkout = root / "checkout"
        binary_directory = checkout / ".venv" / "bin"
        binary_directory.mkdir(parents=True)
        executable = binary_directory / "hermes"
        python = binary_directory / "python"
        executable.write_bytes(b"entry")
        python.write_bytes(b"runtime")
        uv = root / "uv"
        uv.write_bytes(b"tool")
        module = checkout / "module.py"
        module.write_bytes(b"module")
        return capture.Runtime(checkout, executable, python), uv, module

    def test_isolated_environment_is_an_exact_allowlist_with_private_directories(self) -> None:
        marker_name = "AMBIENT_" + uuid.uuid4().hex.upper()
        marker_value = canary("ambient")
        session_marker = canary("session")
        parent_pid = os.getpid()
        parent_ticks = str(parent_pid + 17)
        parent_nonce = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="capture-env-test-") as temporary:
            root = Path(temporary)
            runtime = capture.Runtime(root, root / "venv" / "bin" / "hermes", root / "venv" / "bin" / "python")
            with mock.patch.dict(os.environ, {marker_name: marker_value}, clear=False):
                environment, workspace = capture.build_isolated_environment(
                    root / "capture",
                    runtime,
                    session_marker,
                    parent_pid=parent_pid,
                    parent_ticks=parent_ticks,
                    parent_nonce=parent_nonce,
                )
            expected = {
                "HOME",
                "TMPDIR",
                "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME",
                "XDG_DATA_HOME",
                "XDG_STATE_HOME",
                "HERMES_HOME",
                "HERMES_MANAGED_DIR",
                "HERMES_DASHBOARD_SESSION_TOKEN",
                "HERMES_PARENT_PID",
                "HERMES_PARENT_START_MARKER",
                "HERMES_PARENT_NONCE",
                "PATH",
                "LANG",
                "LC_ALL",
                "NO_COLOR",
                "PYTHONHASHSEED",
                "PYTHONNOUSERSITE",
                "PYTHONDONTWRITEBYTECODE",
                "PYTHONUNBUFFERED",
            }
            self.assertEqual(set(environment), expected)
            self.assertNotIn(marker_name, environment)
            self.assertNotIn(marker_value, environment.values())
            self.assertEqual(environment["HERMES_DASHBOARD_SESSION_TOKEN"], session_marker)
            self.assertEqual(environment["HERMES_PARENT_PID"], str(parent_pid))
            self.assertEqual(
                environment["HERMES_PARENT_START_MARKER"], "linux:" + parent_ticks
            )
            self.assertEqual(environment["HERMES_PARENT_NONCE"], parent_nonce)
            self.assertEqual(environment["PYTHONDONTWRITEBYTECODE"], "1")
            self.assertEqual(workspace, root / "capture" / "workspace")
            self.assertFalse(Path(environment["HERMES_MANAGED_DIR"]).exists())
            for key in (
                "HOME",
                "TMPDIR",
                "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME",
                "XDG_DATA_HOME",
                "XDG_STATE_HOME",
                "HERMES_HOME",
            ):
                directory = Path(environment[key])
                self.assertTrue(directory.is_dir())
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

    def test_preexisting_managed_directory_blocks_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-managed-test-") as temporary:
            root = Path(temporary) / "capture"
            root.mkdir()
            (root / "managed-disabled").mkdir()
            runtime = capture.Runtime(root, root / "bin" / "hermes", root / "bin" / "python")
            with self.assertBlocked("runtime-environment"):
                capture.build_isolated_environment(
                    root,
                    runtime,
                    canary("session"),
                    parent_pid=os.getpid(),
                    parent_ticks=str(os.getpid()),
                    parent_nonce=uuid.uuid4().hex,
                )

    def test_invalid_parent_watchdog_values_block_before_creating_state(self) -> None:
        runtime = capture.Runtime(Path("runtime"), Path("runtime/bin/hermes"), Path("runtime/bin/python"))
        cases = (
            {"parent_pid": 0, "parent_ticks": "1", "parent_nonce": uuid.uuid4().hex},
            {"parent_pid": os.getpid(), "parent_ticks": "0", "parent_nonce": uuid.uuid4().hex},
            {
                "parent_pid": os.getpid(),
                "parent_ticks": str(os.getpid()),
                "parent_nonce": canary("invalid-nonce"),
            },
        )
        with tempfile.TemporaryDirectory(prefix="capture-watchdog-test-") as temporary:
            base = Path(temporary)
            for index, values in enumerate(cases):
                root = base / str(index)
                with self.subTest(index=index), self.assertBlocked("runtime-environment"):
                    capture.build_isolated_environment(
                        root, runtime, canary("session"), **values  # type: ignore[arg-type]
                    )
                self.assertFalse(root.exists())

    def test_git_probe_uses_minimal_environment_and_rejects_stderr(self) -> None:
        marker_name = "AMBIENT_" + uuid.uuid4().hex.upper()
        completed = subprocess.CompletedProcess([], 0, stdout="value\n", stderr="")
        with mock.patch.dict(os.environ, {marker_name: canary("ambient")}, clear=False), mock.patch.object(
            capture.subprocess, "run", return_value=completed
        ) as run:
            self.assertEqual(capture._git_output(Path("."), ["status"]), "value")
        environment = run.call_args.kwargs["env"]
        self.assertEqual(
            set(environment),
            {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "LANG", "LC_ALL", "PATH"},
        )
        self.assertNotIn(marker_name, environment)
        completed.stderr = "warning"
        with mock.patch.object(capture.subprocess, "run", return_value=completed):
            with self.assertBlocked("runtime-unavailable"):
                capture._git_output(Path("."), ["status"])

    def test_runtime_is_bound_to_commit_remote_cleanliness_inputs_and_interpreter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-runtime-test-") as temporary:
            root = Path(temporary)
            binary = root / ".venv" / "bin" / "hermes"
            python = binary.with_name("python")
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"entry")
            python.write_bytes(b"runtime")
            source = root / "source.py"
            source.write_bytes(b"source")
            digest = capture.hashlib.sha256(source.read_bytes()).hexdigest()
            expected_catalog = capture.ProtocolCatalog(
                requests=frozenset({"ping"}),
                events=frozenset({"gateway.ready"}),
                source_commit="1" * 40,
                source_repository=canary("repository"),
                source_inputs=((source.name, digest),),
            )
            evidence = [
                expected_catalog.source_commit,
                expected_catalog.source_repository,
                "",
            ]
            with mock.patch.object(capture, "_git_output", side_effect=evidence), mock.patch.object(
                capture.os, "access", return_value=True
            ), mock.patch.object(capture.sys, "executable", os.fspath(python)), mock.patch.object(
                capture.sys, "prefix", os.fspath(python.parent.parent)
            ):
                runtime = capture.verify_runtime(root, expected_catalog)
            self.assertEqual(runtime.root, root.resolve())
            self.assertEqual(runtime.executable, binary.resolve())
            self.assertEqual(runtime.python, python)

    def test_runtime_drift_dirty_tree_dotenv_and_ambiguous_executable_block(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-runtime-block-test-") as temporary:
            root = Path(temporary)
            expected = catalog()
            cases = (
                ([canary("wrong-commit")], "runtime-drift"),
                ([expected.source_commit, canary("wrong-repository")], "runtime-drift"),
                ([expected.source_commit, expected.source_repository, " M source.py"], "runtime-dirty"),
            )
            for evidence, reason in cases:
                with self.subTest(reason=reason), mock.patch.object(
                    capture, "_git_output", side_effect=evidence
                ), self.assertBlocked(reason):
                    capture.verify_runtime(root, expected)
            (root / ".env").write_text("blocked", encoding="utf-8")
            with self.assertBlocked("runtime-environment"):
                capture.verify_runtime(root, expected)
            (root / ".env").unlink()
            for name in (".venv", "venv"):
                binary = root / name / "bin" / "hermes"
                binary.parent.mkdir(parents=True)
                binary.write_bytes(b"entry")
            with mock.patch.object(
                capture, "_git_output", side_effect=[expected.source_commit, expected.source_repository, ""]
            ), mock.patch.object(capture.os, "access", return_value=True), self.assertBlocked(
                "runtime-unavailable"
            ):
                capture.verify_runtime(root, expected)

    def test_locked_environment_uses_exact_offline_uv_contract_and_private_env(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-lock-test-") as temporary:
            runtime, uv, module = self.locked_runtime(Path(temporary))
            version = subprocess.CompletedProcess(
                [], 0, stdout=("uv " + capture.EXPECTED_UV_VERSION + "\n").encode(), stderr=b""
            )
            synced = subprocess.CompletedProcess([], 0, stdout=b"checked\n", stderr=b"")
            specification = mock.Mock(origin=os.fspath(module))
            ambient_name = "AMBIENT_" + uuid.uuid4().hex.upper()
            with mock.patch.dict(os.environ, {ambient_name: canary("ambient")}, clear=False), mock.patch.object(
                capture.os, "access", return_value=True
            ), mock.patch.object(
                capture.subprocess, "run", side_effect=[version, synced]
            ) as run, mock.patch.object(
                capture, "_git_output", return_value=""
            ) as git, mock.patch.object(
                capture.importlib.util, "find_spec", return_value=specification
            ) as find_spec:
                capture.verify_locked_environment(runtime, uv)

            self.assertEqual(run.call_count, 2)
            version_call, sync_call = run.call_args_list
            self.assertEqual(version_call.args[0], [os.fspath(uv.resolve()), "--version"])
            self.assertEqual(
                sync_call.args[0],
                [
                    os.fspath(uv.resolve()),
                    "sync",
                    "--check",
                    "--frozen",
                    "--no-dev",
                    "--extra",
                    "web",
                    "--python",
                    os.fspath(runtime.python),
                    "--offline",
                    "--no-progress",
                    "--no-config",
                    "--color",
                    "never",
                ],
            )
            for call in run.call_args_list:
                environment = call.kwargs["env"]
                self.assertEqual(
                    set(environment),
                    {
                        "HOME",
                        "LANG",
                        "LC_ALL",
                        "PATH",
                        "UV_NO_CONFIG",
                        "UV_NO_PROGRESS",
                        "UV_OFFLINE",
                        "UV_PYTHON_DOWNLOADS",
                    },
                )
                self.assertNotIn(ambient_name, environment)
                self.assertEqual(environment["UV_OFFLINE"], "1")
                self.assertEqual(call.kwargs["cwd"], runtime.root)
            git.assert_called_once_with(
                runtime.root, ["status", "--porcelain=v1", "--untracked-files=all"]
            )
            self.assertEqual(
                [call.args[0] for call in find_spec.call_args_list],
                list(capture.RUNTIME_MODULES),
            )

    def test_locked_environment_blocks_missing_wrong_or_empty_uv_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-lock-block-test-") as temporary:
            runtime, uv, _module = self.locked_runtime(Path(temporary))
            wrong_version = subprocess.CompletedProcess(
                [], 0, stdout=b"uv 0.0.0\n", stderr=b""
            )
            empty = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
            expected_version = subprocess.CompletedProcess(
                [], 0, stdout=("uv " + capture.EXPECTED_UV_VERSION + "\n").encode(), stderr=b""
            )
            for results in ([wrong_version], [empty], [expected_version, empty]):
                with self.subTest(count=len(results)), mock.patch.object(
                    capture.os, "access", return_value=True
                ), mock.patch.object(
                    capture.subprocess, "run", side_effect=results
                ), self.assertBlocked("runtime-drift"):
                    capture.verify_locked_environment(runtime, uv)
            with mock.patch.object(capture.shutil, "which", return_value=None), self.assertBlocked(
                "runtime-unavailable"
            ):
                capture.verify_locked_environment(runtime)

    def test_locked_environment_blocks_dirty_checkout_and_bad_module_origins(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-lock-origin-test-") as temporary:
            root = Path(temporary)
            runtime, uv, module = self.locked_runtime(root)
            successful = [
                subprocess.CompletedProcess(
                    [], 0, stdout=("uv " + capture.EXPECTED_UV_VERSION + "\n").encode(), stderr=b""
                ),
                subprocess.CompletedProcess([], 0, stdout=b"checked\n", stderr=b""),
            ]
            with mock.patch.object(capture.os, "access", return_value=True), mock.patch.object(
                capture.subprocess, "run", side_effect=successful
            ), mock.patch.object(capture, "_git_output", return_value=" M lock"), self.assertBlocked(
                "runtime-dirty"
            ):
                capture.verify_locked_environment(runtime, uv)

            outside = root / "outside.py"
            outside.write_bytes(b"outside")
            bad_specifications = (None, mock.Mock(origin=None), mock.Mock(origin=os.fspath(outside)), mock.Mock(origin=os.fspath(root / "missing.py")))
            for specification in bad_specifications:
                runs = [
                    subprocess.CompletedProcess(
                        [], 0, stdout=("uv " + capture.EXPECTED_UV_VERSION + "\n").encode(), stderr=b""
                    ),
                    subprocess.CompletedProcess([], 0, stdout=b"checked\n", stderr=b""),
                ]
                with self.subTest(origin=getattr(specification, "origin", None)), mock.patch.object(
                    capture.os, "access", return_value=True
                ), mock.patch.object(
                    capture.subprocess, "run", side_effect=runs
                ), mock.patch.object(
                    capture, "_git_output", return_value=""
                ), mock.patch.object(
                    capture.importlib.util, "find_spec", return_value=specification
                ), self.assertBlocked("runtime-drift"):
                    capture.verify_locked_environment(runtime, uv)
            self.assertTrue(module.is_file())

    def test_proc_enumeration_counts_only_exact_serve_and_dashboard_argv_shapes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-proc-test-") as temporary:
            root = Path(temporary)
            commands = (
                ("101", ["/runtime/hermes", "serve"]),
                ("102", ["/runtime/hermes", "dashboard", "--isolated"]),
                ("103", ["/runtime/python", "/runtime/hermes", "serve"]),
                ("104", ["/runtime/python", "-m", "hermes_cli", "dashboard"]),
                ("105", ["/runtime/python", "-m", "hermes_cli.main", "serve"]),
                ("113", ["/runtime/python", "-I", "-B", "-m", "hermes_cli.main", "serve"]),
                ("106", ["/runtime/hermes", "other"]),
                ("107", ["/runtime/not-hermes", "serve"]),
                ("108", ["/runtime/hermes", "flag", "serve"]),
                ("109", ["/runtime/shell", "/runtime/hermes", "serve"]),
                ("110", ["/runtime/shell", "-m", "hermes_cli", "serve"]),
                ("111", ["/runtime/python", "-m", "other", "serve"]),
                ("112", ["/runtime/python", "-m", "hermes_cli", "flag", "serve"]),
            )
            for pid, arguments in commands:
                directory = root / pid
                directory.mkdir()
                (directory / "cmdline").write_bytes(
                    b"\0".join(item.encode("utf-8") for item in arguments) + b"\0"
                )
            (root / "not-a-pid").mkdir()
            self.assertEqual(capture.count_existing_backends(root), 6)

    def test_process_start_ticks_handles_spaces_and_parentheses_in_command_name(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-stat-test-") as temporary:
            root = Path(temporary)
            pid = 321
            directory = root / str(pid)
            directory.mkdir()
            fields = ["S", *[str(index) for index in range(1, 19)], "start-marker"]
            (directory / "stat").write_text(
                f"{pid} (command ) with space) " + " ".join(fields), encoding="utf-8"
            )
            self.assertEqual(capture.process_start_ticks(pid, root), "start-marker")
            self.assertIsNone(capture.process_start_ticks(pid + 1, root))

    def test_parent_death_guard_arms_sigkill_and_rechecks_exact_parent(self) -> None:
        parent_pid = os.getpid()
        libc = mock.Mock()
        libc.prctl.return_value = 0
        with mock.patch.object(capture.ctypes, "CDLL", return_value=libc), mock.patch.object(
            capture.os, "getppid", return_value=parent_pid
        ), mock.patch.object(capture.os, "_exit") as exit_process:
            capture.arm_parent_death_signal(parent_pid)
        libc.prctl.assert_called_once_with(1, int(signal.SIGKILL), 0, 0, 0)
        exit_process.assert_not_called()

    def test_parent_death_guard_exits_if_arming_or_parent_recheck_fails(self) -> None:
        parent_pid = os.getpid()
        for prctl_result, observed_parent in ((1, parent_pid), (0, parent_pid + 1)):
            libc = mock.Mock()
            libc.prctl.return_value = prctl_result
            with self.subTest(prctl_result=prctl_result), mock.patch.object(
                capture.ctypes, "CDLL", return_value=libc
            ), mock.patch.object(
                capture.os, "getppid", return_value=observed_parent
            ), mock.patch.object(capture.os, "_exit") as exit_process:
                capture.arm_parent_death_signal(parent_pid)
            exit_process.assert_called_with(127)


class ReadinessAndOwnershipTests(BlockedAssertions):
    def test_ready_parser_requires_one_exact_bounded_marker(self) -> None:
        self.assertEqual(
            capture.parse_ready_output(b"startup\nHERMES_BACKEND_READY port=4321\n"), 4321
        )
        cases = (
            b"",
            b"prefix HERMES_BACKEND_READY port=4321\n",
            b"HERMES_BACKEND_READY port=0\n",
            b"HERMES_BACKEND_READY port=65536\n",
            b"HERMES_BACKEND_READY port=12\nHERMES_BACKEND_READY port=13\n",
            b"HERMES_BACKEND_READY port=12 suffix\n",
            b"x" * (capture.MAX_LOG_BYTES + 1),
        )
        for raw in cases:
            with self.subTest(size=len(raw)), self.assertBlocked("readiness-failed"):
                capture.parse_ready_output(raw)

    def test_wait_for_ready_checks_process_identity_and_returns_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-ready-test-") as temporary:
            log = Path(temporary) / "gateway.log"
            log.write_bytes(b"HERMES_BACKEND_READY port=7654\n")
            process = FakeProcess(pid=8081)
            with mock.patch.object(capture, "process_start_ticks", return_value="ticks"), mock.patch.object(
                capture.time, "monotonic", side_effect=[0.0, 0.0]
            ):
                self.assertEqual(capture.wait_for_ready(process, log, "ticks", 1.0), 7654)

    def test_wait_for_ready_blocks_exit_pid_reuse_duplicate_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-ready-block-test-") as temporary:
            log = Path(temporary) / "gateway.log"
            log.write_bytes(b"")
            cases = (
                (FakeProcess(poll_result=3), "ticks", [0.0, 0.0], "gateway-exited"),
                (FakeProcess(), "different", [0.0, 0.0], "gateway-exited"),
            )
            for process, observed, clock, reason in cases:
                with self.subTest(reason=reason), mock.patch.object(
                    capture, "process_start_ticks", return_value=observed
                ), mock.patch.object(capture.time, "monotonic", side_effect=clock), self.assertBlocked(reason):
                    capture.wait_for_ready(process, log, "ticks", 1.0)
            log.write_bytes(
                b"HERMES_BACKEND_READY port=12\nHERMES_BACKEND_READY port=13\n"
            )
            with mock.patch.object(capture, "process_start_ticks", return_value="ticks"), mock.patch.object(
                capture.time, "monotonic", side_effect=[0.0, 0.0]
            ), self.assertBlocked("readiness-failed"):
                capture.wait_for_ready(FakeProcess(), log, "ticks", 1.0)
            log.write_bytes(b"")
            with mock.patch.object(capture, "process_start_ticks", return_value="ticks"), mock.patch.object(
                capture.time, "monotonic", side_effect=[0.0, 0.0, 2.0]
            ), mock.patch.object(capture.time, "sleep") as sleep, self.assertBlocked(
                "gateway-timeout"
            ):
                capture.wait_for_ready(FakeProcess(), log, "ticks", 1.0)
            sleep.assert_called_once_with(0.1)

    def test_ownership_requires_both_authenticated_probes_and_exact_nonce(self) -> None:
        marker = canary("session")
        nonce = canary("nonce")
        evidence = [
            {
                "ok": True,
                "version": capture.EXPECTED_HERMES_VERSION,
                "auth_required": False,
            },
            {"ok": True, "sshOwnerNonce": nonce, "protocolVersion": 1},
        ]
        with mock.patch.object(capture, "_http_json", side_effect=evidence) as probe:
            capture.prove_ownership(4001, marker, nonce)
        self.assertEqual(
            probe.call_args_list,
            [
                mock.call(4001, "/api/health", marker),
                mock.call(4001, "/api/ssh/ownership", marker),
            ],
        )

    def test_http_probe_disables_proxy_and_redirect_following(self) -> None:
        session_marker = canary("session")
        body = encoded({"ok": True}).encode("utf-8")
        opener = FakeHTTPOpener(FakeHTTPResponse(body))
        with mock.patch.object(
            capture.urlrequest, "build_opener", return_value=opener
        ) as build:
            self.assertEqual(capture._http_json(4101, "/api/health", session_marker), {"ok": True})
        handlers = build.call_args.args
        self.assertEqual(len(handlers), 2)
        self.assertIsInstance(handlers[0], capture.urlrequest.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], capture._NoRedirect)
        self.assertIsNone(
            handlers[1].redirect_request(object(), object(), 302, "moved", object(), "ignored")
        )
        request, timeout = opener.requests[0]
        self.assertEqual(timeout, 5)
        self.assertEqual(request.get_header("X-hermes-session-token"), session_marker)

    def test_http_redirect_response_is_never_followed_or_accepted(self) -> None:
        target = capture._network_url("http", 4102, "/redirected")
        redirect = urlerror.HTTPError(target, 302, "moved", {}, None)
        opener = FakeHTTPOpener(redirect)
        with mock.patch.object(capture.urlrequest, "build_opener", return_value=opener), self.assertBlocked(
            "ownership-failed"
        ):
            capture._http_json(4102, "/api/health", canary("session"))
        self.assertEqual(len(opener.requests), 1)

    def test_ownership_rejects_health_auth_and_every_ownership_mismatch(self) -> None:
        marker = canary("session")
        nonce = canary("nonce")
        valid_health = {
            "ok": True,
            "version": capture.EXPECTED_HERMES_VERSION,
            "auth_required": False,
        }
        cases = (
            [{"ok": False, "version": capture.EXPECTED_HERMES_VERSION, "auth_required": False}],
            [{"ok": True, "version": capture.EXPECTED_HERMES_VERSION, "auth_required": True}],
            [{"ok": True, "version": canary("wrong-version"), "auth_required": False}],
            [{**valid_health, "extra": False}],
            [valid_health, {"ok": True, "sshOwnerNonce": canary("other"), "protocolVersion": 1}],
            [valid_health, {"ok": True, "sshOwnerNonce": nonce, "protocolVersion": 2}],
            [valid_health, {"ok": True, "sshOwnerNonce": nonce, "protocolVersion": 1, "extra": False}],
        )
        for evidence in cases:
            with self.subTest(count=len(evidence)), mock.patch.object(
                capture, "_http_json", side_effect=evidence
            ), self.assertBlocked("ownership-failed"):
                capture.prove_ownership(4001, marker, nonce)


class WebSocketTests(BlockedAssertions):
    def run_gateway(self, incoming: list[object], **kwargs: object):
        websocket = FakeWebSocket(incoming, kwargs.pop("send_error", None))
        connector = RecordingConnector(websocket)
        result = asyncio.run(
            capture.drive_gateway(
                5001,
                kwargs.pop("session_marker", canary("session")),
                kwargs.pop("protocol_catalog", catalog()),
                connector=connector,
                timeout=0.5,
            )
        )
        return result, websocket, connector

    def valid_messages(self) -> list[str]:
        return [ready_frame().text, response_frame(1).text]

    def test_minimal_registered_ping_capture_uses_bounded_no_proxy_connection(self) -> None:
        session_marker = canary("session")
        frames, websocket, connector = self.run_gateway(
            self.valid_messages(), session_marker=session_marker
        )
        self.assertEqual(frames, [ready_frame(), request_frame(1), response_frame(1)])
        self.assertEqual(len(websocket.sent), 1)
        self.assertEqual(json.loads(websocket.sent[0]), {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        uri, options = connector.calls[0]
        parsed = urlparse.urlsplit(uri)
        self.assertEqual(parsed.hostname, capture.LOOPBACK)
        self.assertEqual(urlparse.parse_qs(parsed.query), {"token": [session_marker]})
        self.assertEqual(
            options,
            {
                "max_size": capture.MAX_FRAME_BYTES,
                "open_timeout": 0.5,
                "close_timeout": 5,
                "proxy": None,
            },
        )

    def test_binary_duplicate_invalid_ready_and_send_failure_block(self) -> None:
        duplicate_ready = '{"jsonrpc":"2.0","method":"event","method":"event","params":{"payload":{},"type":"gateway.ready"}}'
        invalid_ready = encoded({"jsonrpc": "2.0", "method": "event", "params": {"type": "gateway.ready"}})
        cases = (
            ([b"binary", response_frame(1).text], None, "raw-frame-binary"),
            ([duplicate_ready, response_frame(1).text], None, "raw-frame-invalid"),
            ([invalid_ready, response_frame(1).text], None, "capture-flow-invalid"),
            (self.valid_messages(), OSError(), "gateway-transport"),
        )
        for incoming, send_error, reason in cases:
            with self.subTest(reason=reason), self.assertBlocked(reason):
                self.run_gateway(incoming, send_error=send_error)

    def test_timeout_transport_and_wrong_responses_are_reason_specific(self) -> None:
        wrong = (
            encoded({"jsonrpc": "2.0", "id": 2, "result": {"pong": True}}),
            encoded({"jsonrpc": "2.0", "id": True, "result": {"pong": True}}),
            encoded({"jsonrpc": "2.0", "id": 1, "result": {"pong": False}}),
            encoded({"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "x"}}),
            encoded({"jsonrpc": "2.0", "method": "event", "params": {"type": "session.update", "payload": {}}}),
        )
        for response in wrong:
            with self.subTest(response=response), self.assertBlocked("capture-flow-invalid"):
                self.run_gateway([ready_frame().text, response])
        with self.assertBlocked("gateway-timeout"):
            self.run_gateway([asyncio.TimeoutError()])
        with self.assertBlocked("gateway-transport"):
            self.run_gateway([OSError()])

    def test_missing_registered_ping_or_ready_blocks_before_connecting(self) -> None:
        for protocol_catalog in (
            capture.ProtocolCatalog(frozenset(), frozenset({"gateway.ready"}), "", "", ()),
            capture.ProtocolCatalog(frozenset({"ping"}), frozenset(), "", "", ()),
        ):
            connector = mock.Mock()
            with self.subTest(protocol_catalog=protocol_catalog), self.assertBlocked("catalog-invalid"):
                asyncio.run(
                    capture.drive_gateway(
                        5001,
                        canary("session"),
                        protocol_catalog,
                        connector=connector,
                    )
                )
            connector.assert_not_called()


class ProcessCleanupTests(BlockedAssertions):
    def test_unverified_direct_child_is_terminated_and_reaped(self) -> None:
        process = FakeProcess()
        with mock.patch.object(capture.os, "getpgid", return_value=process.pid + 1):
            capture.terminate_direct_child(process)
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 0)
        self.assertEqual(process.wait_calls, [15])

    def test_unverified_direct_child_escalates_after_timeout(self) -> None:
        timeout = subprocess.TimeoutExpired("gateway", 15)
        process = FakeProcess(waits=[timeout, 0])
        with mock.patch.object(capture.os, "getpgid", return_value=process.pid + 1):
            capture.terminate_direct_child(process)
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(process.wait_calls, [15, 5])

    def test_already_exited_leader_with_absent_group_is_only_reaped(self) -> None:
        process = FakeProcess(poll_result=0)
        with mock.patch.object(
            capture, "process_group_has_members", return_value=False
        ), mock.patch.object(capture.os, "killpg") as kill:
            capture.terminate_owned_process(process, "start")
        self.assertEqual(process.wait_calls, [None])
        kill.assert_not_called()

    def test_already_exited_leader_with_lingering_group_still_signals_group(self) -> None:
        process = FakeProcess(poll_result=0, waits=[0, 0])
        with mock.patch.object(
            capture, "process_group_has_members", return_value=True
        ), mock.patch.object(
            capture, "wait_for_process_group_exit", return_value=True
        ) as group_exit, mock.patch.object(capture.os, "killpg") as kill:
            capture.terminate_owned_process(process, "start")
        kill.assert_called_once_with(process.pid, signal.SIGTERM)
        self.assertEqual(process.wait_calls, [None, 15])
        group_exit.assert_called_once_with(process.pid, 15)

    def test_child_ignoring_term_causes_whole_group_kill(self) -> None:
        process = FakeProcess(waits=[0, 0])
        with mock.patch.object(capture, "process_start_ticks", return_value="start"), mock.patch.object(
            capture.os, "getpgid", return_value=process.pid
        ), mock.patch.object(
            capture, "wait_for_process_group_exit", side_effect=[False, True]
        ) as group_exit, mock.patch.object(capture.os, "killpg") as kill:
            capture.terminate_owned_process(process, "start")
        self.assertEqual(
            kill.call_args_list,
            [mock.call(process.pid, signal.SIGTERM), mock.call(process.pid, signal.SIGKILL)],
        )
        self.assertEqual(process.wait_calls, [15, 5])
        self.assertEqual(
            group_exit.call_args_list,
            [mock.call(process.pid, 15), mock.call(process.pid, 5)],
        )

    def test_leader_ignoring_term_is_revalidated_before_group_kill(self) -> None:
        timeout = subprocess.TimeoutExpired("gateway", 15)
        process = FakeProcess(waits=[timeout, 0])
        with mock.patch.object(capture, "process_start_ticks", side_effect=["start", "start"]), mock.patch.object(
            capture.os, "getpgid", return_value=process.pid
        ), mock.patch.object(
            capture, "wait_for_process_group_exit", return_value=True
        ), mock.patch.object(capture.os, "killpg") as kill:
            capture.terminate_owned_process(process, "start")
        self.assertEqual(
            kill.call_args_list,
            [mock.call(process.pid, signal.SIGTERM), mock.call(process.pid, signal.SIGKILL)],
        )

    def test_cleanup_blocks_if_group_still_exists_after_kill(self) -> None:
        process = FakeProcess(waits=[0, 0])
        with mock.patch.object(capture, "process_start_ticks", return_value="start"), mock.patch.object(
            capture.os, "getpgid", return_value=process.pid
        ), mock.patch.object(
            capture, "wait_for_process_group_exit", return_value=False
        ), mock.patch.object(capture.os, "killpg") as kill, self.assertBlocked(
            "cleanup-failed"
        ):
            capture.terminate_owned_process(process, "start")
        self.assertEqual(kill.call_args_list[-1], mock.call(process.pid, signal.SIGKILL))

    def test_identity_or_process_group_mismatch_never_signals(self) -> None:
        cases = (("different", 7001), ("start", 7002))
        for observed, group in cases:
            process = FakeProcess(pid=7001)
            with self.subTest(observed=observed, group=group), mock.patch.object(
                capture, "process_start_ticks", return_value=observed
            ), mock.patch.object(capture.os, "getpgid", return_value=group), mock.patch.object(
                capture.os, "killpg"
            ) as kill, self.assertBlocked("cleanup-failed"):
                capture.terminate_owned_process(process, "start")
            kill.assert_not_called()

    def test_pid_reuse_after_term_timeout_prevents_kill(self) -> None:
        timeout = subprocess.TimeoutExpired("gateway", 15)
        process = FakeProcess(waits=[timeout])
        with mock.patch.object(capture, "process_start_ticks", side_effect=["start", "reused"]), mock.patch.object(
            capture.os, "getpgid", return_value=process.pid
        ), mock.patch.object(capture.os, "killpg") as kill, self.assertBlocked("cleanup-failed"):
            capture.terminate_owned_process(process, "start")
        self.assertEqual(kill.call_args_list, [mock.call(process.pid, signal.SIGTERM)])

    def test_process_group_membership_uses_stat_group_field_and_fails_malformed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="capture-group-test-") as temporary:
            root = Path(temporary)
            group = 7101
            for pid, process_group in (("201", group), ("202", group + 1)):
                directory = root / pid
                directory.mkdir()
                fields = ["S", "1", str(process_group), *["0"] * 17]
                (directory / "stat").write_text(
                    pid + " (worker) " + " ".join(fields), encoding="utf-8"
                )
            self.assertTrue(capture.process_group_has_members(group, root))
            self.assertFalse(capture.process_group_has_members(group + 2, root))
            (root / "202" / "stat").write_text("202 malformed", encoding="utf-8")
            with self.assertBlocked("cleanup-failed"):
                capture.process_group_has_members(group + 2, root)


class RunCaptureOrchestrationTests(BlockedAssertions):
    def patched_run(
        self,
        *,
        child_ticks: object = "24680",
        group_owned: bool = True,
        ownership_error: BaseException | None = None,
    ):
        protocol_catalog = catalog()
        runtime = capture.Runtime(Path("runtime"), Path("runtime/bin/hermes"), Path("runtime/bin/python"))
        process = FakeProcess(pid=9001)
        frames = complete_capture(1)
        lines = (b"one", b"two", b"three")
        events: list[str] = []
        parent_pid = os.getpid()
        parent_ticks = str(parent_pid + 31)
        session_marker = canary("session")
        owner_nonce = uuid.uuid4().hex[:16]
        parent_nonce = uuid.uuid4().hex
        environment = {"SAFE_ENV": canary("environment")}
        workspace = Path("workspace")

        def note(name: str, result: object = None):
            def operation(*_args: object, **_kwargs: object) -> object:
                events.append(name)
                if isinstance(result, BaseException):
                    raise result
                return result

            return operation

        def ticks_for(pid: int) -> object:
            return parent_ticks if pid == parent_pid else child_ticks

        environment_builder = mock.Mock(return_value=(environment, workspace))
        popen = mock.Mock(return_value=process)
        locked = mock.Mock()

        patches = (
            mock.patch.object(capture.sys, "platform", "linux"),
            mock.patch.object(capture.Path, "is_dir", return_value=True),
            mock.patch.object(capture, "load_catalog", return_value=protocol_catalog),
            mock.patch.object(capture, "verify_runtime", return_value=runtime),
            mock.patch.object(capture, "verify_locked_environment", new=locked),
            mock.patch.object(capture, "count_existing_backends", return_value=4),
            mock.patch.object(capture, "build_isolated_environment", new=environment_builder),
            mock.patch.object(capture.subprocess, "Popen", new=popen),
            mock.patch.object(capture, "process_start_ticks", side_effect=ticks_for),
            mock.patch.object(
                capture.os,
                "getpgid",
                return_value=process.pid if group_owned else process.pid + 1,
            ),
            mock.patch.object(capture.secrets, "token_urlsafe", return_value=session_marker),
            mock.patch.object(
                capture.secrets, "token_hex", side_effect=[owner_nonce, parent_nonce]
            ),
            mock.patch.object(capture, "wait_for_ready", side_effect=note("ready", 6001)),
            mock.patch.object(
                capture,
                "prove_ownership",
                side_effect=note("ownership", ownership_error),
            ),
            mock.patch.object(capture, "drive_gateway", new=mock.AsyncMock(side_effect=note("websocket", frames))),
            mock.patch.object(capture, "terminate_owned_process", side_effect=note("cleanup")),
            mock.patch.object(
                capture, "terminate_direct_child", side_effect=note("direct-cleanup")
            ),
            mock.patch.object(capture, "sanitize_capture", side_effect=note("sanitize", lines)),
            mock.patch.object(capture, "write_fixture_atomic", side_effect=note("fixture")),
        )
        state = {
            "events": events,
            "process": process,
            "runtime": runtime,
            "parent_pid": parent_pid,
            "parent_ticks": parent_ticks,
            "session_marker": session_marker,
            "owner_nonce": owner_nonce,
            "parent_nonce": parent_nonce,
            "environment": environment,
            "workspace": workspace,
            "environment_builder": environment_builder,
            "popen": popen,
            "locked": locked,
        }
        return patches, state

    def invoke_with_patches(
        self, patches: tuple[object, ...], fixture_path: Path
    ) -> tuple[int, int]:
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)  # type: ignore[arg-type]
            return capture.run_capture(Path("runtime"), fixture_path=fixture_path)

    def test_cleanup_precedes_sanitization_and_fixture_emission(self) -> None:
        patches, state = self.patched_run()
        with tempfile.TemporaryDirectory(prefix="capture-run-test-") as temporary:
            result = self.invoke_with_patches(
                patches, Path(temporary) / "golden.jsonl"
            )
        self.assertEqual(result, (4, 3))
        self.assertEqual(
            state["events"],
            ["ready", "ownership", "websocket", "cleanup", "sanitize", "fixture"],
        )

    def test_ownership_failure_cleans_up_and_never_writes_raw_or_fixture(self) -> None:
        failure = capture.CaptureBlocked("ownership-failed")
        patches, state = self.patched_run(ownership_error=failure)
        with tempfile.TemporaryDirectory(prefix="capture-run-block-test-") as temporary:
            with self.assertBlocked("ownership-failed"):
                self.invoke_with_patches(
                    patches, Path(temporary) / "golden.jsonl"
                )
        self.assertEqual(state["events"], ["ready", "ownership", "cleanup"])

    def test_production_command_watchdog_and_preexec_are_exact(self) -> None:
        patches, state = self.patched_run()
        with tempfile.TemporaryDirectory(prefix="capture-launch-test-") as temporary:
            self.invoke_with_patches(patches, Path(temporary) / "golden.jsonl")
        state["locked"].assert_called_once_with(state["runtime"], None)
        state["environment_builder"].assert_called_once_with(
            mock.ANY,
            state["runtime"],
            state["session_marker"],
            parent_pid=state["parent_pid"],
            parent_ticks=state["parent_ticks"],
            parent_nonce=state["parent_nonce"],
        )
        call = state["popen"].call_args
        self.assertEqual(
            call.args[0],
            [
                os.fspath(state["runtime"].python),
                "-I",
                "-B",
                "-m",
                "hermes_cli.main",
                "serve",
                "--host",
                capture.LOOPBACK,
                "--port",
                "0",
                "--isolated",
                "--ssh-owner-nonce",
                state["owner_nonce"],
            ],
        )
        self.assertEqual(call.kwargs["cwd"], state["workspace"])
        self.assertIs(call.kwargs["env"], state["environment"])
        self.assertTrue(call.kwargs["start_new_session"])
        guard = call.kwargs["preexec_fn"]
        self.assertIsInstance(guard, capture.functools.partial)
        self.assertIs(guard.func, capture.arm_parent_death_signal)
        self.assertEqual(guard.args, (state["parent_pid"],))

    def test_success_path_publishes_only_the_sanitized_fixture(self) -> None:
        protocol_catalog = catalog()
        runtime = capture.Runtime(
            Path("runtime"), Path("runtime/bin/hermes"), Path("runtime/bin/python")
        )
        process = FakeProcess(pid=9002)
        frames = complete_capture(1)
        patches = (
            mock.patch.object(capture.sys, "platform", "linux"),
            mock.patch.object(capture.Path, "is_dir", return_value=True),
            mock.patch.object(capture, "load_catalog", return_value=protocol_catalog),
            mock.patch.object(capture, "verify_runtime", return_value=runtime),
            mock.patch.object(capture, "verify_locked_environment"),
            mock.patch.object(capture, "count_existing_backends", return_value=0),
            mock.patch.object(
                capture,
                "build_isolated_environment",
                return_value=({}, Path("workspace")),
            ),
            mock.patch.object(capture.subprocess, "Popen", return_value=process),
            mock.patch.object(
                capture,
                "process_start_ticks",
                side_effect=lambda pid: "13579" if pid == os.getpid() else "start",
            ),
            mock.patch.object(capture.os, "getpgid", return_value=process.pid),
            mock.patch.object(capture, "wait_for_ready", return_value=6002),
            mock.patch.object(capture, "prove_ownership"),
            mock.patch.object(
                capture, "drive_gateway", new=mock.AsyncMock(return_value=frames)
            ),
            mock.patch.object(capture, "terminate_owned_process"),
        )
        with tempfile.TemporaryDirectory(prefix="capture-publish-test-") as temporary:
            root = Path(temporary)
            fixture = root / "golden.jsonl"
            self.invoke_with_patches(patches, fixture)
            self.assertEqual([item.name for item in root.iterdir()], [fixture.name])
            expected = capture.sanitize_capture(frames, protocol_catalog)
            self.assertEqual(fixture.read_bytes(), b"\n".join(expected) + b"\n")

    def test_spawned_process_is_cleaned_up_when_initial_identity_probe_fails(self) -> None:
        patches, state = self.patched_run(child_ticks=None)
        with tempfile.TemporaryDirectory(prefix="capture-run-identity-test-") as temporary:
            with self.assertBlocked("gateway-exited"):
                self.invoke_with_patches(
                    patches, Path(temporary) / "golden.jsonl"
                )
        self.assertIn("cleanup", state["events"])

    def test_group_identity_failure_uses_direct_child_cleanup(self) -> None:
        patches, state = self.patched_run(group_owned=False)
        with tempfile.TemporaryDirectory(prefix="capture-run-group-test-") as temporary:
            with self.assertBlocked("gateway-exited"):
                self.invoke_with_patches(
                    patches, Path(temporary) / "golden.jsonl"
                )
        self.assertEqual(state["events"], ["direct-cleanup"])


if __name__ == "__main__":
    unittest.main()
