#!/bin/bash
set -euo pipefail

CRON_FILE="/home/kpkg/.local/share/kpkg/brew_cron.json"

if [[ $# -gt 0 ]]; then
    # Explicit args passed -- use them directly
    exec kpkg "$@"
elif [[ -f "${CRON_FILE}" ]]; then
    # No args -- build -b flags from brew_cron.json
    CASK_ARGS=$(jq -r '.brew_casks | map("-b " + .) | join(" ")' "${CRON_FILE}")
    echo "Using brew_cron.json casks: ${CASK_ARGS}"
    # shellcheck disable=SC2086
    exec kpkg ${CASK_ARGS}
else
    exec kpkg "$@"
fi
