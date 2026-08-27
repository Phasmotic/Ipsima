#!/usr/bin/env python3
"""Capture a minimal real JSON-RPC transcript from an isolated Hermes gateway.

Only a recursively sanitized, canonical JSONL projection may leave the private
temporary capture root. The child is source-bound, receives an allowlisted
environment, chooses its own loopback port, and must answer an authenticated
ownership challenge before the WebSocket is driven.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
from dataclasses import dataclass
import functools
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

if __package__:
    from scripts import check_conformance as conformance
    from scripts import derive_protocol as derivation
else:  # direct execution from the scripts directory
    import check_conformance as conformance  # type: ignore[no-redef]
    import derive_protocol as derivation  # type: ignore[no-redef]


REPO = Path(__file__).resolve().parent.parent
CATALOG_PATH = REPO / "protocol" / "methods.json"
FIXTURE_PATH = (
    REPO
    / "Packages"
    / "HermesKit"
    / "Tests"
    / "HermesKitTests"
    / "Fixtures"
    / "golden.jsonl"
)
DEFAULT_HERMES_ROOT = REPO / ".gauntlet" / "hermes-capture-src"
LOOPBACK = "127.0.0.1"
MAX_FRAME_BYTES = 1_048_576
MAX_LOG_BYTES = 1_048_576
MAX_COLLECTION_ITEMS = 256
MAX_JSON_DEPTH = 16
MAX_CAPTURE_FRAMES = 64
EXPECTED_HERMES_VERSION = "0.20.5"
EXPECTED_UV_VERSION = "0.11.7"
RUNTIME_MODULES = (
    "hermes_cli",
    "hermes_cli.main",
    "hermes_cli.web_server",
    "tui_gateway.server",
    "tui_gateway.ws",
)
READY_PATTERN = re.compile(
    rb"(?m)^HERMES_BACKEND_READY port=([1-9][0-9]{0,4})\r?$"
)
SAFE_BLOCK_REASONS = frozenset(
    {
        "catalog-unavailable",
        "catalog-drift",
        "catalog-invalid",
        "raw-frame-binary",
        "raw-frame-invalid",
        "raw-frame-too-large",
        "capture-flow-invalid",
        "capture-incomplete",
        "gateway-exited",
        "gateway-timeout",
        "gateway-transport",
        "fixture-invalid",
        "fixture-write",
        "ownership-failed",
        "platform-unsupported",
        "readiness-failed",
        "runtime-dirty",
        "runtime-drift",
        "runtime-environment",
        "runtime-unavailable",
        "cleanup-failed",
        "unexpected-failure",
    }
)


class CaptureBlocked(RuntimeError):
    """A fail-closed capture condition with an input-independent diagnostic."""

    def __init__(self, reason: str) -> None:
        self.reason = reason if reason in SAFE_BLOCK_REASONS else "unexpected-failure"
        super().__init__(self.reason)


class DuplicateJSONKeyError(ValueError):
    """A purported JSON object repeated a member name."""


@dataclass(frozen=True)
class ProtocolCatalog:
    requests: frozenset[str]
    events: frozenset[str]
    source_commit: str
    source_repository: str
    source_inputs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CapturedFrame:
    direction: str
    text: str


@dataclass(frozen=True)
class Runtime:
    root: Path
    executable: Path
    python: Path


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJSONKeyError
        value[key] = item
    return value


def _parse_constant(_token: str) -> None:
    raise ValueError


def _parse_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError
    return value


def _parse_int(token: str) -> int:
    if len(token.lstrip("-")) > 64:
        raise ValueError
    return int(token)


def _validate_json_tree(
    value: object,
    *,
    depth: int,
    max_depth: int,
    max_collection_items: int,
) -> None:
    if depth > max_depth:
        raise ValueError
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError
        return
    if isinstance(value, list):
        if len(value) > max_collection_items:
            raise ValueError
        for item in value:
            _validate_json_tree(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_collection_items=max_collection_items,
            )
        return
    if isinstance(value, dict):
        if len(value) > max_collection_items:
            raise ValueError
        for key, item in value.items():
            _validate_json_tree(
                key,
                depth=depth + 1,
                max_depth=max_depth,
                max_collection_items=max_collection_items,
            )
            _validate_json_tree(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_collection_items=max_collection_items,
            )


def strict_json_loads(
    text: str,
    *,
    max_bytes: int = MAX_FRAME_BYTES,
    max_depth: int = MAX_JSON_DEPTH,
    max_collection_items: int = MAX_COLLECTION_ITEMS,
) -> object:
    if not isinstance(text, str) or not text:
        raise CaptureBlocked("raw-frame-invalid")
    try:
        encoded = text.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise CaptureBlocked("raw-frame-invalid") from error
    if len(encoded) > max_bytes:
        raise CaptureBlocked("raw-frame-too-large")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_parse_constant,
            parse_float=_parse_float,
            parse_int=_parse_int,
        )
        _validate_json_tree(
            value,
            depth=0,
            max_depth=max_depth,
            max_collection_items=max_collection_items,
        )
    except (DuplicateJSONKeyError, RecursionError, ValueError, json.JSONDecodeError) as error:
        raise CaptureBlocked("raw-frame-invalid") from error
    return value


def _catalog_entry_names(value: object, provenance_key: str) -> frozenset[str]:
    if not isinstance(value, list) or not value:
        raise CaptureBlocked("catalog-invalid")
    names: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"name", provenance_key}:
            raise CaptureBlocked("catalog-invalid")
        name = entry.get("name")
        locations = entry.get(provenance_key)
        if (
            not isinstance(name, str)
            or conformance.CATALOG_NAME.fullmatch(name) is None
            or name in names
            or not isinstance(locations, list)
            or not locations
            or any(not isinstance(location, str) or not location for location in locations)
        ):
            raise CaptureBlocked("catalog-invalid")
        names.add(name)
    return frozenset(names)


def load_catalog(path: Path = CATALOG_PATH) -> ProtocolCatalog:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CaptureBlocked("catalog-unavailable") from error
    if hashlib.sha256(raw).hexdigest() != derivation.PINNED_CATALOG_SHA256:
        raise CaptureBlocked("catalog-drift")
    try:
        document = strict_json_loads(
            raw.decode("utf-8", errors="strict"),
            max_bytes=4_194_304,
            max_depth=32,
            max_collection_items=2_048,
        )
    except (CaptureBlocked, UnicodeError) as error:
        raise CaptureBlocked("catalog-invalid") from error
    if not isinstance(document, dict):
        raise CaptureBlocked("catalog-invalid")
    source = document.get("source")
    if not isinstance(source, dict):
        raise CaptureBlocked("catalog-invalid")
    repository = source.get("repository")
    commit = source.get("commit")
    inputs = source.get("inputs")
    if (
        not isinstance(repository, str)
        or not isinstance(commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", commit) is None
        or not isinstance(inputs, list)
        or not inputs
    ):
        raise CaptureBlocked("catalog-invalid")
    source_inputs: list[tuple[str, str]] = []
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise CaptureBlocked("catalog-invalid")
        relative = item.get("path")
        digest = item.get("sha256")
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise CaptureBlocked("catalog-invalid")
        source_inputs.append((relative, digest))
    if len(source_inputs) != len(set(source_inputs)):
        raise CaptureBlocked("catalog-invalid")
    return ProtocolCatalog(
        requests=_catalog_entry_names(document.get("requests"), "defined_at"),
        events=_catalog_entry_names(document.get("events"), "emitted_at"),
        source_commit=commit,
        source_repository=repository,
        source_inputs=tuple(source_inputs),
    )


def _rpc_id_key(value: object) -> tuple[str, object]:
    if value is None:
        return ("null", None)
    if type(value) is int and -(1 << 63) <= value < (1 << 63):
        return ("int", value)
    if isinstance(value, str):
        return ("string", value)
    raise CaptureBlocked("capture-flow-invalid")


class IDAliases:
    def __init__(self) -> None:
        self._integer: dict[int, int] = {}
        self._string: dict[str, str] = {}

    def alias(self, value: object) -> object:
        kind, raw = _rpc_id_key(value)
        if kind == "null":
            return None
        if kind == "int":
            assert isinstance(raw, int)
            if raw not in self._integer:
                self._integer[raw] = len(self._integer) + 1
            return self._integer[raw]
        assert isinstance(raw, str)
        if raw not in self._string:
            self._string[raw] = f"id-{len(self._string) + 1}"
        return self._string[raw]


def _sanitize_generic(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, bool):
        return False
    if type(value) is int:
        return 0
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CaptureBlocked("raw-frame-invalid")
        return 0.5
    if isinstance(value, str):
        return conformance.REDACTED_TEXT
    if isinstance(value, list):
        return [_sanitize_generic(item) for item in value]
    if isinstance(value, dict):
        sanitized_values = [_sanitize_generic(item) for item in value.values()]
        sanitized_values.sort(key=conformance.canonical)
        return {
            f"field_{index:03d}": item
            for index, item in enumerate(sanitized_values, 1)
        }
    raise CaptureBlocked("raw-frame-invalid")


def sanitize_envelope(
    value: object,
    catalog: ProtocolCatalog,
    aliases: IDAliases,
) -> dict[str, object]:
    problem = conformance.envelope_problem(value)
    if problem is not None or not isinstance(value, dict):
        raise CaptureBlocked("raw-frame-invalid")
    output: dict[str, object] = {"jsonrpc": "2.0"}
    if "id" in value:
        output["id"] = aliases.alias(value["id"])

    method = value.get("method")
    if method == "event":
        if "id" in value:
            raise CaptureBlocked("capture-flow-invalid")
        params = value.get("params")
        if not isinstance(params, dict):
            raise CaptureBlocked("raw-frame-invalid")
        event_type = params.get("type")
        if not isinstance(event_type, str) or event_type not in catalog.events:
            raise CaptureBlocked("capture-flow-invalid")
        if "payload" not in params:
            raise CaptureBlocked("raw-frame-invalid")
        output["method"] = "event"
        output["params"] = {
            "payload": _sanitize_generic(params["payload"]),
            "type": event_type,
        }
    elif method is not None:
        if not isinstance(method, str) or method not in catalog.requests:
            raise CaptureBlocked("capture-flow-invalid")
        output["method"] = method
        if "params" in value:
            output["params"] = _sanitize_generic(value["params"])
    elif "result" in value:
        output["result"] = _sanitize_generic(value["result"])
    else:
        error = value.get("error")
        if not isinstance(error, dict):
            raise CaptureBlocked("raw-frame-invalid")
        sanitized_error: dict[str, object] = {
            "code": -32000,
            "message": conformance.REDACTED_TEXT,
        }
        if "data" in error:
            sanitized_error["data"] = _sanitize_generic(error["data"])
        output["error"] = sanitized_error

    safety_problem = conformance.sanitized_fixture_problem(
        output, catalog.requests, catalog.events
    )
    if safety_problem is not None:
        raise CaptureBlocked("fixture-invalid")
    return output


def _classify_flow_frame(
    frame: CapturedFrame,
    value: object,
    pending: dict[tuple[str, object], None],
) -> tuple[bool, bool, bool]:
    if frame.direction not in {"in", "out"} or not isinstance(value, dict):
        raise CaptureBlocked("capture-flow-invalid")
    method = value.get("method")
    has_id = "id" in value
    if frame.direction == "out":
        if not isinstance(method, str) or not has_id:
            raise CaptureBlocked("capture-flow-invalid")
        identifier = _rpc_id_key(value["id"])
        if identifier in pending:
            raise CaptureBlocked("capture-flow-invalid")
        pending[identifier] = None
        return (True, False, False)

    if method is not None:
        if method != "event" or has_id:
            raise CaptureBlocked("capture-flow-invalid")
        return (False, False, True)

    if not has_id:
        raise CaptureBlocked("capture-flow-invalid")
    identifier = _rpc_id_key(value["id"])
    if identifier not in pending:
        raise CaptureBlocked("capture-flow-invalid")
    del pending[identifier]
    return (False, True, False)


def sanitize_capture(
    frames: list[CapturedFrame], catalog: ProtocolCatalog
) -> tuple[bytes, ...]:
    if len(frames) != 3:
        raise CaptureBlocked("capture-incomplete")
    pending: dict[tuple[str, object], None] = {}
    aliases = IDAliases()
    sanitized: list[bytes] = []
    decoded: list[object] = []

    for frame in frames:
        value = strict_json_loads(frame.text)
        _classify_flow_frame(frame, value, pending)
        decoded.append(value)
        envelope = sanitize_envelope(value, catalog, aliases)
        encoded = conformance.canonical(envelope)
        if encoded != conformance.canonical(conformance.strict_json_loads(encoded.decode("utf-8"))):
            raise CaptureBlocked("fixture-invalid")
        sanitized.append(encoded)

    ready, request, response = decoded
    ready_params = ready.get("params") if isinstance(ready, dict) else None
    if (
        frames[0].direction != "in"
        or not isinstance(ready, dict)
        or ready.get("method") != "event"
        or not isinstance(ready_params, dict)
        or ready_params.get("type") != "gateway.ready"
        or frames[1].direction != "out"
        or not isinstance(request, dict)
        or request.get("method") != "ping"
        or "id" not in request
        or frames[2].direction != "in"
        or not isinstance(response, dict)
        or response.get("result") != {"pong": True}
        or "error" in response
        or "id" not in response
        or _rpc_id_key(request["id"]) != _rpc_id_key(response["id"])
        or pending
    ):
        raise CaptureBlocked("capture-incomplete")
    return tuple(sanitized)


def _validate_fixture_bytes(data: bytes, catalog: ProtocolCatalog) -> None:
    if not data or not data.endswith(b"\n") or data.startswith(b"\xef\xbb\xbf"):
        raise CaptureBlocked("fixture-invalid")
    lines = data.splitlines()
    if not lines or any(not line for line in lines):
        raise CaptureBlocked("fixture-invalid")
    for line in lines:
        try:
            text = line.decode("utf-8", errors="strict")
            value = conformance.strict_json_loads(text)
        except (UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise CaptureBlocked("fixture-invalid") from error
        if conformance.envelope_problem(value) is not None:
            raise CaptureBlocked("fixture-invalid")
        if (
            conformance.sanitized_fixture_problem(
                value, catalog.requests, catalog.events
            )
            is not None
        ):
            raise CaptureBlocked("fixture-invalid")
        if conformance.canonical(value) != line:
            raise CaptureBlocked("fixture-invalid")


def write_fixture_atomic(
    path: Path, lines: tuple[bytes, ...], catalog: ProtocolCatalog
) -> None:
    data = b"\n".join(lines) + b"\n"
    _validate_fixture_bytes(data, catalog)
    temporary_path: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".golden.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary_path, path)
        temporary_path = None
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except (OSError, ValueError) as error:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
        raise CaptureBlocked("fixture-write") from error


def _git_output(root: Path, arguments: list[str]) -> str:
    environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.defpath,
    }
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(root), *arguments],
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CaptureBlocked("runtime-unavailable") from error
    if result.returncode != 0 or result.stderr:
        raise CaptureBlocked("runtime-unavailable")
    return result.stdout.strip()


def _launcher_path(value: str | os.PathLike[str]) -> Path:
    """Normalize directory aliases without following the final launcher symlink."""

    path = Path(value).expanduser().absolute()
    try:
        return path.parent.resolve(strict=True) / path.name
    except OSError as error:
        raise CaptureBlocked("runtime-unavailable") from error


def verify_runtime(root: Path, catalog: ProtocolCatalog) -> Runtime:
    try:
        resolved = root.expanduser().resolve(strict=True)
    except OSError as error:
        raise CaptureBlocked("runtime-unavailable") from error
    if not resolved.is_dir() or (resolved / ".env").exists():
        raise CaptureBlocked("runtime-environment")
    if _git_output(resolved, ["rev-parse", "HEAD"]) != catalog.source_commit:
        raise CaptureBlocked("runtime-drift")
    if _git_output(resolved, ["remote", "get-url", "origin"]) != catalog.source_repository:
        raise CaptureBlocked("runtime-drift")
    if _git_output(resolved, ["status", "--porcelain=v1", "--untracked-files=all"]):
        raise CaptureBlocked("runtime-dirty")
    for relative, expected_digest in catalog.source_inputs:
        try:
            source = (resolved / relative).resolve(strict=True)
            source.relative_to(resolved)
            actual_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        except (OSError, ValueError) as error:
            raise CaptureBlocked("runtime-drift") from error
        if actual_digest != expected_digest:
            raise CaptureBlocked("runtime-drift")

    candidates = [
        resolved / ".venv" / "bin" / "hermes",
        resolved / "venv" / "bin" / "hermes",
    ]
    executables: list[Path] = []
    for candidate in candidates:
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        try:
            executable = candidate.resolve(strict=True)
            executable.relative_to(resolved)
        except (OSError, ValueError) as error:
            raise CaptureBlocked("runtime-unavailable") from error
        executables.append(executable)
    if len(executables) != 1:
        raise CaptureBlocked("runtime-unavailable")
    executable = executables[0]
    runtime_python = executable.with_name("python")
    if not runtime_python.is_file() or not os.access(runtime_python, os.X_OK):
        raise CaptureBlocked("runtime-unavailable")
    current_python = _launcher_path(sys.executable)
    expected_python = _launcher_path(runtime_python)
    try:
        current_prefix = Path(sys.prefix).resolve(strict=True)
        expected_prefix = runtime_python.parent.parent.resolve(strict=True)
    except OSError as error:
        raise CaptureBlocked("runtime-unavailable") from error
    if current_python != expected_python or current_prefix != expected_prefix:
        raise CaptureBlocked("runtime-environment")
    return Runtime(root=resolved, executable=executable, python=runtime_python)


def verify_locked_environment(
    runtime: Runtime, uv_executable: Path | None = None
) -> None:
    """Prove that the ignored venv matches the pinned lock and source checkout."""

    candidate = (
        os.fspath(uv_executable)
        if uv_executable is not None
        else shutil.which("uv")
    )
    if not candidate:
        raise CaptureBlocked("runtime-unavailable")
    try:
        uv = Path(candidate).expanduser().resolve(strict=True)
    except OSError as error:
        raise CaptureBlocked("runtime-unavailable") from error
    if not uv.is_file() or not os.access(uv, os.X_OK):
        raise CaptureBlocked("runtime-unavailable")

    temporary_home = tempfile.TemporaryDirectory(prefix="talaria-uv-check-")
    try:
        private_home = Path(temporary_home.name)
        private_home.chmod(0o700)
        environment = {
            "HOME": os.fspath(private_home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "UV_NO_CONFIG": "1",
            "UV_NO_PROGRESS": "1",
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
        }

        def run_uv(arguments: list[str]) -> bytes:
            try:
                result = subprocess.run(
                    [os.fspath(uv), *arguments],
                    cwd=runtime.root,
                    env=environment,
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise CaptureBlocked("runtime-unavailable") from error
            evidence = result.stdout + result.stderr
            if result.returncode != 0 or not evidence.strip():
                raise CaptureBlocked("runtime-drift")
            return evidence

        version_evidence = run_uv(["--version"])
        try:
            version = version_evidence.decode("utf-8", errors="strict").strip()
        except UnicodeError as error:
            raise CaptureBlocked("runtime-drift") from error
        version_pattern = rf"uv {re.escape(EXPECTED_UV_VERSION)}(?: \([^\r\n]+\))?"
        if re.fullmatch(version_pattern, version) is None:
            raise CaptureBlocked("runtime-drift")

        run_uv(
            [
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
            ]
        )
    finally:
        temporary_home.cleanup()
    if _git_output(
        runtime.root, ["status", "--porcelain=v1", "--untracked-files=all"]
    ):
        raise CaptureBlocked("runtime-dirty")

    try:
        runtime_root = runtime.root.resolve(strict=True)
    except OSError as error:
        raise CaptureBlocked("runtime-unavailable") from error
    importlib.invalidate_caches()
    for module_name in RUNTIME_MODULES:
        try:
            specification = importlib.util.find_spec(module_name)
            if specification is None or specification.origin is None:
                raise CaptureBlocked("runtime-drift")
            origin = Path(specification.origin).resolve(strict=True)
            origin.relative_to(runtime_root)
        except CaptureBlocked:
            raise
        except (ImportError, ModuleNotFoundError, OSError, ValueError) as error:
            raise CaptureBlocked("runtime-drift") from error


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    path.chmod(0o700)


def build_isolated_environment(
    capture_root: Path,
    runtime: Runtime,
    session_token: str,
    *,
    parent_pid: int,
    parent_ticks: str,
    parent_nonce: str,
) -> tuple[dict[str, str], Path]:
    if (
        parent_pid <= 0
        or re.fullmatch(r"[1-9][0-9]*", parent_ticks) is None
        or re.fullmatch(r"[0-9a-f]{32}", parent_nonce) is None
    ):
        raise CaptureBlocked("runtime-environment")
    home = capture_root / "home"
    temporary = capture_root / "tmp"
    config = capture_root / "config"
    cache = capture_root / "cache"
    data = capture_root / "data"
    state = capture_root / "state"
    workspace = capture_root / "workspace"
    profiles = capture_root / "profiles"
    for directory in (
        home,
        temporary,
        config,
        cache,
        data,
        state,
        workspace,
        profiles,
    ):
        _private_directory(directory)
    hermes_home = profiles / "capture"
    _private_directory(hermes_home)
    managed = capture_root / "managed-disabled"
    if managed.exists():
        raise CaptureBlocked("runtime-environment")
    environment = {
        "HOME": os.fspath(home),
        "TMPDIR": os.fspath(temporary),
        "XDG_CONFIG_HOME": os.fspath(config),
        "XDG_CACHE_HOME": os.fspath(cache),
        "XDG_DATA_HOME": os.fspath(data),
        "XDG_STATE_HOME": os.fspath(state),
        "HERMES_HOME": os.fspath(hermes_home),
        "HERMES_MANAGED_DIR": os.fspath(managed),
        "HERMES_DASHBOARD_SESSION_TOKEN": session_token,
        "HERMES_PARENT_PID": str(parent_pid),
        "HERMES_PARENT_START_MARKER": f"linux:{parent_ticks}",
        "HERMES_PARENT_NONCE": parent_nonce,
        "PATH": os.pathsep.join(
            (os.fspath(runtime.executable.parent), "/usr/bin", "/bin")
        ),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    return environment, workspace


def count_existing_backends(proc_root: Path = Path("/proc")) -> int:
    count = 0
    try:
        entries = list(proc_root.iterdir())
    except OSError as error:
        raise CaptureBlocked("platform-unsupported") from error
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            arguments = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        decoded = [item.decode("utf-8", errors="ignore") for item in arguments if item]
        python_launcher = bool(
            decoded
            and re.fullmatch(r"python(?:3(?:\.[0-9]+)?)?", Path(decoded[0]).name)
        )
        action: str | None = None
        if len(decoded) >= 2 and Path(decoded[0]).name == "hermes":
            action = decoded[1]
        elif (
            python_launcher
            and len(decoded) >= 3
            and Path(decoded[1]).name == "hermes"
        ):
            action = decoded[2]
        elif python_launcher and "-m" in decoded:
            module_index = decoded.index("-m")
            if (
                len(decoded) > module_index + 2
                and decoded[module_index + 1]
                in {"hermes_cli", "hermes_cli.main"}
            ):
                action = decoded[module_index + 2]
        if action in {"serve", "dashboard"}:
            count += 1
    return count


def process_start_ticks(pid: int, proc_root: Path = Path("/proc")) -> str | None:
    try:
        raw = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
        close = raw.rfind(")")
        fields = raw[close + 2 :].split()
        return fields[19] if close >= 0 and len(fields) > 19 else None
    except (OSError, UnicodeError):
        return None


def arm_parent_death_signal(expected_parent_pid: int) -> None:
    """Kill the not-yet-ready child if this exact harness parent disappears."""

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, int(signal.SIGKILL), 0, 0, 0) != 0:
            os._exit(127)
        if os.getppid() != expected_parent_pid:
            os._exit(127)
    except BaseException:
        os._exit(127)


def parse_ready_output(raw: bytes) -> int:
    if len(raw) > MAX_LOG_BYTES:
        raise CaptureBlocked("readiness-failed")
    matches = READY_PATTERN.findall(raw)
    if len(matches) != 1:
        raise CaptureBlocked("readiness-failed")
    try:
        port = int(matches[0])
    except ValueError as error:
        raise CaptureBlocked("readiness-failed") from error
    if not 1 <= port <= 65_535:
        raise CaptureBlocked("readiness-failed")
    return port


def wait_for_ready(
    process: subprocess.Popen[bytes],
    log_path: Path,
    start_ticks: str,
    timeout: float,
) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CaptureBlocked("gateway-exited")
        if process_start_ticks(process.pid) != start_ticks:
            raise CaptureBlocked("gateway-exited")
        try:
            raw = log_path.read_bytes()
        except OSError as error:
            raise CaptureBlocked("readiness-failed") from error
        matches = READY_PATTERN.findall(raw)
        if len(matches) > 1 or len(raw) > MAX_LOG_BYTES:
            raise CaptureBlocked("readiness-failed")
        if len(matches) == 1:
            return parse_ready_output(raw)
        time.sleep(0.1)
    raise CaptureBlocked("gateway-timeout")


def _network_url(scheme: str, port: int, path: str) -> str:
    return f"{scheme}://{LOOPBACK}:{port}{path}"


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: object,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        return None


def _http_json(port: int, path: str, session_token: str) -> object:
    request = urlrequest.Request(_network_url("http", port, path), method="GET")
    request.add_header("X-Hermes-Session-Token", session_token)
    opener = urlrequest.build_opener(urlrequest.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=5) as response:
            if response.status != 200:
                raise CaptureBlocked("ownership-failed")
            raw = response.read(MAX_FRAME_BYTES + 1)
    except CaptureBlocked:
        raise
    except urlerror.HTTPError as error:
        error.close()
        raise CaptureBlocked("ownership-failed") from error
    except Exception as error:
        raise CaptureBlocked("ownership-failed") from error
    if len(raw) > MAX_FRAME_BYTES:
        raise CaptureBlocked("ownership-failed")
    try:
        return strict_json_loads(raw.decode("utf-8", errors="strict"))
    except (CaptureBlocked, UnicodeError) as error:
        raise CaptureBlocked("ownership-failed") from error


def prove_ownership(port: int, session_token: str, owner_nonce: str) -> None:
    health = _http_json(port, "/api/health", session_token)
    if health != {
        "ok": True,
        "version": EXPECTED_HERMES_VERSION,
        "auth_required": False,
    }:
        raise CaptureBlocked("ownership-failed")
    ownership = _http_json(port, "/api/ssh/ownership", session_token)
    if ownership != {
        "ok": True,
        "sshOwnerNonce": owner_nonce,
        "protocolVersion": 1,
    }:
        raise CaptureBlocked("ownership-failed")


def _received_text(value: object) -> str:
    if not isinstance(value, str):
        raise CaptureBlocked("raw-frame-binary")
    strict_json_loads(value)
    return value


async def drive_gateway(
    port: int,
    session_token: str,
    catalog: ProtocolCatalog,
    *,
    connector: Callable[..., Any] | None = None,
    timeout: float = 30.0,
) -> list[CapturedFrame]:
    if "ping" not in catalog.requests or "gateway.ready" not in catalog.events:
        raise CaptureBlocked("catalog-invalid")
    if connector is None:
        try:
            import websockets
        except ImportError as error:
            raise CaptureBlocked("runtime-unavailable") from error
        connector = websockets.connect
    query = urlparse.urlencode({"token": session_token})
    uri = _network_url("ws", port, "/api/ws") + "?" + query
    frames: list[CapturedFrame] = []
    try:
        connection = connector(
            uri,
            max_size=MAX_FRAME_BYTES,
            open_timeout=timeout,
            close_timeout=5,
            proxy=None,
        )
        async with connection as websocket:
            ready_text = _received_text(
                await asyncio.wait_for(websocket.recv(), timeout=timeout)
            )
            ready = strict_json_loads(ready_text)
            if not isinstance(ready, dict) or ready.get("method") != "event":
                raise CaptureBlocked("capture-flow-invalid")
            params = ready.get("params")
            if (
                not isinstance(params, dict)
                or params.get("type") != "gateway.ready"
                or "payload" not in params
                or "id" in ready
            ):
                raise CaptureBlocked("capture-flow-invalid")
            frames.append(CapturedFrame("in", ready_text))

            request_id = 1
            request_value = {"jsonrpc": "2.0", "id": request_id, "method": "ping"}
            request_text = conformance.canonical(request_value).decode("utf-8")
            await websocket.send(request_text)
            frames.append(CapturedFrame("out", request_text))

            incoming_text = _received_text(
                await asyncio.wait_for(websocket.recv(), timeout=timeout)
            )
            incoming = strict_json_loads(incoming_text)
            if (
                not isinstance(incoming, dict)
                or incoming.get("method") is not None
                or "id" not in incoming
                or _rpc_id_key(incoming["id"]) != _rpc_id_key(request_id)
                or incoming.get("result") != {"pong": True}
                or "error" in incoming
            ):
                raise CaptureBlocked("capture-flow-invalid")
            frames.append(CapturedFrame("in", incoming_text))
            return frames
    except CaptureBlocked:
        raise
    except asyncio.TimeoutError as error:
        raise CaptureBlocked("gateway-timeout") from error
    except Exception as error:
        raise CaptureBlocked("gateway-transport") from error


def terminate_direct_child(process: subprocess.Popen[bytes]) -> None:
    """Reap the exact Popen child when process-group proof was unavailable."""

    if process.poll() is not None:
        process.wait()
        return
    try:
        if os.getpgid(process.pid) == process.pid:
            terminate_owned_process(process, None)
            return
    except (CaptureBlocked, OSError):
        pass
    try:
        process.terminate()
        process.wait(timeout=15)
        return
    except subprocess.TimeoutExpired:
        pass
    except OSError as error:
        raise CaptureBlocked("cleanup-failed") from error
    try:
        process.kill()
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        raise CaptureBlocked("cleanup-failed") from error


def process_group_has_members(
    process_group: int, proc_root: Path = Path("/proc")
) -> bool:
    """Return whether Linux procfs still contains a member of an owned group."""

    if process_group <= 0:
        raise CaptureBlocked("cleanup-failed")
    try:
        entries = list(proc_root.iterdir())
    except OSError as error:
        raise CaptureBlocked("cleanup-failed") from error
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="utf-8")
        except OSError:
            continue
        close = raw.rfind(")")
        fields = raw[close + 2 :].split()
        if close < 0 or len(fields) <= 2:
            raise CaptureBlocked("cleanup-failed")
        try:
            member_group = int(fields[2])
        except ValueError as error:
            raise CaptureBlocked("cleanup-failed") from error
        if member_group == process_group:
            return True
    return False


def wait_for_process_group_exit(process_group: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while process_group_has_members(process_group):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def terminate_owned_process(
    process: subprocess.Popen[bytes], start_ticks: str | None
) -> None:
    leader_running = process.poll() is None
    if not leader_running:
        process.wait()
        if not process_group_has_members(process.pid):
            return
    if leader_running:
        if (
            start_ticks is not None
            and process_start_ticks(process.pid) != start_ticks
        ) or os.getpgid(process.pid) != process.pid:
            raise CaptureBlocked("cleanup-failed")
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as error:
        raise CaptureBlocked("cleanup-failed") from error

    leader_timed_out = False
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        leader_timed_out = True
    except (OSError, subprocess.SubprocessError) as error:
        raise CaptureBlocked("cleanup-failed") from error
    if not leader_timed_out and wait_for_process_group_exit(process.pid, 15):
        return

    if process.poll() is None and (
        (
            start_ticks is not None
            and process_start_ticks(process.pid) != start_ticks
        )
        or os.getpgid(process.pid) != process.pid
    ):
        raise CaptureBlocked("cleanup-failed")
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as error:
        raise CaptureBlocked("cleanup-failed") from error
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        raise CaptureBlocked("cleanup-failed") from error
    if not wait_for_process_group_exit(process.pid, 5):
        raise CaptureBlocked("cleanup-failed")


def run_capture(
    hermes_root: Path,
    *,
    fixture_path: Path = FIXTURE_PATH,
    readiness_timeout: float = 90.0,
    uv_executable: Path | None = None,
) -> tuple[int, int]:
    if not sys.platform.startswith("linux") or not Path("/proc").is_dir():
        raise CaptureBlocked("platform-unsupported")
    catalog = load_catalog()
    runtime = verify_runtime(hermes_root, catalog)
    verify_locked_environment(runtime, uv_executable)
    existing_backends = count_existing_backends()
    parent_pid = os.getpid()
    parent_ticks = process_start_ticks(parent_pid)
    if parent_ticks is None:
        raise CaptureBlocked("platform-unsupported")

    with tempfile.TemporaryDirectory(prefix="talaria-golden-") as temporary_name:
        capture_root = Path(temporary_name)
        capture_root.chmod(0o700)
        session_token = secrets.token_urlsafe(32)
        owner_nonce = secrets.token_hex(8)
        parent_nonce = secrets.token_hex(16)
        environment, workspace = build_isolated_environment(
            capture_root,
            runtime,
            session_token,
            parent_pid=parent_pid,
            parent_ticks=parent_ticks,
            parent_nonce=parent_nonce,
        )
        log_path = capture_root / "gateway.log"
        log_descriptor = os.open(
            log_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        command = [
            os.fspath(runtime.python),
            "-I",
            "-B",
            "-m",
            "hermes_cli.main",
            "serve",
            "--host",
            LOOPBACK,
            "--port",
            "0",
            "--isolated",
            "--ssh-owner-nonce",
            owner_nonce,
        ]
        with os.fdopen(log_descriptor, "wb", buffering=0) as log_stream:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=workspace,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    preexec_fn=functools.partial(
                        arm_parent_death_signal, parent_pid
                    ),
                    close_fds=True,
                )
            except OSError as error:
                raise CaptureBlocked("runtime-unavailable") from error
        start_ticks: str | None = None
        group_owned = False
        frames: list[CapturedFrame]
        try:
            try:
                group_owned = os.getpgid(process.pid) == process.pid
            except OSError:
                group_owned = False
            if not group_owned:
                raise CaptureBlocked("gateway-exited")
            start_ticks = process_start_ticks(process.pid)
            if start_ticks is None:
                raise CaptureBlocked("gateway-exited")
            port = wait_for_ready(
                process, log_path, start_ticks, readiness_timeout
            )
            if process.poll() is not None or process_start_ticks(process.pid) != start_ticks:
                raise CaptureBlocked("gateway-exited")
            prove_ownership(port, session_token, owner_nonce)
            if process.poll() is not None or process_start_ticks(process.pid) != start_ticks:
                raise CaptureBlocked("gateway-exited")
            frames = asyncio.run(
                drive_gateway(port, session_token, catalog)
            )
        finally:
            if group_owned:
                terminate_owned_process(process, start_ticks)
            else:
                terminate_direct_child(process)

        lines = sanitize_capture(frames, catalog)
        write_fixture_atomic(fixture_path, lines, catalog)
        return existing_backends, len(lines)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture a sanitized golden transcript from an isolated gateway."
    )
    parser.add_argument(
        "--hermes-root",
        type=Path,
        default=DEFAULT_HERMES_ROOT,
        help="clean checkout whose commit and source inputs match the pinned catalog",
    )
    parser.add_argument(
        "--readiness-timeout",
        type=float,
        default=90.0,
        help="bounded foreground startup deadline in seconds",
    )
    parser.add_argument(
        "--uv",
        type=Path,
        help="uv 0.11.7 executable used for the read-only lock/environment check",
    )
    arguments = parser.parse_args(argv)
    if not 1.0 <= arguments.readiness_timeout <= 300.0:
        parser.error("--readiness-timeout must be between 1 and 300 seconds")
    return arguments


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        existing, frames = run_capture(
            arguments.hermes_root,
            readiness_timeout=arguments.readiness_timeout,
            uv_executable=arguments.uv,
        )
    except CaptureBlocked as error:
        print(f"CAPTURE BLOCKED: {error.reason}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, Exception):
        print("CAPTURE BLOCKED: unexpected-failure", file=sys.stderr)
        return 2
    print(f"existing Hermes backends observed and left untouched: {existing}")
    print(f"CAPTURE PASS: sanitized canonical frames={frames}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
