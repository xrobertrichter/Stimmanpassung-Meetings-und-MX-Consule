#!/usr/bin/env bash
# Start the live chain (Rode NT-USB -> voicechain -> BlackHole 2ch).
# Creates the virtual environment on first use and installs the requirements.
# All arguments are passed on to "python -m voicechain run", e.g.
#   scripts/run_live.sh --config config/chain.calibrated.yaml --freeze
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "${REPO_ROOT}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Creating virtual environment in ${VENV_DIR} ..." >&2
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

if [[ ! -f "${VENV_DIR}/.requirements.stamp" || "requirements.txt" -nt "${VENV_DIR}/.requirements.stamp" ]]; then
    echo "Installing requirements ..." >&2
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
    touch "${VENV_DIR}/.requirements.stamp"
fi

exec python -m voicechain run "$@"
