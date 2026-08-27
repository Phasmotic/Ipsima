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
KIT="Packages/HermesKit"
TOOLS="$ART/tools"

blocked_preflight() {
    printf 'GAUNTLET BLOCKED — %s\n' "$1" >&2
    exit 1
}

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
SOURCEKIT_LIB_DIR="$TOOLCHAIN_USR/lib"
[ -x "$LLVM_COV_BIN" ] \
    || blocked_preflight "the pinned Swift toolchain does not provide llvm-cov"
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
    local index gate status detail
    for index in "${!RESULTS[@]}"; do
        IFS='|' read -r gate status detail <<<"${RESULTS[$index]}"
        if [ "$gate" = "G6*" ] && [ "$status" = "DEFER->B" ]; then
            RESULTS[$index]="G6|PASS|authoritative two-generation hash check passed in Tier B run $1"
            return
        fi
    done
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
    local g2_temp g2_scratch test_rc warning_rc report_rc export_rc coverage_values
    local covered_lines total_lines raw_pct display_pct
    local ignore_regex='(^|/)(Tests|\.build|\.gauntlet)(/|$)'
    local -a profdata_files test_binaries

    g2_temp="$(mktemp -d /tmp/talaria-hermeskit-swift-6.3.3.XXXXXX 2>/dev/null || true)"
    g2_scratch="$g2_temp/.build"
    if [ -z "$g2_temp" ] || [ ! -d "$g2_temp" ] || ! mkdir -p "$g2_scratch"; then
        g2_cleanup_after_setup_failure "$g2_temp"
        record G2 BLOCKED "could not create a native WSL coverage scratch directory"
        return
    fi

    swift test \
        --package-path "$KIT" \
        --scratch-path "$g2_scratch" \
        -c debug \
        -Xswiftc -warnings-as-errors \
        --enable-code-coverage \
        >"$ART/g2.log" 2>&1
    test_rc=$?
    if [ "$test_rc" -ne 0 ]; then
        finish_g2 FAIL "test failure under SwiftPM coverage instrumentation (see .gauntlet/g2.log)" "$g2_temp"
        return
    fi
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

    mapfile -t profdata_files < <(
        find "$g2_scratch" -type f -path '*/debug/codecov/*.profdata' -print 2>/dev/null
    )
    mapfile -t test_binaries < <(
        find "$g2_scratch" -type f -name '*.xctest' -perm -u+x -print 2>/dev/null
    )
    if [ "${#profdata_files[@]}" -ne 1 ] || [ "${#test_binaries[@]}" -ne 1 ]; then
        printf 'expected one coverage profile and one test executable; found %s and %s\n' \
            "${#profdata_files[@]}" "${#test_binaries[@]}" >>"$ART/g2.log"
        finish_g2 BLOCKED "ambiguous SwiftPM coverage artifacts (see .gauntlet/g2.log)" "$g2_temp"
        return
    fi

    "$LLVM_COV_BIN" report "${test_binaries[0]}" \
        -instr-profile="${profdata_files[0]}" \
        -ignore-filename-regex="$ignore_regex" \
        >"$ART/g2-coverage.txt" 2>>"$ART/g2.log"
    report_rc=$?
    "$LLVM_COV_BIN" export "${test_binaries[0]}" \
        -instr-profile="${profdata_files[0]}" \
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
    python3 -B -m unittest scripts.test_check_conformance \
        >"$ART/g3-selftest.log" 2>&1
    if [ $? -ne 0 ]; then
        record G3 BLOCKED "conformance checker self-tests failed (see .gauntlet/g3-selftest.log)"
        return
    fi
    python3 -B scripts/check_conformance.py >"$ART/g3.log" 2>&1
    if [ $? -eq 0 ]; then
        record G3 PASS "$(tail -1 "$ART/g3.log")"
    else
        record G3 FAIL "see .gauntlet/g3.log"
    fi
}

# ---- G4: lint -----------------------------------------------------------------
g4() {
    section "G4 · swift-format --lint + SwiftLint --strict"
    local arch prerequisite archive part actual format_rc lint_rc version_rc
    local swiftformat_bin="$TOOLS/swiftformat-$SWIFTFORMAT_VERSION"
    local swiftlint_bin="$TOOLS/swiftlint-$SWIFTLINT_VERSION"
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

    {
        echo "SwiftFormat $SWIFTFORMAT_VERSION sha256:$SWIFTFORMAT_SHA256"
        echo "SwiftLint $SWIFTLINT_VERSION sha256:$SWIFTLINT_SHA256"
        echo "--- swiftformat --lint ---"
        "$swiftformat_bin" --lint --quiet Packages/HermesKit App Tests 2>&1
        format_rc=$?
        echo "--- swiftlint --strict ---"
        env \
            SWIFT_EXEC="$SWIFT_BIN" \
            LINUX_SOURCEKIT_LIB_PATH="$SOURCEKIT_LIB_DIR" \
            "$swiftlint_bin" --strict --quiet 2>&1
        lint_rc=$?
    } >"$ART/g4.log" 2>&1
    if [ "$format_rc" -ne 0 ] || [ "$lint_rc" -ne 0 ]; then
        record G4 FAIL "lint violations (see .gauntlet/g4.log)"
    else
        record G4 PASS "zero violations (SwiftFormat $SWIFTFORMAT_VERSION + SwiftLint $SWIFTLINT_VERSION)"
    fi
}

# ---- G5: secrets + host hygiene ------------------------------------------------
g5() {
    section "G5 · secret scan + hardcoded-host grep"
    local prerequisite archive part actual version_rc history_rc worktree_rc literal_rc
    local gl_bin="$TOOLS/gitleaks-$GITLEAKS_VERSION"
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

    "$gl_bin" detect --source . --redact -v >"$ART/g5-history.log" 2>&1
    history_rc=$?
    "$gl_bin" detect --source . --no-git --redact -v >"$ART/g5-worktree.log" 2>&1
    worktree_rc=$?
    python3 -B -m unittest scripts.test_check_source_hygiene \
        >"$ART/g5-selftest.log" 2>&1
    if [ $? -ne 0 ]; then
        record G5 BLOCKED "source-literal checker self-tests failed (see .gauntlet/g5-selftest.log)"
        return
    fi
    python3 -B scripts/check_source_hygiene.py >"$ART/g5-literals.log" 2>&1
    literal_rc=$?
    if [ "$literal_rc" -gt 1 ]; then
        record G5 BLOCKED "source-literal scan was indeterminate (see .gauntlet/g5-literals.log)"
        return
    fi
    {
        echo "gitleaks $GITLEAKS_VERSION sha256:$GITLEAKS_SHA256"
        echo "--- gitleaks git history ---"
        cat "$ART/g5-history.log"
        echo "--- gitleaks working tree ---"
        cat "$ART/g5-worktree.log"
        echo "--- hardcoded hosts / credential-shaped literals ---"
        cat "$ART/g5-literals.log"
    } >"$ART/g5.log" 2>&1
    if [ "$history_rc" -gt 1 ] || [ "$worktree_rc" -gt 1 ]; then
        record G5 BLOCKED "gitleaks execution failed (history rc=$history_rc; worktree rc=$worktree_rc)"
    elif [ "$history_rc" -eq 1 ] || [ "$worktree_rc" -eq 1 ]; then
        record G5 FAIL "secret findings (redacted) in .gauntlet/g5.log"
    elif [ "$literal_rc" -eq 1 ]; then
        record G5 FAIL "hardcoded host/token-shaped literal (see .gauntlet/g5.log)"
    else
        record G5 PASS "no secrets in history or working tree; no hardcoded hosts"
    fi
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
    if command -v xcodegen >/dev/null 2>&1; then
        local actual rc
        : >"$ART/g6.log"
        if ! actual="$(xcodegen --version 2>>"$ART/g6.log")"; then
            record G6 BLOCKED "XcodeGen could not report its version"
            return
        fi
        case "$actual" in
            "$XCODEGEN_VERSION"|"Version: $XCODEGEN_VERSION") ;;
            *)
                record G6 BLOCKED "expected XcodeGen $XCODEGEN_VERSION, found ${actual:-unknown}"
                return
                ;;
        esac
        python3 -B scripts/check_xcodegen_determinism.py \
            --xcodegen "$(command -v xcodegen)" >"$ART/g6.log" 2>&1
        rc=$?
        case "$rc" in
            0) record G6 PASS "two independent generations produced the same recursive hash" ;;
            1) record G6 FAIL "generation failed or output differed (see .gauntlet/g6.log)" ;;
            *) record G6 BLOCKED "determinism check was indeterminate rc=$rc (see .gauntlet/g6.log)" ;;
        esac
    else
        record "G6*" "DEFER->B" "XcodeGen is unavailable on Linux; Tier B installs and verifies 2.46.0"
    fi
}

# ---- Tier B dispatch -------------------------------------------------------------
tier_b() {
    section "Tier B · dispatching macos-26 workflow"
    local branch dispatch_started run_record run_id run_sha list_rc watch_rc attempt
    local head_sha remote_record remote_sha dirty_state final_record view_rc
    local final_status final_conclusion final_sha
    branch="$(git rev-parse --abbrev-ref HEAD)"
    if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
        record "B*" BLOCKED "Tier B dispatch requires a named branch"
        return
    fi
    dirty_state="$(git status --porcelain=v1 --untracked-files=all 2>>"$ART/tierb-dispatch.log")"
    if [ $? -ne 0 ]; then
        record "B*" BLOCKED "could not verify the Tier B source tree state"
        return
    fi
    if [ -n "$dirty_state" ]; then
        record "B*" BLOCKED "Tier B requires a clean committed source tree"
        return
    fi
    head_sha="$(git rev-parse HEAD 2>>"$ART/tierb-dispatch.log")"
    if [ -z "$head_sha" ]; then
        record "B*" BLOCKED "could not resolve the local checkpoint commit"
        return
    fi
    remote_record="$(git ls-remote --exit-code origin "refs/heads/$branch" \
        2>>"$ART/tierb-dispatch.log")"
    if [ $? -ne 0 ] || [ -z "$remote_record" ]; then
        record "B*" BLOCKED "the checkpoint branch is not published on origin"
        return
    fi
    read -r remote_sha _ <<<"$remote_record"
    if [ "$remote_sha" != "$head_sha" ]; then
        record "B*" BLOCKED "origin does not point to the local checkpoint commit"
        return
    fi
    dispatch_started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    gh workflow run tier-b.yml --ref "$branch" \
        >"$ART/tierb-dispatch.log" 2>&1 \
        || { record "B*" BLOCKED "workflow dispatch failed (see .gauntlet/tierb-dispatch.log)"; return; }
    run_id=""
    run_sha=""
    for attempt in $(seq 1 20); do
        run_record="$(gh run list \
            --workflow tier-b.yml \
            --branch "$branch" \
            --event workflow_dispatch \
            --created ">=$dispatch_started" \
            --limit 1 \
            --json databaseId,headSha \
            --jq '.[0] | select(. != null) | "\(.databaseId) \(.headSha)"' \
            2>>"$ART/tierb-dispatch.log")"
        list_rc=$?
        if [ "$list_rc" -ne 0 ]; then
            record "B*" BLOCKED "could not identify the dispatched Tier B run"
            return
        fi
        if [ -n "$run_record" ]; then
            read -r run_id run_sha <<<"$run_record"
            break
        fi
        sleep 2
    done
    case "$run_id" in
        ''|*[!0-9]*)
            record "B*" BLOCKED "dispatch succeeded but no matching Tier B run appeared"
            return
            ;;
    esac
    if [ "$run_sha" != "$head_sha" ]; then
        record "B*" BLOCKED "the selected Tier B run targets a different commit"
        return
    fi
    echo "following run $run_id ..."
    gh run watch "$run_id" --exit-status >"$ART/tierb-watch.log" 2>&1
    watch_rc=$?
    final_record="$(gh run view "$run_id" \
        --json status,conclusion,headSha \
        --jq '"\(.status)|\(.conclusion // "")|\(.headSha)"' \
        2>>"$ART/tierb-watch.log")"
    view_rc=$?
    if [ "$view_rc" -ne 0 ] || [ -z "$final_record" ]; then
        record "B*" BLOCKED "could not verify the final Tier B run state"
        return
    fi
    IFS='|' read -r final_status final_conclusion final_sha <<<"$final_record"
    if [ "$final_sha" != "$head_sha" ]; then
        record "B*" BLOCKED "the completed Tier B run reports a different commit"
        return
    fi
    if [ "$final_status" != "completed" ]; then
        record "B*" BLOCKED "Tier B observation ended before the run completed (watch rc=$watch_rc)"
        return
    fi
    case "$final_conclusion" in
        success)
            resolve_tier_b_g6 "$run_id"
            record "TierB" PASS "run https://github.com/markschonfeld/Talaria/actions/runs/$run_id"
            ;;
        failure)
            record "TierB" FAIL "run failed: https://github.com/markschonfeld/Talaria/actions/runs/$run_id (gh run view --log-failed)"
            ;;
        *)
            record "B*" BLOCKED "Tier B completed without a decisive success/failure conclusion (${final_conclusion:-unknown})"
            ;;
    esac
}

# ---- main -------------------------------------------------------------------------
g1; g2; g3; g4; g5; g6

if [ "${1:-}" = "--tier-b" ]; then
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
    if [ "${1:-}" = "--tier-b" ]; then
        echo "GAUNTLET GREEN"
    else
        echo "TIER A GREEN"
    fi
else
    echo "GAUNTLET RED — failures remain visible."
fi
exit $FAILED
