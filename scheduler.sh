#!/bin/bash
# Reads brew_cron.json and runs irupkg -b for each listed cask on the configured interval.
set -euo pipefail

CRON_FILE="/home/irupkg/.local/share/irupkg/brew_cron.json"

if [[ ! -f "${CRON_FILE}" ]]; then
    echo "ERROR: brew_cron.json not found at ${CRON_FILE}" >&2
    exit 1
fi

# Deprecation shims: fall back to KANDJI_* names with a warning
if [[ -z "${IRU_API_URL:-}" && -n "${KANDJI_API_URL:-}" ]]; then
    echo "WARNING: KANDJI_API_URL is deprecated, use IRU_API_URL instead" >&2
    export IRU_API_URL="${KANDJI_API_URL}"
fi
if [[ -z "${IRUPKG_TOKEN:-}" && -n "${KANDJI_TOKEN:-}" ]]; then
    echo "WARNING: KANDJI_TOKEN is deprecated, use IRUPKG_TOKEN instead" >&2
    export IRUPKG_TOKEN="${KANDJI_TOKEN}"
fi

# Verify required credentials are available before entering the loop
if [[ -z "${IRU_API_URL:-}" || -z "${IRUPKG_TOKEN:-}" ]]; then
    echo "ERROR: IRU_API_URL and IRUPKG_TOKEN must be set" >&2
    exit 1
fi

while true; do
    # Re-read each iteration so config changes take effect without restart
    CASK_ARGS=$(jq -er '.brew_casks | map("-b " + .) | join(" ")' "${CRON_FILE}")
    HOURS=$(jq -er '.every_n_hours' "${CRON_FILE}")
    SLEEP_SECS=$((HOURS * 3600))

    # Refresh cask formulas so we fetch current upstream URLs/SHAs
    # Without this, brew can serve a stale formula whose recorded SHA mismatches upstream
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Updating Homebrew taps"
    brew update >/dev/null || echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARNING: brew update failed; continuing with cached formulas"

    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Running: irupkg ${CASK_ARGS}"
    # irupkg exits non-zero if any single cask fails; without this guard `set -e`
    # would terminate the loop and compose's restart policy would respawn the
    # container in a tight crash/restart cycle. Log and continue instead.
    # shellcheck disable=SC2086
    if ! irupkg ${CASK_ARGS}; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARNING: irupkg run reported failure; continuing to next interval"
    fi

    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Next run in $((SLEEP_SECS / 3600))h -- sleeping"
    sleep "${SLEEP_SECS}"
done
