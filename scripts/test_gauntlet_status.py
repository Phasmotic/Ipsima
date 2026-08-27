from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parent.parent
HELPERS = REPO / "scripts" / "gauntlet_status.sh"
GAUNTLET = REPO / "scripts" / "gauntlet.sh"
WORKFLOW = REPO / ".github" / "workflows" / "tier-b.yml"
SHA_A = "a" * 40
SHA_B = "b" * 40
TOKEN = "talaria-" + "c" * 32
OTHER_TOKEN = "talaria-" + "d" * 32
TITLE = f"Talaria Tier B: {TOKEN}"
OTHER_TITLE = f"Talaria Tier B: {OTHER_TOKEN}"


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


class G4ClassificationTests(ShellClassifierTests):
    def test_gauntlet_uses_machine_readable_swiftlint_evidence(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        g4 = script.split("g4() {", maxsplit=1)[1].split("# ---- G5", maxsplit=1)[0]
        self.assertIn("--reporter json", g4)
        self.assertIn("check_swiftlint_report.py", g4)
        self.assertIn("talaria_classify_g4", g4)
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
        self.assertEqual(workflow.count("- name: Emit Tier B job status"), 3)
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
        self.assertIn("scripts.test_check_tier_b_status_log", tier_b)
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("scripts.test_check_tier_b_status_log", workflow)

    def test_every_gh_operation_is_bound_to_the_verified_repository(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        tier_b = script.split("tier_b() {", maxsplit=1)[1]
        self.assertEqual(tier_b.count('--repo "$TIER_B_REPOSITORY"'), 5)
        self.assertIn("talaria_classify_tier_b_repository", tier_b)

    def test_exact_run_logs_are_checked_with_the_dispatch_correlation(self) -> None:
        script = GAUNTLET.read_text(encoding="utf-8")
        tier_b = script.split("tier_b() {", maxsplit=1)[1]
        self.assertIn('gh run view "$run_id" --repo "$TIER_B_REPOSITORY" --log', tier_b)
        self.assertIn("scripts/check_tier_b_status_log.py", tier_b)
        self.assertIn('--correlation "$correlation_token"', tier_b)
        self.assertIn('--conclusion "$final_conclusion"', tier_b)
        self.assertIn('mapfile -t evidence_lines <"$ART/tierb-evidence.log"', tier_b)
        for marker in (
            "TIER B EVIDENCE PASS: ",
            "TIER B EVIDENCE FAIL: ",
            "TIER B EVIDENCE BLOCKED: ",
        ):
            self.assertIn(marker, tier_b)

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
            "https://github.com/markschonfeld/Talaria.git",
            "git@github.com:markschonfeld/Talaria.git",
            "ssh://git@github.com/markschonfeld/Talaria.git",
        )
        for url in urls:
            with self.subTest(url=url):
                status, _, repository, _ = self.classify(
                    "talaria_classify_tier_b_repository",
                    "0",
                    url,
                    "markschonfeld/Talaria",
                )
                self.assertEqual((status, repository), ("PASS", "markschonfeld/Talaria"))

    def test_unreadable_or_noncanonical_origin_blocks(self) -> None:
        cases = (
            ("2", "https://github.com/markschonfeld/Talaria.git"),
            ("0", "https://github.com/markschonfeld/talaria.git"),
            ("0", "https://github.com/example/Talaria.git"),
        )
        for remote_url_rc, url in cases:
            with self.subTest(remote_url_rc=remote_url_rc, url=url):
                status, _, _, _ = self.classify(
                    "talaria_classify_tier_b_repository",
                    remote_url_rc,
                    url,
                    "markschonfeld/Talaria",
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

    def test_completed_success_and_failure_envelopes_require_job_evidence(self) -> None:
        success = self.classify(
            "talaria_classify_tier_b_final",
            "0",
            f"completed|success|{SHA_A}|{TITLE}",
            SHA_A,
            TITLE,
            "0",
        )
        failure = self.classify(
            "talaria_classify_tier_b_final",
            "0",
            f"completed|failure|{SHA_A}|{TITLE}",
            SHA_A,
            TITLE,
            "1",
        )
        self.assertEqual((success[0], success[3]), ("READY", "success"))
        self.assertEqual((failure[0], failure[3]), ("READY", "failure"))
        self.assertIn("job evidence required", failure[1])

    def test_final_conclusion_must_agree_with_watch_exit_status(self) -> None:
        cases = (
            (f"completed|success|{SHA_A}|{TITLE}", "1"),
            (f"completed|failure|{SHA_A}|{TITLE}", "0"),
        )
        for record, watch_rc in cases:
            with self.subTest(record=record, watch_rc=watch_rc):
                status, detail, _, _ = self.classify(
                    "talaria_classify_tier_b_final",
                    "0",
                    record,
                    SHA_A,
                    TITLE,
                    watch_rc,
                )
                self.assertEqual(status, "BLOCKED")
                self.assertIn("disagreed", detail)

    def test_final_view_error_or_malformed_record_blocks(self) -> None:
        cases = (
            ("1", f"completed|success|{SHA_A}|{TITLE}"),
            ("0", "completed|success"),
            ("0", f"completed|success|{SHA_A}|{TITLE}|extra"),
        )
        for view_rc, record in cases:
            with self.subTest(view_rc=view_rc, record=record):
                status, detail, _, _ = self.classify(
                    "talaria_classify_tier_b_final",
                    view_rc,
                    record,
                    SHA_A,
                    TITLE,
                    "1",
                )
                self.assertEqual(status, "BLOCKED")
                self.assertIn("could not verify", detail)

        status, detail, _, _ = self.classify(
            "talaria_classify_tier_b_final",
            "0",
            f"completed|success|{SHA_A}|{TITLE}",
            SHA_A,
            TITLE,
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("could not verify", detail)

    def test_final_sha_mismatch_blocks(self) -> None:
        status, detail, _, _ = self.classify(
            "talaria_classify_tier_b_final",
            "0",
            f"completed|success|{SHA_B}|{TITLE}",
            SHA_A,
            TITLE,
            "0",
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("different commit", detail)

    def test_final_correlation_title_mismatch_blocks(self) -> None:
        status, detail, _, _ = self.classify(
            "talaria_classify_tier_b_final",
            "0",
            f"completed|success|{SHA_A}|{OTHER_TITLE}",
            SHA_A,
            TITLE,
            "0",
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("correlation title", detail)

    def test_incomplete_or_indecisive_final_state_blocks(self) -> None:
        records = (
            f"in_progress||{SHA_A}|{TITLE}",
            f"completed|cancelled|{SHA_A}|{TITLE}",
        )
        for record in records:
            with self.subTest(record=record):
                status, _, _, _ = self.classify(
                    "talaria_classify_tier_b_final", "0", record, SHA_A, TITLE, "1"
                )
                self.assertEqual(status, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
