#!/usr/bin/env bash
#
# Talaria gauntlet — native Linux gates.
#
# Supported local entry path (PowerShell only):
#   pwsh -File scripts/gauntlet.ps1             run every Tier A gate
#   pwsh -File scripts/gauntlet.ps1 -TierB      additionally dispatch + follow macOS CI
# Supported CI entry path (pinned reusable workflow only):
#   bash scripts/gauntlet.sh --github-pr        run advisory G1-G5 on GitHub-hosted Linux
#
# The PowerShell launcher enters Ubuntu through wsl.exe and marks that namespace.
# Do not enter through an Ubuntu shortcut or Git Bash: this machine can assign those
# launchers different PID namespaces, making prerequisite checks silently disagree.
#
# Documented deviation from the original container design: Swift runs natively in
# WSL because Docker is unavailable on this host. Exact compiler and tool versions
# remain enforced, but the Linux userspace is no longer hermetic. Tier B on macos-26
# remains authoritative. See docs/GOVERNANCE.md.
#
# Rules of the house (docs/GOVERNANCE.md):
#   * A gate is never weakened to make it pass. Failures are fixed or reported.
#   * Missing prerequisites produce BLOCKED — visible, never a silent skip.
#   * Every gate writes its full log to .gauntlet/<gate>.log for the PR evidence table.
set -u
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
ART="$ROOT/.gauntlet"
mkdir -p "$ART"

# ---- pinned toolchain -------------------------------------------------------
# These MUST match the toolchain supplied by Xcode 26.6 / macos-26 CI. Bump
# local and CI pins together; see docs/GOVERNANCE.md.
EXPECTED_ENTRY="powershell-wsl"
EXPECTED_NAMESPACE="pwsh-wsl-ubuntu-swift-6.3.3"
EXPECTED_SWIFT_LINE="Swift version 6.3.3 (swift-6.3.3-RELEASE)"
EXPECTED_SWIFT_TARGET="x86_64-unknown-"linux-gnu
EXPECTED_GH_RUN_LINE="gh version 2.45.0 (2025-07-18 Ubuntu 2.45.0-1ubuntu0.3)"
EXPECTED_GH_LOG_LINE="gh version 2.88.1 (2026-03-12)"
SWIFTFORMAT_VERSION="0.62.1"
SWIFTFORMAT_URL="https://github.com/nicklockwood/SwiftFormat/releases/download/0.62.1/swiftformat_linux.zip"
SWIFTFORMAT_SHA256="61ff55f3581e2144a4ad114831167102c38be853df75c1477d20b40a8e8120aa"
SWIFTLINT_VERSION="0.65.0"
SWIFTLINT_URL="https://github.com/realm/SwiftLint/releases/download/0.65.0/swiftlint_linux_amd64.zip"
SWIFTLINT_SHA256="79306a34e5c7cc55a220cd108cbb861dcad5f10138dcdf261e2624ae8b0a486b"
GITLEAKS_VERSION="8.30.1"
GITLEAKS_URL="https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz"
GITLEAKS_SHA256="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
XCODEGEN_VERSION="2.46.0"
TIER_B_REPOSITORY="Phasmotic/Ipsima"
KIT="Packages/HermesKit"
TOOLS="$ART/tools"
GH_RUN_BIN=""
GH_LOG_BIN=""
TIER_B_CLIENT_STATUS=""
TIER_B_CLIENT_DETAIL=""

blocked_preflight() {
    printf 'GAUNTLET BLOCKED — %s\n' "$1" >&2
    exit 1
}

RUN_TIER_B=0
RUN_GITHUB_PR=0
if [ "$#" -gt 1 ]; then
    blocked_preflight "expected at most one gauntlet argument"
fi
case "${1:-}" in
    "") ;;
    --tier-b) RUN_TIER_B=1 ;;
    --github-pr) RUN_GITHUB_PR=1 ;;
    *) blocked_preflight "unknown gauntlet argument: $1" ;;
esac

STATUS_HELPERS="$ROOT/scripts/gauntlet_status.sh"
[ -r "$STATUS_HELPERS" ] \
    || blocked_preflight "gate status classifiers are unavailable"
# shellcheck source=gauntlet_status.sh
source "$STATUS_HELPERS" \
    || blocked_preflight "gate status classifiers could not be loaded"

G1_ENVIRONMENT_LINE=""
G1_ENTRY_LINE=""
if [ "$RUN_GITHUB_PR" -eq 1 ]; then
    command -v git >/dev/null 2>&1 \
        || blocked_preflight "git is required to bind the pull-request source"
    command -v python3 >/dev/null 2>&1 \
        || blocked_preflight "python3 is required to inspect the hosted Linux environment"
    github_head_rc=0
    github_head_sha="$(git rev-parse HEAD 2>"$ART/github-pr-head.stderr")" \
        || github_head_rc=$?
    github_head_stderr_state="empty"
    if [ -s "$ART/github-pr-head.stderr" ]; then
        github_head_stderr_state="nonempty"
    fi
    os_id="$(sed -n 's/^ID=//p' /etc/os-release 2>/dev/null)"
    os_id="${os_id#\"}"
    os_id="${os_id%\"}"
    os_version="$(sed -n 's/^VERSION_ID=//p' /etc/os-release 2>/dev/null)"
    os_version="${os_version#\"}"
    os_version="${os_version%\"}"
    kernel_release="$(uname -r 2>/dev/null || true)"
    machine="$(uname -m 2>/dev/null || true)"
    wsl_state="absent"
    if [ -n "${WSL_INTEROP:-}" ] || [ -n "${WSL_DISTRO_NAME:-}" ]; then
        wsl_state="present"
    fi
    talaria_classify_github_pr_entry \
        "${GITHUB_ACTIONS:-}" \
        "${GITHUB_EVENT_NAME:-}" \
        "${RUNNER_ENVIRONMENT:-}" \
        "${RUNNER_OS:-}" \
        "${RUNNER_ARCH:-}" \
        "${GITHUB_REPOSITORY:-}" \
        "${GITHUB_REF:-}" \
        "${GITHUB_SHA:-}" \
        "$github_head_rc" \
        "$github_head_sha" \
        "$github_head_stderr_state" \
        "$os_id" \
        "$os_version" \
        "$kernel_release" \
        "$machine" \
        "$wsl_state"
    [ "$TALARIA_CLASS_STATUS" = "PASS" ] \
        || blocked_preflight "$TALARIA_CLASS_DETAIL"
    G1_ENVIRONMENT_LINE="GitHub-hosted Ubuntu 24.04 x64 toolchain: verified"
    G1_ENTRY_LINE="pull_request merge checkout: verified"
else
    [ "${TALARIA_GAUNTLET_ENTRY:-}" = "$EXPECTED_ENTRY" ] \
        || blocked_preflight "launch with pwsh -File scripts/gauntlet.ps1"
    [ "${TALARIA_GAUNTLET_NAMESPACE:-}" = "$EXPECTED_NAMESPACE" ] \
        || blocked_preflight "unexpected WSL launcher namespace"
    [ -n "${WSL_INTEROP:-}" ] \
        || blocked_preflight "Tier A requires PowerShell-launched WSL"
    [ "${WSL_DISTRO_NAME:-}" = "Ubuntu" ] \
        || blocked_preflight "Tier A requires the Ubuntu WSL distro"
    grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null \
        || blocked_preflight "Tier A is not running inside WSL"
    G1_ENVIRONMENT_LINE="native WSL toolchain: verified"
    G1_ENTRY_LINE="PowerShell launcher namespace marker: verified"
fi

# Swiftly installs user-local proxies. Add only its documented bin directory;
# never inherit a Windows Swift executable through WSL interop.
if [ "$RUN_GITHUB_PR" -eq 0 ] && [ -d "$HOME/.local/share/swiftly/bin" ]; then
    PATH="$HOME/.local/share/swiftly/bin:$PATH"
    export PATH
fi
SWIFT_BIN="$(command -v swift 2>/dev/null || true)"
[ -n "$SWIFT_BIN" ] || blocked_preflight "native Swift is not installed"
case "$SWIFT_BIN" in
    /mnt/*|*.exe) blocked_preflight "Swift resolved outside the Linux namespace" ;;
esac
if ! SWIFT_VERSION_OUTPUT="$(swift --version 2>&1)"; then
    blocked_preflight "swift --version failed"
fi
[ -n "$SWIFT_VERSION_OUTPUT" ] || blocked_preflight "swift --version returned empty output"
SWIFT_VERSION_LINE="$(printf '%s\n' "$SWIFT_VERSION_OUTPUT" | sed -n '1p')"
[ "$SWIFT_VERSION_LINE" = "$EXPECTED_SWIFT_LINE" ] \
    || blocked_preflight "expected $EXPECTED_SWIFT_LINE; found $SWIFT_VERSION_LINE"
command -v python3 >/dev/null 2>&1 \
    || blocked_preflight "python3 is required to inspect the pinned Swift toolchain"
if ! SWIFT_TARGET_INFO="$(swift -print-target-info 2>&1)"; then
    blocked_preflight "swift -print-target-info failed"
fi
[ -n "$SWIFT_TARGET_INFO" ] \
    || blocked_preflight "swift -print-target-info returned empty output"
SWIFT_RESOURCE_PATH="$(printf '%s\n' "$SWIFT_TARGET_INFO" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["paths"]["runtimeResourcePath"])' \
        2>/dev/null || true)"
SWIFT_TARGET_TRIPLE="$(printf '%s\n' "$SWIFT_TARGET_INFO" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["target"]["triple"])' \
        2>/dev/null || true)"
[ "$SWIFT_TARGET_TRIPLE" = "$EXPECTED_SWIFT_TARGET" ] \
    || blocked_preflight "expected Swift target $EXPECTED_SWIFT_TARGET"
[ -n "$SWIFT_RESOURCE_PATH" ] \
    || blocked_preflight "could not resolve the pinned Swift runtime resource path"
TOOLCHAIN_USR="$(cd "$SWIFT_RESOURCE_PATH/../.." 2>/dev/null && pwd -P)"
[ -n "$TOOLCHAIN_USR" ] \
    || blocked_preflight "could not resolve the pinned Swift toolchain"
LLVM_COV_BIN="$TOOLCHAIN_USR/bin/llvm-cov"
LLVM_PROFDATA_BIN="$TOOLCHAIN_USR/bin/llvm-profdata"
SOURCEKIT_LIB_DIR="$TOOLCHAIN_USR/lib"
[ -x "$LLVM_COV_BIN" ] \
    || blocked_preflight "the pinned Swift toolchain does not provide llvm-cov"
[ -x "$LLVM_PROFDATA_BIN" ] \
    || blocked_preflight "the pinned Swift toolchain does not provide llvm-profdata"
[ -f "$SOURCEKIT_LIB_DIR/libsourcekitdInProc.so" ] \
    || blocked_preflight "the pinned Swift toolchain does not provide SourceKit"

RESULTS=()
FAILED=0

record() { # gate status detail
    RESULTS+=("$1|$2|$3")
    case "$2" in
        FAIL|BLOCKED) FAILED=1 ;;
    esac
}

talaria_classify_tier_b_client_families() {
    local run_bin="${1-}" log_bin="${2-}"
    TIER_B_CLIENT_STATUS="BLOCKED"
    TIER_B_CLIENT_DETAIL="Tier B GitHub CLI selection is malformed"
    [ "$#" -eq 2 ] || return
    case "$run_bin" in
        /*) ;;
        *)
            TIER_B_CLIENT_DETAIL="the native WSL GitHub CLI is unavailable"
            return
            ;;
    esac
    case "$run_bin" in
        /mnt/*|*.exe)
            TIER_B_CLIENT_DETAIL="Tier B run operations require the native WSL GitHub CLI"
            return
            ;;
    esac
    case "$log_bin" in
        /mnt/[a-zA-Z]/*/gh.exe) ;;
        *)
            TIER_B_CLIENT_DETAIL="Tier B job logs require the PowerShell-selected Windows GitHub CLI"
            return
            ;;
    esac
    TIER_B_CLIENT_STATUS="PASS"
    TIER_B_CLIENT_DETAIL="Tier B GitHub CLI families are explicit"
}

verify_tier_b_cli_version() {
    local cli_bin="${1-}" expected_line="${2-}" evidence_key="${3-}"
    local stdout_path stderr_path version_rc version_line
    [ "$#" -eq 3 ] && [ -n "$cli_bin" ] && [ -x "$cli_bin" ] || return 1
    case "$evidence_key" in
        run|log) ;;
        *) return 1 ;;
    esac
    stdout_path="$ART/tierb-gh-$evidence_key-version.stdout"
    stderr_path="$ART/tierb-gh-$evidence_key-version.stderr"
    : >"$stdout_path" || return 1
    : >"$stderr_path" || return 1
    "$cli_bin" --version >"$stdout_path" 2>"$stderr_path"
    version_rc=$?
    version_line="$(tr -d '\r' <"$stdout_path" | sed -n '1p')"
    [ "$version_rc" -eq 0 ] \
        && [ -s "$stdout_path" ] \
        && [ ! -s "$stderr_path" ] \
        && [ "$version_line" = "$expected_line" ]
}

tier_b_run_gh() {
    [ "$#" -gt 0 ] || return 1
    "$GH_RUN_BIN" "$@"
}

tier_b_log_gh() {
    [ "$#" -gt 0 ] || return 1
    "$GH_LOG_BIN" "$@"
}

cleanup_g2_temp() {
    local cleanup_target="$1"
    case "$cleanup_target" in
        /tmp/talaria-hermeskit-swift-6.3.3.*)
            rm -rf -- "$cleanup_target"
            ;;
        *)
            return 1
            ;;
    esac
}

finish_g2() {
    local status="$1" detail="$2" cleanup_target="$3"
    if cleanup_g2_temp "$cleanup_target"; then
        record G2 "$status" "$detail"
    else
        record G2 BLOCKED "refused or failed to clean the validated coverage scratch directory"
    fi
}

resolve_tier_b_g6() {
    local index gate status detail g6_row_count=0 deferred_count=0 deferred_index=""
    for index in "${!RESULTS[@]}"; do
        IFS='|' read -r gate status detail <<<"${RESULTS[$index]}"
        if [ "$gate" = "G6" ] || [ "$gate" = "G6*" ]; then
            g6_row_count=$((g6_row_count + 1))
        fi
        if [ "$gate" = "G6*" ] && [ "$status" = "DEFER->B" ]; then
            deferred_count=$((deferred_count + 1))
            deferred_index="$index"
        fi
    done
    if [ "$g6_row_count" -ne 1 ] || [ "$deferred_count" -ne 1 ] || [ -z "$deferred_index" ]; then
        return 1
    fi
    RESULTS[$deferred_index]="G6|PASS|authoritative two-generation hash check passed in Tier B run $1"
}

fetch_tier_b_job_log() {
    local job_id="${1-}" job_key="${2-}"
    local output_path="" stderr_path="" fetch_rc
    if [ "$#" -ne 2 ] || [[ ! "$job_id" =~ ^[1-9][0-9]*$ ]]; then
        return 1
    fi
    case "$job_key" in
        ios|watchos|archive) output_path="$ART/tierb-$job_key.log" ;;
        *) return 1 ;;
    esac
    stderr_path="$output_path.stderr"
    : >"$output_path" || return 1
    : >"$stderr_path" || return 1
    # gh ignores a positional run ID when --job is present. Source binding
    # therefore comes from the validated canonical job URL in the run snapshot,
    # and the positional run argument is deliberately omitted here.
    tier_b_log_gh run view --repo "$TIER_B_REPOSITORY" --job "$job_id" --log \
        >"$output_path" 2>"$stderr_path"
    fetch_rc=$?
    [ "$fetch_rc" -eq 0 ] \
        && [ -s "$output_path" ] \
        && [ ! -s "$stderr_path" ]
}

capture_tier_b_snapshot() {
    local run_id="${1-}" head_sha="${2-}" expected_title="${3-}"
    local watch_rc="${4-}" expected_marker="${5-}"
    local snapshot_key="${6-}" snapshot_path="" stderr_path="" verdict_path=""
    local snapshot_attempt view_rc checker_rc snapshot_marker=""
    local snapshot_pattern='^TIER B SNAPSHOT PASS: ios=([1-9][0-9]*)/(success|failure) watchos=([1-9][0-9]*)/(success|failure) archive=([1-9][0-9]*)/(success|failure) conclusion=(success|failure) digest=([0-9a-f]{64})$'
    local -a snapshot_lines=()
    TIER_B_SNAPSHOT_MARKER=""
    TIER_B_IOS_JOB_ID=""
    TIER_B_IOS_CONCLUSION=""
    TIER_B_WATCHOS_JOB_ID=""
    TIER_B_WATCHOS_CONCLUSION=""
    TIER_B_ARCHIVE_JOB_ID=""
    TIER_B_ARCHIVE_CONCLUSION=""
    TIER_B_RUN_CONCLUSION=""
    TIER_B_SNAPSHOT_DIGEST=""
    if [ "$#" -ne 6 ] \
        || [[ ! "$run_id" =~ ^[1-9][0-9]*$ ]] \
        || [[ ! "$head_sha" =~ ^[0-9a-f]{40}$ ]] \
        || [[ ! "$expected_title" =~ ^Talaria\ Tier\ B:\ talaria-[0-9a-f]{32}$ ]] \
        || [[ ! "$watch_rc" =~ ^[0-9]+$ ]]; then
        return 1
    fi
    case "$snapshot_key" in
        pre|post)
            snapshot_path="$ART/tierb-$snapshot_key-snapshot.json"
            stderr_path="$ART/tierb-$snapshot_key-snapshot.stderr"
            verdict_path="$ART/tierb-$snapshot_key-snapshot-verdict.log"
            ;;
        *) return 1 ;;
    esac
    for snapshot_attempt in $(seq 1 12); do
        : >"$snapshot_path" || return 1
        : >"$stderr_path" || return 1
        : >"$verdict_path" || return 1
        tier_b_run_gh run view "$run_id" \
            --repo "$TIER_B_REPOSITORY" \
            --attempt 1 \
            --json status,conclusion,databaseId,headSha,displayTitle,url,jobs \
            >"$snapshot_path" 2>"$stderr_path"
        view_rc=$?
        if [ "$view_rc" -eq 0 ] \
            && [ -s "$snapshot_path" ] \
            && [ ! -s "$stderr_path" ]; then
            python3 -B scripts/check_tier_b_run_snapshot.py \
                --snapshot "$snapshot_path" \
                --expected-run-id "$run_id" \
                --expected-head-sha "$head_sha" \
                --expected-title "$expected_title" \
                --expected-repository "$TIER_B_REPOSITORY" \
                --watch-rc "$watch_rc" \
                --expected-attempt 1 \
                >"$verdict_path" 2>&1
            checker_rc=$?
            snapshot_lines=()
            if [ "$checker_rc" -eq 0 ] \
                && mapfile -t snapshot_lines <"$verdict_path" \
                && [ "${#snapshot_lines[@]}" -eq 1 ]; then
                snapshot_marker="${snapshot_lines[0]}"
                if [[ "$snapshot_marker" =~ $snapshot_pattern ]]; then
                    if [ -n "$expected_marker" ] \
                        && [ "$snapshot_marker" != "$expected_marker" ]; then
                        # This is validated contradictory evidence, not eventual
                        # unavailability. Do not retry until an earlier value
                        # happens to reappear.
                        return 2
                    fi
                    TIER_B_SNAPSHOT_MARKER="$snapshot_marker"
                    TIER_B_IOS_JOB_ID="${BASH_REMATCH[1]}"
                    TIER_B_IOS_CONCLUSION="${BASH_REMATCH[2]}"
                    TIER_B_WATCHOS_JOB_ID="${BASH_REMATCH[3]}"
                    TIER_B_WATCHOS_CONCLUSION="${BASH_REMATCH[4]}"
                    TIER_B_ARCHIVE_JOB_ID="${BASH_REMATCH[5]}"
                    TIER_B_ARCHIVE_CONCLUSION="${BASH_REMATCH[6]}"
                    TIER_B_RUN_CONCLUSION="${BASH_REMATCH[7]}"
                    TIER_B_SNAPSHOT_DIGEST="${BASH_REMATCH[8]}"
                    return 0
                fi
            fi
        fi
        if [ "$snapshot_attempt" -lt 12 ]; then
            sleep 5
        fi
    done
    return 1
}

section() { printf '\n=== %s ===\n' "$1"; }

# ---- G1: swift build debug + release, zero warnings -------------------------
g1() {
    section "G1 · swift build (debug + release, warnings-as-errors)"
    local debug_rc release_rc warning_rc
    python3 -B -m unittest \
        scripts.test_check_xcode_log \
        scripts.test_gauntlet_status.GitHubPRModeTests \
        scripts.test_gauntlet_status.GitHubPRCoreWorkflowTests \
        scripts.test_gauntlet_status.GitHubPRCallerWorkflowTests \
        >"$ART/g1-selftest.log" 2>&1
    if [ $? -ne 0 ]; then
        record G1 BLOCKED "G1 and Linux workflow self-tests failed (see .gauntlet/g1-selftest.log)"
        return
    fi
    {
        printf '%s\n' "$SWIFT_VERSION_LINE"
        printf '%s\n' "$G1_ENVIRONMENT_LINE"
        printf '%s\n' "$G1_ENTRY_LINE"
        printf 'Swift target: %s\n' "$SWIFT_TARGET_TRIPLE"
        echo "--- debug ---"
        swift build --package-path "$KIT" -c debug -Xswiftc -warnings-as-errors
        debug_rc=$?
        echo "--- release ---"
        swift build --package-path "$KIT" -c release -Xswiftc -warnings-as-errors
        release_rc=$?
    } >"$ART/g1.log" 2>&1
    if [ "$debug_rc" -ne 0 ] || [ "$release_rc" -ne 0 ]; then
        record G1 FAIL "see .gauntlet/g1.log ($(grep -c 'error:' "$ART/g1.log" || true) errors)"
        return
    fi
    python3 -B scripts/check_xcode_log.py \
        --log "$ART/g1.log" \
        --success-marker "--- debug ---" \
        --success-marker "--- release ---" \
        >"$ART/g1-warning-check.log" 2>&1
    warning_rc=$?
    case "$warning_rc" in
        0) record G1 PASS "$(head -1 "$ART/g1.log") · zero warnings verified" ;;
        1) record G1 FAIL "warning diagnostic in build log (see .gauntlet/g1.log)" ;;
        *) record G1 BLOCKED "build warning scan was indeterminate (see .gauntlet/g1-warning-check.log)" ;;
    esac
}

g2_cleanup_after_setup_failure() {
    local cleanup_target="$1"
    if [ -n "$cleanup_target" ]; then
        cleanup_g2_temp "$cleanup_target" >/dev/null 2>&1 || true
    fi
}

# ---- G2: swift test + coverage ----------------------------------------------
g2() {
    section "G2 · swift test + line coverage (>=85% on kit sources)"
    local g2_temp g2_scratch build_rc list_rc discovery_rc test_rc warning_rc
    local evidence_rc evidence_marker merge_rc report_rc export_rc coverage_values
    local binary_digest_before binary_digest_after hash_rc
    local profile_raw profile_data
    local covered_lines total_lines raw_pct display_pct
    local evidence_file
    local ignore_regex='(^|/)(Tests|\.build|\.gauntlet)(/|$)'
    local -a test_binaries

    for evidence_file in \
        g2.log g2-build.log g2-list.txt g2-list.stderr \
        g2-discovery.json g2-discovery.stderr g2-test.log \
        g2-test-evidence.log g2-warning-check.log g2-coverage.txt g2-summary.json; do
        if ! : >"$ART/$evidence_file"; then
            record G2 BLOCKED "could not initialize fresh G2 evidence files"
            return
        fi
    done

    python3 -B -m unittest scripts.test_check_swift_test_execution \
        >"$ART/g2-execution-selftest.log" 2>&1
    if [ $? -ne 0 ]; then
        record G2 BLOCKED "test-discovery checker self-tests failed (see .gauntlet/g2-execution-selftest.log)"
        return
    fi

    g2_temp="$(mktemp -d /tmp/talaria-hermeskit-swift-6.3.3.XXXXXX 2>/dev/null || true)"
    g2_scratch="$g2_temp/.build"
    profile_raw="$g2_temp/g2.profraw"
    profile_data="$g2_temp/g2.profdata"
    if [ -z "$g2_temp" ] || [ ! -d "$g2_temp" ] || ! mkdir -p "$g2_scratch"; then
        g2_cleanup_after_setup_failure "$g2_temp"
        record G2 BLOCKED "could not create a Linux coverage scratch directory"
        return
    fi

    swift build \
        --package-path "$KIT" \
        --scratch-path "$g2_scratch" \
        -c debug \
        -Xswiftc -warnings-as-errors \
        --build-tests \
        --enable-code-coverage \
        >"$ART/g2-build.log" 2>&1
    build_rc=$?
    if [ "$build_rc" -ne 0 ]; then
        cp "$ART/g2-build.log" "$ART/g2.log" 2>/dev/null || true
        if [ -s "$ART/g2-build.log" ] && \
            grep -Eq '(^error:|:[0-9]+:[0-9]+: error:)' "$ART/g2-build.log"; then
            finish_g2 FAIL "coverage test build has compiler or manifest errors (see .gauntlet/g2.log)" "$g2_temp"
        else
            finish_g2 BLOCKED "coverage test build failed without a source diagnostic (see .gauntlet/g2.log)" "$g2_temp"
        fi
        return
    fi

    swift test \
        --package-path "$KIT" \
        --scratch-path "$g2_scratch" \
        -c debug \
        --skip-build \
        --enable-xctest \
        --enable-swift-testing \
        list \
        >"$ART/g2-list.txt" 2>"$ART/g2-list.stderr"
    list_rc=$?
    if [ "$list_rc" -ne 0 ]; then
        {
            cat "$ART/g2-build.log"
            cat "$ART/g2-list.stderr"
        } >"$ART/g2.log"
        finish_g2 BLOCKED "SwiftPM could not list the already-built test suite (see .gauntlet/g2.log)" "$g2_temp"
        return
    fi
    if [ ! -s "$ART/g2-list.txt" ]; then
        cp "$ART/g2-list.stderr" "$ART/g2.log" 2>/dev/null || true
        finish_g2 BLOCKED "SwiftPM returned an empty test inventory (see .gauntlet/g2.log)" "$g2_temp"
        return
    fi

    mapfile -t test_binaries < <(
        find "$g2_scratch" -type f -name '*.xctest' -perm -u+x -print 2>/dev/null
    )
    if [ "${#test_binaries[@]}" -ne 1 ]; then
        printf 'expected one test executable; found %s\n' \
            "${#test_binaries[@]}" >>"$ART/g2-list.stderr"
        cp "$ART/g2-list.stderr" "$ART/g2.log" 2>/dev/null || true
        finish_g2 BLOCKED "ambiguous SwiftPM test executable (see .gauntlet/g2.log)" "$g2_temp"
        return
    fi

    "${test_binaries[0]}" --dump-tests-json \
        >"$ART/g2-discovery.json" 2>"$ART/g2-discovery.stderr"
    discovery_rc=$?
    if [ "$discovery_rc" -ne 0 ] || [ ! -s "$ART/g2-discovery.json" ]; then
        finish_g2 BLOCKED "XCTest discovery evidence is unavailable" "$g2_temp"
        return
    fi
    if [ -s "$ART/g2-discovery.stderr" ]; then
        finish_g2 BLOCKED "XCTest discovery emitted unexpected stderr" "$g2_temp"
        return
    fi

    binary_digest_before="$(python3 - "${test_binaries[0]}" <<'PY'
import hashlib
import pathlib
import sys

print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
    hash_rc=$?
    if [ "$hash_rc" -ne 0 ] || [[ ! "$binary_digest_before" =~ ^[0-9a-f]{64}$ ]]; then
        finish_g2 BLOCKED "could not bind execution to the discovered test binary" "$g2_temp"
        return
    fi

    LLVM_PROFILE_FILE="$profile_raw" swift test \
        --package-path "$KIT" \
        --scratch-path "$g2_scratch" \
        -c debug \
        --skip-build \
        --enable-xctest \
        --disable-swift-testing \
        >"$ART/g2-test.log" 2>&1
    test_rc=$?

    binary_digest_after="$(python3 - "${test_binaries[0]}" <<'PY'
import hashlib
import pathlib
import sys

print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
    hash_rc=$?
    if [ "$hash_rc" -ne 0 ] || [ "$binary_digest_after" != "$binary_digest_before" ]; then
        finish_g2 BLOCKED "test executable changed between discovery and execution" "$g2_temp"
        return
    fi

    {
        echo "--- swift build --build-tests ---"
        cat "$ART/g2-build.log"
        echo "--- swift test list --skip-build ---"
        cat "$ART/g2-list.stderr"
        echo "--- swift test exact XCTest execution ---"
        cat "$ART/g2-test.log"
    } >"$ART/g2.log"

    python3 -B scripts/check_xcode_log.py \
        --log "$ART/g2.log" >"$ART/g2-warning-check.log" 2>&1
    warning_rc=$?
    if [ "$warning_rc" -eq 1 ]; then
        finish_g2 FAIL "warning diagnostic in SwiftPM test log (see .gauntlet/g2.log)" "$g2_temp"
        return
    elif [ "$warning_rc" -ne 0 ]; then
        finish_g2 BLOCKED "test warning scan was indeterminate (see .gauntlet/g2-warning-check.log)" "$g2_temp"
        return
    fi

    python3 -B scripts/check_swift_test_execution.py \
        --swiftpm-list "$ART/g2-list.txt" \
        --discovery-json "$ART/g2-discovery.json" \
        --execution-log "$ART/g2-test.log" \
        --catalog-json protocol/methods.json \
        --test-rc "$test_rc" \
        >"$ART/g2-test-evidence.log" 2>&1
    evidence_rc=$?
    evidence_marker="$(awk 'NF { marker=$0 } END { print marker }' "$ART/g2-test-evidence.log")"
    case "$evidence_rc" in
        0)
            case "$evidence_marker" in
                "G2 TEST PASS: "*) ;;
                *) finish_g2 BLOCKED "test checker status/marker evidence disagreed (see .gauntlet/g2-test-evidence.log)" "$g2_temp"; return ;;
            esac
            ;;
        1)
            case "$evidence_marker" in
                "G2 TEST FAIL: "*) finish_g2 FAIL "test discovery or execution contract failed (see .gauntlet/g2-test-evidence.log)" "$g2_temp"; return ;;
                *) finish_g2 BLOCKED "test checker status/marker evidence disagreed (see .gauntlet/g2-test-evidence.log)" "$g2_temp"; return ;;
            esac
            ;;
        2)
            case "$evidence_marker" in
                "G2 TEST BLOCKED: "*) finish_g2 BLOCKED "test discovery or execution evidence was indeterminate (see .gauntlet/g2-test-evidence.log)" "$g2_temp"; return ;;
                *) finish_g2 BLOCKED "test checker status/marker evidence disagreed (see .gauntlet/g2-test-evidence.log)" "$g2_temp"; return ;;
            esac
            ;;
        *)
            finish_g2 BLOCKED "test checker exited unexpectedly (rc=$evidence_rc; see .gauntlet/g2-test-evidence.log)" "$g2_temp"
            return
            ;;
    esac

    if [ ! -s "$profile_raw" ]; then
        finish_g2 BLOCKED "the exact test executable produced no raw coverage profile" "$g2_temp"
        return
    fi
    "$LLVM_PROFDATA_BIN" merge -sparse "$profile_raw" -o "$profile_data" \
        >>"$ART/g2.log" 2>&1
    merge_rc=$?
    if [ "$merge_rc" -ne 0 ] || [ ! -s "$profile_data" ]; then
        finish_g2 BLOCKED "llvm-profdata could not merge exact-binary coverage" "$g2_temp"
        return
    fi

    "$LLVM_COV_BIN" report "${test_binaries[0]}" \
        -instr-profile="$profile_data" \
        -ignore-filename-regex="$ignore_regex" \
        >"$ART/g2-coverage.txt" 2>>"$ART/g2.log"
    report_rc=$?
    "$LLVM_COV_BIN" export "${test_binaries[0]}" \
        -instr-profile="$profile_data" \
        -summary-only \
        -ignore-filename-regex="$ignore_regex" \
        >"$ART/g2-summary.json" 2>>"$ART/g2.log"
    export_rc=$?
    if [ "$report_rc" -ne 0 ] || [ "$export_rc" -ne 0 ]; then
        finish_g2 BLOCKED "llvm-cov could not produce coverage summaries (see .gauntlet/g2.log)" "$g2_temp"
        return
    fi

    coverage_values="$(python3 - "$ART/g2-summary.json" <<'PY' 2>>"$ART/g2.log" || true
import json
import math
import pathlib
import sys

lines = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["data"][0]["totals"]["lines"]
covered = lines["covered"]
count = lines["count"]
value = lines["percent"]
if type(covered) is not int or type(count) is not int or covered < 0 or count <= 0 or covered > count:
    raise ValueError("line coverage counts are invalid")
if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
    raise ValueError("line coverage percent is not finite")
if not math.isclose(value, covered * 100.0 / count, rel_tol=1e-9, abs_tol=1e-9):
    raise ValueError("line coverage percent disagrees with counts")
print(covered, count, repr(value), f"{value:.2f}")
PY
)"
    if [ -z "$coverage_values" ]; then
        finish_g2 BLOCKED "could not parse llvm-cov line coverage (see .gauntlet/g2-summary.json)" "$g2_temp"
        return
    fi
    read -r covered_lines total_lines raw_pct display_pct <<<"$coverage_values"
    if [ -z "$covered_lines" ] || [ -z "$total_lines" ] || [ -z "$raw_pct" ] || [ -z "$display_pct" ]; then
        finish_g2 BLOCKED "coverage summary did not contain all required line metrics" "$g2_temp"
        return
    fi
    if awk "BEGIN{exit !(($covered_lines * 100) >= (85 * $total_lines))}"; then
        finish_g2 PASS "tests green · line coverage ${display_pct}% (>=85; ${covered_lines}/${total_lines})" "$g2_temp"
    else
        finish_g2 FAIL "line coverage ${display_pct}% below 85 floor (${covered_lines}/${total_lines}; raw ${raw_pct}%)" "$g2_temp"
    fi
}

# ---- G3: protocol conformance ------------------------------------------------
g3() {
    section "G3 · protocol conformance (methods.json <-> golden fixtures)"
    local conformance_rc conformance_marker
    python3 -B -m unittest scripts.test_capture_golden \
        scripts.test_check_conformance \
        scripts.test_derive_protocol \
        >"$ART/g3-selftest.log" 2>&1
    if [ $? -ne 0 ]; then
        record G3 BLOCKED "conformance checker self-tests failed (see .gauntlet/g3-selftest.log)"
        return
    fi
    python3 -B scripts/check_conformance.py >"$ART/g3.log" 2>&1
    conformance_rc=$?
    conformance_marker="$(awk 'NF { marker=$0 } END { print marker }' "$ART/g3.log")"
    case "$conformance_rc|$conformance_marker" in
        "0|G3: PASS") record G3 PASS "$conformance_marker" ;;
        "1|G3: FAIL") record G3 FAIL "see .gauntlet/g3.log" ;;
        "2|G3: BLOCKED") record G3 BLOCKED "conformance evidence was indeterminate (see .gauntlet/g3.log)" ;;
        *) record G3 BLOCKED "checker status/marker evidence disagreed (rc=$conformance_rc; see .gauntlet/g3.log)" ;;
    esac
}

# ---- G4: lint -----------------------------------------------------------------
g4() {
    section "G4 · swift-format --lint + SwiftLint --strict"
    local arch prerequisite archive part actual format_rc lint_rc version_rc
    local report_rc violation_count stderr_state
    local swiftformat_bin="$TOOLS/swiftformat-$SWIFTFORMAT_VERSION"
    local swiftlint_bin="$TOOLS/swiftlint-$SWIFTLINT_VERSION"
    if ! python3 -B -m unittest \
        scripts.test_gauntlet_status.G4ClassificationTests \
        scripts.test_check_swiftlint_report \
        >"$ART/g4-wrapper-selftest.log" 2>&1; then
        record G4 BLOCKED "G4 result-classifier self-tests failed (see .gauntlet/g4-wrapper-selftest.log)"
        return
    fi
    mkdir -p "$TOOLS"
    : >"$ART/g4-onboard.log"

    arch="$(uname -m 2>/dev/null || true)"
    if [ "$arch" != "x86_64" ]; then
        record G4 BLOCKED "verified formatter assets require x86_64 Linux (found ${arch:-unknown})"
        return
    fi
    for prerequisite in curl sha256sum unzip; do
        if ! command -v "$prerequisite" >/dev/null 2>&1; then
            record G4 BLOCKED "$prerequisite is required for verified native tool onboarding"
            return
        fi
    done

    archive="$TOOLS/swiftformat-$SWIFTFORMAT_VERSION.zip"
    if [ ! -f "$archive" ]; then
        part="$archive.part"
        curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
            --output "$part" "$SWIFTFORMAT_URL" >>"$ART/g4-onboard.log" 2>&1 \
            || { record G4 BLOCKED "SwiftFormat download failed (see .gauntlet/g4-onboard.log)"; return; }
        printf '%s  %s\n' "$SWIFTFORMAT_SHA256" "$part" \
            | sha256sum -c - >>"$ART/g4-onboard.log" 2>&1 \
            || { record G4 BLOCKED "SwiftFormat checksum mismatch"; return; }
        mv "$part" "$archive"
    fi
    printf '%s  %s\n' "$SWIFTFORMAT_SHA256" "$archive" \
        | sha256sum -c - >>"$ART/g4-onboard.log" 2>&1 \
        || { record G4 BLOCKED "cached SwiftFormat checksum mismatch"; return; }
    unzip -p "$archive" swiftformat_linux >"$swiftformat_bin.part" 2>>"$ART/g4-onboard.log" \
        || { record G4 BLOCKED "SwiftFormat extraction failed"; return; }
    chmod +x "$swiftformat_bin.part"
    mv "$swiftformat_bin.part" "$swiftformat_bin"
    actual="$("$swiftformat_bin" --version 2>>"$ART/g4-onboard.log")"
    version_rc=$?
    if [ "$version_rc" -ne 0 ] || [ "$actual" != "$SWIFTFORMAT_VERSION" ]; then
        record G4 BLOCKED "expected SwiftFormat $SWIFTFORMAT_VERSION, found ${actual:-unknown}"
        return
    fi

    archive="$TOOLS/swiftlint-$SWIFTLINT_VERSION.zip"
    if [ ! -f "$archive" ]; then
        part="$archive.part"
        curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
            --output "$part" "$SWIFTLINT_URL" >>"$ART/g4-onboard.log" 2>&1 \
            || { record G4 BLOCKED "SwiftLint download failed (see .gauntlet/g4-onboard.log)"; return; }
        printf '%s  %s\n' "$SWIFTLINT_SHA256" "$part" \
            | sha256sum -c - >>"$ART/g4-onboard.log" 2>&1 \
            || { record G4 BLOCKED "SwiftLint checksum mismatch"; return; }
        mv "$part" "$archive"
    fi
    printf '%s  %s\n' "$SWIFTLINT_SHA256" "$archive" \
        | sha256sum -c - >>"$ART/g4-onboard.log" 2>&1 \
        || { record G4 BLOCKED "cached SwiftLint checksum mismatch"; return; }
    unzip -p "$archive" swiftlint >"$swiftlint_bin.part" 2>>"$ART/g4-onboard.log" \
        || { record G4 BLOCKED "SwiftLint extraction failed"; return; }
    chmod +x "$swiftlint_bin.part"
    mv "$swiftlint_bin.part" "$swiftlint_bin"
    actual="$(env \
        SWIFT_EXEC="$SWIFT_BIN" \
        LINUX_SOURCEKIT_LIB_PATH="$SOURCEKIT_LIB_DIR" \
        "$swiftlint_bin" version 2>>"$ART/g4-onboard.log")"
    version_rc=$?
    if [ "$version_rc" -ne 0 ] || [ "$actual" != "$SWIFTLINT_VERSION" ]; then
        record G4 BLOCKED "expected SwiftLint $SWIFTLINT_VERSION, found ${actual:-unknown}"
        return
    fi

    format_rc=""
    if ! {
        # The static Linux release binary can terminate with SIGSEGV in its
        # quiet execution path on otherwise healthy hosts (swiftlang/swift#77841).
        # Verbose mode preserves the same lint rules, inputs, and exit contract
        # while also leaving useful private evidence if the process fails.
        "$swiftformat_bin" --lint --verbose Packages/HermesKit App Tests
        format_rc=$?
    } >"$ART/g4-swiftformat.log" 2>&1; then
        record G4 BLOCKED "could not capture SwiftFormat evidence"
        return
    fi
    lint_rc=""
    if ! {
        env \
            SWIFT_EXEC="$SWIFT_BIN" \
            LINUX_SOURCEKIT_LIB_PATH="$SOURCEKIT_LIB_DIR" \
            "$swiftlint_bin" --strict --quiet --reporter json
        lint_rc=$?
    } >"$ART/g4-swiftlint.json" 2>"$ART/g4-swiftlint.stderr"; then
        record G4 BLOCKED "could not capture SwiftLint evidence"
        return
    fi
    if [ ! -f "$ART/g4-swiftlint.stderr" ]; then
        record G4 BLOCKED "SwiftLint stderr evidence was not created"
        return
    elif [ -s "$ART/g4-swiftlint.stderr" ]; then
        stderr_state="nonempty"
    else
        stderr_state="empty"
    fi
    violation_count="$(python3 -B scripts/check_swiftlint_report.py \
        "$ART/g4-swiftlint.json" 2>"$ART/g4-swiftlint-evidence.log")"
    report_rc=$?

    if ! {
        echo "SwiftFormat $SWIFTFORMAT_VERSION sha256:$SWIFTFORMAT_SHA256" \
            && echo "SwiftLint $SWIFTLINT_VERSION sha256:$SWIFTLINT_SHA256" \
            && echo "--- swiftformat --lint ---" \
            && cat "$ART/g4-swiftformat.log" \
            && echo "--- swiftlint --strict --reporter json ---" \
            && cat "$ART/g4-swiftlint.json" \
            && { [ ! -s "$ART/g4-swiftlint.stderr" ] \
                || { echo "--- swiftlint stderr ---" \
                    && cat "$ART/g4-swiftlint.stderr"; }; } \
            && { [ ! -s "$ART/g4-swiftlint-evidence.log" ] \
                || { echo "--- SwiftLint evidence checker ---" \
                    && cat "$ART/g4-swiftlint-evidence.log"; }; } \
            && echo "--- exit evidence ---" \
            && echo "SwiftFormat rc=$format_rc" \
            && echo "SwiftLint rc=$lint_rc; report rc=$report_rc; findings=${violation_count:-unknown}; stderr=$stderr_state"
    } >"$ART/g4.log" 2>&1; then
        record G4 BLOCKED "could not assemble G4 evidence (see .gauntlet/g4.log)"
        return
    fi
    talaria_classify_g4 \
        "$format_rc" "$lint_rc" "$report_rc" "$violation_count" "$stderr_state"
    record G4 "$TALARIA_CLASS_STATUS" "$TALARIA_CLASS_DETAIL"
}

# ---- G5: secrets + host hygiene ------------------------------------------------
g5() {
    section "G5 · secret scan + hardcoded-host grep"
    local prerequisite archive part actual version_rc history_rc worktree_rc literal_rc
    local shallow_rc shallow_state shallow_stderr_state
    local gl_bin="$TOOLS/gitleaks-$GITLEAKS_VERSION"
    if ! python3 -B -m unittest \
        scripts.test_gauntlet_status.G5ClassificationTests \
        scripts.test_gitleaks_canary \
        >"$ART/g5-wrapper-selftest.log" 2>&1; then
        record G5 BLOCKED "G5 result-classifier self-tests failed (see .gauntlet/g5-wrapper-selftest.log)"
        return
    fi
    : >"$ART/g5-history-shape.stderr"
    shallow_rc=0
    shallow_state="$(git rev-parse --is-shallow-repository \
        2>"$ART/g5-history-shape.stderr")" || shallow_rc=$?
    shallow_stderr_state="empty"
    if [ -s "$ART/g5-history-shape.stderr" ]; then
        shallow_stderr_state="nonempty"
    fi
    talaria_classify_complete_git_history \
        "$shallow_rc" "$shallow_state" "$shallow_stderr_state"
    if [ "$TALARIA_CLASS_STATUS" != "PASS" ]; then
        record G5 BLOCKED "$TALARIA_CLASS_DETAIL"
        return
    fi
    printf 'git rev-parse --is-shallow-repository: %s\n' "$shallow_state" \
        >"$ART/g5-history-shape.log"
    mkdir -p "$TOOLS"
    : >"$ART/g5-onboard.log"
    for prerequisite in curl sha256sum tar; do
        if ! command -v "$prerequisite" >/dev/null 2>&1; then
            record G5 BLOCKED "$prerequisite is required for verified gitleaks onboarding"
            return
        fi
    done
    archive="$TOOLS/gitleaks-$GITLEAKS_VERSION.tar.gz"
    if [ ! -f "$archive" ]; then
        part="$archive.part"
        curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
            --output "$part" "$GITLEAKS_URL" >>"$ART/g5-onboard.log" 2>&1 \
            || { record G5 BLOCKED "gitleaks download failed (see .gauntlet/g5-onboard.log)"; return; }
        printf '%s  %s\n' "$GITLEAKS_SHA256" "$part" \
            | sha256sum -c - >>"$ART/g5-onboard.log" 2>&1 \
            || { record G5 BLOCKED "gitleaks checksum mismatch"; return; }
        mv "$part" "$archive"
    fi
    printf '%s  %s\n' "$GITLEAKS_SHA256" "$archive" \
        | sha256sum -c - >>"$ART/g5-onboard.log" 2>&1 \
        || { record G5 BLOCKED "cached gitleaks checksum mismatch"; return; }
    tar -xOzf "$archive" gitleaks >"$gl_bin.part" 2>>"$ART/g5-onboard.log" \
        || { record G5 BLOCKED "gitleaks extraction failed"; return; }
    chmod +x "$gl_bin.part"
    mv "$gl_bin.part" "$gl_bin"
    actual="$("$gl_bin" version 2>>"$ART/g5-onboard.log")"
    version_rc=$?
    actual="${actual#v}"
    if [ "$version_rc" -ne 0 ] || [ "$actual" != "$GITLEAKS_VERSION" ]; then
        record G5 BLOCKED "expected gitleaks $GITLEAKS_VERSION, found ${actual:-unknown}"
        return
    fi
    if ! bash scripts/check_gitleaks_canary.sh \
        "$gl_bin" "$TALARIA_GITLEAKS_FINDINGS_RC" \
        >"$ART/g5-canary.log" 2>&1; then
        record G5 BLOCKED "pinned gitleaks failed its offline functional canary (see .gauntlet/g5-canary.log)"
        return
    fi

    if "$gl_bin" git --redact -v \
        --exit-code "$TALARIA_GITLEAKS_FINDINGS_RC" . >"$ART/g5-history.log" 2>&1; then
        history_rc=0
    else
        history_rc=$?
    fi
    if "$gl_bin" dir --redact -v \
        --exit-code "$TALARIA_GITLEAKS_FINDINGS_RC" . >"$ART/g5-worktree.log" 2>&1; then
        worktree_rc=0
    else
        worktree_rc=$?
    fi
    if ! python3 -B -m unittest scripts.test_check_source_hygiene \
        >"$ART/g5-selftest.log" 2>&1; then
        record G5 BLOCKED "source-literal checker self-tests failed (see .gauntlet/g5-selftest.log)"
        return
    fi
    if python3 -B scripts/check_source_hygiene.py >"$ART/g5-literals.log" 2>&1; then
        literal_rc=0
    else
        literal_rc=$?
    fi
    if ! talaria_assemble_g5_evidence \
        "$ART/g5.log" \
        "$GITLEAKS_VERSION" \
        "$GITLEAKS_SHA256" \
        "$TALARIA_GITLEAKS_FINDINGS_RC" \
        "$ART/g5-canary.log" \
        "$ART/g5-history.log" \
        "$ART/g5-worktree.log" \
        "$ART/g5-literals.log"; then
        record G5 BLOCKED "could not assemble G5 evidence (see .gauntlet/g5.log)"
        return
    fi
    talaria_classify_g5 "$history_rc" "$worktree_rc" "$literal_rc"
    record G5 "$TALARIA_CLASS_STATUS" "$TALARIA_CLASS_DETAIL"
}

# ---- G6: XcodeGen determinism ---------------------------------------------------
g6() {
    section "G6 · xcodegen generate determinism"
    python3 -B -m unittest scripts.test_check_xcodegen_determinism \
        >"$ART/g6-selftest.log" 2>&1
    if [ $? -ne 0 ]; then
        record G6 BLOCKED "determinism checker self-tests failed (see .gauntlet/g6-selftest.log)"
        return
    fi
    # XcodeGen publishes a macOS executable, not a pinned Linux artifact. Do
    # not trust an arbitrary PATH executable on WSL based on self-reported
    # version text. Tier B installs the digest-verified official 2.46.0 asset.
    if [ "$RUN_TIER_B" -eq 1 ]; then
        record "G6*" "DEFER->B" "Tier B installs and verifies the official XcodeGen $XCODEGEN_VERSION asset"
    else
        record G6 BLOCKED "no verified Linux XcodeGen artifact; rerun through PowerShell with -TierB for authoritative G6"
    fi
}

# ---- Tier B dispatch -------------------------------------------------------------
tier_b() {
    section "Tier B · dispatching macos-26 workflow"
    local branch branch_rc origin_url origin_url_rc correlation_token token_rc expected_title
    local run_record run_id list_rc watch_rc attempt
    local head_sha head_rc remote_record remote_rc dirty_state dirty_rc
    local final_conclusion evidence_rc evidence_marker snapshot_marker snapshot_rc
    local ios_job_id watchos_job_id archive_job_id
    local ios_conclusion watchos_conclusion archive_conclusion
    local evidence_attempt evidence_ready=0 logs_ready
    local -a evidence_lines=()
    GH_RUN_BIN="$(type -P gh 2>/dev/null || true)"
    GH_LOG_BIN="${TALARIA_GH_LOG_BIN:-}"
    talaria_classify_tier_b_client_families "$GH_RUN_BIN" "$GH_LOG_BIN"
    if [ "$TIER_B_CLIENT_STATUS" != "PASS" ]; then
        record "B*" BLOCKED "$TIER_B_CLIENT_DETAIL"
        return
    fi
    if ! verify_tier_b_cli_version "$GH_RUN_BIN" "$EXPECTED_GH_RUN_LINE" run; then
        record "B*" BLOCKED "expected $EXPECTED_GH_RUN_LINE for Tier B run operations"
        return
    fi
    if ! verify_tier_b_cli_version "$GH_LOG_BIN" "$EXPECTED_GH_LOG_LINE" log; then
        record "B*" BLOCKED "expected $EXPECTED_GH_LOG_LINE for Tier B job logs"
        return
    fi
    if ! python3 -B -m unittest \
        scripts.test_gauntlet_status.TierBSourceBindingTests \
        scripts.test_check_tier_b_run_snapshot \
        scripts.test_check_tier_b_status_log \
        >"$ART/tierb-binding-selftest.log" 2>&1; then
        record "B*" BLOCKED "Tier B source-binding self-tests failed (see .gauntlet/tierb-binding-selftest.log)"
        return
    fi
    branch="$(git symbolic-ref --quiet --short HEAD 2>>"$ART/tierb-dispatch.log")"
    branch_rc=$?
    talaria_classify_tier_b_branch "$branch_rc" "$branch"
    if [ "$TALARIA_CLASS_STATUS" != "PASS" ]; then
        record "B*" BLOCKED "$TALARIA_CLASS_DETAIL"
        return
    fi
    origin_url="$(git remote get-url origin 2>>"$ART/tierb-dispatch.log")"
    origin_url_rc=$?
    talaria_classify_tier_b_repository \
        "$origin_url_rc" "$origin_url" "$TIER_B_REPOSITORY"
    if [ "$TALARIA_CLASS_STATUS" != "PASS" ]; then
        record "B*" BLOCKED "$TALARIA_CLASS_DETAIL"
        return
    fi
    dirty_state="$(git status --porcelain=v1 --untracked-files=all 2>>"$ART/tierb-dispatch.log")"
    dirty_rc=$?
    talaria_classify_tier_b_clean_tree "$dirty_rc" "$dirty_state"
    if [ "$TALARIA_CLASS_STATUS" != "PASS" ]; then
        record "B*" BLOCKED "$TALARIA_CLASS_DETAIL"
        return
    fi
    head_sha="$(git rev-parse --verify 'HEAD^{commit}' 2>>"$ART/tierb-dispatch.log")"
    head_rc=$?
    talaria_classify_tier_b_local_sha "$head_rc" "$head_sha"
    if [ "$TALARIA_CLASS_STATUS" != "PASS" ]; then
        record "B*" BLOCKED "$TALARIA_CLASS_DETAIL"
        return
    fi
    remote_record="$(git ls-remote --exit-code origin "refs/heads/$branch" \
        2>>"$ART/tierb-dispatch.log")"
    remote_rc=$?
    talaria_classify_tier_b_remote "$remote_rc" "$remote_record" "$branch" "$head_sha"
    if [ "$TALARIA_CLASS_STATUS" != "PASS" ]; then
        record "B*" BLOCKED "$TALARIA_CLASS_DETAIL"
        return
    fi
    correlation_token="$(python3 -c \
        'import secrets; print("talaria-" + secrets.token_hex(16))' \
        2>>"$ART/tierb-dispatch.log")"
    token_rc=$?
    talaria_classify_tier_b_correlation "$token_rc" "$correlation_token"
    if [ "$TALARIA_CLASS_STATUS" != "PASS" ]; then
        record "B*" BLOCKED "$TALARIA_CLASS_DETAIL"
        return
    fi
    expected_title="$TALARIA_CLASS_VALUE2"
    tier_b_run_gh workflow run tier-b.yml --repo "$TIER_B_REPOSITORY" --ref "$branch" \
        --field "correlation_token=$correlation_token" \
        >"$ART/tierb-dispatch.log" 2>&1 \
        || { record "B*" BLOCKED "workflow dispatch failed (see .gauntlet/tierb-dispatch.log)"; return; }
    run_id=""
    for attempt in $(seq 1 20); do
        run_record="$(tier_b_run_gh run list \
            --repo "$TIER_B_REPOSITORY" \
            --workflow tier-b.yml \
            --branch "$branch" \
            --event workflow_dispatch \
            --limit 100 \
            --json databaseId,headSha,displayTitle \
            --jq '.[] | "\(.databaseId)|\(.headSha)|\(.displayTitle)"' \
            2>>"$ART/tierb-dispatch.log")"
        list_rc=$?
        run_record="${run_record//$'\r'/}"
        talaria_classify_tier_b_run_selection \
            "$list_rc" "$run_record" "$head_sha" "$expected_title"
        case "$TALARIA_CLASS_STATUS" in
            PASS)
                run_id="$TALARIA_CLASS_VALUE"
                break
                ;;
            WAIT) sleep 2 ;;
            *)
                record "B*" BLOCKED "$TALARIA_CLASS_DETAIL"
                return
                ;;
        esac
    done
    if [ -z "$run_id" ]; then
        record "B*" BLOCKED "dispatch succeeded but no matching Tier B run appeared"
        return
    fi
    echo "following run $run_id ..."
    tier_b_run_gh run watch "$run_id" --repo "$TIER_B_REPOSITORY" \
        --exit-status >"$ART/tierb-watch.log" 2>&1
    watch_rc=$?
    if ! capture_tier_b_snapshot \
        "$run_id" "$head_sha" "$expected_title" "$watch_rc" "" pre; then
        record "B*" BLOCKED "complete source-bound Tier B run and job evidence did not become available"
        return
    fi
    snapshot_marker="$TIER_B_SNAPSHOT_MARKER"
    ios_job_id="$TIER_B_IOS_JOB_ID"
    ios_conclusion="$TIER_B_IOS_CONCLUSION"
    watchos_job_id="$TIER_B_WATCHOS_JOB_ID"
    watchos_conclusion="$TIER_B_WATCHOS_CONCLUSION"
    archive_job_id="$TIER_B_ARCHIVE_JOB_ID"
    archive_conclusion="$TIER_B_ARCHIVE_CONCLUSION"
    final_conclusion="$TIER_B_RUN_CONCLUSION"

    # A combined log can omit a completed job for minutes. Fetch the three
    # validated job IDs independently and require each file to contain exactly
    # its own correlation-bound final record. Every retry replaces all evidence.
    for evidence_attempt in $(seq 1 12); do
        logs_ready=1
        fetch_tier_b_job_log "$ios_job_id" ios || logs_ready=0
        fetch_tier_b_job_log "$watchos_job_id" watchos || logs_ready=0
        fetch_tier_b_job_log "$archive_job_id" archive || logs_ready=0
        if [ "$logs_ready" -eq 1 ]; then
            python3 -B scripts/check_tier_b_status_log.py \
                --ios-log "$ART/tierb-ios.log" \
                --ios-conclusion "$ios_conclusion" \
                --watchos-log "$ART/tierb-watchos.log" \
                --watchos-conclusion "$watchos_conclusion" \
                --archive-log "$ART/tierb-archive.log" \
                --archive-conclusion "$archive_conclusion" \
                --correlation "$correlation_token" \
                --conclusion "$final_conclusion" \
                >"$ART/tierb-evidence.log" 2>&1
            evidence_rc=$?
            evidence_lines=()
            if mapfile -t evidence_lines <"$ART/tierb-evidence.log" \
                && [ "${#evidence_lines[@]}" -eq 1 ]; then
                evidence_marker="${evidence_lines[0]}"
                case "$evidence_rc:$evidence_marker" in
                    "0:TIER B EVIDENCE PASS: all three jobs reported PASS and the workflow run succeeded"|\
                    "2:TIER B EVIDENCE BLOCKED: at least one job reported BLOCKED and the workflow run failed")
                        evidence_ready=1
                        break
                        ;;
                esac
            fi
        fi
        if [ "$evidence_attempt" -lt 12 ]; then
            sleep 5
        fi
    done
    if [ "$evidence_ready" -ne 1 ]; then
        record "B*" BLOCKED "complete source-bound per-job Tier B logs did not become available within the bounded retry window"
        return
    fi
    # Re-fetch and revalidate the full attempt-1 snapshot after log acquisition.
    # The exact marker must be unchanged, so a job/run drift cannot be mixed
    # with previously downloaded logs.
    capture_tier_b_snapshot \
        "$run_id" "$head_sha" "$expected_title" "$watch_rc" \
        "$snapshot_marker" post
    snapshot_rc=$?
    if [ "$snapshot_rc" -ne 0 ]; then
        record "B*" BLOCKED "Tier B run or job evidence changed during log acquisition"
        return
    fi
    case "$evidence_rc:$evidence_marker" in
        "0:TIER B EVIDENCE PASS: all three jobs reported PASS and the workflow run succeeded")
            if resolve_tier_b_g6 "$run_id"; then
                record "TierB" PASS "run https://github.com/Phasmotic/Ipsima/actions/runs/$run_id"
            else
                record "B*" BLOCKED "successful Tier B run could not resolve exactly one deferred G6 row"
            fi
            ;;
        "2:TIER B EVIDENCE BLOCKED: at least one job reported BLOCKED and the workflow run failed")
            record "TierB" BLOCKED "at least one job reported BLOCKED: https://github.com/Phasmotic/Ipsima/actions/runs/$run_id"
            ;;
        *)
            record "B*" BLOCKED "Tier B evidence checker status and marker disagreed"
            ;;
    esac
}

# ---- main -------------------------------------------------------------------------
g1; g2; g3; g4; g5

if [ "$RUN_GITHUB_PR" -eq 0 ]; then
    g6
fi

if [ "$RUN_TIER_B" -eq 1 ]; then
    if [ "$FAILED" -eq 1 ]; then
        echo "Tier A is honestly red; Tier B is diagnostic and cannot make the gauntlet green."
    fi
    tier_b
fi

printf '\n==================== GAUNTLET · RESULTS ====================\n\n'
printf '%-6s %-10s %s\n' "GATE" "STATUS" "DETAIL"
printf '%.0s-' {1..100}; echo
for row in "${RESULTS[@]}"; do
    IFS='|' read -r gate status detail <<<"$row"
    printf '%-6s %-10s %s\n' "$gate" "$status" "$detail"
done
echo
printf '%.0s-' {1..100}; echo
if [ "$RUN_GITHUB_PR" -eq 1 ]; then
    talaria_classify_g1_g5_inventory "${RESULTS[@]}"
    if [ "$FAILED" -eq 0 ] && [ "$TALARIA_CLASS_STATUS" = "PASS" ]; then
        echo "G1–G5 GREEN"
        exit 0
    fi
    echo "G1–G5 RED — failures remain visible."
    exit 1
elif [ "$FAILED" -eq 0 ]; then
    if [ "$RUN_TIER_B" -eq 1 ]; then
        echo "GAUNTLET GREEN"
    else
        echo "TIER A GREEN"
    fi
else
    echo "GAUNTLET RED — failures remain visible."
fi
exit $FAILED
