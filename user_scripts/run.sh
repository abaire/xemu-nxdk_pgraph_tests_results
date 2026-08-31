#!/usr/bin/env bash

set -eu
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -d "${SCRIPT_DIR}/venv" ]]; then
  echo "Creating virtualenv..."
  python3 -m venv "${SCRIPT_DIR}/venv"
fi

if ! "${SCRIPT_DIR}/venv/bin/python" -c "import xemu_pgraph_ci_tools" 2>/dev/null; then
  "${SCRIPT_DIR}/venv/bin/pip" install "xemu-pgraph-ci-tools @ git+https://github.com/abaire/xemu-pgraph-ci-tools.git"
fi

"${SCRIPT_DIR}/venv/bin/python" -m xemu_pgraph_ci_tools.runner "$@"
