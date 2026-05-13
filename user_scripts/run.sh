#!/usr/bin/env bash

set -eu
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -d "${SCRIPT_DIR}/venv" ]]; then
  python3 -m venv "${SCRIPT_DIR}/venv"
  "${SCRIPT_DIR}/venv/bin/pip3" install -r "${SCRIPT_DIR}/requirements.txt"

  echo "Run ./execute.py"
  exit 1
fi

"${SCRIPT_DIR}/venv/bin/python" execute.py "$@"