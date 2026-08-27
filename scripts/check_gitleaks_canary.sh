#!/usr/bin/env bash
# Prove the pinned scanner detects both committed-and-removed and worktree-only
# findings without using the network or exposing the synthetic value.
set -u

gitleaks_bin="${1-}"
findings_rc="${2-}"
canary_root=""

fail() {
    printf 'G5 gitleaks canary: BLOCKED — %s\n' "$1" >&2
    exit 2
}

cleanup() {
    case "$canary_root" in
        /tmp/talaria-g5-canary.*)
            rm -rf -- "$canary_root"
            ;;
        '') ;;
        *)
            printf 'G5 gitleaks canary: refused unsafe cleanup target\n' >&2
            return 1
            ;;
    esac
}

write_canary() {
    local destination="$1"
    # Keep the recognized prefix and suffix separate in this tracked source.
    # The generated value is synthetic and exists only inside the guarded temp tree.
    printf 'aws_access_key_id = "%s%s"\n' \
        'AKIA' 'Z3N7P5Q2R6T4V2WX' >"$destination"
}

run_probe() {
    local mode="$1" source="$2" actual_rc
    if [ "$mode" = "worktree" ]; then
        if "$gitleaks_bin" dir --redact --no-banner \
            --exit-code "$findings_rc" "$source" >/dev/null 2>&1; then
            actual_rc=0
        else
            actual_rc=$?
        fi
    else
        if "$gitleaks_bin" git --redact --no-banner \
            --exit-code "$findings_rc" "$source" >/dev/null 2>&1; then
            actual_rc=0
        else
            actual_rc=$?
        fi
    fi
    [ "$actual_rc" = "$findings_rc" ] \
        || fail "$mode probe returned rc=$actual_rc instead of findings rc=$findings_rc"
}

[ "$#" -eq 2 ] || fail "expected gitleaks path and findings exit code"
[ -x "$gitleaks_bin" ] || fail "gitleaks executable is unavailable"
case "$findings_rc" in
    0|1|*[!0-9]*) fail "findings exit code must be an integer from 2 through 125" ;;
esac
[ "$findings_rc" -le 125 ] \
    || fail "findings exit code must be an integer from 2 through 125"
command -v git >/dev/null 2>&1 || fail "git is unavailable"
command -v mktemp >/dev/null 2>&1 || fail "mktemp is unavailable"

canary_root="$(mktemp -d /tmp/talaria-g5-canary.XXXXXX)" \
    || fail "could not create guarded temporary directory"
trap 'cleanup || exit 2' EXIT HUP INT TERM

history_repo="$canary_root/history"
worktree="$canary_root/worktree"
mkdir -p "$history_repo" "$worktree" || fail "could not create canary fixtures"

git -C "$history_repo" init --quiet || fail "could not initialize history fixture"
git -C "$history_repo" config user.name "Talaria Gate Canary" \
    || fail "could not configure history fixture"
git -C "$history_repo" config user.email "canary.invalid" \
    || fail "could not configure history fixture"
write_canary "$history_repo/canary.txt" || fail "could not write history fixture"
git -C "$history_repo" add canary.txt || fail "could not stage history fixture"
git -C "$history_repo" commit --quiet -m "canary present" \
    || fail "could not commit history fixture"
git -C "$history_repo" rm --quiet canary.txt || fail "could not remove history fixture"
git -C "$history_repo" commit --quiet -m "canary removed" \
    || fail "could not commit history removal"

write_canary "$worktree/canary.txt" || fail "could not write worktree fixture"
run_probe history "$history_repo"
run_probe worktree "$worktree"

printf 'G5 gitleaks canary: PASS (history + worktree findings rc=%s)\n' "$findings_rc"
