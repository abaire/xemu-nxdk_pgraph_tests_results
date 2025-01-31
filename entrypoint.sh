#!/usr/bin/env bash

set -eu
set -o pipefail

cache_dir="${PWD}/cache"
readonly cache_dir

results_dir="${PWD}/results"
readonly results_dir

inputs_dir="${PWD}/inputs"
readonly inputs_dir

extract_xiso_working_path="${HOME}/.local/share/nxdk-pgraph-test-repacker/extract-xiso"
readonly extract_xiso_working_path
extract_xiso_cache_path="${cache_dir}/extract-xiso"
readonly extract_xiso_cache_path

nxdk_pgraph_tests_repo_api=https://api.github.com/repos/abaire/nxdk_pgraph_tests
readonly nxdk_pgraph_tests_repo_api
pgraph_tag="latest"

xemu_repo_api=https://api.github.com/repos/xemu-project/xemu
readonly xemu_repo_api
xemu_tag="latest"

xemu_hdd_repo_api=https://api.github.com/repos/xemu-project/xemu-hdd-image
readonly xemu_hdd_repo_api

iso=""
xemu=""
hdd=""

function print_help_and_exit() {
  echo "Usage: $0 <option ...>"
  echo ""
  echo "Options:"
  echo "  --help             - Print this message"
  echo "  --iso <file>       - Use the given nxdk_pgraph_tests ISO instead of downloading automatically"
  echo "  --xemu <file>      - Use the given xemu AppImage instead of downloading automatically"
  echo "  --pgraph-tag <tag> - Use the given nxdk_pgraph_tests release instead of the latest"
  echo "  --xemu-tag <tag>   - Use the given xemu release instead of the latest"
  echo "  --hdd <file>       - Use the given qcow2 image instead of downloading automatically"
  exit 1
}

# Extracts a tag and download URL from a GitHub release.
github_artifact_info_tag=""
github_artifact_info_url=""
function fetch_github_artifact_info() {
  local api_url
  api_url=${1}
  local jq_url_extraction_command
  jq_url_extraction_command=${2}

  local release_info
  release_info=$(
    curl -s \
                 -H "Accept: application/vnd.github+json" \
                 -H "X-GitHub-Api-Version: 2022-11-28" \
                 "${api_url}"
  )

  github_artifact_info_tag=$(
    echo "${release_info}" | jq -r '.tag_name'
  )

  github_artifact_info_url=$(
    echo "${release_info}" | jq -r "${jq_url_extraction_command}"
  )
}

# Downloads an artifact from the given URL if it does not already exist.
function download_artifact() {
  local target
  local url
  target="${1}"
  url="${2}"

  if [[ ! -e "${target}" ]]; then
    echo "- Downloading ${target} from ${url}..."
    curl -s -L "${url}" --output "${target}"
  else
    echo "- Found cached ${target}"
  fi
}

function download_latest_iso() {
  echo "Fetching info on latest nxdk_pgraph_tests ISO..."

  fetch_github_artifact_info \
      "${nxdk_pgraph_tests_repo_api}/releases/${pgraph_tag}" \
      '.assets[] | select(.name | contains(".iso")).browser_download_url'

  local tag
  tag="${github_artifact_info_tag}"

  local iso_url
  iso_url=${github_artifact_info_url}

  iso="${cache_dir}/nxdk_pgraph_tests-${tag}.iso"
  download_artifact "${iso}" "${iso_url}"
}

function download_latest_xemu() {
  echo "Fetching info on latest xemu AppImage"

  fetch_github_artifact_info \
      "${xemu_repo_api}/releases/${xemu_tag}" \
      '.assets[] | select(.name | startswith("xemu-v") and contains(".AppImage") and (contains("-dbg-") | not)) |.browser_download_url'

  local tag
  tag="${github_artifact_info_tag}"

  local app_image_url
  app_image_url=${github_artifact_info_url}

  xemu="${cache_dir}/$(basename "${app_image_url}")"

  download_artifact "${xemu}" "${app_image_url}"

  chmod 0700 "${xemu}"
}

function download_xbox_hdd_image() {
  hdd="${cache_dir}/xbox_hdd.qcow2.zip"

  if [[ -e "${hdd}" ]]; then
    echo "Using cached HDD image at ${hdd}"
    return
  fi

  echo "Fetching info on latest xemu HDD image"

  fetch_github_artifact_info \
      "${xemu_hdd_repo_api}/releases/latest" \
      '.assets[] | select(.name | contains(".qcow2.zip")).browser_download_url'

  echo "${github_artifact_info_tag}" "${github_artifact_info_url}"

  local tag
  tag="${github_artifact_info_tag}"

  local artifact_url
  artifact_url=${github_artifact_info_url}

  local hdd_archive
  hdd_archive="${cache_dir}/$(basename "${artifact_url}")"

  download_artifact "${hdd_archive}" "${artifact_url}"

  unzip -o -q "${hdd_archive}" -d "${cache_dir}/"
}

function restore_extract_xiso() {
  if [[ -e "${extract_xiso_cache_path}" ]]; then
    echo "Found cached extract-xiso"
    mkdir -p "$(dirname "${extract_xiso_working_path}")"
    cp "${extract_xiso_cache_path}" "${extract_xiso_working_path}"
  fi
}

function cache_extract_xiso() {
  if [[ -e "${extract_xiso_working_path}" ]]; then
    mkdir -p "$(dirname "${extract_xiso_cache_path}")"
    cp "${extract_xiso_working_path}" "${extract_xiso_cache_path}"
  fi
}

function install_xemu_toml() {
  if [[ -e "${cache_dir}/xemu.toml" ]]; then
    return
  fi

  cat <<EOF > "${cache_dir}/xemu.toml"
[general]
show_welcome = false
skip_boot_anim = true

[general.updates]
check = false

[net]
enable = true

[sys]
mem_limit = '64'

[sys.files]
bootrom_path = '${inputs_dir}/mcpx.bin'
flashrom_path = '${inputs_dir}/bios.bin'
eeprom_path = '${inputs_dir}/xemu/eeprom.bin'
hdd_path = '${inputs_dir}/xemu/xbox_hdd.qcow2'
EOF
}

function main() {
  set +u
  while [ ! -z "${1}" ]; do
    case "${1}" in
    '--iso'*)
      iso="${2}"
      shift 2
      ;;

    '--pgraph-tag'*)
      pgraph_tag="${2}"
      shift 2
      ;;

    '--xemu-tag'*)
      xemu_tag="${2}"
      shift 2
      ;;

    '--xemu'*)
      xemu="${2}"
      shift 2
      ;;

    '--hdd'*)
      hdd="${2}"
      shift 2
      ;;

    '-h'*)
      print_help_and_exit
      ;;
    '-?'*)
      print_help_and_exit
      ;;
    '--help'*)
      print_help_and_exit
      ;;
    *)
      echo "Ignoring unknown option '${1}'"
      break
      ;;
    esac
  done
  set -u

  if [[ "${iso:+x}" != "x" ]]; then
    download_latest_iso
  fi

  if [[ "${iso:+x}" != "x" ]]; then
    echo "No ISO found, exiting."
    echo ""
    print_help_and_exit
  fi


  if [[ "${xemu:+x}" != "x" ]]; then
    download_latest_xemu
  fi

  if [[ "${xemu:+x}" != "x" ]]; then
    echo "No xemu AppImage found, exiting."
    echo ""
    print_help_and_exit
  fi

  if [[ ! -x "${xemu}" ]]; then
    echo "xemu AppImage at ${xemu} is not executable"
    echo ""
    print_help_and_exit
  fi

  if [[ "${hdd:+x}" != "x" ]]; then
    download_xbox_hdd_image
  fi

  if [[ ! -e "${hdd}" ]]; then
    echo "No xemu hard drive image found at '${hdd}', exiting."
    echo ""
    print_help_and_exit
  fi

  restore_extract_xiso

  if [[ ! -e .venv/bin/activate ]]; then
    python -m venv .venv
  fi

  . .venv/bin/activate

  pip install -r requirements.txt

  install_xemu_toml

  set +e
  python -m nxdk_pgraph_test_runner \
    --emulator-commandline "${xemu} --appimage-extract-and-run -dvd_path \"{ISO}\"" \
    --override-ftp-ip 10.0.2.2 \
    --iso-path "${iso}" \
    -I lo \
    --xbox-artifact-path "c:\\pgraph_tests" \
    --output-dir "${results_dir}" \
    --test-failure-retries 2 \
    ;
  local exit_code
  exit_code=$?
  set -e

  cache_extract_xiso

  return ${exit_code}
}

main "$@"
