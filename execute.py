#!/usr/bin/env python3

# ruff: noqa: T201 `print` found

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from shutil import SameFileError
from time import sleep
from typing import Any
from urllib.request import urlcleanup, urlretrieve

import nxdk_pgraph_test_runner
import requests
from nxdk_pgraph_test_runner import Config
from nxdk_pgraph_test_runner.emulator_output import EmulatorOutput
from nxdk_pgraph_test_runner.host_profile import HostProfile
from nxdk_pgraph_test_runner.runner import get_output_directory

logger = logging.getLogger(__name__)


def _fetch_github_release_info(api_url: str, tag: str = "latest") -> dict[str, Any] | None:
    full_url = f"{api_url}/releases/latest" if not tag or tag == "latest" else f"{api_url}/releases"

    def fetch_and_filter(url: str):
        try:
            response = requests.get(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=15,
            )
            response.raise_for_status()
            release_info = response.json()

        except requests.exceptions.RequestException:
            logger.exception("Failed to retrieve information from %s", url)
            return None

        if isinstance(release_info, list):
            release_info = _filter_release_info_by_tag(release_info, tag)
        if release_info:
            return release_info

        if not response.links:
            return None

        next_link = response.links.get("next", {}).get("url")
        if not next_link:
            return None
        next_link = next_link + "&per_page=60"
        return fetch_and_filter(next_link)

    return fetch_and_filter(full_url)


def _download_artifact(target_path: str, download_url: str, artifact_path_override: str | None = None) -> bool:
    """Downloads an artifact from the given URL, if it does not already exist. Returns True if download was needed."""
    if os.path.exists(target_path):
        return False

    if artifact_path_override and os.path.exists(artifact_path_override):
        return True

    if not download_url.startswith("https://"):
        logger.error("Download URL '%s' has unexpected scheme", download_url)
        msg = f"Bad download_url '{download_url} - non HTTPS scheme"
        raise ValueError(msg)

    logger.debug("Downloading %s from %s", target_path, download_url)
    if artifact_path_override:
        target_path = artifact_path_override
        logger.debug(
            "> downloading artifact %s containing %s",
            artifact_path_override,
            target_path,
        )
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    urlretrieve(download_url, target_path)  # noqa: S310 - checked just above
    urlcleanup()

    return True


def _filter_release_info_by_tag(release_infos: list[dict[str, Any]], tag: str) -> dict[str, Any] | None:
    for info in release_infos:
        if info.get("tag_name") == tag:
            return info
    return None


def _download_tester_iso(output_dir: str, tag: str = "latest") -> str | None:
    logger.info("Fetching info on nxdk_pgraph_tests ISO at release tag %s...", tag)

    release_info = _fetch_github_release_info("https://api.github.com/repos/abaire/nxdk_pgraph_tests", tag)
    if not release_info:
        return None

    release_tag = release_info.get("tag_name")
    if not release_tag:
        logger.error("Failed to retrieve release tag from GitHub.")
        return None

    download_url = ""
    for asset in release_info.get("assets", []):
        if not asset.get("name", "").endswith(".iso"):
            continue
        download_url = asset.get("browser_download_url", "")
        break

    if not download_url:
        logger.error("Failed to fetch download URL for latest nxdk_pgraph_tests release")
        return None

    target_file = os.path.join(output_dir, f"nxdk_pgraph_tests-{release_tag}.iso")
    _download_artifact(target_file, download_url)

    return target_file


def _macos_extract_app(archive_file: str, target_app_bundle: str) -> None:
    """Extracts the xemu.app bundle from the given archive and renames it."""
    app_bundle_directory = os.path.dirname(target_app_bundle)

    try:
        with zipfile.ZipFile(archive_file, "r") as zip_ref:
            os.makedirs(app_bundle_directory, exist_ok=True)

            for file_info in zip_ref.infolist():
                if file_info.filename.startswith("xemu.app/") and not file_info.is_dir():
                    zip_ref.extract(file_info, app_bundle_directory)

            if not os.path.isfile(os.path.join(app_bundle_directory, "xemu.app", "Contents", "MacOS", "xemu")):
                msg = f"xemu archive was downloaded at '{archive_file}' but app bundle could not be extracted"
                raise ValueError(msg)

    except FileNotFoundError:
        logger.exception("Archive not found when extracting xemu app bundle")
        raise
    except zipfile.BadZipFile:
        logger.exception("Invalid zip archive when extracting xemu app bundle")
        raise


def _windows_extract_app(archive_file: str, target_executable: str) -> None:
    """Extracts xemu.exe from the given archive."""

    try:
        with zipfile.ZipFile(archive_file, "r") as zip_ref:
            for file_info in zip_ref.infolist():
                if file_info.filename == "xemu.exe":
                    target_dir = os.path.dirname(target_executable)
                    zip_ref.extract(file_info, target_dir)
                    if os.path.basename(target_executable) != "xemu.exe":
                        os.rename(os.path.join(target_dir, "xemu.exe"), target_executable)
                    return

    except FileNotFoundError:
        logger.exception("Archive not found when extracting xemu.exe")
        raise
    except zipfile.BadZipFile:
        logger.exception("Invalid zip archive when extracting xemu.exe")
        raise


def _download_xemu(output_dir: str, tag: str = "latest") -> str | None:
    logger.info("Fetching info on xemu at release tag %s...", tag)
    release_info = _fetch_github_release_info("https://api.github.com/repos/xemu-project/xemu", tag)
    if not release_info:
        return None

    release_tag = release_info.get("tag_name")
    if not release_tag:
        logger.error("Failed to retrieve release tag from GitHub.")
        return None

    system = platform.system()
    if system == "Linux":
        # xemu-v0.8.15-x86_64.AppImage
        def check_asset(asset_name: str) -> bool:
            if not asset_name.startswith("xemu-v") or "-dbg-" in asset_name:
                return False
            return asset_name.endswith(".AppImage") and platform.machine() in asset_name
    elif system == "Darwin":
        # xemu-macos-universal-release.zip
        def check_asset(asset_name: str) -> bool:
            return asset_name == "xemu-macos-universal-release.zip"
    elif system == "Windows":
        # xemu-win-x86_64-release.zip
        def check_asset(asset_name: str) -> bool:
            if not asset_name.startswith("xemu-win-") or not asset_name.endswith("release.zip"):
                return False
            platform_name = platform.machine()
            if platform_name == "AMD64":
                platform_name = "x86_64"
            return platform_name.lower() in asset_name
    else:
        msg = f"System '{system} not supported"
        raise NotImplementedError(msg)

    asset_name = ""
    download_url = ""
    for asset in release_info.get("assets", []):
        asset_name = asset.get("name", "")
        if not check_asset(asset_name):
            continue
        download_url = asset.get("browser_download_url", "")
        break

    if not download_url:
        logger.error("Failed to fetch download URL for latest nxdk_pgraph_tests release")
        return None

    if system == "Linux":
        target_file = os.path.join(output_dir, asset_name)
        artifact_path_override = None
    elif system == "Darwin":
        target_file = os.path.join(output_dir, f"xemu-macos-{release_tag}", "xemu.app")
        artifact_path_override = f"{target_file}.zip"
    elif system == "Windows":
        target_file = os.path.join(output_dir, "xemu.exe")
        artifact_path_override = f"{target_file}.zip"
    else:
        msg = f"System '{system} not supported"
        raise NotImplementedError(msg)

    logger.debug("Xemu %s %s", target_file, download_url)
    was_downloaded = _download_artifact(target_file, download_url, artifact_path_override)

    if was_downloaded:
        if system == "Linux":
            os.chmod(target_file, 0o700)
        elif system == "Darwin":
            _macos_extract_app(artifact_path_override, target_file)
        elif system == "Windows":
            _windows_extract_app(artifact_path_override, target_file)

    return target_file


def _download_xemu_hdd(output_dir: str, tag: str = "latest") -> str | None:
    logger.info("Fetching info on xemu_hdd at release tag %s...", tag)

    release_info = _fetch_github_release_info("https://api.github.com/repos/xemu-project/xemu-hdd-image", tag)
    if not release_info:
        return None

    release_tag = release_info.get("tag_name")
    if not release_tag:
        logger.error("Failed to retrieve release tag from GitHub.")
        return None

    download_url = ""
    for asset in release_info.get("assets", []):
        if not asset.get("name", "").endswith(".zip"):
            continue
        download_url = asset.get("browser_download_url", "")
        break

    if not download_url:
        logger.error("Failed to fetch download URL for latest nxdk_pgraph_tests release")
        return None

    target_file = os.path.join(output_dir, f"xemu_hdd-{release_tag}.qcow2")
    archive_file = f"{target_file}.zip"
    if _download_artifact(target_file, download_url, archive_file):
        try:
            with zipfile.ZipFile(archive_file, "r") as zip_ref:
                for file_info in zip_ref.infolist():
                    if file_info.filename == "xbox_hdd.qcow2":
                        zip_ref.extract(file_info, output_dir)
                        hdd_image = os.path.join(output_dir, "xbox_hdd.qcow2")
                        os.rename(hdd_image, target_file)
                        break

        except FileNotFoundError:
            logger.exception("Archive not found when extracting xemu_hdd app bundle")
            raise
        except zipfile.BadZipFile:
            logger.exception("Invalid zip archive when extracting xemu_hdd app bundle")
            raise

    return target_file


def _generate_xemu_toml(
    file_path: str,
    bootrom_path: str,
    flashrom_path: str,
    eeprom_path: str,
    hdd_path: str,
    *,
    use_vulkan: bool = False,
) -> None:
    content = [
        "[general]",
        "show_welcome = false",
        "skip_boot_anim = true",
        "",
        "[general.updates]",
        "check = false",
        "",
        "[net]",
        "enable = true",
        "",
        "[sys]",
        "mem_limit = '64'",
        "",
        "[sys.files]",
        f"bootrom_path = '{bootrom_path}'",
        f"flashrom_path = '{flashrom_path}'",
        f"eeprom_path = '{eeprom_path}'",
        f"hdd_path = '{hdd_path}'",
    ]

    if use_vulkan:
        content.extend(["", "[display]", "renderer = 'VULKAN'"])

    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w") as outfile:
        outfile.write("\n".join(content))


def _build_macos_xemu_binary_paths(xemu_app_bundle_path: str) -> tuple[str, str]:
    contents_path = os.path.join(xemu_app_bundle_path, "Contents")
    library_path = ":".join(
        [
            os.path.join(contents_path, "Libraries", platform.uname().machine),
            os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", ""),
        ]
    )
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = library_path

    xemu_binary = os.path.join(contents_path, "MacOS", "xemu")
    os.chmod(xemu_binary, 0o700)
    return xemu_binary, os.path.join(contents_path, "Resources")


def _build_emulator_command(xemu_path: str, *, no_bundle: bool = False) -> tuple[str, str]:
    portable_mode_config_path = os.path.dirname(xemu_path)

    system = platform.system()
    if system == "Darwin":
        if not no_bundle:
            xemu_path, portable_mode_config_path = _build_macos_xemu_binary_paths(xemu_path)
    elif system == "Linux":
        if xemu_path.endswith("AppImage"):
            # AppImages need to have the xemu.toml file within their home dir.
            portable_mode_config_path = os.path.join(f"{xemu_path}.home", ".local", "share", "xemu", "xemu")
    elif system == "Windows":
        pass
    else:
        msg = f"Platform {system} not supported."
        raise NotImplementedError(msg)

    return xemu_path + " -dvd_path {ISO}", os.path.join(portable_mode_config_path, "xemu.toml")


def _determine_output_directory(results_path: str, emulator_command: str, *, is_vulkan: bool) -> str | None:
    command = Config(emulator_command=emulator_command).build_emulator_command("__this_file_does_not_exist")
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=1)
        stderr = result.stderr
    except subprocess.TimeoutExpired as err:
        # Windows Python 3.13 returns a string rather than bytes.
        stderr = err.stderr.decode() if isinstance(err.stderr, bytes) else err.stderr

        # Give tne GL subsystem time to settle after the hard kill. Prevents deadlock in get_output_directory.
        sleep(0.5)
    except subprocess.CalledProcessError as err:
        stderr = err.stderr.decode() if isinstance(err.stderr, bytes) else err.stderr
        logger.error(stderr)  # noqa: TRY400 Use `logging.exception` instead of `logging.error`
        logger.exception(err)  # noqa: TRY401 Redundant exception object included in `logging.exception` call
        raise

    emulator_output = EmulatorOutput.parse(stdout=[], stderr=stderr.split("\n"))
    output_directory = get_output_directory(emulator_output.emulator_version, HostProfile(), is_vulkan=is_vulkan)

    return os.path.join(
        results_path,
        output_directory,
    )


def run(
    iso_path: str,
    work_path: str,
    inputs_path: str,
    results_path: str,
    xemu_path: str,
    hdd_path: str,
    *,
    overwrite_existing_outputs: bool,
    no_bundle: bool = False,
    use_vulkan: bool = False,
):
    emulator_command, portable_mode_config_path = _build_emulator_command(xemu_path, no_bundle=no_bundle)
    if not emulator_command:
        return 1

    _generate_xemu_toml(
        portable_mode_config_path,
        bootrom_path=os.path.join(inputs_path, "mcpx.bin"),
        flashrom_path=os.path.join(inputs_path, "bios.bin"),
        eeprom_path=os.path.join(inputs_path, "eeprom.bin"),
        hdd_path=hdd_path,
        use_vulkan=use_vulkan,
    )

    output_directory = _determine_output_directory(
        results_path, emulator_command=emulator_command, is_vulkan=use_vulkan
    )
    if not overwrite_existing_outputs and os.path.isdir(output_directory):
        logger.error("Output directory %s already exists, exiting", output_directory)
        return 200

    config = Config(
        work_dir=work_path,
        output_dir=results_path,
        emulator_command=emulator_command,
        iso_path=iso_path,
        ftp_ip="127.0.0.1",
        ftp_ip_override="10.0.2.2",
        xbox_artifact_path=r"c:\nxdk_pgraph_tests",
        test_failure_retries=2,
        network_config={"config_automatic": True},
    )

    ret = nxdk_pgraph_test_runner.entrypoint(config)
    if os.path.isdir(output_directory):
        with open(os.path.join(output_directory, "renderer.json"), "w") as outfile:
            json.dump({"vulkan": use_vulkan}, outfile)

    return ret


def _ensure_path(path: str) -> str:
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(path, exist_ok=True)
    return path


def _ensure_cache_path(cache_path: str) -> str:
    if not cache_path:
        msg = "cache_path may not be empty"
        raise ValueError(msg)
    return _ensure_path(cache_path)


def _ensure_results_path(results_path: str) -> str:
    if not results_path:
        msg = "results_path may not be empty"
        raise ValueError(msg)
    return _ensure_path(results_path)


def _process_arguments_and_run():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verbose",
        "-v",
        help="Enables verbose logging information",
        action="store_true",
    )
    parser.add_argument("--iso", "-I", help="Path to the nxdk_pgraph_tests.iso xiso file.")
    parser.add_argument(
        "--pgraph-tag",
        metavar="github_release_tag",
        default="latest",
        help="Release tag to use when downloading nxdk_pgraph_tests iso from GitHub.",
    )
    parser.add_argument("--xemu", "-X", help="Path to the xemu executable.")
    parser.add_argument(
        "--xemu-tag",
        metavar="github_release_tag",
        default="latest",
        help="Release tag to use when downloading xemu from GitHub.",
    )
    parser.add_argument("--hdd", "-H", help="Path to xemu hard disk image to use.")
    parser.add_argument(
        "--bios",
        "-B",
        default="inputs/bios.bin",
        help="Path to Xbox BIOS image to use.",
    )
    parser.add_argument(
        "--mcpx",
        "-M",
        default="inputs/mcpx.bin",
        help="Path to Xbox MCPX boot ROM image to use.",
    )
    parser.add_argument("--cache-path", "-C", default="cache", help="Path to persistent cache area.")
    parser.add_argument("--temp-path", help="Temporary path used during execution of tests")
    parser.add_argument(
        "--results-path",
        "-R",
        default="results",
        help="Path to directory into which results should be stored.",
    )
    parser.add_argument(
        "--overwrite-existing-outputs",
        "-f",
        action="store_true",
        help="Run even if the expected outputs already exist.",
    )
    parser.add_argument(
        "--no-bundle", action="store_true", help="Suppress attempt to set DYLD_FALLBACK_LIBRARY_PATH on macOS."
    )
    parser.add_argument("--use-vulkan", action="store_true", help="Use the Vulkan renderer instead of OpenGL.")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level)

    cache_path = _ensure_cache_path(args.cache_path)
    results_path = _ensure_results_path(args.results_path)

    if args.iso:
        iso = os.path.abspath(os.path.expanduser(args.iso))
    else:
        iso = _download_tester_iso(cache_path, args.pgraph_tag)
    if not iso or not os.path.isfile(iso):
        logger.error("Invalid ISO path '%s'", iso)
        return 1

    xemu = os.path.abspath(os.path.expanduser(args.xemu)) if args.xemu else _download_xemu(cache_path, args.xemu_tag)
    if not xemu:
        logger.error("Failed to download xemu")
        return 1
    if not os.path.exists(xemu):
        logger.error("Invalid xemu path '%s'", xemu)
        return 1

    hdd = os.path.abspath(os.path.expanduser(args.hdd)) if args.hdd else _download_xemu_hdd(cache_path)
    if not hdd:
        logger.error("Failed to download xemu_hdd")
        return 1
    if not os.path.isfile(hdd):
        logger.error("Invalid xemu_hdd path '%s'", hdd)
        return 1

    def _copy_inputs_and_run(temp_path: str, *, overwrite_existing_outputs: bool) -> int:
        inputs_path = os.path.join(temp_path, "inputs")
        os.makedirs(inputs_path, exist_ok=True)
        with contextlib.suppress(SameFileError):
            shutil.copy(args.mcpx, os.path.join(inputs_path, "mcpx.bin"))
        with contextlib.suppress(SameFileError):
            shutil.copy(args.bios, os.path.join(inputs_path, "bios.bin"))
        return run(
            iso_path=iso,
            work_path=temp_path,
            inputs_path=inputs_path,
            results_path=results_path,
            xemu_path=xemu,
            hdd_path=hdd,
            overwrite_existing_outputs=overwrite_existing_outputs,
            no_bundle=args.no_bundle,
            use_vulkan=args.use_vulkan,
        )

    if args.temp_path:
        return _copy_inputs_and_run(
            _ensure_path(args.temp_path), overwrite_existing_outputs=args.overwrite_existing_outputs
        )

    with tempfile.TemporaryDirectory() as temp_path:
        return _copy_inputs_and_run(_ensure_path(temp_path), overwrite_existing_outputs=args.overwrite_existing_outputs)


if __name__ == "__main__":
    sys.exit(_process_arguments_and_run())
