from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unittest
import uuid

from scripts import check_launch_activity_sources as checker


REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "check_launch_activity_sources.py"
TEST_ROOT = REPO / ".gauntlet" / "launch-activity-source-tests"

BASELINE_FACTORY = """\
enum AuditedLaunchResourceFactory {
    static func session() -> Session {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.urlSession)
        return URLSession(configuration: configuration)
    }
    static func shared() -> Session {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.urlSession)
        return URLSession.shared
    }
    static func transport() -> Transport {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.webSocketTransport)
        return WebSocketHermesTransport(configuration: configuration)
    }
    static func path() -> Monitor {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.networkPathMonitor)
        return NWPathMonitor()
    }
    static func reachability() -> Reachability {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.reachability)
        return SCNetworkReachabilityCreateWithName(nil, hostName)
    }
    static func timer() -> Clock {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.timer)
        return Timer(timeInterval: 1, repeats: false) { _ in }
    }
    static func scheduled() -> Clock {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.scheduledTimer)
        return Timer.scheduledTimer(withTimeInterval: 1, repeats: false) { _ in }
    }
    static func dispatch() -> Clock {
        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.dispatchTimer)
        return DispatchSource.makeTimerSource()
    }
}
"""


class LaunchActivitySourceTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        self.repository = TEST_ROOT / f"repo-{uuid.uuid4().hex}"
        self.repository.mkdir()
        self.write("App/Talaria/AuditedLaunchResourceFactory.swift", BASELINE_FACTORY)
        self.write("App/Talaria/ContentView.swift", "struct ContentView {}\n")

    def tearDown(self) -> None:
        def make_writable_and_retry(function, target, _exception_info) -> None:
            os.chmod(target, stat.S_IWRITE)
            function(target)

        if self.repository.parent == TEST_ROOT and self.repository.name.startswith("repo-"):
            shutil.rmtree(self.repository, onerror=make_writable_and_retry)

    def write(self, relative: str, source: str) -> None:
        path = self.repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8", newline="\n")

    def assert_bypass_fails(self, expression: str, expected_name: str) -> None:
        self.write("App/Talaria/Bypass.swift", f"let resource = {expression}\n")

        findings = checker.check_repository(self.repository)

        self.assertTrue(
            any(expected_name in finding.message for finding in findings),
            [finding.render() for finding in findings],
        )

    def test_baseline_audited_factory_passes(self) -> None:
        self.assertEqual(checker.check_repository(self.repository), [])

    def test_repository_sources_pass(self) -> None:
        self.assertEqual(checker.check_repository(REPO), [])

    def test_raw_url_session_constructor_fails(self) -> None:
        for expression in (
            "URLSession(configuration: configuration)",
            "URLSession.init(configuration: configuration)",
        ):
            with self.subTest(expression=expression):
                self.assert_bypass_fails(expression, "URLSession constructor")

    def test_shared_url_session_fails(self) -> None:
        self.assert_bypass_fails("URLSession.shared", "URLSession.shared")

    def test_direct_web_socket_transport_fails(self) -> None:
        self.assert_bypass_fails(
            "WebSocketHermesTransport(configuration: configuration)",
            "WebSocketHermesTransport constructor",
        )

    def test_raw_path_monitor_fails(self) -> None:
        for expression in ("NWPathMonitor()", "NWPathMonitor.init()"):
            with self.subTest(expression=expression):
                self.assert_bypass_fails(expression, "NWPathMonitor constructor")

    def test_both_reachability_constructors_fail(self) -> None:
        for expression in (
            "SCNetworkReachabilityCreateWithName(nil, hostName)",
            "SCNetworkReachabilityCreateWithAddress(nil, address)",
        ):
            with self.subTest(expression=expression):
                self.assert_bypass_fails(expression, expression.split("(")[0])

    def test_raw_timer_constructors_fail(self) -> None:
        for expression, expected in (
            ("Timer(timeInterval: 1, repeats: false) { _ in }", "Timer constructor"),
            (
                "Timer.scheduledTimer(withTimeInterval: 1, repeats: false) { _ in }",
                "Timer.scheduledTimer",
            ),
            ("DispatchSource.makeTimerSource()", "DispatchSource timer constructor"),
        ):
            with self.subTest(expression=expression):
                self.assert_bypass_fails(expression, expected)

    def test_factory_constructor_requires_immediately_preceding_audit(self) -> None:
        broken = BASELINE_FACTORY.replace(
            "        return NWPathMonitor()",
            "        let constructionCanMove = true\n"
            "        return NWPathMonitor()",
        )
        self.write("App/Talaria/AuditedLaunchResourceFactory.swift", broken)

        findings = checker.check_repository(self.repository)

        self.assertTrue(
            any("NWPathMonitor constructor is not immediately preceded" in item.message for item in findings)
        )

    def test_factory_constructor_rejects_wrong_audit_case(self) -> None:
        broken = BASELINE_FACTORY.replace(
            "LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.dispatchTimer)",
            "LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.timer)",
        )
        self.write("App/Talaria/AuditedLaunchResourceFactory.swift", broken)

        findings = checker.check_repository(self.repository)

        self.assertTrue(
            any("DispatchSource timer constructor is not immediately preceded" in item.message for item in findings)
        )

    def test_missing_factory_wrapper_fails_closed(self) -> None:
        broken = BASELINE_FACTORY.replace(
            "    static func path() -> Monitor {\n"
            "        LaunchActivityAudit.shared.recordTalariaOwnedConstruction(.networkPathMonitor)\n"
            "        return NWPathMonitor()\n"
            "    }\n",
            "",
        )
        self.write("App/Talaria/AuditedLaunchResourceFactory.swift", broken)

        findings = checker.check_repository(self.repository)

        self.assertTrue(
            any("required audited wrapper for NWPathMonitor constructor is missing" in item.message for item in findings)
        )

    def test_comments_and_string_literals_are_not_constructions(self) -> None:
        self.write(
            "App/Talaria/Decoys.swift",
            """\
// URLSession(configuration: configuration)
/* Timer.scheduledTimer(withTimeInterval: 1, repeats: false) { _ in } */
let ordinary = "NWPathMonitor()"
let raw = #"DispatchSource.makeTimerSource()"#
let multiline = """ + '"""' + "\nWebSocketHermesTransport(configuration: config)\n" + '"""' + "\n",
        )

        self.assertEqual(checker.check_repository(self.repository), [])

    def test_line_comment_at_end_of_file_is_valid_source(self) -> None:
        self.write("App/Talaria/Comment.swift", "// harmless comment without newline")

        self.assertEqual(checker.check_repository(self.repository), [])

    def test_constructor_inside_string_interpolation_is_executable(self) -> None:
        self.write(
            "App/Talaria/Interpolation.swift",
            'let value = "\\(URLSession(configuration: configuration))"\n',
        )

        findings = checker.check_repository(self.repository)

        self.assertTrue(any("URLSession constructor bypasses" in item.message for item in findings))

    def test_unterminated_source_blocks_instead_of_passing(self) -> None:
        self.write("App/Talaria/Broken.swift", "/* never closed\n")

        with self.assertRaises(checker.SourceAuditBlocked):
            checker.check_repository(self.repository)

    def test_cli_distinguishes_pass_fail_and_blocked(self) -> None:
        passing = subprocess.run(
            [sys.executable, "-B", str(CHECKER), "--repository", str(self.repository)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)
        self.assertIn("LAUNCH ACTIVITY SOURCES PASS", passing.stdout)

        self.write("App/Talaria/Bypass.swift", "let monitor = NWPathMonitor()\n")
        failing = subprocess.run(
            [sys.executable, "-B", str(CHECKER), "--repository", str(self.repository)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(failing.returncode, 1, failing.stdout + failing.stderr)
        self.assertIn("LAUNCH ACTIVITY SOURCES FAIL", failing.stderr)

        (self.repository / "App/Talaria").rename(self.repository / "App/Missing")
        blocked = subprocess.run(
            [sys.executable, "-B", str(CHECKER), "--repository", str(self.repository)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn("LAUNCH ACTIVITY SOURCES BLOCKED", blocked.stderr)


if __name__ == "__main__":
    unittest.main()
