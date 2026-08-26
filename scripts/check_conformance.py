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

REPO = pathlib.Path(__file__).resolve().parent.parent
CATALOG = REPO / "protocol" / "methods.json"
FIXTURE_DIR = REPO / "Packages" / "HermesKit" / "Tests" / "HermesKitTests" / "Fixtures"
GENERATED = REPO / "Packages" / "HermesKit" / "Tests" / "HermesKitTests" / "ProtocolConformanceTests.swift"
GEN_SCRIPT = REPO / "scripts" / "gen_conformance_tests.py"


def canonical(obj: object) -> bytes:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def main() -> int:
    failures: list[str] = []

    catalog = json.loads(CATALOG.read_text())
    required = {m["name"] for m in catalog.get("requests", [])}
    required |= {e["name"] for e in catalog.get("events", [])}
    if not required:
        print("catalog is EMPTY — protocol derivation not done; refusing hollow pass")
        print("\nG3: FAIL")
        return 1

    # --- 1. golden frames ---------------------------------------------------
    total_frames = 0
    for fixture in sorted(FIXTURE_DIR.glob("*.jsonl")):
        for lineno, raw in enumerate(fixture.read_text().splitlines(), 1):
            if not raw.strip():
                continue
            total_frames += 1
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError as exc:
                failures.append(f"{fixture.name}:{lineno}: invalid JSON ({exc})")
                continue
            if canonical(envelope) != raw.encode("utf-8"):
                failures.append(f"{fixture.name}:{lineno}: not canonical")

    # --- 2. generated-test coverage -----------------------------------------
    gen_text = GENERATED.read_text() if GENERATED.exists() else ""
    uncovered = sorted(n for n in required if f'"{n}"' not in gen_text)
    for name in uncovered:
        failures.append(f"catalog entry missing from generated conformance tests: {name}")

    # --- 3. generator determinism -------------------------------------------
    before = GENERATED.read_bytes() if GENERATED.exists() else b""
    proc = subprocess.run([sys.executable, str(GEN_SCRIPT)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        failures.append(f"generator failed: {proc.stderr.strip()[:200]}")
    else:
        after = GENERATED.read_bytes()
        if after != before:
            failures.append("regenerating ProtocolConformanceTests.swift produced a diff "
                            "(committed file is stale)")

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
