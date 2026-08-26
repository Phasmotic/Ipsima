#!/usr/bin/env python3
"""Derive protocol/methods.json from installed Hermes Agent source.

Source of truth (in priority order):
  1. tui_gateway/methods_*.py + server.py  — @method("name") registrations
  2. tui_gateway/*.py                      — client-facing _emit("name") events
     (compute_host.py / host_supervisor.py excluded: internal supervisor
     control channel, not the client WebSocket surface)
  3. tui_gateway/server.py:_block callers  — interactive .request/.expire
     families (emitted via helpers, invisible to literal scanning)
  4. gateway/platforms/api_server.py       — REST route tuple table

Run from the repo root with the Hermes checkout path as argument:
    python3 scripts/derive_protocol.py ~/.hermes/hermes-agent

The output feeds G3 conformance; see docs/PROTOCOL.md for the human contract
and the doc-vs-source delta log.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

INTERNAL_MODULES = {"compute_host.py", "host_supervisor.py"}

# Interactive request families emitted through _block() rather than _emit();
# each also gains a "<stem>.expire" event on timeout (server.py:4081,4106).
BLOCK_FAMILIES = [
    "approval.request",
    "clarify.request",
    "sudo.request",
    "secret.request",
    "terminal.read.request",
    "preview.read.request",
    "preview.act.request",
    "window.read.request",
    "mcp.setup.request",
    "tour.request",
]

# Events written through direct transport writes rather than _emit().
DIRECT_EVENTS = ["gateway.ready"]


def main(source_root: pathlib.Path, out_path: pathlib.Path) -> int:
    tg = source_root / "tui_gateway"
    if not tg.is_dir():
        print(f"error: {tg} not found", file=sys.stderr)
        return 2

    methods: dict[str, list[str]] = {}
    events: dict[str, list[str]] = {}

    for f in sorted(tg.glob("*.py")):
        text = f.read_text(errors="replace")
        for m in re.finditer(r'@method\(\s*"([a-z0-9_.]+)"', text):
            methods.setdefault(m.group(1), []).append(_loc(f, text, m.start()))
        if f.name in INTERNAL_MODULES:
            continue
        for m in re.finditer(r'\b(?:_emit|emit)\(\s*"([a-z0-9_.]+)"', text):
            events.setdefault(m.group(1), []).append(_loc(f, text, m.start()))

    # _block families + their expire counterparts
    block_lines = _grep(tg / "server.py", r'_block\(\s*"([a-z0-9_.]+)\.request"')
    for stem, locs in block_lines.items():
        events.setdefault(f"{stem}.request", []).extend(locs)
        events.setdefault(f"{stem}.expire", []).append(
            f"server.py (derived: {stem}.request timeout)")
    for ev in DIRECT_EVENTS:
        where = "ws.py (direct transport write at connect)"
        events.setdefault(ev, []).append(where)

    # REST routes from the (METHOD, path, handler) tuple table in api_server.py
    api = source_root / "gateway" / "platforms" / "api_server.py"
    rest_routes = []
    if api.exists():
        text = api.read_text(errors="replace")
        for m in re.finditer(
            r'\("(GET|POST|PUT|DELETE|PATCH)",\s*"(/[^"]*)"', text
        ):
            rest_routes.append({"method": m.group(1), "path": m.group(2)})
    seen = set()
    rest_routes = [r for r in rest_routes
                   if not (r["path"] in seen or seen.add(r["path"]))]

    catalog = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Hermes tui_gateway + api_server protocol catalog",
        "derived_from": [
            "tui_gateway/methods_*.py",
            "tui_gateway/server.py",
            "tui_gateway/ws.py",
            "tui_gateway/entry.py",
            "gateway/platforms/api_server.py",
            "hermes_cli/web_server.py",
        ],
        "derived_at": "2026-08-26",
        "source_note": "Derived from installed Hermes source; regenerate with scripts/derive_protocol.py. Source wins over docs.",
        "framing": {
            "transport": "websocket (primary) / REST+SSE (fallback)",
            "path": "/api/ws",
            "encoding": "newline-delimited JSON-RPC 2.0, both directions",
            "event_wrapping": {
                "shape": {"jsonrpc": "2.0", "method": "event",
                          "params": {"type": "<event-name>", "payload": {}}},
                "note": "Server-pushed events ride as notifications named 'event'; the app-level name is params.type."
            },
            "canonical_form": "sorted keys, no insignificant whitespace; fixtures stored canonically",
            "heartbeat": {
                "client_method": "gateway.ping",
                "result": {"ok": True},
                "note": "Handled inline before normal dispatch; keep interval modest."
            },
            "parse_error_reply": {"code": -32700, "id": None},
            "token_coalescing": "*.delta stream frames may be batched server-side (~30 fps flush); do not rely on per-token timing."
        },
        "auth": {
            "websocket_gated": "?ticket=<single-use 30s ticket> minted via dashboard auth; legacy ?token= rejected",
            "websocket_loopback_or_insecure": "?token=<dashboard session token>",
            "websocket_internal": "?internal=<process credential> — server-spawned children only, never clients",
            "dashboard_http_header": "X-Hermes-Session-Token (legacy Authorization: Bearer accepted)",
            "api_server": "Authorization: Bearer <API_SERVER_KEY>, required even on loopback"
        },
        "requests": [{"name": k, "defined_at": sorted(set(v))}
                     for k, v in sorted(methods.items())],
        "events": [{"name": k, "emitted_at": sorted(set(v))}
                   for k, v in sorted(events.items())],
        "rest_routes": rest_routes,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(catalog, indent=2) + "\n")
    print(f"methods: {len(methods)}  events: {len(events)}  rest: {len(rest_routes)}")
    print(f"wrote {out_path}")
    return 0


def _loc(file: pathlib.Path, text: str, offset: int) -> str:
    return f"{file.name}:{text[:offset].count(chr(10)) + 1}"


def _grep(path: pathlib.Path, pattern: str) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    out: dict[str, list[str]] = {}
    text = path.read_text(errors="replace")
    for m in re.finditer(pattern, text):
        out.setdefault(m.group(1), []).append(_loc(path, text, m.start()))
    return out


if __name__ == "__main__":
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "~/.hermes/hermes-agent").expanduser()
    repo = pathlib.Path(__file__).resolve().parent.parent
    sys.exit(main(root, repo / "protocol" / "methods.json"))
