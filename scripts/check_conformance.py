#!/usr/bin/env python3
"""G3 — protocol conformance.

Three checks, all derived from committed artifacts:

1. Golden frames: every fixture line in
   Packages/HermesKit/Tests/HermesKitTests/Fixtures/*.jsonl decodes as
   JSON-RPC and is byte-identical to its canonical re-encode (sorted keys,
   compact separators) — the same canonical form WireCodec.swift produces.
2. Generated-test coverage: the committed ProtocolConformanceTests.swift
   (scripts/gen_conformance_tests.py) mentions EVERY method and event name in
   protocol/methods.json.
3. Regeneration determinism: re-running the generator produces no diff.

Exit 0 only if all three hold.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
CATALOG = REPO / "protocol" / "methods.json"
FIXTURE_DIR = REPO / "Packages" / "HermesKit" / "Tests" / "HermesKitTests" / "Fixtures"
GENERATED = REPO / "Packages" / "HermesKit" / "Tests" / "HermesKitTests" / "ProtocolConformanceTests.swift"
GEN_SCRIPT = REPO / "scripts" / "gen_conformance_tests.py"
CATALOG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
GENERATED_TEST_BLOCK = re.compile(
    r"^    func (?P<identity>test[ME]_[A-Za-z0-9_]+)\(\) throws \{\n"
    r"(?P<body>.*?)^    \}$",
    re.MULTILINE | re.DOTALL,
)
REQUEST_HELPER_TEMPLATE = '''    private func assertRoundTrip(
        _ kind: String, name: String, id: JSONRPCID?, file: StaticString = #filePath, line: UInt = #line
    ) throws {
        var envelope = JSONRPCEnvelope(method: name)
        envelope.id = id
        envelope.params = .object(["__probe": .string(name)])
        let data = try codec.encode(envelope)
        let decoded = try codec.decode(data)
        XCTAssertEqual(decoded, envelope, "\\(kind) \\(name) changed on decode", file: file, line: line)
        XCTAssertEqual(decoded.method, name, "\\(kind) \\(name) lost its name", file: file, line: line)
        XCTAssertEqual(decoded.params?["__probe"], .string(name), file: file, line: line)
        let again = try codec.decode(try codec.encode(decoded))
        XCTAssertEqual(again, decoded, "\\(kind) \\(name) not a fixed point", file: file, line: line)
    }'''
EVENT_HELPER_TEMPLATE = '''    private func assertEventRoundTrip(
        type: String, file: StaticString = #filePath, line: UInt = #line
    ) throws {
        let payload: JSONValue = .object(["__probe": .string(type)])
        let params: JSONValue = .object([
            "payload": payload,
            "type": .string(type),
        ])
        let envelope = JSONRPCEnvelope(method: "event", params: params)
        let decoded = try codec.decode(try codec.encode(envelope))
        XCTAssertEqual(decoded, envelope, "event \\(type) changed on decode", file: file, line: line)
        XCTAssertEqual(decoded.method, "event", file: file, line: line)
        XCTAssertNil(decoded.id, file: file, line: line)
        XCTAssertEqual(decoded.params, params, file: file, line: line)
        let again = try codec.decode(try codec.encode(decoded))
        XCTAssertEqual(again, decoded, "event \\(type) not a fixed point", file: file, line: line)
    }'''


class DuplicateJSONKeyError(ValueError):
    """A purported JSON object repeated a member name."""


class NonFiniteJSONNumberError(ValueError):
    """A purported JSON document used NaN or Infinity."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJSONKeyError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_json_constant(token: str) -> None:
    raise NonFiniteJSONNumberError(f"non-finite JSON number {token!r}")


def strict_json_loads(text: str) -> object:
    return json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_json_constant,
    )


def canonical(obj: object) -> bytes:
    text = json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")


def envelope_problem(value: object) -> str | None:
    """Return why *value* is not a supported JSON-RPC 2.0 envelope."""
    if not isinstance(value, dict):
        return "top-level value must be an object"
    if value.get("jsonrpc") != "2.0":
        return 'jsonrpc must equal "2.0"'

    has_method = "method" in value
    has_result = "result" in value
    has_error = "error" in value
    if has_method:
        if not isinstance(value["method"], str) or not value["method"]:
            return "method must be a non-empty string"
        if has_result or has_error:
            return "request/notification must not contain result or error"
    else:
        if has_result == has_error:
            return "response must contain exactly one of result or error"
        if "id" not in value:
            return "response must contain id"

    if "params" in value and not isinstance(value["params"], (dict, list)):
        return "params must be an object or array"

    if "id" in value:
        identifier = value["id"]
        valid_int = type(identifier) is int and -(1 << 63) <= identifier < (1 << 63)
        if identifier is not None and not isinstance(identifier, str) and not valid_int:
            return "id must be null, a string, or an Int64"

    if has_error:
        error = value["error"]
        if not isinstance(error, dict):
            return "error must be an object"
        code = error.get("code")
        if type(code) is not int or not -(1 << 63) <= code < (1 << 63):
            return "error.code must be an Int64"
        if not isinstance(error.get("message"), str):
            return "error.message must be a string"
    return None


def helper_contract_problem(source: str, template: str, label: str) -> str | None:
    """Bind generated helpers to reviewed executable Swift templates."""

    if source.count(template) != 1:
        return f"generated suite does not contain exactly one reviewed {label} helper"
    return None


class ConformanceBlocked(RuntimeError):
    """Required G3 evidence could not be read or evaluated reliably."""


def run_checks() -> int:
    failures: list[str] = []

    if not CATALOG.is_file():
        raise ConformanceBlocked("protocol catalog is missing")
    if not GEN_SCRIPT.is_file():
        raise ConformanceBlocked("conformance generator is missing")
    try:
        catalog = strict_json_loads(CATALOG.read_text(encoding="utf-8"))
    except (
        json.JSONDecodeError,
        DuplicateJSONKeyError,
        NonFiniteJSONNumberError,
    ) as error:
        raise ConformanceBlocked("protocol catalog is invalid JSON") from error
    if not isinstance(catalog, dict):
        raise ConformanceBlocked("protocol catalog root is not an object")
    requests = catalog.get("requests")
    events = catalog.get("events")
    if not isinstance(requests, list) or not isinstance(events, list):
        raise ConformanceBlocked("protocol catalog requests/events are not arrays")
    entries = requests + events
    if any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("name"), str)
        or not entry["name"]
        for entry in entries
    ):
        raise ConformanceBlocked("protocol catalog contains an invalid entry")
    catalog_entries = [
        *(("method", entry["name"]) for entry in requests),
        *(("event", entry["name"]) for entry in events),
    ]
    if any(CATALOG_NAME.fullmatch(name) is None for _, name in catalog_entries):
        raise ConformanceBlocked("protocol catalog contains an unsupported name")
    if len(catalog_entries) != len(set(catalog_entries)):
        raise ConformanceBlocked("protocol catalog contains a duplicate entry")
    if not catalog_entries:
        print("catalog is EMPTY — protocol derivation not done; refusing hollow pass")
        print("\nG3: FAIL")
        return 1

    # --- 1. golden frames ---------------------------------------------------
    total_frames = 0
    for fixture in sorted(FIXTURE_DIR.glob("*.jsonl")):
        for lineno, raw in enumerate(fixture.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            total_frames += 1
            try:
                envelope = strict_json_loads(raw)
            except (
                json.JSONDecodeError,
                DuplicateJSONKeyError,
                NonFiniteJSONNumberError,
            ) as exc:
                failures.append(f"{fixture.name}:{lineno}: invalid JSON ({exc})")
                continue
            problem = envelope_problem(envelope)
            if problem is not None:
                failures.append(
                    f"{fixture.name}:{lineno}: not a JSON-RPC envelope ({problem})"
                )
                continue
            if canonical(envelope) != raw.encode("utf-8"):
                failures.append(f"{fixture.name}:{lineno}: not canonical")
    if total_frames == 0:
        failures.append(
            "no nonblank golden JSON-RPC frames found under "
            f"{FIXTURE_DIR.relative_to(REPO)}"
        )

    # --- 2. generated-test coverage -----------------------------------------
    gen_text = GENERATED.read_text(encoding="utf-8") if GENERATED.exists() else ""
    expected_tests: dict[str, tuple[str, str]] = {}
    for kind, name in catalog_entries:
        tag = "M" if kind == "method" else "E"
        cleaned = "".join(
            part.capitalize() for part in name.replace("-", "_").split(".")
        )
        identity = f"test{tag}_{cleaned}"
        previous = expected_tests.get(identity)
        if previous is not None:
            failures.append(
                "catalog entries collide on generated test identity "
                f"{identity}: {previous[0]} {previous[1]} and {kind} {name}"
            )
        else:
            expected_tests[identity] = (kind, name)

    actual_test_bodies: dict[str, list[str]] = {}
    for match in GENERATED_TEST_BLOCK.finditer(gen_text):
        actual_test_bodies.setdefault(match.group("identity"), []).append(
            match.group("body")
        )
    actual_test_counts = {
        identity: len(bodies) for identity, bodies in actual_test_bodies.items()
    }
    expected_identities = set(expected_tests)
    actual_identities = set(actual_test_counts)
    missing_tests = sorted(expected_identities - actual_identities)
    extra_tests = sorted(actual_identities - expected_identities)
    duplicate_tests = sorted(
        identity for identity, count in actual_test_counts.items() if count != 1
    )
    for identity in missing_tests:
        kind, name = expected_tests[identity]
        failures.append(
            f"catalog {kind} missing generated conformance test {identity}: {name}"
        )
    for identity in extra_tests:
        failures.append(f"unexpected generated conformance test: {identity}")
    for identity in duplicate_tests:
        failures.append(f"generated conformance test is duplicated: {identity}")

    request_helper_problem = helper_contract_problem(
        gen_text, REQUEST_HELPER_TEMPLATE, "request round-trip"
    )
    if request_helper_problem is not None:
        failures.append(request_helper_problem)
    wrong_request_tests = []
    for identity, (kind, name) in expected_tests.items():
        if kind != "method":
            continue
        bodies = actual_test_bodies.get(identity, [])
        required_body = f'try assertRoundTrip("method", name: "{name}", id: .int(1))'
        if len(bodies) == 1 and bodies[0].strip() != required_body:
            wrong_request_tests.append(identity)
    if wrong_request_tests:
        preview = ", ".join(wrong_request_tests[:3])
        suffix = (
            f", ... ({len(wrong_request_tests)} total)"
            if len(wrong_request_tests) > 3
            else ""
        )
        failures.append(
            "generated request tests do not call the reviewed round-trip helper: "
            + preview
            + suffix
        )

    expected_events = {
        identity: name
        for identity, (kind, name) in expected_tests.items()
        if kind == "event"
    }
    if expected_events:
        helper_problem = helper_contract_problem(
            gen_text, EVENT_HELPER_TEMPLATE, "real-event-envelope round-trip"
        )
        if helper_problem is not None:
            failures.append(helper_problem)
        wrong_event_tests = []
        for identity, name in expected_events.items():
            bodies = actual_test_bodies.get(identity, [])
            required_call = f'try assertEventRoundTrip(type: "{name}")'
            if len(bodies) == 1 and bodies[0].strip() != required_call:
                wrong_event_tests.append(identity)
        if wrong_event_tests:
            preview = ", ".join(wrong_event_tests[:3])
            suffix = (
                f", ... ({len(wrong_event_tests)} total)"
                if len(wrong_event_tests) > 3
                else ""
            )
            failures.append(
                "generated event tests do not call the real-envelope helper: "
                + preview
                + suffix
            )

    # --- 3. generator determinism -------------------------------------------
    committed = GENERATED.read_bytes() if GENERATED.exists() else b""
    with tempfile.TemporaryDirectory(prefix="talaria-g3-") as temp_dir:
        candidate = pathlib.Path(temp_dir) / GENERATED.name
        proc = subprocess.run(
            [sys.executable, str(GEN_SCRIPT), "--output", str(candidate)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise ConformanceBlocked(
                f"generator exited with status {proc.returncode}"
            )
        if not candidate.is_file():
            raise ConformanceBlocked(
                "generator succeeded without writing candidate output"
            )
        if candidate.read_bytes() != committed:
            failures.append(
                "regenerating ProtocolConformanceTests.swift produced a diff "
                "(committed file is stale)"
            )

    print(f"catalog entries : {len(catalog_entries)}")
    print(f"golden frames   : {total_frames}")
    print(f"uncovered       : {len(missing_tests)}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        print("\nG3: FAIL")
        return 1
    print("\nG3: PASS")
    return 0


def main() -> int:
    try:
        return run_checks()
    except (
        ConformanceBlocked,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        print(
            f"G3 evidence blocked: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        print("G3: BLOCKED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
