#!/bin/bash
set -euo pipefail

CRON_FILE="/home/irupkg/.local/share/irupkg/brew_cron.json"

if [[ $# -gt 0 ]]; then
    # Explicit args passed -- use them directly
    exec irupkg "$@"
elif [[ -f "${CRON_FILE}" ]]; then
    # No args -- build -b flags from brew_cron.json
    CASK_ARGS=$(jq -r '.brew_casks | map("-b " + .) | join(" ")' "${CRON_FILE}")
    echo "Using brew_cron.json casks: ${CASK_ARGS}"
    # shellcheck disable=SC2086
    exec irupkg ${CASK_ARGS}
else
    exec irupkg "$@"
fi
