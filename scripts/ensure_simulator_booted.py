#!/usr/bin/env python3
"""Fail closed unless one exact simulator is booted and fully ready."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any


UDID = re.compile(
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)
Runner = Callable[..., subprocess.CompletedProcess[str]]


class SimulatorBlocked(RuntimeError):
    """Simulator readiness could not be established decisively."""


def invoke(
    arguments: list[str], *, runner: Runner, xcrun: str
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            [xcrun, *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise SimulatorBlocked("xcrun could not be executed") from error


def require_exact_booted_inventory(
    result: subprocess.CompletedProcess[str], udid: str
) -> None:
    if result.returncode != 0 or not result.stdout.strip():
        raise SimulatorBlocked("simulator inventory was unavailable")
    try:
        document: Any = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SimulatorBlocked("simulator inventory was malformed") from error
    if not isinstance(document, dict) or not isinstance(document.get("devices"), dict):
        raise SimulatorBlocked("simulator inventory had an unexpected schema")

    matches: list[dict[str, Any]] = []
    for devices in document["devices"].values():
        if not isinstance(devices, list):
            raise SimulatorBlocked("simulator inventory had an unexpected schema")
        for device in devices:
            if not isinstance(device, dict):
                raise SimulatorBlocked("simulator inventory had an unexpected schema")
            if device.get("udid") == udid:
                matches.append(device)

    if len(matches) != 1:
        raise SimulatorBlocked("exactly one matching simulator was not found")
    if matches[0].get("isAvailable") is not True:
        raise SimulatorBlocked("the matching simulator was unavailable")
    if matches[0].get("state") != "Booted":
        raise SimulatorBlocked("the matching simulator was not booted")


def ensure_ready(
    udid: str, *, runner: Runner = subprocess.run, xcrun: str = "xcrun"
) -> str:
    if UDID.fullmatch(udid) is None:
        raise SimulatorBlocked("the simulator identifier was invalid")

    boot = invoke(["simctl", "boot", udid], runner=runner, xcrun=xcrun)
    if boot.returncode != 0:
        inventory = invoke(
            ["simctl", "list", "devices", "available", "-j"],
            runner=runner,
            xcrun=xcrun,
        )
        require_exact_booted_inventory(inventory, udid)

    readiness = invoke(
        ["simctl", "bootstatus", udid, "-b"], runner=runner, xcrun=xcrun
    )
    if readiness.returncode != 0:
        raise SimulatorBlocked("simulator boot readiness failed")
    readiness_evidence = "\n".join((readiness.stdout, readiness.stderr)).strip()
    if not readiness_evidence:
        raise SimulatorBlocked("simulator boot readiness returned empty evidence")

    final_inventory = invoke(
        ["simctl", "list", "devices", "available", "-j"],
        runner=runner,
        xcrun=xcrun,
    )
    require_exact_booted_inventory(final_inventory, udid)
    evidence_bytes = len(readiness_evidence.encode("utf-8"))
    return f"simulator is booted and ready ({evidence_bytes} evidence bytes)"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--udid", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        evidence = ensure_ready(arguments.udid)
    except SimulatorBlocked as error:
        print(f"SIMULATOR BLOCKED: {error}", file=sys.stderr)
        return 2
    print(f"SIMULATOR PASS: {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
