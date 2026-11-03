#!/usr/bin/env bash
# Calibrate the chain from a speech sample (WAV, 30-120 s, speech with pauses).
# Usage: scripts/calibrate.sh PATH/TO/sample.wav [further "calibrate" options]
# Writes the proposed configuration and the report (see "python -m voicechain calibrate --help").
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 PATH/TO/sample.wav [--config BASE.yaml] [--out NEW.yaml] [--report REPORT.md]" >&2
    exit 2
fi

WAV_PATH="$1"
shift

if [[ ! -f "${WAV_PATH}" ]]; then
    echo "error: WAV file not found: ${WAV_PATH}" >&2
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Resolve the sample path before changing into the repository.
WAV_PATH="$(cd "$(dirname "${WAV_PATH}")" && pwd)/$(basename "${WAV_PATH}")"

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

exec python -m voicechain calibrate --wav "${WAV_PATH}" "$@"
