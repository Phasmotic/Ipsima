#!/usr/bin/env bash
#
# Pure, network-free result classifiers and evidence helpers used by
# scripts/gauntlet.sh.
#
# Every classifier starts BLOCKED and only changes state after validating all
# of its inputs. Results are returned in the TALARIA_CLASS_* variables so the
# caller does not need to parse diagnostic text.

# Gitleaks can use exit 1 for both findings and an operational failure. Give
# findings a reserved code so every other nonzero result can fail closed.
TALARIA_GITLEAKS_FINDINGS_RC=42

talaria_classification_reset() {
    TALARIA_CLASS_STATUS="BLOCKED"
    TALARIA_CLASS_DETAIL="classification did not complete"
    TALARIA_CLASS_VALUE=""
    TALARIA_CLASS_VALUE2=""
}

talaria_is_exit_code() {
    local value="${1-}"
    [[ "$value" =~ ^(0|[1-9][0-9]{0,2})$ ]] || return 1
    ((10#$value <= 255))
}

talaria_is_commit_sha() {
    [[ "${1-}" =~ ^[0-9a-f]{40}$ ]]
}

talaria_is_single_line() {
    local value="${1-}"
    [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]]
}

talaria_classify_g4() {
    talaria_classification_reset
    local format_rc="${1-}" lint_rc="${2-}" report_rc="${3-}" violation_count="${4-}"
    local stderr_state="${5-}"

    if [ "$#" -ne 5 ] \
        || ! talaria_is_exit_code "$format_rc" \
        || ! talaria_is_exit_code "$lint_rc" \
        || ! talaria_is_exit_code "$report_rc" \
        || [[ ! "$violation_count" =~ ^(0|[1-9][0-9]*)$ ]] \
        || { [ "$stderr_state" != "empty" ] && [ "$stderr_state" != "nonempty" ]; }; then
        TALARIA_CLASS_DETAIL="G4 received invalid tool or evidence status"
        return 0
    fi

    # SwiftFormat 0.62.1 documents 0 as clean, 1 as lint findings, and 70
    # as a program error. Signals (for example 139) and every other status
    # are likewise operational failures, never lint evidence.
    if [ "$format_rc" != "0" ] && [ "$format_rc" != "1" ]; then
        TALARIA_CLASS_DETAIL="SwiftFormat execution failed (rc=$format_rc; see .gauntlet/g4.log)"
    # SwiftLint 0.65.0 exits 2 when one or more reported violations have
    # Error severity. --strict promotes warnings to errors, so 2 plus valid,
    # nonempty JSON is the expected findings result. Thrown/configuration
    # errors use other statuses and must remain BLOCKED.
    elif [ "$lint_rc" != "0" ] && [ "$lint_rc" != "2" ]; then
        TALARIA_CLASS_DETAIL="SwiftLint execution failed (rc=$lint_rc; see .gauntlet/g4.log)"
    elif [ "$report_rc" != "0" ]; then
        TALARIA_CLASS_DETAIL="SwiftLint JSON evidence was invalid (see .gauntlet/g4.log)"
    elif [ "$stderr_state" != "empty" ]; then
        TALARIA_CLASS_DETAIL="SwiftLint wrote operational stderr (see .gauntlet/g4.log)"
    elif { [ "$lint_rc" = "0" ] && [ "$violation_count" != "0" ]; } \
        || { [ "$lint_rc" = "2" ] && [ "$violation_count" = "0" ]; }; then
        TALARIA_CLASS_DETAIL="SwiftLint exit status disagreed with its JSON evidence (see .gauntlet/g4.log)"
    elif [ "$format_rc" = "1" ] || [ "$lint_rc" = "2" ]; then
        TALARIA_CLASS_STATUS="FAIL"
        TALARIA_CLASS_DETAIL="lint violations (see .gauntlet/g4.log)"
    else
        TALARIA_CLASS_STATUS="PASS"
        TALARIA_CLASS_DETAIL="zero violations with valid formatter evidence"
    fi
}

talaria_classify_g5() {
    talaria_classification_reset
    local history_rc="${1-}" worktree_rc="${2-}" literal_rc="${3-}"

    if [ "$#" -ne 3 ] \
        || ! talaria_is_exit_code "$history_rc" \
        || ! talaria_is_exit_code "$worktree_rc" \
        || ! talaria_is_exit_code "$literal_rc"; then
        TALARIA_CLASS_DETAIL="G5 received an invalid scanner exit status"
        return 0
    fi
    if ((10#$literal_rc > 1)); then
        TALARIA_CLASS_DETAIL="source-literal scan was indeterminate (see .gauntlet/g5-literals.log)"
    elif { [ "$history_rc" != "0" ] \
        && [ "$history_rc" != "$TALARIA_GITLEAKS_FINDINGS_RC" ]; } \
        || { [ "$worktree_rc" != "0" ] \
        && [ "$worktree_rc" != "$TALARIA_GITLEAKS_FINDINGS_RC" ]; }; then
        TALARIA_CLASS_DETAIL="gitleaks execution failed (history rc=$history_rc; worktree rc=$worktree_rc)"
    elif [ "$history_rc" = "$TALARIA_GITLEAKS_FINDINGS_RC" ] \
        || [ "$worktree_rc" = "$TALARIA_GITLEAKS_FINDINGS_RC" ]; then
        TALARIA_CLASS_STATUS="FAIL"
        TALARIA_CLASS_DETAIL="secret findings (redacted) in .gauntlet/g5.log"
    elif ((10#$literal_rc == 1)); then
        TALARIA_CLASS_STATUS="FAIL"
        TALARIA_CLASS_DETAIL="hardcoded host/token-shaped literal (see .gauntlet/g5.log)"
    else
        TALARIA_CLASS_STATUS="PASS"
        TALARIA_CLASS_DETAIL="no secrets in history or working tree; no hardcoded hosts"
    fi
}

talaria_assemble_g5_evidence() {
    local output_path="${1-}" version="${2-}" digest="${3-}" findings_rc="${4-}"
    local canary_log="${5-}" history_log="${6-}" worktree_log="${7-}"
    local literals_log="${8-}" evidence

    [ "$#" -eq 8 ] \
        && [ -n "$output_path" ] \
        && [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        && [[ "$digest" =~ ^[0-9a-f]{64}$ ]] \
        && talaria_is_exit_code "$findings_rc" \
        && [ "$findings_rc" != "0" ] \
        || return 1

    for evidence in "$canary_log" "$history_log" "$worktree_log" "$literals_log"; do
        [ -f "$evidence" ] && [ -r "$evidence" ] || return 1
    done

    {
        printf 'gitleaks %s sha256:%s\n' "$version" "$digest" \
            && printf 'gitleaks findings exit code: %s\n' "$findings_rc" \
            && printf '%s\n' '--- pinned scanner functional canary ---' \
            && cat -- "$canary_log" \
            && printf '%s\n' '--- gitleaks git history ---' \
            && cat -- "$history_log" \
            && printf '%s\n' '--- gitleaks working tree ---' \
            && cat -- "$worktree_log" \
            && printf '%s\n' '--- hardcoded hosts / credential-shaped literals ---' \
            && cat -- "$literals_log"
    } >"$output_path"
}

talaria_classify_tier_b_branch() {
    talaria_classification_reset
    local branch_rc="${1-}" branch="${2-}"

    if [ "$#" -ne 2 ] \
        || ! talaria_is_exit_code "$branch_rc" || ((10#$branch_rc != 0)) \
        || [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
        TALARIA_CLASS_DETAIL="Tier B dispatch requires a named branch"
    elif ! talaria_is_single_line "$branch" || [[ "$branch" =~ [[:space:]] ]]; then
        TALARIA_CLASS_DETAIL="Tier B dispatch received an invalid branch name"
    else
        TALARIA_CLASS_STATUS="PASS"
        TALARIA_CLASS_DETAIL="named branch verified"
        TALARIA_CLASS_VALUE="$branch"
    fi
}

talaria_classify_tier_b_repository() {
    talaria_classification_reset
    local remote_url_rc="${1-}" remote_url="${2-}" expected_repository="${3-}"

    if [ "$#" -ne 3 ] \
        || ! talaria_is_exit_code "$remote_url_rc" \
        || ((10#$remote_url_rc != 0)) \
        || [[ ! "$expected_repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
        TALARIA_CLASS_DETAIL="could not verify the canonical Tier B repository"
        return 0
    fi
    case "$remote_url" in
        "https://github.com/$expected_repository"|\
        "https://github.com/$expected_repository.git"|\
        "git@github.com:$expected_repository"|\
        "git@github.com:$expected_repository.git"|\
        "ssh://git@github.com/$expected_repository"|\
        "ssh://git@github.com/$expected_repository.git")
            TALARIA_CLASS_STATUS="PASS"
            TALARIA_CLASS_DETAIL="canonical Tier B repository verified"
            TALARIA_CLASS_VALUE="$expected_repository"
            ;;
        *)
            TALARIA_CLASS_DETAIL="origin does not name the canonical Tier B repository"
            ;;
    esac
}

talaria_classify_tier_b_clean_tree() {
    talaria_classification_reset
    local status_rc="${1-}" dirty_state="${2-}"

    if [ "$#" -ne 2 ] \
        || ! talaria_is_exit_code "$status_rc" || ((10#$status_rc != 0)); then
        TALARIA_CLASS_DETAIL="could not verify the Tier B source tree state"
    elif [ -n "$dirty_state" ]; then
        TALARIA_CLASS_DETAIL="Tier B requires a clean committed source tree"
    else
        TALARIA_CLASS_STATUS="PASS"
        TALARIA_CLASS_DETAIL="clean source tree verified"
    fi
}

talaria_classify_tier_b_local_sha() {
    talaria_classification_reset
    local revision_rc="${1-}" head_sha="${2-}"

    if [ "$#" -ne 2 ] \
        || ! talaria_is_exit_code "$revision_rc" || ((10#$revision_rc != 0)) \
        || ! talaria_is_commit_sha "$head_sha"; then
        TALARIA_CLASS_DETAIL="could not resolve the local checkpoint commit"
    else
        TALARIA_CLASS_STATUS="PASS"
        TALARIA_CLASS_DETAIL="local checkpoint commit verified"
        TALARIA_CLASS_VALUE="$head_sha"
    fi
}

talaria_classify_tier_b_remote() {
    talaria_classification_reset
    local remote_rc="${1-}" remote_record="${2-}" branch="${3-}" head_sha="${4-}"
    local remote_sha="" remote_ref="" extra=""

    if [ "$#" -ne 4 ] \
        || ! talaria_is_exit_code "$remote_rc" || ((10#$remote_rc != 0)) \
        || [ -z "$remote_record" ]; then
        TALARIA_CLASS_DETAIL="the checkpoint branch is not published on origin"
        return 0
    fi
    if ! talaria_is_single_line "$remote_record" \
        || ! talaria_is_single_line "$branch" \
        || [ -z "$branch" ] \
        || [ "$branch" = "HEAD" ] \
        || [[ "$branch" =~ [[:space:]] ]] \
        || ! talaria_is_commit_sha "$head_sha"; then
        TALARIA_CLASS_DETAIL="could not verify the published checkpoint branch"
        return 0
    fi
    read -r remote_sha remote_ref extra <<<"$remote_record"
    if ! talaria_is_commit_sha "$remote_sha" \
        || [ "$remote_ref" != "refs/heads/$branch" ] \
        || [ -n "$extra" ] \
        || [ "$remote_record" != "$remote_sha"$'\t'"$remote_ref" ]; then
        TALARIA_CLASS_DETAIL="could not verify the published checkpoint branch"
    elif [ "$remote_sha" != "$head_sha" ]; then
        TALARIA_CLASS_DETAIL="origin does not point to the local checkpoint commit"
    else
        TALARIA_CLASS_STATUS="PASS"
        TALARIA_CLASS_DETAIL="published checkpoint commit verified"
        TALARIA_CLASS_VALUE="$remote_sha"
    fi
}

talaria_classify_tier_b_correlation() {
    talaria_classification_reset
    local token_rc="${1-}" token="${2-}"

    if [ "$#" -ne 2 ] \
        || ! talaria_is_exit_code "$token_rc" \
        || ((10#$token_rc != 0)) \
        || [[ ! "$token" =~ ^talaria-[0-9a-f]{32}$ ]]; then
        TALARIA_CLASS_DETAIL="could not create a valid Tier B correlation token"
        return 0
    fi
    TALARIA_CLASS_STATUS="PASS"
    TALARIA_CLASS_DETAIL="Tier B correlation token verified"
    TALARIA_CLASS_VALUE="$token"
    TALARIA_CLASS_VALUE2="Talaria Tier B: $token"
}

talaria_classify_tier_b_run_selection() {
    talaria_classification_reset
    local list_rc="${1-}" run_records="${2-}" head_sha="${3-}" expected_title="${4-}"
    local line="" run_id="" run_sha="" run_title=""
    local matching_id="" matching_sha="" match_count=0

    if [ "$#" -ne 4 ] \
        || ! talaria_is_exit_code "$list_rc" || ((10#$list_rc != 0)); then
        TALARIA_CLASS_DETAIL="could not identify the dispatched Tier B run"
        return 0
    fi
    if ! talaria_is_commit_sha "$head_sha" \
        || [[ ! "$expected_title" =~ ^Talaria\ Tier\ B:\ talaria-[0-9a-f]{32}$ ]]; then
        TALARIA_CLASS_DETAIL="could not validate the selected Tier B run"
        return 0
    fi
    if [ -z "$run_records" ]; then
        TALARIA_CLASS_STATUS="WAIT"
        TALARIA_CLASS_DETAIL="matching Tier B run has not appeared yet"
        return 0
    fi
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ ! "$line" =~ ^([1-9][0-9]*)\|([0-9a-f]{40})\|([^|]+)$ ]]; then
            TALARIA_CLASS_DETAIL="could not validate the Tier B run candidates"
            return 0
        fi
        run_id="${BASH_REMATCH[1]}"
        run_sha="${BASH_REMATCH[2]}"
        run_title="${BASH_REMATCH[3]}"
        if [ "$run_sha" = "$head_sha" ] && [ "$run_title" = "$expected_title" ]; then
            match_count=$((match_count + 1))
            matching_id="$run_id"
            matching_sha="$run_sha"
        fi
    done <<<"$run_records"
    case "$match_count" in
        0)
            TALARIA_CLASS_STATUS="WAIT"
            TALARIA_CLASS_DETAIL="matching Tier B run has not appeared yet"
            ;;
        1)
            TALARIA_CLASS_STATUS="PASS"
            TALARIA_CLASS_DETAIL="unique source-matched Tier B run selected"
            TALARIA_CLASS_VALUE="$matching_id"
            TALARIA_CLASS_VALUE2="$matching_sha"
            ;;
        *)
            TALARIA_CLASS_DETAIL="multiple source-matched Tier B runs appeared after dispatch"
            ;;
    esac
}
