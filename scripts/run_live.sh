#!/usr/bin/env bash
# Start the live chain (Rode NT-USB -> voicechain -> BlackHole 2ch).
# Creates the virtual environment on first use and installs the requirements.
# All arguments are passed on to "python -m voicechain run", e.g.
#   scripts/run_live.sh --config config/chain.calibrated.yaml --freeze
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Python 3.11+ is required (README); macOS ships older python3 binaries, which would
# fail later with cryptic syntax errors.
if ! "${PYTHON_BIN}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    echo "error: ${PYTHON_BIN} is $("${PYTHON_BIN}" --version 2>&1); voicechain needs Python 3.11 or newer." >&2
    echo "       Install it with 'brew install python@3.12' and run again with PYTHON_BIN=python3.12 $0 ..." >&2
    exit 2
fi

cd "${REPO_ROOT}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Creating virtual environment in ${VENV_DIR} ..." >&2
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

if ! python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "error: the virtual environment in ${VENV_DIR} uses $(python --version 2>&1); delete it and run again." >&2
    exit 2
fi

if [[ ! -f "${VENV_DIR}/.requirements.stamp" || "requirements.txt" -nt "${VENV_DIR}/.requirements.stamp" ]]; then
    echo "Installing requirements ..." >&2
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
    touch "${VENV_DIR}/.requirements.stamp"
fi

exec python -m voicechain run ${1+"$@"}
