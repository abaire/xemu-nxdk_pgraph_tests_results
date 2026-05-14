#!/usr/bin/env python3

# ruff: noqa: T201 `print` found
# ruff: noqa: RUF001 String contains ambiguous

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import glob
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
from subprocess import CalledProcessError
from time import sleep
from typing import TYPE_CHECKING, Any
from urllib.request import urlcleanup, urlretrieve

import nxdk_pgraph_test_runner
import requests
from nxdk_pgraph_test_repacker import ensure_extract_xiso, extract_config, repack_config
from nxdk_pgraph_test_runner import Config
from nxdk_pgraph_test_runner.emulator_output import EmulatorOutput
from nxdk_pgraph_test_runner.host_profile import HostProfile
from nxdk_pgraph_test_runner.runner import get_output_directory

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

if TYPE_CHECKING:
    from collections.abc import Collection

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import threading
    import time

    import win32con
    import win32gui

    class AbortDialogHandler:
        def __init__(self):
            self.stop_event = threading.Event()
            self.dialog_found = False

        def find_and_click_abort(self):
            """Periodically scans for the CRT assertion dialog and clicks 'Abort'."""
            dialog_title = "Microsoft Visual C++ Runtime Library"

            while not self.stop_event.is_set():
                hwnd = win32gui.FindWindow(None, dialog_title)
                if hwnd:
                    print(f"ℹ️ Found dialog: '{dialog_title}'")

                    def enum_child_proc(child_hwnd, lparam):
                        del lparam
                        button_text = win32gui.GetWindowText(child_hwnd)
                        if "abort" in button_text.lower():
                            print("   -> Found 'Abort' button. Clicking it now.")
                            win32gui.SendMessage(child_hwnd, win32con.BM_CLICK, 0, 0)
                            self.dialog_found = True
                        return True

                    win32gui.EnumChildWindows(hwnd, enum_child_proc, None)

                    if self.dialog_found:
                        time.sleep(2)

                time.sleep(0.2)

        def start(self):
            """Starts the handler in a background thread."""
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.find_and_click_abort, daemon=True)
            self.thread.start()
            print("✅ Dialog handler started in the background.")

        def stop(self):
            """Stops the background handler."""
            self.stop_event.set()
            print("⏹️ Dialog handler stopped.")


def _fetch_github_release_info(api_url: str, tag: str = "latest") -> dict[str, Any] | None:
    full_url = f"{api_url}/releases/latest" if not tag or tag == "latest" else f"{api_url}/releases?per_page=60"

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
        if "per_page=60" not in next_link:
            next_link = next_link + "&per_page=60"
        return fetch_and_filter(next_link)

    return fetch_and_filter(full_url)


def _download_artifact(
    target_path: str, download_url: str, artifact_path_override: str | None = None, *, force_download: bool = False
) -> bool:
    """Downloads an artifact from the given URL, if it does not already exist. Returns True if download was needed."""
    if os.path.exists(target_path) and not force_download:
        return False

    if artifact_path_override and os.path.exists(artifact_path_override) and not force_download:
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
            if not asset_name.startswith("xemu-") or "-dbg-" in asset_name:
                return False
            return asset_name.endswith(".AppImage") and platform.machine() in asset_name
    elif system == "Darwin":
        # xemu-macos-universal-release.zip
        def check_asset(asset_name: str) -> bool:
            return asset_name == "xemu-macos-universal-release.zip" or asset_name.endswith(
                "-macos-universal-unsigned.zip"
            )
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
        logger.error("Failed to fetch download URL for latest xemu release")
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

    tag_info_file_path = os.path.join(output_dir, "xemu-tag.info")

    requested_version = release_info.get("tag_name")
    if not requested_version or not os.path.isfile(tag_info_file_path):
        force_download = True
    else:
        with open(tag_info_file_path) as tag_info_file:
            cached_tag = tag_info_file.readline()
            force_download = cached_tag != requested_version

    was_downloaded = _download_artifact(
        target_file, download_url, artifact_path_override, force_download=force_download
    )

    if was_downloaded:
        if system == "Linux":
            os.chmod(target_file, 0o700)
        elif system == "Darwin":
            _macos_extract_app(artifact_path_override, target_file)
        elif system == "Windows":
            _windows_extract_app(artifact_path_override, target_file)

        with open(tag_info_file_path, "w") as tag_info_file:
            tag_info_file.write(requested_version)

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


def _build_emulator_command(
    xemu_path: str, *, no_bundle: bool = False, custom_toml_path: str | None = None
) -> tuple[str, str]:
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

    cmd = xemu_path + " -dvd_path {ISO}"
    if custom_toml_path:
        cmd += f' -config_path "{custom_toml_path}"'
        toml_path = custom_toml_path
    else:
        toml_path = os.path.join(portable_mode_config_path, "xemu.toml")

    return cmd, toml_path


def _determine_output_directory(results_path: str, emulator_command: str, *, is_vulkan: bool) -> str | None:
    command = Config(emulator_command=emulator_command + " -display none").build_emulator_command(
        "__this_file_does_not_exist"
    )
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


def _get_macos_bundle_identifier(xemu_path: str, *, no_bundle: bool) -> str | None:
    if no_bundle or platform.system() != "Darwin":
        return None

    command = ["mdls", "-name", "kMDItemCFBundleIdentifier", "-r", xemu_path]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return result.stdout


def _set_apple_persistence_ignore_state(macos_bundle_identifier: str, *, ignore: bool | None) -> bool | None:
    command = [
        "defaults",
        "read",
        macos_bundle_identifier,
        "ApplePersistenceIgnoreState",
    ]

    current_value = None
    with contextlib.suppress(CalledProcessError):
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        current_value = result.stdout.startswith("1")

    if current_value != ignore:
        if ignore is None:
            command = [
                "defaults",
                "delete",
                macos_bundle_identifier,
                "ApplePersistenceIgnoreState",
            ]
        else:
            command = [
                "defaults",
                "write",
                macos_bundle_identifier,
                "ApplePersistenceIgnoreState",
                "-bool",
                "true" if ignore else "false",
            ]
        subprocess.run(command, capture_output=True, text=True, check=True)

    return current_value


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
    just_suites: Collection[str] | None = None,
    custom_toml_path: str | None = None,
):
    emulator_command, toml_path = _build_emulator_command(
        xemu_path, no_bundle=no_bundle, custom_toml_path=custom_toml_path
    )
    if not emulator_command:
        return 1

    _generate_xemu_toml(
        toml_path,
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

    test_failure_retries = 2

    config = Config(
        work_dir=work_path,
        output_dir=results_path,
        emulator_command=emulator_command,
        iso_path=iso_path,
        ftp_ip="127.0.0.1",
        ftp_ip_override="10.0.2.2",
        xbox_artifact_path=r"c:\nxdk_pgraph_tests",
        test_failure_retries=test_failure_retries,
        network_config={"config_automatic": True},
        suite_allowlist=just_suites,
    )

    # Disable persistence on macOS to avoid modal dialogs after (expected) crashes.
    macos_bundle_identifier = _get_macos_bundle_identifier(xemu_path, no_bundle=no_bundle)
    original_ignore_value: bool | None = None
    if macos_bundle_identifier:
        original_ignore_value = _set_apple_persistence_ignore_state(macos_bundle_identifier, ignore=True)

    handler: AbortDialogHandler | None = None
    if sys.platform == "win32":
        handler = AbortDialogHandler()
        handler.start()

    ret = nxdk_pgraph_test_runner.entrypoint(config)

    if handler:
        handler.stop()

    if os.path.isdir(output_directory):
        with open(os.path.join(output_directory, "renderer.json"), "w") as outfile:
            json.dump({"vulkan": use_vulkan}, outfile)
        with open(os.path.join(output_directory, "runner.json"), "w") as outfile:
            json.dump(
                {
                    "iso": os.path.basename(iso_path),
                    "test_failure_retries": test_failure_retries,
                    "suite_allowlist": just_suites,
                },
                outfile,
            )

        # Truncate full paths to just filenames for artifacts in results.json
        manifest_path = os.path.join(output_directory, "results.json")
        if os.path.isfile(manifest_path):
            with open(manifest_path) as f:
                manifest = json.load(f)
            for state in ("passed", "failed", "flaky"):
                for test_info in manifest.get(state, {}).values():
                    if "artifacts" in test_info:
                        test_info["artifacts"] = [os.path.basename(p) for p in test_info["artifacts"]]
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2, sort_keys=True)

    if macos_bundle_identifier:
        _set_apple_persistence_ignore_state(macos_bundle_identifier, ignore=original_ignore_value)

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


def _extract_info_from_xemu_toml(toml_path: str) -> tuple[str, str] | None:
    toml_path = os.path.abspath(os.path.expanduser(toml_path))
    if os.path.isdir(toml_path):
        toml_path = os.path.join(toml_path, "xemu.toml")
    if not os.path.isfile(toml_path):
        logger.error("No xemu toml file found at '%s'", toml_path)
        return None

    with open(toml_path, "rb") as infile:
        data = tomllib.load(infile)

    files = data.get("sys", {}).get("files", {})
    return files.get("bootrom_path"), files.get("flashrom_path")


def _prepare_sharded_iso(iso_path: str, shard_index: int, shard_count: int, output_iso_path: str) -> bool:
    extract_xiso = ensure_extract_xiso(None)
    if not extract_xiso:
        logger.error("extract-xiso is unavailable")
        return False

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "config.json")
        if not extract_config(iso_path, config_path, extract_xiso):
            logger.error("Failed to extract JSON config for sharding")
            return False

        with open(config_path) as f:
            config_data = json.load(f)

        if "settings" not in config_data:
            config_data["settings"] = {}
        config_data["settings"]["sharding"] = {"index": shard_index, "count": shard_count}

        updated_config_path = os.path.join(tmpdir, "updated_config.json")
        with open(updated_config_path, "w") as f:
            json.dump(config_data, f)

        if not repack_config(iso_path, output_iso_path, updated_config_path, extract_xiso):
            logger.error("Failed to repack ISO for shard %d", shard_index)
            return False

    return True


def _run_shard(
    shard_index: int,
    shard_count: int,
    temp_path: str,
    iso_path: str,
    hdd_path: str,
    mcpx_path: str,
    bios_path: str,
    xemu_path: str,
    results_path: str,
    *,
    overwrite_existing_outputs: bool,
    no_bundle: bool,
    use_vulkan: bool,
    just_suites: Collection[str] | None,
) -> int:
    inputs_path = os.path.join(temp_path, "inputs")
    os.makedirs(inputs_path, exist_ok=True)

    if shard_count > 1:
        effective_iso_path = os.path.join(inputs_path, "test_runner_shard.iso")
        if not _prepare_sharded_iso(iso_path, shard_index, shard_count, effective_iso_path):
            return 1
    else:
        effective_iso_path = iso_path

    with contextlib.suppress(SameFileError):
        shutil.copy(mcpx_path, os.path.join(inputs_path, "mcpx.bin"))
    with contextlib.suppress(SameFileError):
        shutil.copy(bios_path, os.path.join(inputs_path, "bios.bin"))
    hdd_copy = os.path.join(inputs_path, "test_runner_hdd.qcow2")
    with contextlib.suppress(SameFileError):
        shutil.copy(hdd_path, hdd_copy)

    return run(
        iso_path=effective_iso_path,
        work_path=temp_path,
        inputs_path=inputs_path,
        results_path=results_path,
        xemu_path=xemu_path,
        hdd_path=hdd_copy,
        overwrite_existing_outputs=overwrite_existing_outputs,
        no_bundle=no_bundle,
        use_vulkan=use_vulkan,
        just_suites=just_suites,
        custom_toml_path=os.path.join(inputs_path, "xemu.toml"),
    )


def _merge_shard_results(temp_path: str, shard_count: int, final_results_path: str) -> None:
    merged_passed = {}
    merged_failed = {}
    merged_flaky = {}
    merged_missing = []

    output_dir_rel = None

    for i in range(shard_count):
        shard_results_path = os.path.join(temp_path, f"shard_{i}", "results")

        manifest_path = None
        for root, _, files in os.walk(shard_results_path):
            if "results.json" in files:
                manifest_path = os.path.join(root, "results.json")
                break

        if not manifest_path:
            logger.warning("No results.json found for shard %d", i)
            continue

        if not output_dir_rel:
            output_dir_rel = os.path.relpath(os.path.dirname(manifest_path), shard_results_path)

        with open(manifest_path) as f:
            manifest = json.load(f)

        merged_passed.update(manifest.get("passed", {}))
        merged_failed.update(manifest.get("failed", {}))
        merged_flaky.update(manifest.get("flaky", {}))
        merged_missing.extend(manifest.get("missing_artifacts", []))

        src_dir = os.path.dirname(manifest_path)
        dest_dir = os.path.join(final_results_path, output_dir_rel)
        os.makedirs(dest_dir, exist_ok=True)

        for item in os.listdir(src_dir):
            src_item = os.path.join(src_dir, item)
            dest_item = os.path.join(dest_dir, item)
            if os.path.isdir(src_item):
                if not os.path.exists(dest_item):
                    shutil.copytree(src_item, dest_item)
                else:
                    for suite_item in os.listdir(src_item):
                        shutil.copy2(os.path.join(src_item, suite_item), os.path.join(dest_item, suite_item))
            elif item in ("machine_info.txt", "renderer.json", "runner.json") and not os.path.exists(dest_item):
                shutil.copy2(src_item, dest_item)

    if output_dir_rel:
        final_manifest_path = os.path.join(final_results_path, output_dir_rel, "results.json")
        merged_manifest = {"passed": merged_passed, "failed": merged_failed, "flaky": merged_flaky}
        if merged_missing:
            merged_manifest["missing_artifacts"] = merged_missing

        with open(final_manifest_path, "w") as f:
            json.dump(merged_manifest, f, indent=2, sort_keys=True)


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
    parser.add_argument("--just-suites", nargs="+", help="Just run the given suites rather than the full test set.")
    parser.add_argument(
        "--toml",
        "-T",
        help="Import bios and mcpx from an existing xemu install",
        metavar="xemu_toml_path",
    )
    parser.add_argument(
        "--shard-count",
        "-S",
        type=int,
        default=0,
        help="Number of shards to split the execution into (must be > 1 to enable sharding).",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level)

    cache_path = _ensure_cache_path(args.cache_path)
    results_path = _ensure_results_path(args.results_path)

    xemu = os.path.abspath(os.path.expanduser(args.xemu)) if args.xemu else _download_xemu(cache_path, args.xemu_tag)
    if not xemu:
        logger.error("Failed to download xemu")
        return 1
    if not os.path.exists(xemu):
        logger.error("Invalid xemu path '%s'", xemu)
        return 1

    # Check for existing results to avoid redundant runs
    if not args.overwrite_existing_outputs and not args.just_suites:
        try:
            emulator_command, _ = _build_emulator_command(xemu, no_bundle=args.no_bundle)
            if emulator_command:
                output_directory = _determine_output_directory(
                    results_path, emulator_command=emulator_command, is_vulkan=args.use_vulkan
                )

                # If we find summary.json files in subdirectories, we assume it's done.
                existing_summaries = glob.glob(os.path.join(output_directory, "*", "summary.json"))
                if existing_summaries:
                    logger.warning(
                        "Found %d existing summary.json files in %s. Skipping execution. Use --overwrite-existing-outputs to force run.",
                        len(existing_summaries),
                        output_directory,
                    )
                    return 0
        except Exception:
            logger.exception("Failed to check for existing results")
            # If we fail to check, assume we need to run.

    if args.iso:
        iso = os.path.abspath(os.path.expanduser(args.iso))
    else:
        iso = _download_tester_iso(cache_path, args.pgraph_tag)
    if not iso or not os.path.isfile(iso):
        logger.error("Invalid ISO path '%s'", iso)
        return 1

    hdd = os.path.abspath(os.path.expanduser(args.hdd)) if args.hdd else _download_xemu_hdd(cache_path)
    if not hdd:
        logger.error("Failed to download xemu_hdd")
        return 1
    if not os.path.isfile(hdd):
        logger.error("Invalid xemu_hdd path '%s'", hdd)
        return 1

    if args.toml:
        result = _extract_info_from_xemu_toml(args.toml)
        if not result:
            logger.error("Failed to extract mcpx and bios from xemu toml at '%s'", args.toml)
            return 1
        args.mcpx, args.bios = result

    def _copy_inputs_and_run(temp_path: str, *, overwrite_existing_outputs: bool) -> int:
        if args.shard_count <= 1:
            return _run_shard(
                0,
                1,
                temp_path,
                iso,
                hdd,
                args.mcpx,
                args.bios,
                xemu,
                results_path,
                overwrite_existing_outputs=overwrite_existing_outputs,
                no_bundle=args.no_bundle,
                use_vulkan=args.use_vulkan,
                just_suites=args.just_suites,
            )

        futures = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.shard_count) as executor:
            for i in range(args.shard_count):
                shard_temp_path = os.path.join(temp_path, f"shard_{i}")
                os.makedirs(shard_temp_path, exist_ok=True)
                shard_results_path = os.path.join(shard_temp_path, "results")

                futures.append(
                    executor.submit(
                        _run_shard,
                        i,
                        args.shard_count,
                        shard_temp_path,
                        iso,
                        hdd,
                        args.mcpx,
                        args.bios,
                        xemu,
                        shard_results_path,
                        overwrite_existing_outputs=True,
                        no_bundle=args.no_bundle,
                        use_vulkan=args.use_vulkan,
                        just_suites=args.just_suites,
                    )
                )

            for future in concurrent.futures.as_completed(futures):
                ret = future.result()
                if ret != 0:
                    logger.error("Shard failed with exit code %d, aborting all shards.", ret)
                    for f in futures:
                        f.cancel()
                    return ret

        _merge_shard_results(temp_path, args.shard_count, results_path)
        return 0

    if args.temp_path:
        return _copy_inputs_and_run(
            _ensure_path(args.temp_path), overwrite_existing_outputs=args.overwrite_existing_outputs
        )

    with tempfile.TemporaryDirectory() as temp_path:
        return _copy_inputs_and_run(_ensure_path(temp_path), overwrite_existing_outputs=args.overwrite_existing_outputs)


if __name__ == "__main__":
    sys.exit(_process_arguments_and_run())
