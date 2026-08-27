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
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
CATALOG = REPO / "protocol" / "methods.json"
FIXTURE_DIR = REPO / "Packages" / "HermesKit" / "Tests" / "HermesKitTests" / "Fixtures"
GENERATED = REPO / "Packages" / "HermesKit" / "Tests" / "HermesKitTests" / "ProtocolConformanceTests.swift"
GEN_SCRIPT = REPO / "scripts" / "gen_conformance_tests.py"


def canonical(obj: object) -> bytes:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
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


def main() -> int:
    failures: list[str] = []

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    required = {m["name"] for m in catalog.get("requests", [])}
    required |= {e["name"] for e in catalog.get("events", [])}
    if not required:
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
                envelope = json.loads(raw)
            except json.JSONDecodeError as exc:
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
    gen_text = GENERATED.read_text() if GENERATED.exists() else ""
    uncovered = sorted(n for n in required if f'"{n}"' not in gen_text)
    for name in uncovered:
        failures.append(f"catalog entry missing from generated conformance tests: {name}")

    # --- 3. generator determinism -------------------------------------------
    committed = GENERATED.read_bytes() if GENERATED.exists() else b""
    with tempfile.TemporaryDirectory(prefix="talaria-g3-") as temp_dir:
        candidate = pathlib.Path(temp_dir) / GENERATED.name
        proc = subprocess.run(
            [sys.executable, str(GEN_SCRIPT), "--output", str(candidate)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            failures.append(f"generator failed: {proc.stderr.strip()[:200]}")
        elif not candidate.is_file():
            failures.append("generator succeeded without writing candidate output")
        elif candidate.read_bytes() != committed:
            failures.append(
                "regenerating ProtocolConformanceTests.swift produced a diff "
                "(committed file is stale)"
            )

    print(f"catalog entries : {len(required)}")
    print(f"golden frames   : {total_frames}")
    print(f"uncovered       : {len(uncovered)}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        print("\nG3: FAIL")
        return 1
    print("\nG3: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
