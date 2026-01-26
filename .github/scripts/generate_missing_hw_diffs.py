#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def _find_results_paths(results_dir: str) -> set[str]:
    ret: set[str] = set()

    for root, dirnames, filenames in os.walk(results_dir):
        if not dirnames:
            continue

        if "results.json" not in filenames:
            continue

        ret.add(root)

        # No need to recurse into test suite directories.
        dirnames.clear()

    return ret


def _find_hw_comparison_paths(output_dir: str) -> set[str]:
    ret: set[str] = set()

    for root, dirnames, filenames in os.walk(output_dir):
        if not dirnames:
            continue

        if "summary.json" not in filenames:
            continue

        if os.path.basename(root) != "Xbox__Xbox__DirectX__nv2a":
            continue
        ret.add(root)

        # No need to recurse into test suite directories.
        dirnames.clear()

    return ret


def _comparison_path_to_source_path(comparison_path: str) -> str:
    components = comparison_path.split("/")

    xemu = components[-4]
    platform = components[-3]
    graphics_pair = components[-2]

    return os.path.join(xemu, platform, *graphics_pair.split("--")).replace(":", "--")


def find_result_dirs_without_hw_diffs(results_dir: str, output_dir: str) -> set[str]:
    result_paths = _find_results_paths(results_dir)

    hw_comparison_paths = _find_hw_comparison_paths(output_dir)
    source_paths = {os.path.join(results_dir, _comparison_path_to_source_path(path)) for path in hw_comparison_paths}

    return result_paths - source_paths


def generate_missing_hw_diffs(results_dir: str, output_dir: str, compare_script: str, only_dir: str | None = None, *,
                              print_dirs_only: bool = False) -> None:
    results_missing_comparisons = find_result_dirs_without_hw_diffs(results_dir, output_dir)

    if print_dirs_only:
        for result in results_missing_comparisons:
            print(result)
        return

    if only_dir:
        if only_dir not in results_missing_comparisons:
            print(f"'{only_dir}' does not require comparison")
            return
        subprocess.run([compare_script, only_dir, "--verbose"], check=False)
        return

    for result in results_missing_comparisons:
        subprocess.run([compare_script, result, "--verbose"], check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results-dir",
        default="results",
        help="Directory including test outputs that will be processed",
    )
    parser.add_argument(
        "--output-dir",
        default="compare-results",
        help="Directory into which diff results will be generated",
    )
    parser.add_argument(
        "--compare-script",
        default="compare.py",
        help="The compare.py script used to generate results",
    )
    parser.add_argument(
        "--print-dirs-only",
        action="store_true",
        help="Print a list of directories that need to be processed and exit",
    )
    parser.add_argument(
        "--only-dir",
        help="Restrict generation to the given path",
    )

    args = parser.parse_args()

    compare_script = os.path.abspath(os.path.expanduser(args.compare_script))
    generate_missing_hw_diffs(args.results_dir, args.output_dir, compare_script, only_dir=args.only_dir,
                              print_dirs_only=args.print_dirs_only)

    return 0


if __name__ == "__main__":
    sys.exit(main())
