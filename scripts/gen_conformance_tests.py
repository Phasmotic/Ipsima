#!/usr/bin/env python3
"""Generate deterministic Swift conformance tests from protocol/methods.json.

Requests and events are separate protocol namespaces. Request tests exercise a
normal JSON-RPC request; event tests exercise Hermes' real server-push envelope
(`method == "event"`, with the catalog event name in `params.type`).

Six committed files keep the mechanical suite below the project's file- and
type-length limits without suppressing either lint rule. Every file is UTF-8,
LF-only, and written atomically after the full catalog has been validated.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
CATALOG = REPO / "protocol" / "methods.json"
OUT_DIR = REPO / "Packages" / "HermesKit" / "Tests" / "HermesKitTests"
REQUEST_FILE = "ProtocolConformanceTests.swift"
REQUEST_SHARD_FILES = tuple(
    f"ProtocolRequestConformanceTests{index}.swift" for index in range(1, 5)
)
EVENT_FILE = "ProtocolEventConformanceTests.swift"
OUTPUT_FILES = (REQUEST_FILE, *REQUEST_SHARD_FILES, EVENT_FILE)
CATALOG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
SWIFT_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class GenerationError(ValueError):
    """A readable catalog cannot produce a one-to-one Swift test suite."""


class GenerationBlocked(RuntimeError):
    """Catalog or output evidence could not be read or written reliably."""


class DuplicateJSONKeyError(ValueError):
    """A JSON object repeated a member name."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJSONKeyError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number {token!r}")


def _swift_ident(kind: str, name: str) -> str:
    tag = "M" if kind == "request" else "E"
    cleaned = "".join(
        part.capitalize() for part in name.replace("-", "_").split(".")
    )
    identity = f"test{tag}_{cleaned}"
    if SWIFT_IDENTIFIER.fullmatch(identity) is None:
        raise GenerationError(
            f"catalog {kind} {name!r} does not form a Swift identifier"
        )
    return identity


def _catalog_names(catalog: object, key: str) -> list[str]:
    if not isinstance(catalog, dict):
        raise GenerationError("catalog root must be an object")
    entries = catalog.get(key)
    if not isinstance(entries, list):
        raise GenerationError(f"catalog {key!r} must be an array")

    names: list[str] = []
    seen: set[str] = set()
    kind = "request" if key == "requests" else "event"
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise GenerationError(f"catalog {key}[{index}] must be an object")
        name = entry.get("name")
        if not isinstance(name, str) or CATALOG_NAME.fullmatch(name) is None:
            raise GenerationError(f"catalog {key}[{index}] has an invalid name")
        if name in seen:
            raise GenerationError(f"catalog contains duplicate {kind} name {name!r}")
        seen.add(name)
        names.append(name)
    return sorted(names)


def load_catalog(path: pathlib.Path) -> tuple[list[str], list[str]]:
    try:
        raw = path.read_bytes()
        if not raw:
            raise GenerationError("catalog is empty")
        text = raw.decode("utf-8", errors="strict")
        catalog = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except OSError as error:
        raise GenerationBlocked("catalog could not be read") from error
    except UnicodeError as error:
        raise GenerationBlocked("catalog is not valid UTF-8") from error
    except (json.JSONDecodeError, DuplicateJSONKeyError, ValueError) as error:
        raise GenerationBlocked("catalog is not strict JSON") from error

    requests = _catalog_names(catalog, "requests")
    events = _catalog_names(catalog, "events")
    if not requests:
        raise GenerationError("catalog has no requests — refusing a hollow namespace")
    if not events:
        raise GenerationError("catalog has no events — refusing a hollow namespace")

    identities: dict[str, tuple[str, str]] = {}
    for kind, names in (("request", requests), ("event", events)):
        for name in names:
            identity = _swift_ident(kind, name)
            previous = identities.get(identity)
            if previous is not None:
                raise GenerationError(
                    "catalog entries collide on generated Swift identity "
                    f"{identity!r}: {previous[0]} {previous[1]!r} and "
                    f"{kind} {name!r}"
                )
            identities[identity] = (kind, name)
    return requests, events


def _test_lines(kind: str, name: str) -> list[str]:
    identity = _swift_ident(kind, name)
    if kind == "request":
        call = f'try assertRoundTrip("method", name: "{name}", id: .int(1))'
    else:
        call = f'try assertEventRoundTrip(type: "{name}")'
    return [
        f"    func {identity}() throws {{",
        f"        {call.replace('try ', 'try self.', 1)}",
        "    }",
    ]


def _append_extension(lines: list[str], names: list[str], kind: str) -> None:
    lines.append("extension ProtocolConformanceTests {")
    for index, name in enumerate(names):
        lines.extend(_test_lines(kind, name))
        if index != len(names) - 1:
            lines.append("")
    lines.append("}")


def render_outputs(
    requests: list[str], events: list[str]
) -> dict[str, bytes]:
    request_lines = [
        "@testable import HermesKit",
        "import XCTest",
        "",
        "/// Generated canonical request tests: decode → encode must remain stable.",
        "final class ProtocolConformanceTests: XCTestCase {",
        "    private let codec = WireCodec()",
        "",
        "    func assertRoundTrip(",
        "        _ kind: String, name: String, id: JSONRPCID?, file: StaticString = #filePath, line: UInt = #line",
        "    ) throws {",
        "        var envelope = JSONRPCEnvelope(method: name)",
        "        envelope.id = id",
        '        envelope.params = .object(["__probe": .string(name)])',
        "        let data = try codec.encode(envelope)",
        "        let decoded = try codec.decode(data)",
        '        XCTAssertEqual(decoded, envelope, "\\(kind) \\(name) changed on decode", file: file, line: line)',
        '        XCTAssertEqual(decoded.method, name, "\\(kind) \\(name) lost its name", file: file, line: line)',
        '        XCTAssertEqual(decoded.params?["__probe"], .string(name), file: file, line: line)',
        "        let again = try codec.decode(self.codec.encode(decoded))",
        '        XCTAssertEqual(again, decoded, "\\(kind) \\(name) not a fixed point", file: file, line: line)',
        "    }",
        "}",
        "",
    ]
    request_shards: list[list[str]] = [[] for _ in REQUEST_SHARD_FILES]
    for index, name in enumerate(requests):
        request_shards[index % len(request_shards)].append(name)

    event_lines = [
        "import Foundation",
        "@testable import HermesKit",
        "import XCTest",
        "",
        "extension ProtocolConformanceTests {",
        "    private func assertEventRoundTrip(",
        "        type: String, file: StaticString = #filePath, line: UInt = #line",
        "    ) throws {",
        "        let codec = WireCodec()",
        '        let payload: JSONValue = .object(["__probe": .string(type)])',
        "        let params: JSONValue = .object([",
        '            "payload": payload,',
        '            "type": .string(type),',
        "        ])",
        '        let envelope = JSONRPCEnvelope(method: "event", params: params)',
        "        let data = try codec.encode(envelope)",
        "        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])",
        '        XCTAssertFalse(object.keys.contains("id"), "event \\(type) encoded an id", file: file, line: line)',
        "        let decoded = try codec.decode(data)",
        '        XCTAssertEqual(decoded, envelope, "event \\(type) changed on decode", file: file, line: line)',
        '        XCTAssertEqual(decoded.method, "event", file: file, line: line)',
        "        XCTAssertNil(decoded.id, file: file, line: line)",
        "        XCTAssertEqual(decoded.params, params, file: file, line: line)",
        "        let again = try codec.decode(codec.encode(decoded))",
        '        XCTAssertEqual(again, decoded, "event \\(type) not a fixed point", file: file, line: line)',
        "    }",
        "",
    ]
    for index, name in enumerate(events):
        event_lines.extend(_test_lines("event", name))
        if index != len(events) - 1:
            event_lines.append("")
    event_lines.extend(["}", ""])

    outputs = {
        REQUEST_FILE: ("\n".join(request_lines).rstrip("\n") + "\n").encode("utf-8"),
        EVENT_FILE: ("\n".join(event_lines).rstrip("\n") + "\n").encode("utf-8"),
    }
    for filename, names in zip(REQUEST_SHARD_FILES, request_shards, strict=True):
        shard_lines: list[str] = []
        _append_extension(shard_lines, names, "request")
        shard_lines.append("")
        outputs[filename] = (
            "\n".join(shard_lines).rstrip("\n") + "\n"
        ).encode("utf-8")
    return outputs


def _write_atomic(path: pathlib.Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def generate(catalog_path: pathlib.Path, output_directory: pathlib.Path) -> tuple[int, int]:
    requests, events = load_catalog(catalog_path)
    outputs = render_outputs(requests, events)
    for filename in OUTPUT_FILES:
        _write_atomic(output_directory / filename, outputs[filename])
    return len(requests), len(events)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=pathlib.Path, default=CATALOG)
    parser.add_argument("--output-directory", type=pathlib.Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    try:
        request_count, event_count = generate(args.catalog, args.output_directory)
    except GenerationError as error:
        print(f"generation failed: {error}", file=sys.stderr)
        return 1
    except (GenerationBlocked, OSError) as error:
        print(f"generation blocked: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(
        f"wrote {request_count} request tests + {event_count} event tests = "
        f"{request_count + event_count} tests to {args.output_directory}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
