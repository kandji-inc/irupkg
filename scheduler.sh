#!/bin/bash
# Reads brew_cron.json and runs kpkg -b for each listed cask on the configured interval.
set -euo pipefail

CRON_FILE="/home/kpkg/.local/share/kpkg/brew_cron.json"

if [[ ! -f "${CRON_FILE}" ]]; then
    echo "ERROR: brew_cron.json not found at ${CRON_FILE}" >&2
    exit 1
fi

# Verify required credentials are available before entering the loop
if [[ -z "${KANDJI_API_URL:-}" || -z "${KANDJI_TOKEN:-}" ]]; then
    echo "ERROR: KANDJI_API_URL and KANDJI_TOKEN must be set" >&2
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

    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Running: kpkg ${CASK_ARGS}"
    # kpkg exits non-zero if any single cask fails; without this guard `set -e`
    # would terminate the loop and compose's restart policy would respawn the
    # container in a tight crash/restart cycle. Log and continue instead.
    # shellcheck disable=SC2086
    if ! kpkg ${CASK_ARGS}; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARNING: kpkg run reported failure; continuing to next interval"
    fi

    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Next run in $((SLEEP_SECS / 3600))h -- sleeping"
    sleep "${SLEEP_SECS}"
done
