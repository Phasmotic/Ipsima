#!/usr/bin/env python3
"""Derive ``protocol/methods.json`` from a pinned Hermes Git commit.

The checkout is only an object database. Source is always read with ``git
show <commit>:<path>`` so a dirty worktree, a different checked-out branch, or
an upstream branch moving cannot change the result.

Run from the repository root:

    python3 scripts/derive_protocol.py /path/to/hermes-agent
    python3 scripts/derive_protocol.py /path/to/hermes-agent --check

Exit status is an evidence contract: 0/PASS means output was written or is
byte-identical, 1/FAIL means ``--check`` found drift, and 2/BLOCKED means the
pinned source or output evidence could not be evaluated reliably.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence

SOURCE_REPOSITORY = "https://github.com/NousResearch/hermes-agent.git"
SOURCE_COMMIT = "e3b5512b7b3f6cbcb23ba5fffdc66d5015eca246"
PINNED_CATALOG_SHA256 = (
    "c51404eb76d93a37f36155dc2df9688821aab8a9d694135d1407bfe7de96928b"
)
DERIVED_AT = "2026-08-26"

INTERNAL_MODULES = {"compute_host.py", "host_supervisor.py"}
REQUIRED_TUI_INPUTS = {
    "tui_gateway/entry.py",
    "tui_gateway/server.py",
    "tui_gateway/transport.py",
    "tui_gateway/ws.py",
}
EXPLICIT_INPUTS = (
    "gateway/platforms/api_server.py",
    "hermes_cli/dashboard_auth/ws_tickets.py",
    "hermes_cli/web_server.py",
)
EXPECTED_COUNTS = (168, 56, 42)


class DerivationBlocked(RuntimeError):
    """Required source or output evidence was unavailable or indeterminate."""


@dataclasses.dataclass(frozen=True)
class SourceFile:
    """One strict-UTF-8 source blob read from the pinned Git tree."""

    path: str
    raw: bytes
    text: str
    sha256: str

    @classmethod
    def from_bytes(cls, path: str, raw: bytes) -> "SourceFile":
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise DerivationBlocked(
                f"required source blob is not strict UTF-8: {path}"
            ) from error
        return cls(
            path=path,
            raw=raw,
            text=text,
            sha256=hashlib.sha256(raw).hexdigest(),
        )


class GitObjectSource:
    """Read a complete input snapshot from immutable objects in a Git repo."""

    def __init__(
        self,
        repository: pathlib.Path,
        revision: str = SOURCE_COMMIT,
        expected_repository: str = SOURCE_REPOSITORY,
    ) -> None:
        self.repository = repository.resolve()
        self.revision = revision
        self.expected_repository = expected_repository

    def snapshot(self) -> dict[str, SourceFile]:
        if not self.repository.is_dir():
            raise DerivationBlocked("Hermes Git checkout is not a directory")

        origin = self._text("remote", "get-url", "origin").strip()
        if origin != self.expected_repository:
            raise DerivationBlocked(
                "Hermes origin does not match the pinned canonical repository"
            )

        resolved = self._text(
            "rev-parse", "--verify", f"{self.revision}^{{commit}}"
        ).strip()
        if resolved != self.revision:
            raise DerivationBlocked(
                f"pinned Hermes commit resolved unexpectedly: {resolved!r}"
            )

        tree = self._git(
            "ls-tree",
            "-r",
            "--name-only",
            "-z",
            self.revision,
            "--",
            "tui_gateway",
        )
        try:
            tree_paths = [
                item.decode("utf-8", errors="strict")
                for item in tree.split(b"\0")
                if item
            ]
        except UnicodeDecodeError as error:
            raise DerivationBlocked(
                "pinned Hermes tree contains a non-UTF-8 path"
            ) from error

        tui_paths = sorted(
            path
            for path in tree_paths
            if pathlib.PurePosixPath(path).parent
            == pathlib.PurePosixPath("tui_gateway")
            and path.endswith(".py")
        )
        missing_tui = sorted(REQUIRED_TUI_INPUTS.difference(tui_paths))
        if missing_tui:
            raise DerivationBlocked(
                "pinned Hermes tree is missing required inputs: "
                + ", ".join(missing_tui)
            )
        if not any(path.startswith("tui_gateway/methods_") for path in tui_paths):
            raise DerivationBlocked(
                "pinned Hermes tree contains no tui_gateway/methods_*.py inputs"
            )

        paths = [*tui_paths, *EXPLICIT_INPUTS]
        if len(paths) != len(set(paths)):
            raise DerivationBlocked("declared Hermes input manifest contains duplicates")

        files: dict[str, SourceFile] = {}
        for path in paths:
            object_name = f"{self.revision}:{path}"
            raw = self._git("show", object_name, allow_empty=True)
            if not raw:
                size = self._text("cat-file", "-s", object_name).strip()
                if size != "0":
                    raise DerivationBlocked(
                        f"Git returned empty bytes for non-empty blob: {path}"
                    )
            files[path] = SourceFile.from_bytes(path, raw)
        return files

    def _text(self, *args: str) -> str:
        raw = self._git(*args)
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise DerivationBlocked(
                f"Git output was not strict UTF-8: git {' '.join(args)}"
            ) from error

    def _git(self, *args: str, allow_empty: bool = False) -> bytes:
        try:
            result = subprocess.run(
                ["git", "--no-replace-objects", *args],
                cwd=self.repository,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as error:
            raise DerivationBlocked(
                f"could not execute Git ({type(error).__name__})"
            ) from error
        if result.returncode != 0:
            raise DerivationBlocked(
                f"Git command failed ({' '.join(args)}; exit {result.returncode})"
            )
        if not result.stdout and not allow_empty:
            raise DerivationBlocked(
                f"Git command returned empty evidence: {' '.join(args)}"
            )
        return result.stdout


def derive_catalog(
    files: Mapping[str, SourceFile],
    *,
    expected_counts: tuple[int, int, int] | None = EXPECTED_COUNTS,
    source_commit: str = SOURCE_COMMIT,
    derived_at: str = DERIVED_AT,
) -> dict[str, object]:
    """Derive and validate the catalog from a complete pinned source snapshot."""
    _validate_revision(source_commit)
    _validate_derived_at(derived_at)
    _require_inputs(files)
    _validate_contract_facts(files)

    methods: dict[str, list[str]] = {}
    events: dict[str, list[str]] = {}
    tui_files = [
        files[path]
        for path in sorted(files)
        if pathlib.PurePosixPath(path).parent
        == pathlib.PurePosixPath("tui_gateway")
        and path.endswith(".py")
    ]

    for source in tui_files:
        for match in re.finditer(
            r'@method\(\s*["\']([a-z0-9_.]+)["\']', source.text
        ):
            methods.setdefault(match.group(1), []).append(
                _loc(source.path, source.text, match.start())
            )
        if pathlib.PurePosixPath(source.path).name in INTERNAL_MODULES:
            continue
        for match in re.finditer(
            r'\b(?:_emit|emit)\(\s*["\']([a-z0-9_.]+)["\']', source.text
        ):
            events.setdefault(match.group(1), []).append(
                _loc(source.path, source.text, match.start())
            )

    server = files["tui_gateway/server.py"]
    block_lines = _grep(
        server,
        r'_block\(\s*["\']([a-z0-9_.]+)\.request["\']',
    )
    for stem, locations in block_lines.items():
        events.setdefault(f"{stem}.request", []).extend(locations)
        events.setdefault(f"{stem}.expire", []).append(
            f"server.py (derived: {stem}.request timeout)"
        )

    direct_ready = []
    for path in ("tui_gateway/entry.py", "tui_gateway/ws.py"):
        source = files[path]
        for match in re.finditer(
            r'["\']type["\']\s*:\s*["\']gateway\.ready["\']', source.text
        ):
            direct_ready.append(_loc(path, source.text, match.start()))
    if not direct_ready:
        raise DerivationBlocked("no direct gateway.ready emission was derived")
    events.setdefault("gateway.ready", []).extend(direct_ready)

    api = files["gateway/platforms/api_server.py"]
    rest_routes = _derive_rest_routes(api)

    actual_counts = (len(methods), len(events), len(rest_routes))
    if expected_counts is not None and actual_counts != expected_counts:
        raise DerivationBlocked(
            "derived catalog counts disagree with pinned-source contract: "
            f"expected {expected_counts}, got {actual_counts}"
        )

    manifest = [
        {"path": path, "sha256": files[path].sha256}
        for path in sorted(files)
    ]
    return {
        "title": "Hermes tui_gateway + api_server protocol catalog",
        "source": {
            "repository": SOURCE_REPOSITORY,
            "commit": source_commit,
            "inputs": manifest,
        },
        "derived_from": [
            "tui_gateway/*.py",
            "gateway/platforms/api_server.py",
            "hermes_cli/dashboard_auth/ws_tickets.py",
            "hermes_cli/web_server.py",
        ],
        "derived_at": derived_at,
        "source_note": (
            "Derived exclusively from pinned Git objects; mutable checkout "
            "content is ignored. Source wins over docs."
        ),
        "framing": {
            "transport": "websocket (primary) / REST+SSE (fallback)",
            "path": "/api/ws",
            "encoding": (
                "one JSON-RPC 2.0 object per WebSocket text message, both "
                "directions; stdio is newline-delimited"
            ),
            "event_wrapping": {
                "shape": {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {"type": "<event-name>", "payload": {}},
                },
                "note": (
                    "Server-pushed events ride as notifications named 'event'; "
                    "the app-level name is params.type."
                ),
            },
            "canonical_form": (
                "sorted keys, no insignificant whitespace; fixtures stored "
                "canonically"
            ),
            "heartbeat": {
                "client_method": "gateway.ping",
                "result": {"ok": True},
                "note": (
                    "Handled inline before normal dispatch; keep interval modest."
                ),
            },
            "parse_error_reply": {"code": -32700, "id": None},
            "token_coalescing": (
                "*.delta stream frames may be batched server-side (~30 fps "
                "flush); do not rely on per-token timing."
            ),
        },
        "auth": {
            "websocket_gated": (
                "?ticket=<single-use 30s ticket> minted via dashboard auth; "
                "legacy ?token= rejected"
            ),
            "websocket_loopback_or_insecure": "?token=<dashboard session token>",
            "websocket_internal": (
                "?internal=<process credential> — server-spawned children only, "
                "never clients"
            ),
            "dashboard_http_header": (
                "X-Hermes-Session-Token (legacy Authorization: Bearer accepted)"
            ),
            "api_server": (
                "Authorization: Bearer <API_SERVER_KEY>, required even on loopback"
            ),
        },
        "requests": [
            {"name": name, "defined_at": sorted(set(locations))}
            for name, locations in sorted(methods.items())
        ],
        "events": [
            {"name": name, "emitted_at": sorted(set(locations))}
            for name, locations in sorted(events.items())
        ],
        "rest_routes": rest_routes,
    }


def catalog_bytes(catalog: Mapping[str, object]) -> bytes:
    """Return the sole canonical on-disk encoding: strict UTF-8 with LF EOF."""
    return (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8", errors="strict"
    )


def atomic_write(path: pathlib.Path, content: bytes) -> None:
    """Atomically replace *path* with fully flushed deterministic bytes."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        )
    except OSError as error:
        raise DerivationBlocked(
            f"could not create atomic output beside {path.name}"
        ) from error

    temporary = pathlib.Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise DerivationBlocked(
            f"could not atomically replace {path.name}"
        ) from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def run(
    source_root: pathlib.Path,
    output: pathlib.Path,
    *,
    check: bool,
    revision: str = SOURCE_COMMIT,
    expected_counts: tuple[int, int, int] | None = None,
    derived_at: str | None = None,
) -> int:
    _validate_revision(revision)
    default_output = pathlib.Path(__file__).resolve().parent.parent / "protocol" / "methods.json"
    is_default_output = output.resolve() == default_output

    if revision == SOURCE_COMMIT:
        resolved_counts = EXPECTED_COUNTS if expected_counts is None else expected_counts
        resolved_date = DERIVED_AT if derived_at is None else derived_at
    else:
        if is_default_output:
            raise DerivationBlocked(
                "an alternate Hermes revision requires an explicit non-default output"
            )
        if derived_at is None or expected_counts is None:
            raise DerivationBlocked(
                "an alternate Hermes revision requires explicit derived-at and expected counts"
            )
        resolved_counts = expected_counts
        resolved_date = derived_at

    _validate_derived_at(resolved_date)
    if any(count < 0 for count in resolved_counts):
        raise DerivationBlocked("expected catalog counts must be nonnegative")
    if is_default_output and (
        revision != SOURCE_COMMIT
        or resolved_date != DERIVED_AT
        or resolved_counts != EXPECTED_COUNTS
    ):
        raise DerivationBlocked(
            "the default catalog requires the complete pinned provenance profile"
        )

    files = GitObjectSource(source_root, revision=revision).snapshot()
    catalog = derive_catalog(
        files,
        expected_counts=resolved_counts,
        source_commit=revision,
        derived_at=resolved_date,
    )
    content = catalog_bytes(catalog)
    counts = (
        len(catalog["requests"]),
        len(catalog["events"]),
        len(catalog["rest_routes"]),
    )

    if check:
        try:
            actual = output.read_bytes()
        except FileNotFoundError:
            print(f"catalog drift: {output.name} is missing", file=sys.stderr)
            print("DERIVATION: FAIL")
            return 1
        except OSError as error:
            raise DerivationBlocked(
                f"could not read catalog for --check ({type(error).__name__})"
            ) from error
        if actual != content:
            print(f"catalog drift: {output.name} is not regeneration-identical")
            print("DERIVATION: FAIL")
            return 1
        print(
            f"methods: {counts[0]}  events: {counts[1]}  rest: {counts[2]}"
        )
        print("DERIVATION: PASS")
        return 0

    atomic_write(output, content)
    print(f"methods: {counts[0]}  events: {counts[1]}  rest: {counts[2]}")
    print(f"wrote {output.name}")
    print("DERIVATION: PASS")
    return 0


def _require_inputs(files: Mapping[str, SourceFile]) -> None:
    required = REQUIRED_TUI_INPUTS.union(EXPLICIT_INPUTS)
    missing = sorted(required.difference(files))
    if missing:
        raise DerivationBlocked(
            "source snapshot is missing required inputs: " + ", ".join(missing)
        )
    if not any(path.startswith("tui_gateway/methods_") for path in files):
        raise DerivationBlocked("source snapshot has no methods_*.py input")
    unexpected = [
        path
        for path in files
        if path not in EXPLICIT_INPUTS
        and not (
            pathlib.PurePosixPath(path).parent
            == pathlib.PurePosixPath("tui_gateway")
            and path.endswith(".py")
        )
    ]
    if unexpected:
        raise DerivationBlocked(
            "source snapshot contains undeclared inputs: "
            + ", ".join(sorted(unexpected))
        )


def _validate_revision(revision: str) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise DerivationBlocked(
            "Hermes revision must be a full lowercase 40-hex commit SHA"
        )


def _validate_derived_at(value: str) -> None:
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError as error:
        raise DerivationBlocked(
            "derived-at must be an ISO-8601 calendar date"
        ) from error
    if parsed.isoformat() != value:
        raise DerivationBlocked("derived-at must be an ISO-8601 calendar date")


def _validate_contract_facts(files: Mapping[str, SourceFile]) -> None:
    facts: dict[str, Sequence[tuple[str, str]]] = {
        "tui_gateway/server.py": (
            (
                "event envelope",
                r'return\s*\{\s*["\']jsonrpc["\']\s*:\s*["\']2\.0["\']\s*,\s*'
                r'["\']method["\']\s*:\s*["\']event["\']\s*,\s*'
                r'["\']params["\']\s*:\s*params\s*\}',
            ),
        ),
        "tui_gateway/transport.py": (
            (
                "newline-delimited stdio framing",
                r'json\.dumps\(obj,\s*ensure_ascii=False\)\s*\+\s*["\']\\n["\']',
            ),
        ),
        "tui_gateway/ws.py": (
            (
                "WebSocket text-message serialization",
                r'line[ \t]*=[ \t]*json\.dumps\(obj,[ \t]*'
                r'ensure_ascii=False\)[ \t]*(?:#[^\n]*)?\n',
            ),
            (
                "one JSON value per outbound WebSocket text message",
                r'await\s+self\._ws\.send_text\(line\)',
            ),
            (
                "one inbound WebSocket text message per JSON value",
                r'raw\s*=\s*await\s+ws\.receive_text\(\)',
            ),
            ("JSON-RPC parse error", r'["\']code["\']\s*:\s*-32700'),
            ("heartbeat method", r'req_method\s*==\s*["\']gateway\.ping["\']'),
            ("heartbeat result", r'["\']result["\']\s*:\s*\{["\']ok["\']\s*:\s*True\}'),
            ("token coalescing interval", r'_TOKEN_COALESCE_S\s*=\s*0\.033'),
        ),
        "hermes_cli/dashboard_auth/ws_tickets.py": (
            ("30-second ticket TTL", r'TTL_SECONDS\s*=\s*30'),
            (
                "ticket expiry derived from the TTL",
                r'int\(time\.time\(\)\)\s*\+\s*TTL_SECONDS',
            ),
            (
                "single-use ticket removal",
                r'_tickets\.pop\(ticket,\s*None\)',
            ),
        ),
        "hermes_cli/web_server.py": (
            (
                "dashboard session header",
                r'_SESSION_HEADER_NAME\s*=\s*["\']X-Hermes-Session-Token["\']',
            ),
            (
                "legacy dashboard bearer header",
                r'expected\s*=\s*f["\']Bearer\s+\{_SESSION_TOKEN\}["\']',
            ),
            ("dashboard JSON-RPC websocket", r'@app\.websocket\(["\']/api/ws["\']\)'),
            (
                "gated legacy-token rejection",
                r'legacy\s+``\?token=``\s+path\s+is\s+unconditionally\s+rejected\s+in\s+gated\s+mode',
            ),
            ("ticket consumption", r'consume_ticket\(ticket\)'),
        ),
        "gateway/platforms/api_server.py": (
            (
                "API-server bearer auth",
                r'auth_header\.startswith\(["\']Bearer\s["\']\)',
            ),
            (
                "API key startup requirement",
                r'API_SERVER_KEY\s+is\s+required\s+for\s+the\s+API\s+server',
            ),
            (
                "API key required on loopback",
                r'including\s+loopback-only\s+binds',
            ),
        ),
    }
    for path, requirements in facts.items():
        text = files[path].text
        for label, pattern in requirements:
            if re.search(pattern, text, flags=re.DOTALL) is None:
                raise DerivationBlocked(
                    f"pinned source no longer proves {label}: {path}"
                )


def _derive_rest_routes(source: SourceFile) -> list[dict[str, str]]:
    start = source.text.find("def _http_route_table")
    if start < 0:
        raise DerivationBlocked("api_server.py has no _http_route_table")
    return_match = re.search(
        r"(?m)^[ \t]+return routes[ \t]*$", source.text[start:]
    )
    if return_match is None:
        raise DerivationBlocked("api_server.py route table has no return boundary")
    end = start + return_match.start()
    route_table = source.text[start:end]

    routes: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in re.finditer(
        r'\(["\'](GET|POST|PUT|DELETE|PATCH)["\']\s*,\s*'
        r'["\'](/[^"\']*)["\']',
        route_table,
    ):
        identity = (match.group(1), match.group(2))
        if identity in seen:
            continue
        seen.add(identity)
        routes.append({"method": identity[0], "path": identity[1]})
    if not routes:
        raise DerivationBlocked("api_server.py route table yielded no routes")
    return routes


def _loc(path: str, text: str, offset: int) -> str:
    return (
        f"{pathlib.PurePosixPath(path).name}:"
        f"{text[:offset].count(chr(10)) + 1}"
    )


def _grep(source: SourceFile, pattern: str) -> dict[str, list[str]]:
    matches: dict[str, list[str]] = {}
    for match in re.finditer(pattern, source.text):
        matches.setdefault(match.group(1), []).append(
            _loc(source.path, source.text, match.start())
        )
    return matches


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source_root",
        nargs="?",
        type=pathlib.Path,
        default=pathlib.Path("~/.hermes/hermes-agent").expanduser(),
        help="local Git object database containing the pinned Hermes commit",
    )
    repo = pathlib.Path(__file__).resolve().parent.parent
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=repo / "protocol" / "methods.json",
        help="catalog destination (or comparison target with --check)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare exact bytes without modifying the output",
    )
    parser.add_argument(
        "--revision",
        default=SOURCE_COMMIT,
        help=(
            "full immutable Hermes commit SHA; a non-default revision requires "
            "an explicit non-default --output"
        ),
    )
    parser.add_argument(
        "--derived-at",
        help=(
            "deterministic ISO-8601 catalog derivation date; required for an "
            "alternate revision"
        ),
    )
    parser.add_argument(
        "--expected-counts",
        nargs=3,
        type=int,
        metavar=("REQUESTS", "EVENTS", "REST"),
        help=(
            "exact request, event, and REST count ratchet; required for an "
            "alternate revision"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        return run(
            arguments.source_root.expanduser(),
            arguments.output,
            check=arguments.check,
            revision=arguments.revision,
            expected_counts=(
                None
                if arguments.expected_counts is None
                else tuple(arguments.expected_counts)
            ),
            derived_at=arguments.derived_at,
        )
    except DerivationBlocked as error:
        print(f"derivation blocked: {error}", file=sys.stderr)
        print("DERIVATION: BLOCKED", file=sys.stderr)
        return 2
    except Exception as error:  # pragma: no cover - last-resort fail-closed seam
        print(
            f"derivation blocked by unexpected {type(error).__name__}",
            file=sys.stderr,
        )
        print("DERIVATION: BLOCKED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
