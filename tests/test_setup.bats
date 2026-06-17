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
    local irupkg_token_name="${2:-}"
    local slack_token_name="${3:-}"
    zsh -c "
        source '${SCRIPT}' 2>/dev/null
        irupkg_token_name='${irupkg_token_name}'
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
    [[ "${status}" -eq 0 ]]
    [[ "${output}" == *"Usage"* ]]
}

# ---------------------------------------------------------------------------
# assign_token_name -- dispatches on token_type to copy the matching
# *_token_name global into token_name. Assert against the input value so the
# data flow (input -> output) is visible in the test.
# ---------------------------------------------------------------------------

@test "assign_token_name copies irupkg_token_name when token_type=Iru" {
    local expected="MY_IRUPKG_TOKEN"
    run _run_assign_token_name "Iru" "${expected}" "ignored"
    [[ "${status}" -eq 0 ]]
    [[ "${output}" = "${expected}" ]]
}

@test "assign_token_name copies slack_token_name when token_type=Slack" {
    local expected="MY_SLACK_WEBHOOK"
    run _run_assign_token_name "Slack" "ignored" "${expected}"
    [[ "${status}" -eq 0 ]]
    [[ "${output}" = "${expected}" ]]
}

@test "assign_token_name returns 1 for unknown token type" {
    run _run_assign_token_name "Unknown"
    [[ "${status}" -eq 1 ]]
    [[ "${output}" == *"CRITICAL"* ]]
}

# ---------------------------------------------------------------------------
# main -- flag validation (exits before read_config, so no config.json needed)
# ---------------------------------------------------------------------------

@test "-a without -b exits 1 with error about -b" {
    run zsh "${SCRIPT}" -a
    [[ "${status}" -eq 1 ]]
    [[ "${output}" == *"Invalid flag combination"* ]]
    [[ "${output}" == *"-b"* ]]
}

@test "-a and -u together exits 1 even when -b is present" {
    run zsh "${SCRIPT}" -b -a -u
    [[ "${status}" -eq 1 ]]
    [[ "${output}" == *"Invalid flag combination"* ]]
}

# ---------------------------------------------------------------------------
# migrate_from_legacy -- idempotent one-shot migration called at top of main()
# Tests source setup.zsh to load the function, override HOME to a tmp dir,
# and mock external commands to capture calls without side effects.
# ---------------------------------------------------------------------------

@test "migrate_from_legacy: mv when legacy dir exists and new dir absent" {
    local tmp; tmp=$(mktemp -d)
    mkdir -p "${tmp}/Library/KandjiPackages"

    run zsh -c "
        source '${SCRIPT}' 2>/dev/null
        HOME='${tmp}'
        function launchctl() { return 0; }
        function security() { return 1; }
        function pkgutil() { return 1; }
        function rsync() { return 0; }
        migrate_from_legacy
    "
    [[ "${status}" -eq 0 ]]
    [[ -d "${tmp}/Library/IruPackages" ]]
    [[ ! -d "${tmp}/Library/KandjiPackages" ]]
    rm -rf "${tmp}"
}

@test "migrate_from_legacy: rsync when both dirs exist" {
    local tmp; tmp=$(mktemp -d)
    local calls="${tmp}/calls"
    mkdir -p "${tmp}/Library/KandjiPackages"
    mkdir -p "${tmp}/Library/IruPackages"

    run zsh -c "
        source '${SCRIPT}' 2>/dev/null
        HOME='${tmp}'
        function launchctl() { return 0; }
        function security() { return 1; }
        function pkgutil() { return 1; }
        function rsync() { echo \"rsync \$@\" >> '${calls}'; return 0; }
        migrate_from_legacy
    "
    [[ "${status}" -eq 0 ]]
    [[ -f "${calls}" ]]
    grep -q 'rsync.*--ignore-existing' "${calls}"
    rm -rf "${tmp}"
}

@test "migrate_from_legacy: boots out legacy LaunchAgent when plist exists" {
    local tmp; tmp=$(mktemp -d)
    local calls="${tmp}/calls"
    mkdir -p "${tmp}/Library/LaunchAgents"
    touch "${tmp}/Library/LaunchAgents/io.kandji.kpkg.brewcron.plist"

    run zsh -c "
        source '${SCRIPT}' 2>/dev/null
        HOME='${tmp}'
        function launchctl() { echo \"launchctl \$@\" >> '${calls}'; return 0; }
        function security() { return 1; }
        function pkgutil() { return 1; }
        function rsync() { return 0; }
        migrate_from_legacy
    "
    [[ "${status}" -eq 0 ]]
    grep -q 'launchctl bootout' "${calls}"
    [[ ! -f "${tmp}/Library/LaunchAgents/io.kandji.kpkg.brewcron.plist" ]]
    rm -rf "${tmp}"
}

@test "migrate_from_legacy: no-op when no legacy state exists" {
    local tmp; tmp=$(mktemp -d)
    local calls="${tmp}/calls"

    run zsh -c "
        source '${SCRIPT}' 2>/dev/null
        HOME='${tmp}'
        function launchctl() { echo \"launchctl \$@\" >> '${calls}'; return 0; }
        function security() { return 1; }
        function pkgutil() { return 1; }
        function rsync() { echo \"rsync \$@\" >> '${calls}'; return 0; }
        migrate_from_legacy
    "
    [[ "${status}" -eq 0 ]]
    [[ ! -f "${calls}" ]]
    rm -rf "${tmp}"
}

@test "migrate_from_legacy: keychain migration is idempotent (no WARNING on second run)" {
    local tmp; tmp=$(mktemp -d)
    local state="${tmp}/state"
    mkdir -p "${state}"
    printf '{"token_keystore":{"keychain":true},"kandji":{"token_name":"KANDJI_TOKEN"},"slack":{"webhook_name":"SLACK_TOKEN"}}' \
        > "${tmp}/config.json"

    run zsh -c "
        source '${SCRIPT}' 2>/dev/null
        HOME='${tmp}'
        config_file='${tmp}/config.json'
        function launchctl() { return 0; }
        function pkgutil() { return 1; }
        function rsync() { return 0; }
        function security() {
            local subcmd=\"\$1\"
            if [[ \"\$subcmd\" == find-generic-password ]]; then
                local count=0
                [[ -f '${state}/n' ]] && count=\$(< '${state}/n')
                if (( count < 2 )); then
                    printf '%d' \$((count+1)) > '${state}/n'
                    echo fake-token
                    return 0
                fi
                return 1
            fi
            return 0
        }
        migrate_from_legacy
        migrate_from_legacy
    "
    [[ "${status}" -eq 0 ]]
    [[ "${output}" == *"Migrated"* ]]
    [[ "${output}" != *"WARNING"* ]]
    rm -rf "${tmp}"
}

@test "migrate_from_legacy: keychain uses service names from config.json" {
    local tmp; tmp=$(mktemp -d)
    local calls="${tmp}/calls"
    printf '{"token_keystore":{"keychain":true},"kandji":{"token_name":"MY_API_TOKEN"},"slack":{"webhook_name":"MY_SLACK_HOOK"}}' \
        > "${tmp}/config.json"

    run zsh -c "
        source '${SCRIPT}' 2>/dev/null
        config_file='${tmp}/config.json'
        HOME='${tmp}'
        function launchctl() { return 0; }
        function pkgutil() { return 1; }
        function rsync() { return 0; }
        function security() { echo \"security \$@\" >> '${calls}'; echo fake-token; return 0; }
        migrate_from_legacy
    "
    [[ "${status}" -eq 0 ]]
    grep -q 'MY_API_TOKEN' "${calls}"
    grep -q 'MY_SLACK_HOOK' "${calls}"
    ! grep -q 'KANDJI_TOKEN' "${calls}"
    ! grep -q 'SLACK_TOKEN' "${calls}"
    rm -rf "${tmp}"
}

@test "migrate_from_legacy: keychain uses service names from new-format config.json (iru key)" {
    local tmp; tmp=$(mktemp -d)
    local calls="${tmp}/calls"
    printf '{"token_keystore":{"keychain":true},"iru":{"token_name":"MY_API_TOKEN"},"slack":{"webhook_name":"MY_SLACK_HOOK"}}' \
        > "${tmp}/config.json"

    run zsh -c "
        source '${SCRIPT}' 2>/dev/null
        config_file='${tmp}/config.json'
        HOME='${tmp}'
        function launchctl() { return 0; }
        function pkgutil() { return 1; }
        function rsync() { return 0; }
        function security() { echo \"security \$@\" >> '${calls}'; echo fake-token; return 0; }
        migrate_from_legacy
    "
    [[ "${status}" -eq 0 ]]
    grep -q 'MY_API_TOKEN' "${calls}"
    grep -q 'MY_SLACK_HOOK' "${calls}"
    ! grep -q 'IRUPKG_TOKEN' "${calls}"
    rm -rf "${tmp}"
}

@test "migrate_from_legacy: keychain account migrated from kpkg to irupkg" {
    local tmp; tmp=$(mktemp -d)
    local calls="${tmp}/calls"
    printf '{"token_keystore":{"keychain":true},"kandji":{"token_name":"MY_TOKEN"},"slack":{"webhook_name":"MY_SLACK"}}' \
        > "${tmp}/config.json"

    run zsh -c "
        source '${SCRIPT}' 2>/dev/null
        config_file='${tmp}/config.json'
        HOME='${tmp}'
        function launchctl() { return 0; }
        function pkgutil() { return 1; }
        function rsync() { return 0; }
        function security() {
            local subcmd=\"\$1\"
            echo \"security \$@\" >> '${calls}'
            if [[ \"\$subcmd\" == find-generic-password ]]; then
                echo fake-token
                return 0
            fi
            return 0
        }
        migrate_from_legacy
    "
    [[ "${status}" -eq 0 ]]
    grep -q 'add-generic-password.*-a irupkg' "${calls}"
    grep -q 'delete-generic-password.*-a kpkg' "${calls}"
    [[ "${output}" == *"Migrated"* ]]
    rm -rf "${tmp}"
}

# ---------------------------------------------------------------------------
# set_iru_api_url -- writes iru.api_url regardless of prior config state.
# prompt_for_value is mocked to inject CONFIG_VALUE without TTY interaction.
# ---------------------------------------------------------------------------

@test "set_iru_api_url: creates iru.api_url on legacy config with only kandji key" {
    local tmp; tmp=$(mktemp -d)
    local cfg="${tmp}/config.json"
    printf '{"kandji":{"api_url":"https://legacy.api.kandji.io","token_name":"KANDJI_TOKEN"}}' > "${cfg}"

    run zsh -c "
        source '${SCRIPT}' 2>/dev/null
        config_file='${cfg}'
        function prompt_for_value() { CONFIG_VALUE='https://test.api.iru.com'; }
        set_iru_api_url
    "
    [[ "${status}" -eq 0 ]]
    result=$(plutil -extract iru.api_url raw -o - "${cfg}")
    [[ "${result}" == "https://test.api.iru.com" ]]
    rm -rf "${tmp}"
}

@test "set_iru_api_url: overwrites an existing iru.api_url" {
    local tmp; tmp=$(mktemp -d)
    local cfg="${tmp}/config.json"
    printf '{"iru":{"api_url":"https://old.api.iru.com","token_name":"IRUPKG_TOKEN"}}' > "${cfg}"

    run zsh -c "
        source '${SCRIPT}' 2>/dev/null
        config_file='${cfg}'
        function prompt_for_value() { CONFIG_VALUE='https://new.api.iru.com'; }
        set_iru_api_url
    "
    [[ "${status}" -eq 0 ]]
    result=$(plutil -extract iru.api_url raw -o - "${cfg}")
    [[ "${result}" == "https://new.api.iru.com" ]]
    rm -rf "${tmp}"
}

@test "set_iru_api_url: writes iru.api_url and preserves sibling iru.* keys" {
    local tmp; tmp=$(mktemp -d)
    local cfg="${tmp}/config.json"
    printf '{"iru":{"token_name":"IRUPKG_TOKEN"}}' > "${cfg}"

    run zsh -c "
        source '${SCRIPT}' 2>/dev/null
        config_file='${cfg}'
        function prompt_for_value() { CONFIG_VALUE='https://test.api.iru.com'; }
        set_iru_api_url
    "
    [[ "${status}" -eq 0 ]]
    result=$(plutil -extract iru.api_url raw -o - "${cfg}")
    [[ "${result}" == "https://test.api.iru.com" ]]
    token=$(plutil -extract iru.token_name raw -o - "${cfg}")
    [[ "${token}" == "IRUPKG_TOKEN" ]]
    rm -rf "${tmp}"
}

@test "migrate_from_legacy: new com.iru.irupkg.brewcron plist untouched during migration" {
    local tmp; tmp=$(mktemp -d)
    mkdir -p "${tmp}/Library/LaunchAgents"
    touch "${tmp}/Library/LaunchAgents/io.kandji.kpkg.brewcron.plist"
    touch "${tmp}/Library/LaunchAgents/com.iru.irupkg.brewcron.plist"

    run zsh -c "
        source '${SCRIPT}' 2>/dev/null
        HOME='${tmp}'
        function launchctl() { return 0; }
        function security() { return 1; }
        function pkgutil() { return 1; }
        function rsync() { return 0; }
        migrate_from_legacy
    "
    [[ "${status}" -eq 0 ]]
    [[ ! -f "${tmp}/Library/LaunchAgents/io.kandji.kpkg.brewcron.plist" ]]
    [[ -f "${tmp}/Library/LaunchAgents/com.iru.irupkg.brewcron.plist" ]]
    rm -rf "${tmp}"
}
