#!/usr/bin/env python3
"""G3 — protocol conformance.

Three checks, all derived from committed artifacts:

1. Golden frames: every fixture line in
   Packages/HermesKit/Tests/HermesKitTests/Fixtures/*.jsonl decodes as
   JSON-RPC and is byte-identical to its canonical re-encode (sorted keys,
   compact separators) — the same canonical form WireCodec.swift produces.
2. Generated-test coverage: the six committed generated Swift files mention
   EVERY request and event identity in protocol/methods.json and bind each to
   its reviewed request or real-event-envelope helper.
3. Regeneration determinism: two isolated generations are byte-identical and
   match both committed outputs.

Exit 0 only if all three hold.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

if __package__:
    from scripts import derive_protocol as derivation
else:  # direct execution from this or a copied scripts directory
    import derive_protocol as derivation  # type: ignore[no-redef]

REPO = pathlib.Path(__file__).resolve().parent.parent
CATALOG = REPO / "protocol" / "methods.json"
FIXTURE_DIR = REPO / "Packages" / "HermesKit" / "Tests" / "HermesKitTests" / "Fixtures"
GENERATED_DIR = REPO / "Packages" / "HermesKit" / "Tests" / "HermesKitTests"
GENERATED_FILES = (
    "ProtocolConformanceTests.swift",
    "ProtocolRequestConformanceTests1.swift",
    "ProtocolRequestConformanceTests2.swift",
    "ProtocolRequestConformanceTests3.swift",
    "ProtocolRequestConformanceTests4.swift",
    "ProtocolEventConformanceTests.swift",
)
GEN_SCRIPT = REPO / "scripts" / "gen_conformance_tests.py"
CATALOG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
SANITIZED_FIELD = re.compile(r"field_[0-9]{3}\Z")
SANITIZED_ID = re.compile(r"id-[1-9][0-9]*\Z")
REDACTED_TEXT = "<redacted>"
GENERATED_TEST_BLOCK = re.compile(
    r"^    func (?P<identity>test[ME]_[A-Za-z0-9_]+)\(\) throws \{\n"
    r"        (?P<body>[^\n]+)\n    \}$",
    re.MULTILINE,
)
REQUEST_HELPER_TEMPLATE = '''    func assertRoundTrip(
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
        let again = try codec.decode(self.codec.encode(decoded))
        XCTAssertEqual(again, decoded, "\\(kind) \\(name) not a fixed point", file: file, line: line)
    }'''
EVENT_HELPER_TEMPLATE = '''    private func assertEventRoundTrip(
        type: String, file: StaticString = #filePath, line: UInt = #line
    ) throws {
        let codec = WireCodec()
        let payload: JSONValue = .object(["__probe": .string(type)])
        let params: JSONValue = .object([
            "payload": payload,
            "type": .string(type),
        ])
        let envelope = JSONRPCEnvelope(method: "event", params: params)
        let data = try codec.encode(envelope)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertFalse(object.keys.contains("id"), "event \\(type) encoded an id", file: file, line: line)
        let decoded = try codec.decode(data)
        XCTAssertEqual(decoded, envelope, "event \\(type) changed on decode", file: file, line: line)
        XCTAssertEqual(decoded.method, "event", file: file, line: line)
        XCTAssertNil(decoded.id, file: file, line: line)
        XCTAssertEqual(decoded.params, params, file: file, line: line)
        let again = try codec.decode(codec.encode(decoded))
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


def _sanitized_generic_problem(value: object) -> str | None:
    """Reject any fixture payload that can still carry captured leaf data."""

    if value is None:
        return None
    if isinstance(value, bool):
        return None if value is False else "boolean payload was not normalized"
    if type(value) is int:
        return None if value == 0 else "integer payload was not normalized"
    if isinstance(value, float):
        return None if value == 0.5 else "floating payload was not normalized"
    if isinstance(value, str):
        return None if value == REDACTED_TEXT else "text payload was not redacted"
    if isinstance(value, list):
        for item in value:
            problem = _sanitized_generic_problem(item)
            if problem is not None:
                return problem
        return None
    if isinstance(value, dict):
        expected = {
            f"field_{index:03d}" for index in range(1, len(value) + 1)
        }
        if set(value) != expected or any(
            SANITIZED_FIELD.fullmatch(key) is None for key in value
        ):
            return "object field names were not normalized"
        for item in value.values():
            problem = _sanitized_generic_problem(item)
            if problem is not None:
                return problem
        return None
    return "payload contains an unsupported value"


def sanitized_fixture_problem(
    value: object,
    request_names: frozenset[str],
    event_names: frozenset[str],
) -> str | None:
    """Return why a canonical envelope violates the capture redaction contract."""

    if not isinstance(value, dict):
        return "sanitized fixture root must be an object"
    allowed_root = {"jsonrpc", "id", "method", "params", "result", "error"}
    if not set(value).issubset(allowed_root):
        return "unknown top-level members were not removed"
    if value.get("jsonrpc") != "2.0":
        return "jsonrpc marker was not preserved"

    if "id" in value:
        identifier = value["id"]
        valid = (
            identifier is None
            or (type(identifier) is int and identifier > 0)
            or (
                isinstance(identifier, str)
                and SANITIZED_ID.fullmatch(identifier) is not None
            )
        )
        if not valid:
            return "JSON-RPC id was not deterministically aliased"

    method = value.get("method")
    if method == "event":
        if "id" in value:
            return "server event retained an id"
        params = value.get("params")
        if not isinstance(params, dict) or set(params) != {"payload", "type"}:
            return "server event params were not reduced to type and payload"
        event_type = params.get("type")
        if not isinstance(event_type, str) or event_type not in event_names:
            return "server event type is outside the pinned catalog"
        return _sanitized_generic_problem(params["payload"])

    if method is not None:
        if not isinstance(method, str) or method not in request_names:
            return "request method is outside the pinned catalog"
        if "params" in value:
            return _sanitized_generic_problem(value["params"])
        return None

    if "result" in value:
        return _sanitized_generic_problem(value["result"])

    error = value.get("error")
    if not isinstance(error, dict):
        return "error response is malformed"
    allowed_error = {"code", "message", "data"}
    if not set(error).issubset(allowed_error):
        return "unknown error members were not removed"
    if error.get("code") != -32000 or error.get("message") != REDACTED_TEXT:
        return "error code or message was not normalized"
    if "data" in error:
        return _sanitized_generic_problem(error["data"])
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
        catalog_raw = CATALOG.read_bytes()
        catalog = strict_json_loads(catalog_raw.decode("utf-8", errors="strict"))
    except (
        json.JSONDecodeError,
        DuplicateJSONKeyError,
        NonFiniteJSONNumberError,
    ) as error:
        raise ConformanceBlocked("protocol catalog is invalid JSON") from error
    if not isinstance(catalog, dict):
        raise ConformanceBlocked("protocol catalog root is not an object")
    if hashlib.sha256(catalog_raw).hexdigest() != derivation.PINNED_CATALOG_SHA256:
        failures.append(
            "protocol catalog bytes do not match the pinned Hermes derivation"
        )
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
    request_names = frozenset(entry["name"] for entry in requests)
    event_names = frozenset(entry["name"] for entry in events)
    if any(CATALOG_NAME.fullmatch(name) is None for _, name in catalog_entries):
        raise ConformanceBlocked("protocol catalog contains an unsupported name")
    if len(catalog_entries) != len(set(catalog_entries)):
        failures.append("protocol catalog contains a duplicate request/event entry")
    if not requests:
        failures.append("protocol catalog request namespace is empty")
    if not events:
        failures.append("protocol catalog event namespace is empty")
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
            safety_problem = sanitized_fixture_problem(
                envelope, request_names, event_names
            )
            if safety_problem is not None:
                failures.append(
                    f"{fixture.name}:{lineno}: unsafe golden fixture "
                    f"({safety_problem})"
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
    generated_texts: dict[str, str] = {}
    for filename in GENERATED_FILES:
        path = GENERATED_DIR / filename
        generated_texts[filename] = (
            path.read_text(encoding="utf-8") if path.is_file() else ""
        )
    request_text = generated_texts[GENERATED_FILES[0]]
    event_text = generated_texts[GENERATED_FILES[-1]]
    gen_text = "\n".join(generated_texts.values())
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
        request_text, REQUEST_HELPER_TEMPLATE, "request round-trip"
    )
    if request_helper_problem is not None:
        failures.append(request_helper_problem)
    wrong_request_tests = []
    for identity, (kind, name) in expected_tests.items():
        if kind != "method":
            continue
        bodies = actual_test_bodies.get(identity, [])
        required_body = (
            f'try self.assertRoundTrip("method", name: "{name}", id: .int(1))'
        )
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
            event_text, EVENT_HELPER_TEMPLATE, "real-event-envelope round-trip"
        )
        if helper_problem is not None:
            failures.append(helper_problem)
        wrong_event_tests = []
        for identity, name in expected_events.items():
            bodies = actual_test_bodies.get(identity, [])
            required_call = f'try self.assertEventRoundTrip(type: "{name}")'
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
    committed = {
        filename: (GENERATED_DIR / filename).read_bytes()
        if (GENERATED_DIR / filename).is_file()
        else b""
        for filename in GENERATED_FILES
    }
    with tempfile.TemporaryDirectory(prefix="talaria-g3-") as temp_dir:
        candidates: list[dict[str, bytes]] = []
        generation_failed = False
        for generation in ("first", "second"):
            output_directory = pathlib.Path(temp_dir) / generation
            proc = subprocess.run(
                [
                    sys.executable,
                    str(GEN_SCRIPT),
                    "--catalog",
                    str(CATALOG),
                    "--output-directory",
                    str(output_directory),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 1:
                failures.append("generator rejected the readable catalog contract")
                generation_failed = True
                break
            if proc.returncode != 0:
                raise ConformanceBlocked(
                    f"generator exited with status {proc.returncode}"
                )
            if not proc.stdout.strip() or proc.stderr:
                raise ConformanceBlocked(
                    "generator success evidence was empty or emitted stderr"
                )
            inventory = sorted(
                path.name for path in output_directory.iterdir() if path.is_file()
            ) if output_directory.is_dir() else []
            if inventory != sorted(GENERATED_FILES):
                raise ConformanceBlocked(
                    "generator succeeded without the exact output inventory"
                )
            output_bytes = {
                filename: (output_directory / filename).read_bytes()
                for filename in GENERATED_FILES
            }
            if any(not content for content in output_bytes.values()):
                raise ConformanceBlocked("generator wrote an empty output")
            candidates.append(output_bytes)
        if not generation_failed:
            if candidates[0] != candidates[1]:
                failures.append("two isolated generator runs produced different bytes")
            if candidates[0] != committed:
                failures.append(
                    "regenerating conformance Swift sources produced a diff "
                    "(committed files are stale)"
                )

    print(f"catalog requests: {len(requests)}")
    print(f"catalog events  : {len(events)}")
    print(f"generated tests : {len(actual_identities)}")
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
