#!/usr/bin/env bash
#
# Talaria gauntlet — Tier A (local, native Linux tools in WSL Ubuntu).
#
# Supported entry path (PowerShell only):
#   pwsh -File scripts/gauntlet.ps1             run every Tier A gate
#   pwsh -File scripts/gauntlet.ps1 -TierB      additionally dispatch + follow macOS CI
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
TIER_B_REPOSITORY="markschonfeld/Talaria"
KIT="Packages/HermesKit"
TOOLS="$ART/tools"

blocked_preflight() {
    printf 'GAUNTLET BLOCKED — %s\n' "$1" >&2
    exit 1
}

RUN_TIER_B=0
if [ "$#" -gt 1 ]; then
    blocked_preflight "expected at most one gauntlet argument"
fi
case "${1:-}" in
    "") ;;
    --tier-b) RUN_TIER_B=1 ;;
    *) blocked_preflight "unknown gauntlet argument: $1" ;;
esac

STATUS_HELPERS="$ROOT/scripts/gauntlet_status.sh"
[ -r "$STATUS_HELPERS" ] \
    || blocked_preflight "gate status classifiers are unavailable"
# shellcheck source=gauntlet_status.sh
source "$STATUS_HELPERS" \
    || blocked_preflight "gate status classifiers could not be loaded"

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

# Swiftly installs user-local proxies. Add only its documented bin directory;
# never inherit a Windows Swift executable through WSL interop.
if [ -d "$HOME/.local/share/swiftly/bin" ]; then
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

section() { printf '\n=== %s ===\n' "$1"; }

# ---- G1: swift build debug + release, zero warnings -------------------------
g1() {
    section "G1 · swift build (debug + release, warnings-as-errors)"
    local debug_rc release_rc warning_rc
    python3 -B -m unittest scripts.test_check_xcode_log \
        >"$ART/g1-warning-selftest.log" 2>&1
    if [ $? -ne 0 ]; then
        record G1 BLOCKED "warning-log checker self-tests failed (see .gauntlet/g1-warning-selftest.log)"
        return
    fi
    {
        printf '%s\n' "$SWIFT_VERSION_LINE"
        echo "native WSL toolchain: verified"
        echo "PowerShell launcher namespace marker: verified"
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
        record G2 BLOCKED "could not create a native WSL coverage scratch directory"
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
    python3 -B -m unittest scripts.test_check_conformance \
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
        "$swiftformat_bin" --lint --quiet Packages/HermesKit App Tests
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
    local gl_bin="$TOOLS/gitleaks-$GITLEAKS_VERSION"
    if ! python3 -B -m unittest \
        scripts.test_gauntlet_status.G5ClassificationTests \
        scripts.test_gitleaks_canary \
        >"$ART/g5-wrapper-selftest.log" 2>&1; then
        record G5 BLOCKED "G5 result-classifier self-tests failed (see .gauntlet/g5-wrapper-selftest.log)"
        return
    fi
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
    local head_sha head_rc remote_record remote_rc dirty_state dirty_rc final_record view_rc
    local final_conclusion logs_rc evidence_rc evidence_marker
    local -a evidence_lines=()
    if ! python3 -B -m unittest \
        scripts.test_gauntlet_status.TierBSourceBindingTests \
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
    gh workflow run tier-b.yml --repo "$TIER_B_REPOSITORY" --ref "$branch" \
        --field "correlation_token=$correlation_token" \
        >"$ART/tierb-dispatch.log" 2>&1 \
        || { record "B*" BLOCKED "workflow dispatch failed (see .gauntlet/tierb-dispatch.log)"; return; }
    run_id=""
    for attempt in $(seq 1 20); do
        run_record="$(gh run list \
            --repo "$TIER_B_REPOSITORY" \
            --workflow tier-b.yml \
            --branch "$branch" \
            --event workflow_dispatch \
            --limit 100 \
            --json databaseId,headSha,displayTitle \
            --jq '.[] | "\(.databaseId)|\(.headSha)|\(.displayTitle)"' \
            2>>"$ART/tierb-dispatch.log")"
        list_rc=$?
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
    gh run watch "$run_id" --repo "$TIER_B_REPOSITORY" \
        --exit-status >"$ART/tierb-watch.log" 2>&1
    watch_rc=$?
    final_record="$(gh run view "$run_id" \
        --repo "$TIER_B_REPOSITORY" \
        --json status,conclusion,headSha,displayTitle \
        --jq '"\(.status)|\(.conclusion // "")|\(.headSha)|\(.displayTitle)"' \
        2>>"$ART/tierb-watch.log")"
    view_rc=$?
    talaria_classify_tier_b_final \
        "$view_rc" "$final_record" "$head_sha" "$expected_title" "$watch_rc"
    if [ "$TALARIA_CLASS_STATUS" != "READY" ]; then
        record "B*" BLOCKED "$TALARIA_CLASS_DETAIL"
        return
    fi
    final_conclusion="$TALARIA_CLASS_VALUE2"

    # GitHub's run conclusion collapses exit 1 (FAIL) and exit 2 (BLOCKED)
    # into the same `failure` value. Fetch the exact source-matched run's logs
    # and require one correlation-bound status record from every Tier B job.
    : >"$ART/tierb-full.log"
    : >"$ART/tierb-full.stderr"
    gh run view "$run_id" --repo "$TIER_B_REPOSITORY" --log \
        >"$ART/tierb-full.log" 2>"$ART/tierb-full.stderr"
    logs_rc=$?
    if [ "$logs_rc" -ne 0 ] \
        || [ ! -s "$ART/tierb-full.log" ] \
        || [ -s "$ART/tierb-full.stderr" ]; then
        record "B*" BLOCKED "could not retrieve complete source-bound Tier B job evidence"
        return
    fi
    python3 -B scripts/check_tier_b_status_log.py \
        --log "$ART/tierb-full.log" \
        --correlation "$correlation_token" \
        --conclusion "$final_conclusion" \
        >"$ART/tierb-evidence.log" 2>&1
    evidence_rc=$?
    if ! mapfile -t evidence_lines <"$ART/tierb-evidence.log"; then
        record "B*" BLOCKED "could not read the Tier B evidence verdict"
        return
    fi
    if [ "${#evidence_lines[@]}" -ne 1 ]; then
        record "B*" BLOCKED "Tier B evidence checker emitted an invalid verdict shape"
        return
    fi
    evidence_marker="${evidence_lines[0]}"
    case "$evidence_rc:$evidence_marker" in
        "0:TIER B EVIDENCE PASS: "*)
            if resolve_tier_b_g6 "$run_id"; then
                record "TierB" PASS "run https://github.com/markschonfeld/Talaria/actions/runs/$run_id"
            else
                record "B*" BLOCKED "successful Tier B run could not resolve exactly one deferred G6 row"
            fi
            ;;
        "1:TIER B EVIDENCE FAIL: "*)
            record "TierB" FAIL "run failed: https://github.com/markschonfeld/Talaria/actions/runs/$run_id (gh run view --log-failed)"
            ;;
        "2:TIER B EVIDENCE BLOCKED: "*)
            record "TierB" BLOCKED "run evidence is incomplete or indeterminate: https://github.com/markschonfeld/Talaria/actions/runs/$run_id"
            ;;
        *)
            record "B*" BLOCKED "Tier B evidence checker status and marker disagreed"
            ;;
    esac
}

# ---- main -------------------------------------------------------------------------
g1; g2; g3; g4; g5; g6

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
if [ "$FAILED" -eq 0 ]; then
    if [ "$RUN_TIER_B" -eq 1 ]; then
        echo "GAUNTLET GREEN"
    else
        echo "TIER A GREEN"
    fi
else
    echo "GAUNTLET RED — failures remain visible."
fi
exit $FAILED
