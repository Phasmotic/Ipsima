#!/usr/bin/env python3
"""G3 — protocol conformance.

Two checks, both derived from committed artifacts:

1. Catalog coverage: every request/event name in protocol/methods.json appears
   at least once across the golden fixtures in Tests/HermesKitTests/Fixtures/.
2. Byte identity: every fixture frame, re-encoded canonically
   (sorted keys, compact separators, UTF-8), is byte-identical to the stored
   line. This pins the same canonical form WireCodec.swift produces on the
   Swift side, cross-checked here in Python.

Exit 0 only if both hold.
"""
from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
CATALOG = REPO / "protocol" / "methods.json"
FIXTURE_DIR = REPO / "Packages" / "HermesKit" / "Tests" / "HermesKitTests" / "Fixtures"


def canonical(obj: object) -> bytes:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def main() -> int:
    catalog = json.loads(CATALOG.read_text())
    required = {m["name"] for m in catalog.get("requests", [])}
    required |= {e["name"] for e in catalog.get("events", [])}

    seen: dict[str, int] = {}
    failures: list[str] = []
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
            # byte identity against canonical form
            if canonical(envelope) != raw.encode("utf-8"):
                failures.append(f"{fixture.name}:{lineno}: not canonical (re-encode differs)")
            method = envelope.get("method")
            if method:
                seen[method] = seen.get(method, 0) + 1
            elif "result" in envelope or "error" in envelope:
                # responses carry the method implicitly; count them toward the
                # paired request via params echo when present
                pass

    missing = sorted(required - set(seen))
    if not required:
        # An empty catalog makes every downstream check vacuous.
        print("catalog is EMPTY — protocol derivation not done; refusing hollow pass")
        print("\nG3: FAIL")
        return 1
    for name in missing:
        failures.append(f"catalog method/event never exercised by fixtures: {name}")

    print(f"catalog entries : {len(required)}")
    print(f"fixture frames  : {total_frames}")
    print(f"exercised       : {len(required & set(seen))}/{len(required)}")
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
