#!/usr/bin/env bash
#
# Talaria gauntlet — Tier A (local, Linux, Docker).
#
#   ./scripts/gauntlet.sh              run every Tier A gate, print numbered table
#   ./scripts/gauntlet.sh --tier-b     additionally dispatch + follow the macOS CI workflow
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
# swift:<major.minor> MUST match the Xcode toolchain shipped by macos-26 CI
# (Xcode 26.6 -> Swift 6.3.3). Bump both together; see docs/GOVERNANCE.md.
SWIFT_IMAGE="${TALARIA_SWIFT_IMAGE:-swift:6.3-noble}"
SWIFTLINT_IMAGE="ghcr.io/realm/swiftlint:0.65.0"   # same version preinstalled on macos-26
GITLEAKS_VERSION="v8.30.1"
KIT="Packages/HermesKit"
CONTAINER_WORK="/work"

RESULTS=()
FAILED=0

record() { # gate status detail
    RESULTS+=("$1|$2|$3")
    case "$2" in
        FAIL|BLOCKED) FAILED=1 ;;
    esac
}

section() { printf '\n=== %s ===\n' "$1"; }

# ---- G1: swift build debug + release, zero warnings -------------------------
g1() {
    section "G1 · swift build (debug + release, warnings-as-errors)"
    if ! docker image inspect "$SWIFT_IMAGE" >/dev/null 2>&1; then
        echo "pulling $SWIFT_IMAGE (one-time)..."
        docker pull "$SWIFT_IMAGE" >"$ART/g1-pull.log" 2>&1 \
            || { record G1 BLOCKED "cannot pull $SWIFT_IMAGE (see .gauntlet/g1-pull.log)"; return; }
    fi
    {
        docker run --rm "$SWIFT_IMAGE" swift --version 2>&1 | head -1
    } >"$ART/g1.log" 2>&1
    local ok=1
    echo "--- debug ---" >>"$ART/g1.log"
    docker run --rm -v "$ROOT:$CONTAINER_WORK" -w "$CONTAINER_WORK" "$SWIFT_IMAGE" \
        swift build --package-path "$KIT" -c debug -Xswiftc -warnings-as-errors \
        >>"$ART/g1.log" 2>&1 || ok=0
    echo "--- release ---" >>"$ART/g1.log"
    docker run --rm -v "$ROOT:$CONTAINER_WORK" -w "$CONTAINER_WORK" "$SWIFT_IMAGE" \
        swift build --package-path "$KIT" -c release -Xswiftc -warnings-as-errors \
        >>"$ART/g1.log" 2>&1 || ok=0
    if [ $ok -eq 1 ]; then
        record G1 PASS "$(head -1 "$ART/g1.log") · zero warnings enforced (-warnings-as-errors)"
    else
        record G1 FAIL "see .gauntlet/g1.log ($(grep -c 'error:' "$ART/g1.log" || true) errors)"
    fi
}

# ---- G2: swift test + coverage ----------------------------------------------
g2() {
    section "G2 · swift test + line coverage (>=85% on kit sources)"
    docker run --rm -v "$ROOT:$CONTAINER_WORK" -w "$CONTAINER_WORK" "$SWIFT_IMAGE" bash -lc '
        set -e
        cd '"$KIT"'
        swift test -c debug \
          -Xswiftc -profile-coverage-mapping -Xswiftc -profile-generate \
          2>&1
        PROF=$(ls *.profraw 2>/dev/null | head -1)
        BIN=$(find .build -name "*.xctest" -type f | head -1)
        if [ -z "$PROF" ] || [ -z "$BIN" ]; then
            echo "COVERAGE-HARNESS-MISSING prof=$PROF bin=$BIN"
            exit 3
        fi
        llvm-profdata merge -sparse "$PROF" -o merged.profdata
        llvm-cov report "$BIN" -instr-profile=merged.profdata --ignore-filename-regex="(Tests|\.build)" \
          > ../../.gauntlet/g2-coverage.txt
        grep "^TOTAL" ../../.gauntlet/g2-coverage.txt
    ' >"$ART/g2.log" 2>&1
    local rc=$?
    if [ $rc -ne 0 ]; then
        if grep -q "COVERAGE-HARNESS-MISSING" "$ART/g2.log"; then
            record G2 BLOCKED "coverage harness could not locate profraw/test binary (see .gauntlet/g2.log)"
        elif grep -qi "failed" "$ART/g2.log"; then
            record G2 FAIL "test failure (see .gauntlet/g2.log)"
        else
            record G2 BLOCKED "harness error rc=$rc (see .gauntlet/g2.log)"
        fi
        return
    fi
    local pct
    pct=$(grep "^TOTAL" "$ART/g2-coverage.txt" | awk '{for(i=1;i<=NF;i++) if($i ~ /%$/) {gsub(/%/,"",$i); print $i; exit}}')
    if [ -z "$pct" ]; then
        record G2 BLOCKED "could not parse coverage TOTAL line (see .gauntlet/g2-coverage.txt)"
        return
    fi
    if awk "BEGIN{exit !($pct >= 85)}"; then
        record G2 PASS "tests green · line coverage ${pct}% (>=85)"
    else
        record G2 FAIL "line coverage ${pct}% below 85 floor"
    fi
}

# ---- G3: protocol conformance ------------------------------------------------
g3() {
    section "G3 · protocol conformance (methods.json <-> golden fixtures)"
    python3 scripts/check_conformance.py >"$ART/g3.log" 2>&1
    if [ $? -eq 0 ]; then
        record G3 PASS "$(tail -1 "$ART/g3.log")"
    else
        record G3 FAIL "see .gauntlet/g3.log"
    fi
}

# ---- G4: lint -----------------------------------------------------------------
g4() {
    section "G4 · swift-format --lint + SwiftLint --strict"
    # swiftformat built once into a local onboarding image (network-free afterwards)
    if ! docker image inspect talaria-onboard:swiftformat >/dev/null 2>&1; then
        echo "onboarding swiftformat into talaria-onboard:swiftformat (one-time)..."
        cat > "$ART/Dockerfile.onboard" <<'EOF'
FROM ARG_BASE
RUN git clone --depth 1 --branch 0.55.7 https://github.com/nicklockwood/SwiftFormat /tmp/sf \
 && swift build -c release --package-path /tmp/sf \
 && cp /tmp/sf/.build/release/swiftformat /usr/local/bin/ \
 && rm -rf /tmp/sf
EOF
        sed -i "s|ARG_BASE|$SWIFT_IMAGE|" "$ART/Dockerfile.onboard"
        docker build -t talaria-onboard:swiftformat -f "$ART/Dockerfile.onboard" "$ART" \
            >"$ART/g4-onboard.log" 2>&1 \
            || { record G4 BLOCKED "swiftformat onboarding failed (see .gauntlet/g4-onboard.log)"; return; }
    fi
    {
        echo "--- swiftformat --lint ---"
        docker run --rm -v "$ROOT:$CONTAINER_WORK" -w "$CONTAINER_WORK" talaria-onboard:swiftformat \
            swiftformat --lint --quiet Packages/HermesKit App Tests 2>&1
        echo "--- swiftlint --strict ---"
        docker run --rm -v "$ROOT:$CONTAINER_WORK" -w "$CONTAINER_WORK" --entrypoint swiftlint "$SWIFTLINT_IMAGE" \
            --strict --quiet 2>&1
    } >"$ART/g4.log" 2>&1
    if grep -q "swiftlint: error\|error:" "$ART/g4.log" || grep -qE "Violation" "$ART/g4.log"; then
        record G4 FAIL "lint violations (see .gauntlet/g4.log)"
    else
        record G4 PASS "zero violations (swiftformat 0.55.7 + swiftlint 0.65.0)"
    fi
}

# ---- G5: secrets + host hygiene ------------------------------------------------
g5() {
    section "G5 · secret scan + hardcoded-host grep"
    local GL_BIN="/tmp/gitleaks_${GITLEAKS_VERSION}"
    if [ ! -x "$GL_BIN" ]; then
        curl -sL --max-time 120 -o /tmp/gl.tgz \
            "https://github.com/gitleaks/gitleaks/releases/download/${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION#v}_linux_x64.tar.gz" \
            && tar -xzf /tmp/gl.tgz -C /tmp gitleaks \
            && mv /tmp/gitleaks "$GL_BIN" \
            || { record G5 BLOCKED "gitleaks download failed"; return; }
    fi
    {
        echo "--- gitleaks ---"
        "$GL_BIN" detect --source "$ROOT" --no-git --redact -v 2>&1
        echo "--- hardcoded hosts / credential-shaped literals ---"
        grep -rnE 'https?://' --include='*.swift' Packages App Tests 2>/dev/null \
          | grep -vE '127\.0\.0\.1|localhost|example\.|<GATEWAY_URL>|swift-tools-version' || true
        grep -rniE '(api[_-]?key|secret|passcode|bearer)[[:space:]]*=|=[[:space:]]*"[A-Za-z0-9_-]{24,}"' \
          --include='*.swift' Packages App Tests 2>/dev/null || true
    } >"$ART/g5.log" 2>&1
    if grep -qE 'Finding:|secret detected' "$ART/g5.log"; then
        record G5 FAIL "secret findings (redacted) in .gauntlet/g5.log"
    elif [ -n "$(sed -n '/--- hardcoded/,$p' "$ART/g5.log" | sed '1d' | tr -d '\n[:space:]')" ]; then
        record G5 FAIL "hardcoded host/token-shaped literal (see .gauntlet/g5.log)"
    else
        record G5 PASS "no secrets, no hardcoded hosts"
    fi
}

# ---- G6: XcodeGen determinism ---------------------------------------------------
g6() {
    section "G6 · xcodegen generate determinism"
    if command -v xcodegen >/dev/null 2>&1; then
        cp project.yml "$ART/project.yml.bak"
        xcodegen generate >"$ART/g6.log" 2>&1 && git diff --exit-code -- Talaria.xcodeproj >>"$ART/g6.log" 2>&1
        if [ $? -eq 0 ]; then
            record G6 PASS "regeneration produced no diff"
        else
            record G6 FAIL "regeneration drifted (see .gauntlet/g6.log)"
        fi
    else
        # Honest deferral: xcodegen is macOS-homebrew-first. Tier B runs the real gate.
        record "G6*" "DEFER->B" "xcodegen not available on Linux; enforced in tier-b workflow (step 'Regenerate & diff')"
    fi
}

# ---- Tier B dispatch -------------------------------------------------------------
tier_b() {
    section "Tier B · dispatching macos-26 workflow"
    gh workflow run tier-b.yml --ref "$(git rev-parse --abbrev-ref HEAD)" \
        >"$ART/tierb-dispatch.log" 2>&1 \
        || { record "B*" BLOCKED "workflow dispatch failed (see .gauntlet/tierb-dispatch.log)"; return; }
    sleep 8
    local run_id
    run_id=$(gh run list --workflow=tier-b.yml -L 1 --json databaseId --jq '.[0].databaseId')
    echo "following run $run_id ..."
    gh run watch "$run_id" --exit-status >"$ART/tierb-watch.log" 2>&1
    if [ $? -eq 0 ]; then
        record "TierB" PASS "run https://github.com/markschonfeld/talaria/actions/runs/$run_id"
    else
        record "TierB" FAIL "run failed: https://github.com/markschonfeld/talaria/actions/runs/$run_id (gh run view --log-failed)"
    fi
}

# ---- main -------------------------------------------------------------------------
g1; g2; g3; g4; g5; g6

printf '\n==================== GAUNTLET · TIER A ====================\n\n'
printf '%-6s %-10s %s\n' "GATE" "STATUS" "DETAIL"
printf '%.0s-' {1..100}; echo
for row in "${RESULTS[@]}"; do
    IFS='|' read -r gate status detail <<<"$row"
    printf '%-6s %-10s %s\n' "$gate" "$status" "$detail"
done
echo
if [ "${1:-}" = "--tier-b" ]; then
    if [ "$FAILED" -eq 1 ]; then
        echo "Tier A red — refusing to burn CI minutes. Fix Tier A first."
        exit 1
    fi
    tier_b
fi
printf '%.0s-' {1..100}; echo
if [ "$FAILED" -eq 0 ]; then
    echo "TIER A GREEN"
else
    echo "TIER A RED — fix before push."
fi
exit $FAILED
