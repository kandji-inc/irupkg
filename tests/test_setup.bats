#!/usr/bin/env bats
# Tests for setup.zsh
# Covers: help-flag output, assign_token_name dispatch, and main flag-combo
# validation. Interactive flows (read_config, token prompts, keychain storage)
# require a live config.json and a TTY and are intentionally out of scope.

SCRIPT="${BATS_TEST_DIRNAME}/../setup.zsh"

# Run assign_token_name in a fresh zsh subshell with the supplied token_type
# and *_token_name variables. Prints the resulting token_name to stdout so
# tests can capture it via `run` + $output. Single source of truth for the
# subshell scaffolding shared by every assign_token_name test.
function _run_assign_token_name() {
    local token_type="$1"
    local kandji_token_name="${2:-}"
    local slack_token_name="${3:-}"
    zsh -c "
        source '${SCRIPT}' 2>/dev/null
        kandji_token_name='${kandji_token_name}'
        slack_token_name='${slack_token_name}'
        token_type='${token_type}'
        assign_token_name
        local rc=\$?
        printf '%s' \"\${token_name}\"
        exit \$rc
    "
}

# ---------------------------------------------------------------------------
# -h  (runs before variable assignments -- safe to invoke directly)
# ---------------------------------------------------------------------------

@test "-h exits 0 and prints usage" {
    run zsh "${SCRIPT}" -h
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage"* ]]
}

# ---------------------------------------------------------------------------
# assign_token_name -- dispatches on token_type to copy the matching
# *_token_name global into token_name. Assert against the input value so the
# data flow (input -> output) is visible in the test.
# ---------------------------------------------------------------------------

@test "assign_token_name copies kandji_token_name when token_type=Kandji" {
    local expected="MY_KANDJI_TOKEN"
    run _run_assign_token_name "Kandji" "${expected}" "ignored"
    [ "$status" -eq 0 ]
    [ "$output" = "${expected}" ]
}

@test "assign_token_name copies slack_token_name when token_type=Slack" {
    local expected="MY_SLACK_WEBHOOK"
    run _run_assign_token_name "Slack" "ignored" "${expected}"
    [ "$status" -eq 0 ]
    [ "$output" = "${expected}" ]
}

@test "assign_token_name returns 1 for unknown token type" {
    run _run_assign_token_name "Unknown"
    [ "$status" -eq 1 ]
    [[ "$output" == *"CRITICAL"* ]]
}

# ---------------------------------------------------------------------------
# main -- flag validation (exits before read_config, so no config.json needed)
# ---------------------------------------------------------------------------

@test "-a without -b exits 1 with error about -b" {
    run zsh "${SCRIPT}" -a
    [ "$status" -eq 1 ]
    [[ "$output" == *"Invalid flag combination"* ]]
    [[ "$output" == *"-b"* ]]
}

@test "-a and -u together exits 1 even when -b is present" {
    run zsh "${SCRIPT}" -b -a -u
    [ "$status" -eq 1 ]
    [[ "$output" == *"Invalid flag combination"* ]]
}
