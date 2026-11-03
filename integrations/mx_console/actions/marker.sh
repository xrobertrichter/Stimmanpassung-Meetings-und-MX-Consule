#!/usr/bin/env bash
# MX Creative Console action "marker": Zeitmarke mit Text ins Regelungslog
# Sends the command to the running chain (python -m voicechain run) and shows the
# answer as a macOS notification. Safe to run from a terminal for testing; extra
# arguments are passed on to "python -m voicechain ctl marker" (e.g. --value 1).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
cd -- "${REPO_ROOT}"

if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
else
    PYTHON="${PYTHON_BIN:-python3}"
fi

exec "${PYTHON}" -m voicechain ctl marker --notify ${1+"$@"}
