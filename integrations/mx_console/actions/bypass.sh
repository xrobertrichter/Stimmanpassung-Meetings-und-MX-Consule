#!/usr/bin/env bash
# MX Creative Console action "bypass": Kette umgehen (nur Input Trim + Limiter) zum Vergleich
# Sends the command to the running chain (python -m voicechain run) and shows the
# answer as a macOS notification. Safe to run from a terminal for testing; extra
# arguments are passed on to "python -m voicechain ctl bypass" (e.g. --value 1).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
cd -- "${REPO_ROOT}"

if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
else
    PYTHON="${PYTHON_BIN:-python3}"
fi

exec "${PYTHON}" -m voicechain ctl bypass --notify ${1+"$@"}
