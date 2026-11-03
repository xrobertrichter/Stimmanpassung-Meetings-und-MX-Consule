#!/usr/bin/env bash
# MX Creative Console action "dense": Intensität +1: Kompressor-Schwelle −2 dB, gleichmäßiger
# Sends the command to the running chain (python -m voicechain run) and shows the answer as a macOS notification.
# Safe to run from a terminal for testing; extra arguments are passed on to
# "python -m voicechain ctl dense" (e.g. --socket PATH, --value 0.25).
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
cd -- "${REPO_ROOT}"

# Python with the project's dependencies. Logi Options+ and "do shell script" start
# with a minimal PATH, where "python3" is Apple's system python (no PyYAML, no numpy):
# 1. the repository's .venv (docs/01, step 2), 2. VOICECHAIN_PYTHON (absolute path,
# e.g. /opt/homebrew/bin/python3.12), 3. Homebrew python3, 4. PATH python3.
if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
elif [[ -n "${VOICECHAIN_PYTHON:-}" && -x "${VOICECHAIN_PYTHON}" ]]; then
    PYTHON="${VOICECHAIN_PYTHON}"
elif [[ -x /opt/homebrew/bin/python3 ]]; then
    PYTHON=/opt/homebrew/bin/python3
elif [[ -x /usr/local/bin/python3 ]]; then
    PYTHON=/usr/local/bin/python3
else
    PYTHON="${PYTHON_BIN:-python3}"
fi

exec "${PYTHON}" -m voicechain ctl dense --notify ${1+"$@"}
