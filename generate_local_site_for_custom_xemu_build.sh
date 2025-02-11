#!/usr/bin/env bash

# Generates a comparison between a local development build of xemu and the most
# recent committed results.

set -eu

local_results_dir=local/results
readonly local_results_dir
local_compare_results_dir=local/compare
readonly local_compare_results_dir
local_site_dir=local/site
readonly local_site_dir
local_binary_dir=local/xemu
readonly local_binary_dir

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <path_to_xemu_repo> [--use-vulkan]"
  exit 1
fi

xemu_dir="${1}"
readonly xemu_dir

use_vulkan=""

shift
set +u
while [ ! -z "${1}" ]; do
  case "${1}" in
  '--use-vulkan'*)
    use_vulkan="--use-vulkan"
    shift
    ;;
  *)
    echo "Ignoring unknown option '${1}'"
    break
    ;;
  esac
done
set -u

if [[ ! -d "${xemu_dir}" ]]; then
  echo "Invalid xemu repository root"
  exit 1
fi

xemu_binary="${xemu_dir}/build/qemu-system-i386"

if [[ ! -x "${xemu_binary}" ]]; then
  echo "Invalid xemu repository root - missing ${xemu_binary}. Did you forget to build?"
  exit 1
fi

# Avoid leaving/modifying xemu.toml files owned by the user by copying to a local cache dir.
mkdir -p "${local_binary_dir}"
cp "${xemu_binary}" "${local_binary_dir}/"
xemu_binary="${local_binary_dir}/qemu-system-i386"

readonly xemu_binary

if [[ "$(uname)" == "Darwin" ]]; then
  app_bundle="${xemu_dir}/dist/xemu.app"
  if [[ ! -d "${app_bundle}" ]]; then
    echo "Missing xemu.app bundle at ${app_bundle}. Did you forget to build?"
    exit 1
  fi

  cp -R "${app_bundle}" "${local_binary_dir}/"
  app_bundle="${local_binary_dir}/xemu.app"
  readonly app_bundle

  library_path="${app_bundle}/Contents/Libraries/$(uname -m)"
  readonly library_path
  if [[ ! -d "${library_path}" ]]; then
    echo "Missing libraries for $(uname -m) in ${app_bundle}. Cannot set DYLD_FALLBACK_LIBRARY_PATH, xemu will fail."
    exit 1
  fi

  export DYLD_FALLBACK_LIBRARY_PATH="${library_path}:DYLD_FALLBACK_LIBRARY_PATH"
fi

if [[ ! -d ".venv" ]]; then
  echo "Setting up Python virtualenv"
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/pip install -r .github/scripts/requirements.txt
fi

function execute_tests() {
  local output
  local exit_result
  local command

  set +e
  command=(
    .venv/bin/python3
    execute.py
    --xemu "${xemu_binary}"
    --no-bundle
    -R "${local_results_dir}"
    -f
    "${use_vulkan}"
  )

  echo "Executing tests: ${command[*]}"
  output=$("${command[@]}" 2>&1)
  exit_result=$?
  set -e

  if [[ ${exit_result} && ${exit_result} -ne 200 ]]; then
    echo "Test result generation failed"
    echo "${output}"
    exit ${exit_result}
  fi
}


newest_result=""
function find_newest_result_in_dir() {
  local search_root="${1}"

  # find all results.json and get the modification timestamp.
  #   Feb  8 11:11:46 2025 local_results/fake/results.json
  # Sort by newest modification and pull out the path to the result
  newest_result=$( \
      find "${search_root}" -name results.json -exec stat -f "%Sm %N" {} \; \
          | sort -nr \
          | head -n 1 \
          | tr -s ' ' \
          | cut -d ' ' -f5
      )
  newest_result=$(dirname "${newest_result}")
}


newest_official_result=""
function find_newest_official_result() {
  newest_official_result="$(ls -d results/* | sort | tail -n 1)"
  find_newest_result_in_dir "${newest_official_result}"
  newest_official_result="${newest_result}"
}


function compare_results() {
  local output
  local latest_local_result
  local newest_official_result
  local command

  find_newest_result_in_dir "${local_results_dir}"
  latest_local_result="${newest_result}"

  find_newest_official_result

  echo "Comparing ${latest_local_result} vs ${newest_official_result}"

  command=(
    .venv/bin/python3
    compare.py
    "${latest_local_result}"
    --against "${newest_official_result}"
    --output-dir "${local_compare_results_dir}"
  )

  "${command[@]}"
}

function build_site() {
  echo "Building local site"
  command=(
    .venv/bin/python3
    .github/scripts/generate_results_site.py
    "${local_results_dir}"
    "${local_site_dir}"
    --comparison-dir "${local_compare_results_dir}"
    --base-url "file://$(realpath)"
    --golden-results-dir results
  )

  "${command[@]}"
}

execute_tests
compare_results
build_site

