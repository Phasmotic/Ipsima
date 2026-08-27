from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest

from scripts.ensure_simulator_booted import SimulatorBlocked, ensure_ready


REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "tier-b.yml"
VALID_UDID = "12345678-1234-1234-1234-123456789ABC"


def result(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def inventory(*devices: object) -> str:
    return json.dumps({"devices": {"runtime": list(devices)}})


def device(
    *, state: str = "Booted", available: object = True, udid: str = VALID_UDID
) -> dict[str, object]:
    return {"udid": udid, "state": state, "isAvailable": available}


class QueueRunner:
    def __init__(self, *responses: subprocess.CompletedProcess[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[str]] = []

    def __call__(
        self, arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(arguments)
        if not self.responses:
            raise AssertionError("unexpected process invocation")
        return self.responses.pop(0)


class SimulatorReadinessTests(unittest.TestCase):
    def test_fresh_boot_and_nonempty_readiness_pass(self) -> None:
        runner = QueueRunner(
            result(),
            result(stdout="Finished\n"),
            result(stdout=inventory(device())),
        )

        evidence = ensure_ready(VALID_UDID, runner=runner)

        self.assertIn("booted and ready", evidence)
        self.assertEqual(runner.calls[0], ["xcrun", "simctl", "boot", VALID_UDID])
        self.assertFalse(runner.responses)

    def test_already_booted_is_proven_before_boot_failure_is_tolerated(self) -> None:
        runner = QueueRunner(
            result(returncode=149, stderr="already booted"),
            result(stdout=inventory(device())),
            result(stderr="Ready\n"),
            result(stdout=inventory(device())),
        )

        ensure_ready(VALID_UDID, runner=runner)

        self.assertEqual(
            runner.calls[1],
            ["xcrun", "simctl", "list", "devices", "available", "-j"],
        )
        self.assertFalse(runner.responses)

    def test_unproven_boot_failure_is_blocked(self) -> None:
        invalid_inventories = (
            "not-json",
            json.dumps({"unexpected": {}}),
            inventory(),
            inventory(device(), device()),
            inventory(device(state="Shutdown")),
            inventory(device(state="Booting")),
            inventory(device(available=False)),
            inventory(device(available="YES")),
        )
        for document in invalid_inventories:
            with self.subTest(document=document):
                runner = QueueRunner(result(returncode=1), result(stdout=document))
                with self.assertRaises(SimulatorBlocked):
                    ensure_ready(VALID_UDID, runner=runner)

    def test_failed_or_empty_bootstatus_is_blocked(self) -> None:
        for readiness in (result(returncode=1), result()):
            with self.subTest(readiness=readiness):
                runner = QueueRunner(result(), readiness)
                with self.assertRaises(SimulatorBlocked):
                    ensure_ready(VALID_UDID, runner=runner)

    def test_final_state_must_still_be_exactly_booted(self) -> None:
        runner = QueueRunner(
            result(),
            result(stdout="Finished\n"),
            result(stdout=inventory(device(state="Shutdown"))),
        )

        with self.assertRaises(SimulatorBlocked):
            ensure_ready(VALID_UDID, runner=runner)

    def test_invalid_identifier_never_invokes_xcrun(self) -> None:
        runner = QueueRunner()

        with self.assertRaises(SimulatorBlocked):
            ensure_ready("", runner=runner)

        self.assertEqual(runner.calls, [])

    def test_tier_b_readiness_precedes_ui_tests_without_blanket_tolerance(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        readiness = workflow.index("python3 -B scripts/ensure_simulator_booted.py")
        ui_tests = workflow.index("-scheme Talaria-UI")

        self.assertLess(readiness, ui_tests)
        self.assertIn('--udid "$SMALL_UDID"', workflow[readiness:ui_tests])
        self.assertNotIn("|| true", workflow[readiness:ui_tests])
        self.assertIn("test -s .gauntlet/small-simulator-readiness.log", workflow)


if __name__ == "__main__":
    unittest.main()
