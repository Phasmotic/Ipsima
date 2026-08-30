from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from scripts import check_tier_b_run_snapshot as snapshot_checker


REPO = Path(__file__).resolve().parent.parent
HELPERS = REPO / "scripts" / "gauntlet_status.sh"
GAUNTLET = REPO / "scripts" / "gauntlet.sh"
LAUNCHER = REPO / "scripts" / "gauntlet.ps1"
LAUNCHER_HELPERS = REPO / "scripts" / "gauntlet_launcher_helpers.ps1"
LAUNCHER_TEST = REPO / "scripts" / "test_gauntlet_launcher.ps1"
WORKFLOW = REPO / ".github" / "workflows" / "tier-b.yml"
LINUX_CORE_WORKFLOW = REPO / ".github" / "workflows" / "linux-g1-g5-core.yml"
PR_LINUX_WORKFLOW = REPO / ".github" / "workflows" / "pr-linux-g1-g5.yml"
SHA_A = "a" * 40
SHA_B = "b" * 40
TOKEN = "talaria-" + "c" * 32
OTHER_TOKEN = "talaria-" + "d" * 32
TITLE = f"Talaria Tier B: {TOKEN}"
OTHER_TITLE = f"Talaria Tier B: {OTHER_TOKEN}"
SNAPSHOT_A = (
    "TIER B SNAPSHOT PASS: ios=101/failure watchos=102/success "
    "archive=103/failure conclusion=failure digest=" + "1" * 64
)
SNAPSHOT_B = SNAPSHOT_A[:-64] + "2" * 64


class ShellClassifierTests(unittest.TestCase):
    maxDiff = None

    def classify(self, function: str, *arguments: str) -> tuple[str, str, str, str]:
        script = r"""
set -eu
source "$1"
function_name="$2"
shift 2
"$function_name" "$@"
printf '%s\n%s\n%s\n%s\n' \
    "$TALARIA_CLASS_STATUS" \
    "$TALARIA_CLASS_DETAIL" \
    "$TALARIA_CLASS_VALUE" \
    "$TALARIA_CLASS_VALUE2"
"""
        result = subprocess.run(
            ["bash", "-c", script, "talaria-classifier-test", str(HELPERS), function, *arguments],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 4, result.stdout + result.stderr)
        return lines[0], lines[1], lines[2], lines[3]


class G2EvidenceBindingTests(unittest.TestCase):
    def g2_source(self) -> str:
        script = GAUNTLET.read_text(encoding="utf-8")
        return script.split("\ng2() {", maxsplit=1)[1].split(
            "# ---- G3", maxsplit=1
        )[0]

    def test_swiftpm_and_xctest_inventories_are_both_captured(self) -> None:
        g2 = self.g2_source()
        self.assertIn("--enable-xctest", g2)
        self.assertIn("--enable-swift-testing", g2)
        self.assertIn("--build-tests", g2)
        self.assertIn("--enable-code-coverage", g2)
        self.assertIn("list \\", g2)
        self.assertIn("--dump-tests-json", g2)
        self.assertIn("--disable-swift-testing", g2)

    def test_execution_is_bound_to_the_discovered_binary(self) -> None:
        g2 = self.g2_source()
        self.assertIn('LLVM_PROFILE_FILE="$profile_raw" swift test', g2)
        self.assertIn('"$LLVM_PROFDATA_BIN" merge -sparse', g2)
        self.assertEqual(g2.count("\n        --skip-build \\"), 2)
        self.assertIn("binary_digest_before", g2)
        self.assertIn("binary_digest_after", g2)
        self.assertIn('"$binary_digest_after" != "$binary_digest_before"', g2)

    def test_checker_receives_every_source_of_execution_evidence(self) -> None:
        g2 = self.g2_source()
        self.assertIn("scripts.test_check_swift_test_execution", g2)
        self.assertIn("scripts/check_swift_test_execution.py", g2)
        for argument in (
            "--swiftpm-list",
            "--discovery-json",
            "--execution-log",
            "--catalog-json",
            "--test-rc",
        ):
            self.assertIn(argument, g2)
        for marker in ("G2 TEST PASS: ", "G2 TEST FAIL: ", "G2 TEST BLOCKED: "):
            self.assertIn(marker, g2)


class GitHubPRModeTests(ShellClassifierTests):
    def github_entry(self, *arguments: str) -> tuple[str, str, str, str]:
        return self.classify("talaria_classify_github_pr_entry", *arguments)

    def exact_entry(self) -> list[str]:
        return [
            "true",
            "pull_request",
            "github-hosted",
            "Linux",
            "X64",
            "Phasmotic/Talaria",
            "refs/pull/7/merge",
            SHA_A,
            "0",
            SHA_A,
            "empty",
            "ubuntu",
            "24.04",
            "6.8.0-generic",
            "x86_64",
            "absent",
        ]

    def test_exact_github_pull_request_entry_passes(self) -> None:
        status, detail, _, _ = self.github_entry(*self.exact_entry())
        self.assertEqual(status, "PASS")
        self.assertIn("GitHub-hosted Ubuntu 24.04", detail)

    def test_every_github_pull_request_entry_fact_fails_closed(self) -> None:
        replacements = {
            0: "false",
            1: "workflow_dispatch",
            2: "self-hosted",
            3: "macOS",
            4: "ARM64",
            5: "Phasmotic/talaria",
            6: "refs/heads/main",
            7: "not-a-sha",
            8: "1",
            9: SHA_B,
            10: "nonempty",
            11: "debian",
            12: "22.04",
            13: "5.15.0-microsoft-standard-WSL2",
            14: "aarch64",
            15: "present",
        }
        for index, replacement in replacements.items():
            with self.subTest(index=index, replacement=replacement):
                arguments = self.exact_entry()
                arguments[index] = replacement
                status, _, _, _ = self.github_entry(*arguments)
                self.assertEqual(status, "BLOCKED")

    def test_complete_history_classifier_accepts_only_exact_false_evidence(self) -> None:
        status, detail, _, _ = self.classify(
            "talaria_classify_complete_git_history", "0", "false", "empty"
        )
        self.assertEqual(status, "PASS")
        self.assertIn("non-shallow", detail)

        cases = (
            ("1", "false", "empty"),
            ("0", "true", "empty"),
            ("0", "", "empty"),
            ("0", "false", "nonempty"),
            ("bad", "false", "empty"),
            ("0", "false", "unknown"),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                status, _, _, _ = self.classify(
                    "talaria_classify_complete_git_history", *arguments
                )
                self.assertEqual(status, "BLOCKED")

    def test_g1_g5_green_requires_exact_ordered_pass_inventory(self) -> None:
        passing = tuple(f"G{number}|PASS|evidence" for number in range(1, 6))
        status, detail, _, _ = self.classify(
            "talaria_classify_g1_g5_inventory", *passing
        )
        self.assertEqual(status, "PASS")
        self.assertIn("exact G1-G5", detail)

        cases = (
            passing[:-1],
            passing + (passing[-1],),
            (passing[1], passing[0], *passing[2:]),
            (*passing[:2], "G3|FAIL|finding", *passing[3:]),
            (*passing[:2], "G3|BLOCKED|missing", *passing[3:]),
            (*passing[:2], "G3|DEFER->B|later", *passing[3:]),
            (*passing[:2], "G9|PASS|unknown", *passing[3:]),
            (*passing[:2], "G3|PASS|", *passing[3:]),
        )
        for rows in cases:
            with self.subTest(rows=rows):
                status, _, _, _ = self.classify(
                    "talaria_classify_g1_g5_inventory", *rows
                )
                self.assertEqual(status, "BLOCKED")

    def test_github_mode_is_distinct_and_cannot_run_g6_or_tier_b(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        preflight = script.split("STATUS_HELPERS=", maxsplit=1)[0]
        main = script.split("# ---- main", maxsplit=1)[1]

        self.assertIn("--github-pr) RUN_GITHUB_PR=1", preflight)
        self.assertIn('if [ "$RUN_GITHUB_PR" -eq 1 ]', script)
        self.assertIn('if [ "$RUN_GITHUB_PR" -eq 0 ]; then\n    g6\nfi', main)
        self.assertIn('talaria_classify_g1_g5_inventory "${RESULTS[@]}"', main)
        self.assertIn('echo "G1–G5 GREEN"', main)
        github_result_branch = main.split(
            'if [ "$RUN_GITHUB_PR" -eq 1 ]; then', maxsplit=1
        )[1].split('elif [ "$FAILED" -eq 0 ]; then', maxsplit=1)[0]
        self.assertNotIn("TIER A GREEN", github_result_branch)
        self.assertNotIn("GAUNTLET GREEN", github_result_branch)

    def test_common_preflight_pins_swift_target_and_g5_proves_full_history(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        self.assertIn('EXPECTED_SWIFT_TARGET="x86_64-unknown-"linux-gnu', script)
        self.assertIn('SWIFT_TARGET_TRIPLE" = "$EXPECTED_SWIFT_TARGET"', script)
        g5 = script.split("g5() {", maxsplit=1)[1].split("# ---- G6", maxsplit=1)[0]
        self.assertIn("git rev-parse --is-shallow-repository", g5)
        self.assertIn("talaria_classify_complete_git_history", g5)


class GitHubPRCoreWorkflowTests(unittest.TestCase):
    def source(self) -> str:
        return LINUX_CORE_WORKFLOW.read_text(encoding="utf-8")

    def test_core_is_reusable_read_only_and_fixed_to_hosted_ubuntu(self) -> None:
        workflow = self.source()
        trigger = workflow.split("permissions:", maxsplit=1)[0]
        self.assertIn("on:\n  workflow_call:", trigger)
        self.assertNotIn("pull_request_target", trigger)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertEqual(workflow.count("contents: read"), 2)
        self.assertNotIn("id-token:", workflow)
        self.assertNotIn("secrets:", workflow)

    def test_core_checkout_is_immutable_credential_free_and_complete(self) -> None:
        workflow = self.source()
        self.assertIn(
            "actions/checkout@08c6903cd8c0fde910a37f88322edcfb5dd907a8",
            workflow,
        )
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn("actions/cache", workflow)
        self.assertNotIn(".build/", workflow)
        self.assertNotIn("continue-on-error", workflow)

    def test_pinned_core_proves_source_and_toolchain_before_candidate_code(self) -> None:
        workflow = self.source()
        proof_index = workflow.index("Prove hosted source, complete history, and exact toolchain")
        candidate_index = workflow.index("bash scripts/gauntlet.sh --github-pr")
        self.assertLess(proof_index, candidate_index)
        for evidence in (
            '"$GITHUB_EVENT_NAME" = "pull_request"',
            '"$RUNNER_ENVIRONMENT" = "github-hosted"',
            '"$GITHUB_REPOSITORY" = "Phasmotic/Talaria"',
            '"$head_sha" = "$GITHUB_SHA"',
            '"$shallow_state" = "false"',
            "Swift version 6.3.3 (swift-6.3.3-RELEASE)",
            "x86_64-unknown-linux-gnu",
            "llvm-cov",
            "llvm-profdata",
            "libsourcekitdInProc.so",
        ):
            self.assertIn(evidence, workflow)

    def test_core_accepts_only_exact_subset_sentinel(self) -> None:
        workflow = self.source()
        mkdir_index = workflow.index("mkdir -p .gauntlet")
        gauntlet_index = workflow.index("bash scripts/gauntlet.sh --github-pr")
        self.assertLess(mkdir_index, gauntlet_index)
        self.assertIn("grep -Fx 'G1–G5 GREEN'", workflow)
        self.assertIn("TIER A GREEN|GAUNTLET GREEN", workflow)


class GitHubPRCallerWorkflowTests(unittest.TestCase):
    def source(self) -> str:
        return PR_LINUX_WORKFLOW.read_text(encoding="utf-8")

    def core_pin(self) -> str:
        match = re.search(
            r"^\s+uses: Phasmotic/Talaria/\.github/workflows/"
            r"linux-g1-g5-core\.yml@([0-9a-f]{40})$",
            self.source(),
            re.MULTILINE,
        )
        self.assertIsNotNone(match)
        return match.group(1)

    def test_caller_is_pull_request_only_and_has_no_silent_reduction(self) -> None:
        workflow = self.source()
        trigger = workflow.split("permissions:", maxsplit=1)[0]
        self.assertIn("on:\n  pull_request:", trigger)
        self.assertIn("branches: [main]", trigger)
        self.assertIn("opened", trigger)
        self.assertIn("synchronize", trigger)
        self.assertIn("reopened", trigger)
        self.assertIn("ready_for_review", trigger)
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("workflow_run", trigger)
        self.assertNotIn("workflow_dispatch", trigger)
        self.assertNotIn("paths:", trigger)

    def test_caller_is_advisory_read_only_uncached_and_secretless(self) -> None:
        workflow = self.source()
        self.assertIn("name: Advisory G1–G5", workflow)
        self.assertEqual(workflow.count("contents: read"), 2)
        self.assertNotIn("id-token:", workflow)
        self.assertNotIn("secrets:", workflow)
        self.assertNotIn("actions/cache", workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertNotIn("runs-on:", workflow)
        self.assertNotIn("steps:", workflow)
        self.assertIn(
            "group: pr-linux-g1-g5-${{ github.event.pull_request.number }}", workflow
        )
        self.assertIn("cancel-in-progress: true", workflow)

    def test_caller_uses_one_literal_full_sha_core_reference(self) -> None:
        workflow = self.source()
        pin = self.core_pin()
        self.assertEqual(workflow.count("uses:"), 1)
        uses_line = next(line for line in workflow.splitlines() if "uses:" in line)
        self.assertNotIn("@main", uses_line)
        self.assertNotIn("${{", uses_line)

        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", pin, "HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertEqual(ancestor.returncode, 0, ancestor.stdout + ancestor.stderr)
        self.assertEqual(ancestor.stdout, "")
        self.assertEqual(ancestor.stderr, "")

        pinned_core = subprocess.run(
            ["git", "show", f"{pin}:.github/workflows/linux-g1-g5-core.yml"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            pinned_core.returncode, 0, pinned_core.stdout + pinned_core.stderr
        )
        self.assertEqual(pinned_core.stderr, "")
        self.assertEqual(
            pinned_core.stdout,
            LINUX_CORE_WORKFLOW.read_text(encoding="utf-8"),
        )


class G4ClassificationTests(ShellClassifierTests):
    def test_gauntlet_uses_machine_readable_swiftlint_evidence(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        g4 = script.split("g4() {", maxsplit=1)[1].split("# ---- G5", maxsplit=1)[0]
        self.assertIn("--reporter json", g4)
        self.assertIn("check_swiftlint_report.py", g4)
        self.assertIn("talaria_classify_g4", g4)
        self.assertIn(
            '"$swiftformat_bin" --lint --verbose Packages/HermesKit App Tests',
            g4,
        )
        self.assertNotIn('"$swiftformat_bin" --lint --quiet', g4)
        self.assertNotIn('"$swiftlint_bin" lint ', g4)

    def test_clean_results_with_valid_empty_report_pass(self) -> None:
        status, detail, _, _ = self.classify(
            "talaria_classify_g4", "0", "0", "0", "0", "empty"
        )
        self.assertEqual(status, "PASS")
        self.assertIn("zero violations", detail)

    def test_swiftformat_findings_fail(self) -> None:
        status, detail, _, _ = self.classify(
            "talaria_classify_g4", "1", "0", "0", "0", "empty"
        )
        self.assertEqual(status, "FAIL")
        self.assertIn("lint violations", detail)

    def test_swiftformat_operational_statuses_block(self) -> None:
        for format_rc in ("2", "70", "139"):
            with self.subTest(format_rc=format_rc):
                status, detail, _, _ = self.classify(
                    "talaria_classify_g4", format_rc, "0", "0", "0", "empty"
                )
                self.assertEqual(status, "BLOCKED")
                self.assertIn("SwiftFormat execution failed", detail)

    def test_swiftlint_findings_require_valid_nonempty_evidence(self) -> None:
        status, detail, _, _ = self.classify(
            "talaria_classify_g4", "0", "2", "0", "2", "empty"
        )
        self.assertEqual(status, "FAIL")
        self.assertIn("lint violations", detail)

    def test_swiftlint_status_evidence_disagreement_blocks(self) -> None:
        for lint_rc, count in (("0", "1"), ("2", "0")):
            with self.subTest(lint_rc=lint_rc, count=count):
                status, detail, _, _ = self.classify(
                    "talaria_classify_g4", "0", lint_rc, "0", count, "empty"
                )
                self.assertEqual(status, "BLOCKED")
                self.assertIn("disagreed", detail)

    def test_swiftlint_operational_or_invalid_evidence_blocks(self) -> None:
        cases = (("1", "0", "1"), ("70", "0", "0"), ("134", "0", "0"), ("139", "0", "0"), ("2", "2", "1"))
        for lint_rc, report_rc, count in cases:
            with self.subTest(lint_rc=lint_rc, report_rc=report_rc, count=count):
                status, _, _, _ = self.classify(
                    "talaria_classify_g4", "0", lint_rc, report_rc, count, "empty"
                )
                self.assertEqual(status, "BLOCKED")

    def test_swiftlint_stderr_blocks_otherwise_decisive_results(self) -> None:
        for lint_rc, count in (("0", "0"), ("2", "2")):
            with self.subTest(lint_rc=lint_rc, count=count):
                status, detail, _, _ = self.classify(
                    "talaria_classify_g4", "0", lint_rc, "0", count, "nonempty"
                )
                self.assertEqual(status, "BLOCKED")
                self.assertIn("operational stderr", detail)

    def test_invalid_inputs_block(self) -> None:
        for arguments in (
            ("bad", "0", "0", "0", "empty"),
            ("0", "0", "0", "NaN", "empty"),
            ("0", "0", "0", "0", "unknown"),
        ):
            with self.subTest(arguments=arguments):
                status, detail, _, _ = self.classify("talaria_classify_g4", *arguments)
                self.assertEqual(status, "BLOCKED")
                self.assertIn("invalid", detail)


class G5ClassificationTests(ShellClassifierTests):
    def test_gauntlet_assigns_reserved_findings_status_to_both_scans(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        self.assertEqual(
            script.count('--exit-code "$TALARIA_GITLEAKS_FINDINGS_RC"'),
            2,
        )
        self.assertIn('"$gl_bin" git --redact -v', script)
        self.assertIn('"$gl_bin" dir --redact -v', script)
        self.assertNotIn('"$gl_bin" detect', script)

    def test_clean_scans_pass(self) -> None:
        status, detail, _, _ = self.classify("talaria_classify_g5", "0", "0", "0")
        self.assertEqual(status, "PASS")
        self.assertIn("no secrets", detail)

    def test_history_finding_fails(self) -> None:
        status, detail, _, _ = self.classify("talaria_classify_g5", "42", "0", "0")
        self.assertEqual(status, "FAIL")
        self.assertIn("secret findings", detail)

    def test_worktree_finding_fails(self) -> None:
        status, detail, _, _ = self.classify("talaria_classify_g5", "0", "42", "0")
        self.assertEqual(status, "FAIL")
        self.assertIn("secret findings", detail)

    def test_history_execution_error_blocks(self) -> None:
        status, detail, _, _ = self.classify("talaria_classify_g5", "1", "0", "0")
        self.assertEqual(status, "BLOCKED")
        self.assertIn("execution failed", detail)

    def test_worktree_execution_error_blocks(self) -> None:
        status, detail, _, _ = self.classify("talaria_classify_g5", "0", "1", "0")
        self.assertEqual(status, "BLOCKED")
        self.assertIn("execution failed", detail)

    def test_gitleaks_exit_greater_than_one_blocks_unless_reserved_for_findings(self) -> None:
        for history_rc, worktree_rc in (("2", "0"), ("0", "17"), ("42", "2")):
            with self.subTest(history_rc=history_rc, worktree_rc=worktree_rc):
                status, detail, _, _ = self.classify(
                    "talaria_classify_g5", history_rc, worktree_rc, "0"
                )
                self.assertEqual(status, "BLOCKED")
                self.assertIn("execution failed", detail)

    def test_literal_finding_fails(self) -> None:
        status, detail, _, _ = self.classify("talaria_classify_g5", "0", "0", "1")
        self.assertEqual(status, "FAIL")
        self.assertIn("hardcoded host", detail)

    def test_literal_execution_error_blocks_before_findings(self) -> None:
        status, detail, _, _ = self.classify("talaria_classify_g5", "42", "0", "2")
        self.assertEqual(status, "BLOCKED")
        self.assertIn("indeterminate", detail)

    def test_invalid_exit_status_blocks(self) -> None:
        status, detail, _, _ = self.classify("talaria_classify_g5", "not-a-status", "0", "0")
        self.assertEqual(status, "BLOCKED")
        self.assertIn("invalid", detail)

    def test_wrong_arity_blocks(self) -> None:
        status, detail, _, _ = self.classify("talaria_classify_g5", "0", "0")
        self.assertEqual(status, "BLOCKED")
        self.assertIn("invalid", detail)


class G5EvidenceAssemblyTests(unittest.TestCase):
    def assemble(self, missing: str | None = None, output_parent_exists: bool = True):
        with tempfile.TemporaryDirectory(prefix="talaria-g5-evidence-") as temporary:
            root = Path(temporary)
            inputs = {}
            for name in ("canary", "history", "worktree", "literals"):
                path = root / f"{name}.log"
                path.write_text(f"{name}-evidence\n", encoding="utf-8")
                inputs[name] = path
            if missing is not None:
                inputs[missing].unlink()

            output = root / ("evidence" if output_parent_exists else "missing") / "g5.log"
            if output_parent_exists:
                output.parent.mkdir()
            script = r"""
set -u
source "$1"
talaria_assemble_g5_evidence \
    "$2" "8.30.1" "$3" "42" "$4" "$5" "$6" "$7"
"""
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    script,
                    "talaria-g5-evidence-test",
                    str(HELPERS),
                    str(output),
                    "a" * 64,
                    str(inputs["canary"]),
                    str(inputs["history"]),
                    str(inputs["worktree"]),
                    str(inputs["literals"]),
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            content = output.read_text(encoding="utf-8") if output.is_file() else ""
            return result, content

    def test_complete_evidence_is_assembled(self) -> None:
        result, content = self.assemble()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ("canary", "history", "worktree", "literals"):
            self.assertIn(f"{name}-evidence", content)

    def test_every_missing_input_fails_closed(self) -> None:
        for missing in ("canary", "history", "worktree", "literals"):
            with self.subTest(missing=missing):
                result, content = self.assemble(missing=missing)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(content, "")

    def test_unwritable_destination_shape_fails_closed(self) -> None:
        result, content = self.assemble(output_parent_exists=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(content, "")

    def test_gauntlet_uses_fail_closed_evidence_helper(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        g5 = script.split("g5() {", maxsplit=1)[1].split("# ---- G6", maxsplit=1)[0]
        self.assertIn("talaria_assemble_g5_evidence", g5)


class TierBSourceBindingTests(ShellClassifierTests):
    def tier_b_function(self, name: str, next_name: str) -> str:
        source = GAUNTLET.read_text(encoding="utf-8")
        return f"{name}() {{" + source.split(
            f"{name}() {{", maxsplit=1
        )[1].split(f"{next_name}()", maxsplit=1)[0]

    def test_cli_family_classifier_rejects_crossed_or_ambiguous_clients(self) -> None:
        classifier = self.tier_b_function(
            "talaria_classify_tier_b_client_families",
            "verify_tier_b_cli_version",
        )
        script = classifier + r'''
talaria_classify_tier_b_client_families "$1" "$2"
printf '%s\n%s\n' "$TIER_B_CLIENT_STATUS" "$TIER_B_CLIENT_DETAIL"
'''
        cases = (
            ("/usr/bin/gh", "/mnt/c/Program Files/GitHub CLI/gh.exe", "PASS"),
            ("", "/mnt/c/Program Files/GitHub CLI/gh.exe", "BLOCKED"),
            ("gh", "/mnt/c/Program Files/GitHub CLI/gh.exe", "BLOCKED"),
            ("/mnt/c/gh.exe", "/mnt/c/Program Files/GitHub CLI/gh.exe", "BLOCKED"),
            ("/usr/bin/gh.exe", "/mnt/c/Program Files/GitHub CLI/gh.exe", "BLOCKED"),
            ("/usr/bin/gh", "", "BLOCKED"),
            ("/usr/bin/gh", "/usr/bin/gh", "BLOCKED"),
            ("/usr/bin/gh", "/mnt/c/gh", "BLOCKED"),
        )
        for run_bin, log_bin, expected in cases:
            with self.subTest(run_bin=run_bin, log_bin=log_bin):
                result = subprocess.run(
                    ["bash", "-c", script, "tier-b-client-family-test", run_bin, log_bin],
                    cwd=REPO,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.splitlines()[0], expected)

    def test_cli_version_probe_fails_closed_and_accepts_windows_crlf(self) -> None:
        verifier = self.tier_b_function(
            "verify_tier_b_cli_version",
            "tier_b_run_gh",
        )
        artifact_parent = REPO / ".gauntlet"
        artifact_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="tier b cli version test ", dir=artifact_parent
        ) as temporary:
            artifact = Path(temporary)
            executable = artifact / "mock gh.exe"
            executable.write_text(
                r'''#!/usr/bin/env bash
case "${TALARIA_TEST_MODE:-valid}" in
    valid) printf '%s\n' "$TALARIA_TEST_LINE" ;;
    crlf) printf '%s\r\n' "$TALARIA_TEST_LINE" ;;
    empty) : ;;
    stderr) printf '%s\n' "$TALARIA_TEST_LINE"; printf '%s\n' warning >&2 ;;
    wrong) printf '%s\n' 'gh version 0.0.0' ;;
    nonzero) printf '%s\n' "$TALARIA_TEST_LINE"; exit 7 ;;
esac
''',
                encoding="utf-8",
            )
            executable.chmod(0o755)
            expected_line = "gh version 2.88.1 (2026-03-12)"
            script = verifier + r'''
ART="$1"
verify_tier_b_cli_version "$2" "$3" log
'''
            for mode, expected_rc in (
                ("valid", 0),
                ("crlf", 0),
                ("empty", 1),
                ("stderr", 1),
                ("wrong", 1),
                ("nonzero", 1),
            ):
                with self.subTest(mode=mode):
                    result = subprocess.run(
                        ["bash", "-c", script, "tier-b-version-test", str(artifact), str(executable), expected_line],
                        cwd=REPO,
                        capture_output=True,
                        text=True,
                        env={**os.environ, "TALARIA_TEST_MODE": mode, "TALARIA_TEST_LINE": expected_line},
                    )
                    self.assertEqual(result.returncode, expected_rc, result.stdout + result.stderr)
            non_executable_path = artifact / "not an executable"
            non_executable_path.mkdir()
            not_executable = subprocess.run(
                ["bash", "-c", script, "tier-b-version-test", str(artifact), str(non_executable_path), expected_line],
                cwd=REPO,
                capture_output=True,
                text=True,
                env={**os.environ, "TALARIA_TEST_MODE": "valid", "TALARIA_TEST_LINE": expected_line},
            )
            self.assertNotEqual(not_executable.returncode, 0)
            missing = subprocess.run(
                ["bash", "-c", script, "tier-b-version-test", str(artifact), str(artifact / "missing gh.exe"), expected_line],
                cwd=REPO,
                capture_output=True,
                text=True,
                env={**os.environ, "TALARIA_TEST_MODE": "valid", "TALARIA_TEST_LINE": expected_line},
            )
            self.assertNotEqual(missing.returncode, 0)

    def test_cli_wrappers_route_to_distinct_clients_without_fallback(self) -> None:
        wrappers = self.tier_b_function("tier_b_run_gh", "tier_b_log_gh")
        wrappers += self.tier_b_function("tier_b_log_gh", "cleanup_g2_temp")
        script = wrappers + r'''
native_client() { printf 'native|%s\n' "$*" >>"$1"; }
windows_client() { printf 'windows|%s\n' "$*" >>"$1"; }
record_path="$1"
GH_RUN_BIN=native_client
GH_LOG_BIN=windows_client
tier_b_run_gh "$record_path" workflow run tier-b.yml
tier_b_run_gh "$record_path" run list
tier_b_run_gh "$record_path" run watch 123
tier_b_run_gh "$record_path" run view 123 --json jobs
tier_b_log_gh "$record_path" run view --job 456 --log
'''
        artifact_parent = REPO / ".gauntlet"
        artifact_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="tierb-client-routing-test-", dir=artifact_parent
        ) as temporary:
            record = Path(temporary) / "routes.log"
            result = subprocess.run(
                ["bash", "-c", script, "tier-b-client-routing-test", str(record)],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                record.read_text(encoding="utf-8").splitlines(),
                [
                    f"native|{record} workflow run tier-b.yml",
                    f"native|{record} run list",
                    f"native|{record} run watch 123",
                    f"native|{record} run view 123 --json jobs",
                    f"windows|{record} run view --job 456 --log",
                ],
            )

    def resolve_g6(self, *rows: str) -> subprocess.CompletedProcess[str]:
        source = GAUNTLET.read_text(encoding="utf-8")
        resolver = "resolve_tier_b_g6() {" + source.split(
            "resolve_tier_b_g6() {", maxsplit=1
        )[1].split("section()", maxsplit=1)[0]
        script = resolver + r'''
run_id="$1"
shift
RESULTS=("$@")
if resolve_tier_b_g6 "$run_id"; then
    result=0
else
    result=$?
fi
printf '%s\n' "${RESULTS[@]}"
exit "$result"
'''
        return subprocess.run(
            ["bash", "-c", script, "talaria-g6-resolver-test", "12345", *rows],
            cwd=REPO,
            capture_output=True,
            text=True,
        )

    def fetch_job_log(
        self,
        job_key: str,
        *,
        job_id: str = "67890",
        gh_rc: int = 0,
        emit_output: bool = True,
        emit_stderr: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], str, str, str]:
        source = GAUNTLET.read_text(encoding="utf-8")
        fetcher = "fetch_tier_b_job_log() {" + source.split(
            "fetch_tier_b_job_log() {", maxsplit=1
        )[1].split("section()", maxsplit=1)[0]
        artifact_parent = REPO / ".gauntlet"
        artifact_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="tierb-fetch-test-", dir=artifact_parent
        ) as temporary:
            artifact = Path(temporary)
            script = fetcher + r'''
ART="$1"
TIER_B_REPOSITORY="Phasmotic/Talaria"
GH_LOG_BIN=gh
tier_b_log_gh() { "$GH_LOG_BIN" "$@"; }
MOCK_JOB_ID="$2"
MOCK_JOB_KEY="$3"
MOCK_RC="$4"
MOCK_OUTPUT="$5"
MOCK_STDERR="$6"
gh() {
    printf '%s\n' "$*" >"$ART/gh-arguments"
    if [ "$MOCK_OUTPUT" = "1" ]; then
        printf '%s\n' 'completed per-job runtime log'
    fi
    if [ "$MOCK_STDERR" = "1" ]; then
        printf '%s\n' 'unexpected diagnostic' >&2
    fi
    return "$MOCK_RC"
}
printf '%s\n' 'stale output' >"$ART/tierb-$MOCK_JOB_KEY.log"
printf '%s\n' 'stale stderr' >"$ART/tierb-$MOCK_JOB_KEY.log.stderr"
fetch_tier_b_job_log "$MOCK_JOB_ID" "$MOCK_JOB_KEY"
'''
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    script,
                    "talaria-tierb-fetch-test",
                    str(artifact),
                    job_id,
                    job_key,
                    str(gh_rc),
                    "1" if emit_output else "0",
                    "1" if emit_stderr else "0",
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            arguments_path = artifact / "gh-arguments"
            output_path = artifact / f"tierb-{job_key}.log"
            stderr_path = artifact / f"tierb-{job_key}.log.stderr"
            arguments = (
                arguments_path.read_text(encoding="utf-8").strip()
                if arguments_path.is_file()
                else ""
            )
            output = (
                output_path.read_text(encoding="utf-8")
                if output_path.is_file()
                else ""
            )
            error = (
                stderr_path.read_text(encoding="utf-8")
                if stderr_path.is_file()
                else ""
            )
            return result, arguments, output, error

    def capture_snapshot(
        self,
        markers: tuple[str, ...],
        *,
        expected_marker: str = "",
        snapshot_key: str = "post",
    ) -> tuple[subprocess.CompletedProcess[str], str, str]:
        source = GAUNTLET.read_text(encoding="utf-8")
        capture = "capture_tier_b_snapshot() {" + source.split(
            "capture_tier_b_snapshot() {", maxsplit=1
        )[1].split("section()", maxsplit=1)[0]
        artifact_parent = REPO / ".gauntlet"
        artifact_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="tierb-snapshot-capture-test-", dir=artifact_parent
        ) as temporary:
            artifact = Path(temporary)
            for index, marker in enumerate(markers, start=1):
                (artifact / f"marker-{index}").write_text(marker, encoding="utf-8")
            script = capture + r'''
ART="$1"
TIER_B_REPOSITORY="Phasmotic/Talaria"
GH_RUN_BIN=gh
tier_b_run_gh() { "$GH_RUN_BIN" "$@"; }
EXPECTED_MARKER="$2"
SNAPSHOT_KEY="$3"
gh() {
    count=0
    if [ -f "$ART/call-count" ]; then
        read -r count <"$ART/call-count"
    fi
    count=$((count + 1))
    printf '%s\n' "$count" >"$ART/call-count"
    printf '%s\n' '{}'
}
python3() {
    read -r count <"$ART/call-count"
    marker_path="$ART/marker-$count"
    if [ ! -f "$marker_path" ]; then
        printf '%s\n' 'TIER B SNAPSHOT BLOCKED: evidence is missing, malformed, unavailable, or contradictory' >&2
        return 2
    fi
    marker="$(cat "$marker_path")"
    if [ "$marker" = "BLOCKED" ]; then
        printf '%s\n' 'TIER B SNAPSHOT BLOCKED: evidence is missing, malformed, unavailable, or contradictory' >&2
        return 2
    fi
    printf '%s\n' "$marker"
}
sleep() { :; }
TIER_B_SNAPSHOT_MARKER="stale-marker"
TIER_B_IOS_JOB_ID="999"
capture_tier_b_snapshot \
    12345 \
    aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    "Talaria Tier B: talaria-cccccccccccccccccccccccccccccccc" \
    1 \
    "$EXPECTED_MARKER" \
    "$SNAPSHOT_KEY"
capture_rc=$?
call_count="$(cat "$ART/call-count" 2>/dev/null || true)"
printf '%s\n%s\n%s\n' \
    "$call_count" "$TIER_B_SNAPSHOT_MARKER" "$TIER_B_IOS_JOB_ID"
exit "$capture_rc"
'''
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    script,
                    "talaria-tierb-snapshot-capture-test",
                    str(artifact),
                    expected_marker,
                    snapshot_key,
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            lines = result.stdout.splitlines()
            self.assertEqual(len(lines), 3, result.stdout + result.stderr)
            return result, lines[0], "|".join(lines[1:])

    def test_gauntlet_rejects_extra_or_unknown_arguments(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        preflight = script.split("STATUS_HELPERS=", maxsplit=1)[0]
        self.assertIn('if [ "$#" -gt 1 ]', preflight)
        self.assertIn('*) blocked_preflight "unknown gauntlet argument: $1"', preflight)

    def test_local_g6_defer_cannot_print_tier_a_green(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        g6 = script.split("\ng6() {", maxsplit=1)[1].split(
            "# ---- Tier B", maxsplit=1
        )[0]

        self.assertIn('if [ "$RUN_TIER_B" -eq 1 ]', g6)
        self.assertIn('record "G6*" "DEFER->B"', g6)
        self.assertIn('record G6 BLOCKED "no verified Linux XcodeGen artifact', g6)
        self.assertNotIn("command -v xcodegen", g6)

    def test_tier_b_success_resolves_exactly_one_deferred_g6(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        resolver = script.split("resolve_tier_b_g6() {", maxsplit=1)[1].split(
            "section()", maxsplit=1
        )[0]
        tier_b = script.split("tier_b() {", maxsplit=1)[1]

        self.assertIn('[ "$deferred_count" -ne 1 ]', resolver)
        self.assertIn("if resolve_tier_b_g6", tier_b)
        self.assertIn("could not resolve exactly one deferred G6 row", tier_b)

        deferred = self.resolve_g6("G6*|DEFER->B|awaiting Tier B")
        self.assertEqual(deferred.returncode, 0, deferred.stdout + deferred.stderr)
        self.assertIn("G6|PASS|authoritative two-generation", deferred.stdout)

    def test_tier_b_g6_resolution_rejects_missing_or_ambiguous_local_state(self) -> None:
        cases = (
            (),
            ("G6|BLOCKED|tool missing",),
            ("G6|PASS|untrusted local executable",),
            ("G6*|DEFER->B|one", "G6*|DEFER->B|two"),
            ("G6*|DEFER->B|deferred", "G6*|PASS|wrong status"),
        )
        for rows in cases:
            with self.subTest(rows=rows):
                result = self.resolve_g6(*rows)
                self.assertNotEqual(result.returncode, 0)

    def test_every_tier_b_job_executes_g6_without_a_skip_condition(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step_names = (
            "Verify XcodeGen determinism and generate project (G6, authoritative)",
            "Verify XcodeGen determinism and generate project (G6)",
        )

        self.assertEqual(workflow.count(step_names[0]), 1)
        self.assertEqual(workflow.count(step_names[1]), 2)
        self.assertEqual(
            workflow.count("python3 -B scripts/check_xcodegen_determinism.py"), 3
        )
        for name in step_names:
            start = 0
            while True:
                index = workflow.find(f"- name: {name}", start)
                if index == -1:
                    break
                next_step = workflow.find("\n      - name:", index + 1)
                section = workflow[index : next_step if next_step != -1 else None]
                self.assertNotIn("\n        if:", section)
                start = index + 1

    def test_gauntlet_requests_and_classifies_multiple_run_candidates(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        tier_b = script.split("tier_b() {", maxsplit=1)[1]
        self.assertIn("--limit 100", tier_b)
        self.assertIn("--json databaseId,headSha,displayTitle", tier_b)
        self.assertIn("--jq '.[] |", tier_b)
        self.assertIn("talaria_classify_tier_b_run_selection", tier_b)

    def test_workflow_requires_and_displays_dispatch_correlation(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('run-name: "Talaria Tier B: ${{ inputs.correlation_token }}"', workflow)
        self.assertIn("correlation_token:", workflow)
        self.assertIn("required: true", workflow)
        self.assertIn("type: string", workflow)

        script = GAUNTLET.read_text(encoding="utf-8")
        tier_b = script.split("tier_b() {", maxsplit=1)[1]
        self.assertIn('--field "correlation_token=$correlation_token"', tier_b)
        self.assertIn("displayTitle", tier_b)

    def test_every_tier_b_job_emits_one_fail_closed_status_record(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(
            workflow.count(f"- name: {snapshot_checker.REPORTER_STEP}"), 3
        )
        for expected_name in snapshot_checker.EXPECTED_JOBS.values():
            self.assertEqual(workflow.count(f"name: {expected_name}"), 1)
        self.assertEqual(
            workflow.count("TALARIA_STATUS_CORRELATION: ${{ inputs.correlation_token }}"),
            3,
        )
        self.assertEqual(workflow.count("TALARIA_JOB_STATUS: ${{ job.status }}"), 3)
        self.assertEqual(
            workflow.count("STATUS_PREFIX='TALARIA_'\"TIER_B_JOB_STATUS\""),
            3,
        )
        self.assertNotIn("TALARIA_TIER_B_JOB_STATUS|", workflow)
        for job in ("ios", "watchos", "archive"):
            self.assertEqual(
                workflow.count(
                    f"printf '%s|%s|{job}|%s\\n' \"$STATUS_PREFIX\" "
                    '"$TALARIA_STATUS_CORRELATION" "$STATUS_VALUE"'
                ),
                1,
            )

        for section in workflow.split("- name: Emit Tier B job status")[1:]:
            step = section.split("\n      - name:", maxsplit=1)[0]
            self.assertIn("if: ${{ always() }}", step)
            self.assertIn('success) STATUS_VALUE="PASS"', step)
            self.assertIn('failure|cancelled) STATUS_VALUE="BLOCKED"', step)
            self.assertNotIn('STATUS_VALUE="FAIL"', step)

    def test_status_checker_failure_modes_run_before_dispatch_and_in_tier_b(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        tier_b = script.split("tier_b() {", maxsplit=1)[1]
        self.assertIn("scripts.test_check_tier_b_run_snapshot", tier_b)
        self.assertIn("scripts.test_check_tier_b_status_log", tier_b)
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("scripts.test_check_tier_b_run_snapshot", workflow)
        self.assertIn("scripts.test_check_tier_b_status_log", workflow)

    def test_every_gh_operation_is_bound_to_the_verified_repository(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        tier_b = script.split("tier_b() {", maxsplit=1)[1]
        self.assertEqual(script.count('--repo "$TIER_B_REPOSITORY"'), 5)
        self.assertIn("talaria_classify_tier_b_repository", tier_b)

    def test_launcher_selects_windows_gh_only_for_job_logs(self) -> None:
        launcher = LAUNCHER.read_text(encoding="utf-8")
        helpers = LAUNCHER_HELPERS.read_text(encoding="utf-8")
        launcher_test = LAUNCHER_TEST.read_text(encoding="utf-8")
        script = GAUNTLET.read_text(encoding="utf-8")
        self.assertIn(
            "Get-Command gh.exe -CommandType Application -ErrorAction Stop",
            launcher,
        )
        self.assertIn('"gh version 2.88.1 (2026-03-12)"', launcher)
        self.assertIn("TALARIA_GH_LOG_BIN = $talariaGhWindows", launcher)
        self.assertIn('"TALARIA_GH_LOG_BIN/up"', launcher)
        self.assertIn("-Environment $talariaWslEnvironment", launcher)
        self.assertIn("test_gauntlet_launcher.ps1", launcher)
        self.assertIn("ArgumentList.Add($argument)", helpers)
        self.assertIn("Program Files/GitHub CLI/gh.exe", launcher_test)
        self.assertIn("an invalid version result was accepted", launcher_test)
        self.assertIn("an invalid or ambiguous interop path was accepted", launcher_test)
        self.assertIn(
            'EXPECTED_GH_RUN_LINE="gh version 2.45.0 '
            '(2025-07-18 Ubuntu 2.45.0-1ubuntu0.3)"',
            script,
        )
        self.assertIn('EXPECTED_GH_LOG_LINE="gh version 2.88.1 (2026-03-12)"', script)
        self.assertIn('GH_RUN_BIN="$(type -P gh 2>/dev/null || true)"', script)
        self.assertIn('GH_LOG_BIN="${TALARIA_GH_LOG_BIN:-}"', script)
        self.assertIn('/mnt/[a-zA-Z]/*/gh.exe', script)
        self.assertEqual(script.count("tier_b_run_gh run"), 3)
        self.assertEqual(script.count("tier_b_run_gh workflow"), 1)
        self.assertEqual(script.count("tier_b_log_gh run"), 1)
        self.assertNotIn("TALARIA_GH_BIN", launcher + script)

    def test_exact_snapshot_and_job_logs_are_source_and_origin_bound(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        tier_b = script.split("tier_b() {", maxsplit=1)[1]
        snapshot = script.split("capture_tier_b_snapshot() {", maxsplit=1)[1].split(
            "section()", maxsplit=1
        )[0]
        fetcher = script.split("fetch_tier_b_job_log() {", maxsplit=1)[1].split(
            "capture_tier_b_snapshot()", maxsplit=1
        )[0]
        self.assertIn('tier_b_run_gh run view "$run_id"', snapshot)
        self.assertIn('--repo "$TIER_B_REPOSITORY"', snapshot)
        self.assertIn("--attempt 1", snapshot)
        self.assertIn(
            "--json status,conclusion,databaseId,headSha,displayTitle,url,jobs",
            snapshot,
        )
        self.assertIn("scripts/check_tier_b_run_snapshot.py", snapshot)
        self.assertIn('--expected-run-id "$run_id"', snapshot)
        self.assertIn('--expected-head-sha "$head_sha"', snapshot)
        self.assertIn('--expected-title "$expected_title"', snapshot)
        self.assertIn('--expected-attempt 1', snapshot)
        self.assertIn("fetch_tier_b_job_log", tier_b)
        self.assertIn('tier_b_log_gh run view --repo "$TIER_B_REPOSITORY"', fetcher)
        self.assertIn('--job "$job_id" --log', fetcher)
        self.assertNotIn('run view "$run_id"', fetcher)
        self.assertIn("scripts/check_tier_b_status_log.py", tier_b)
        for argument in (
            "--ios-log",
            "--ios-conclusion",
            "--watchos-log",
            "--watchos-conclusion",
            "--archive-log",
            "--archive-conclusion",
        ):
            self.assertIn(argument, tier_b)
        self.assertIn('--correlation "$correlation_token"', tier_b)
        self.assertIn('--conclusion "$final_conclusion"', tier_b)
        self.assertIn('mapfile -t evidence_lines <"$ART/tierb-evidence.log"', tier_b)
        self.assertIn(
            "TIER B EVIDENCE PASS: all three jobs reported PASS and the workflow run succeeded",
            tier_b,
        )
        self.assertIn(
            "TIER B EVIDENCE BLOCKED: at least one job reported BLOCKED and the workflow run failed",
            tier_b,
        )
        self.assertNotIn("TIER B EVIDENCE FAIL", tier_b)
        self.assertNotIn("talaria_classify_tier_b_final", script)
        self.assertNotIn("talaria_classify_tier_b_job_inventory", script)

    def test_snapshot_and_per_job_logs_use_bounded_fresh_retries(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        snapshot = script.split("capture_tier_b_snapshot() {", maxsplit=1)[1].split(
            "section()", maxsplit=1
        )[0]
        fetcher = script.split("fetch_tier_b_job_log() {", maxsplit=1)[1].split(
            "capture_tier_b_snapshot()", maxsplit=1
        )[0]
        self.assertIn(': >"$output_path"', fetcher)
        self.assertIn(': >"$stderr_path"', fetcher)
        self.assertIn('[ ! -s "$stderr_path" ]', fetcher)
        self.assertIn("ios|watchos|archive", fetcher)
        self.assertIn("for snapshot_attempt in $(seq 1 12); do", snapshot)
        self.assertIn('snapshot_path="$ART/tierb-$snapshot_key-snapshot.json"', snapshot)
        self.assertIn('stderr_path="$ART/tierb-$snapshot_key-snapshot.stderr"', snapshot)
        self.assertIn(
            'verdict_path="$ART/tierb-$snapshot_key-snapshot-verdict.log"',
            snapshot,
        )
        self.assertIn("pre|post", snapshot)
        self.assertIn("digest=([0-9a-f]{64})", snapshot)
        self.assertIn('if [ -n "$expected_marker" ]', snapshot)
        self.assertIn('return 2', snapshot)
        self.assertIn('[ "$snapshot_attempt" -lt 12 ]', snapshot)
        self.assertIn("sleep 5", snapshot)

        tier_b = script.split("tier_b() {", maxsplit=1)[1]
        self.assertIn("for evidence_attempt in $(seq 1 12); do", tier_b)
        self.assertEqual(tier_b.count("fetch_tier_b_job_log"), 3)
        self.assertIn('[ "$evidence_attempt" -lt 12 ]', tier_b)
        self.assertIn("sleep 5", tier_b)
        self.assertEqual(tier_b.count("capture_tier_b_snapshot"), 2)
        self.assertIn('"$snapshot_marker"', tier_b)
        pre_index = tier_b.index('"$run_id" "$head_sha" "$expected_title" "$watch_rc" "" pre')
        fetch_index = tier_b.index('fetch_tier_b_job_log "$ios_job_id" ios')
        post_index = tier_b.index('"$snapshot_marker" post')
        self.assertLess(pre_index, fetch_index)
        self.assertLess(fetch_index, post_index)

    def test_snapshot_capture_retries_unavailable_then_accepts_valid(self) -> None:
        result, calls, state = self.capture_snapshot(
            ("BLOCKED", SNAPSHOT_A), snapshot_key="pre"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, "2")
        self.assertEqual(state, SNAPSHOT_A + "|101")

    def test_validated_snapshot_drift_blocks_immediately_without_reappearance(self) -> None:
        result, calls, state = self.capture_snapshot(
            (SNAPSHOT_B, SNAPSHOT_A),
            expected_marker=SNAPSHOT_A,
            snapshot_key="post",
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(calls, "1")
        self.assertEqual(state, "|")

    def test_snapshot_capture_failure_clears_stale_globals(self) -> None:
        result, calls, state = self.capture_snapshot(("BLOCKED",))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, "12")
        self.assertEqual(state, "|")

        invalid, calls, state = self.capture_snapshot(
            (SNAPSHOT_A,), snapshot_key="unexpected"
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertEqual(calls, "")
        self.assertEqual(state, "|")

    def test_per_job_fetch_replaces_stale_files_and_uses_exact_endpoint(self) -> None:
        for job_key in ("ios", "watchos", "archive"):
            with self.subTest(job_key=job_key):
                result, arguments, output, error = self.fetch_job_log(job_key)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(
                    arguments,
                    "run view --repo Phasmotic/Talaria --job 67890 --log",
                )
                self.assertEqual(output, "completed per-job runtime log\n")
                self.assertEqual(error, "")

    def test_per_job_fetch_blocks_on_cli_error_empty_output_or_stderr(self) -> None:
        cases = (
            {"gh_rc": 1},
            {"emit_output": False},
            {"emit_stderr": True},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result, gh_arguments, _, _ = self.fetch_job_log("ios", **arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    gh_arguments,
                    "run view --repo Phasmotic/Talaria --job 67890 --log",
                )

    def test_per_job_fetch_rejects_unvalidated_identifiers_and_keys(self) -> None:
        cases = (
            {"job_key": "other"},
            {"job_key": "ios", "job_id": "0"},
            {"job_key": "ios", "job_id": "not-a-job"},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result, gh_arguments, _, _ = self.fetch_job_log(**arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(gh_arguments, "")

    def test_named_branch_passes(self) -> None:
        status, _, value, _ = self.classify(
            "talaria_classify_tier_b_branch", "0", "codex/phase-0-handoff"
        )
        self.assertEqual((status, value), ("PASS", "codex/phase-0-handoff"))

    def test_detached_or_empty_branch_blocks(self) -> None:
        for branch in ("", "HEAD"):
            with self.subTest(branch=branch):
                status, detail, _, _ = self.classify(
                    "talaria_classify_tier_b_branch", "0", branch
                )
                self.assertEqual(status, "BLOCKED")
                self.assertIn("named branch", detail)

    def test_branch_resolution_failure_blocks_even_with_plausible_output(self) -> None:
        status, detail, _, _ = self.classify(
            "talaria_classify_tier_b_branch", "128", "codex/phase-0-handoff"
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("named branch", detail)

    def test_canonical_https_and_ssh_origins_pass(self) -> None:
        urls = (
            "https://github.com/Phasmotic/Talaria.git",
            "git@github.com:Phasmotic/Talaria.git",
            "ssh://git@github.com/Phasmotic/Talaria.git",
        )
        for url in urls:
            with self.subTest(url=url):
                status, _, repository, _ = self.classify(
                    "talaria_classify_tier_b_repository",
                    "0",
                    url,
                    "Phasmotic/Talaria",
                )
                self.assertEqual((status, repository), ("PASS", "Phasmotic/Talaria"))

    def test_unreadable_or_noncanonical_origin_blocks(self) -> None:
        cases = (
            ("2", "https://github.com/Phasmotic/Talaria.git"),
            ("0", "https://github.com/Phasmotic/talaria.git"),
            ("0", "https://github.com/example/Talaria.git"),
        )
        for remote_url_rc, url in cases:
            with self.subTest(remote_url_rc=remote_url_rc, url=url):
                status, _, _, _ = self.classify(
                    "talaria_classify_tier_b_repository",
                    remote_url_rc,
                    url,
                    "Phasmotic/Talaria",
                )
                self.assertEqual(status, "BLOCKED")

    def test_clean_tree_passes_and_dirty_tree_blocks(self) -> None:
        status, _, _, _ = self.classify("talaria_classify_tier_b_clean_tree", "0", "")
        self.assertEqual(status, "PASS")

        for dirty_state in (" M scripts/gauntlet.sh", "?? untracked.fixture"):
            with self.subTest(dirty_state=dirty_state):
                status, detail, _, _ = self.classify(
                    "talaria_classify_tier_b_clean_tree", "0", dirty_state
                )
                self.assertEqual(status, "BLOCKED")
                self.assertIn("clean committed", detail)

    def test_indeterminate_tree_state_blocks(self) -> None:
        status, detail, _, _ = self.classify(
            "talaria_classify_tier_b_clean_tree", "128", ""
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("could not verify", detail)

    def test_valid_local_sha_passes_and_revision_failure_blocks(self) -> None:
        status, _, value, _ = self.classify(
            "talaria_classify_tier_b_local_sha", "0", SHA_A
        )
        self.assertEqual((status, value), ("PASS", SHA_A))

        status, detail, _, _ = self.classify(
            "talaria_classify_tier_b_local_sha", "128", ""
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("local checkpoint", detail)

        status, detail, _, _ = self.classify(
            "talaria_classify_tier_b_local_sha", "0", "short-sha"
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("local checkpoint", detail)

    def test_exact_published_branch_passes(self) -> None:
        record = f"{SHA_A}\trefs/heads/codex/phase-0-handoff"
        status, _, value, _ = self.classify(
            "talaria_classify_tier_b_remote",
            "0",
            record,
            "codex/phase-0-handoff",
            SHA_A,
        )
        self.assertEqual((status, value), ("PASS", SHA_A))

    def test_missing_or_unreadable_remote_branch_blocks(self) -> None:
        for remote_rc, record in (("2", ""), ("0", "")):
            with self.subTest(remote_rc=remote_rc, record=record):
                status, detail, _, _ = self.classify(
                    "talaria_classify_tier_b_remote",
                    remote_rc,
                    record,
                    "codex/phase-0-handoff",
                    SHA_A,
                )
                self.assertEqual(status, "BLOCKED")
                self.assertIn("not published", detail)

    def test_remote_sha_mismatch_blocks(self) -> None:
        record = f"{SHA_B}\trefs/heads/codex/phase-0-handoff"
        status, detail, _, _ = self.classify(
            "talaria_classify_tier_b_remote",
            "0",
            record,
            "codex/phase-0-handoff",
            SHA_A,
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("does not point", detail)

    def test_wrong_remote_ref_or_multiple_records_blocks(self) -> None:
        records = (
            f"{SHA_A}\trefs/heads/other",
            f"{SHA_A}\trefs/heads/codex/phase-0-handoff\n{SHA_A}\trefs/heads/other",
            f"{SHA_A}\trefs/heads/codex/phase-0-handoff ",
        )
        for record in records:
            with self.subTest(record=record):
                status, detail, _, _ = self.classify(
                    "talaria_classify_tier_b_remote",
                    "0",
                    record,
                    "codex/phase-0-handoff",
                    SHA_A,
                )
                self.assertEqual(status, "BLOCKED")
                self.assertIn("could not verify", detail)

    def test_valid_correlation_token_produces_exact_public_title(self) -> None:
        status, _, token, title = self.classify(
            "talaria_classify_tier_b_correlation", "0", TOKEN
        )
        self.assertEqual((status, token, title), ("PASS", TOKEN, TITLE))

    def test_failed_or_malformed_correlation_token_blocks(self) -> None:
        for token_rc, token in (("1", TOKEN), ("0", ""), ("0", "talaria-not-hex")):
            with self.subTest(token_rc=token_rc, token=token):
                status, detail, _, _ = self.classify(
                    "talaria_classify_tier_b_correlation", token_rc, token
                )
                self.assertEqual(status, "BLOCKED")
                self.assertIn("correlation token", detail)

    def test_empty_run_selection_waits(self) -> None:
        status, detail, _, _ = self.classify(
            "talaria_classify_tier_b_run_selection", "0", "", SHA_A, TITLE
        )
        self.assertEqual(status, "WAIT")
        self.assertIn("not appeared", detail)

    def test_matching_run_selection_passes(self) -> None:
        status, _, run_id, run_sha = self.classify(
            "talaria_classify_tier_b_run_selection",
            "0",
            f"12345|{SHA_A}|{TITLE}",
            SHA_A,
            TITLE,
        )
        self.assertEqual((status, run_id, run_sha), ("PASS", "12345", SHA_A))

    def test_run_list_error_or_malformed_record_blocks(self) -> None:
        cases = (
            ("1", f"123|{SHA_A}|{TITLE}"),
            ("0", f"not-an-id|{SHA_A}|{TITLE}"),
            ("0", f"123|{SHA_A}|{TITLE}|extra"),
        )
        for list_rc, record in cases:
            with self.subTest(list_rc=list_rc, record=record):
                status, _, _, _ = self.classify(
                    "talaria_classify_tier_b_run_selection",
                    list_rc,
                    record,
                    SHA_A,
                    TITLE,
                )
                self.assertEqual(status, "BLOCKED")

    def test_source_or_title_mismatch_waits_without_selecting_it(self) -> None:
        records = (f"12345|{SHA_B}|{TITLE}", f"12345|{SHA_A}|{OTHER_TITLE}")
        for record in records:
            with self.subTest(record=record):
                status, detail, run_id, _ = self.classify(
                    "talaria_classify_tier_b_run_selection",
                    "0",
                    record,
                    SHA_A,
                    TITLE,
                )
                self.assertEqual(status, "WAIT")
                self.assertEqual(run_id, "")
                self.assertIn("not appeared", detail)

    def test_one_exact_match_among_multiple_candidates_passes(self) -> None:
        records = (
            f"12343|{SHA_A}|{OTHER_TITLE}\n"
            f"12344|{SHA_B}|{TITLE}\n"
            f"12345|{SHA_A}|{TITLE}"
        )
        status, _, run_id, run_sha = self.classify(
            "talaria_classify_tier_b_run_selection", "0", records, SHA_A, TITLE
        )
        self.assertEqual((status, run_id, run_sha), ("PASS", "12345", SHA_A))

    def test_multiple_exact_matches_block_as_ambiguous(self) -> None:
        records = f"12345|{SHA_A}|{TITLE}\n12346|{SHA_A}|{TITLE}"
        status, detail, run_id, _ = self.classify(
            "talaria_classify_tier_b_run_selection", "0", records, SHA_A, TITLE
        )
        self.assertEqual(status, "BLOCKED")
        self.assertEqual(run_id, "")
        self.assertIn("multiple source-matched", detail)

    def test_malformed_candidate_in_multi_record_result_blocks(self) -> None:
        records = f"12345|{SHA_A}|{TITLE}\nmalformed"
        status, detail, _, _ = self.classify(
            "talaria_classify_tier_b_run_selection", "0", records, SHA_A, TITLE
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("candidates", detail)

if __name__ == "__main__":
    unittest.main()
