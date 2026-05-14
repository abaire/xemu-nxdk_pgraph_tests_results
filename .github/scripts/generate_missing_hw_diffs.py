#!/usr/bin/env python3

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


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

        if os.path.basename(root) != "Xbox--Xbox--DirectX--nv2a":
            logger.debug("Skip %s (%s != Xbox--Xbox--DirectX--nv2a)", root, os.path.basename(root))
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

    logger.debug("Result paths: %s", sorted(result_paths))
    logger.debug("Source paths: %s", sorted(source_paths))

    return result_paths - source_paths


def generate_missing_hw_diffs(
    results_dir: str,
    output_dir: str,
    compare_script: str,
    shard_index: int | None = None,
    shard_count: int | None = None,
) -> None:
    results_missing_comparisons = find_result_dirs_without_hw_diffs(results_dir, output_dir)
    logger.debug("Results without comparisons: %s", sorted(results_missing_comparisons))

    all_results = sorted(results_missing_comparisons)
    if shard_index is not None and shard_count is not None:
        all_results = [r for i, r in enumerate(all_results) if i % shard_count == shard_index]
        if not all_results:
            return

    for result in all_results:
        subprocess.run([compare_script, result, "--verbose"], check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verbose",
        "-v",
        help="Enables verbose logging information",
        action="store_true",
    )
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
        "--shard-index",
        type=int,
        default=None,
        help="Index of this shard (0-based). Must be used with --shard-count.",
    )
    parser.add_argument(
        "--shard-count",
        type=int,
        default=None,
        help="Total number of shards. Must be used with --shard-index.",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level)

    if (args.shard_index is None) != (args.shard_count is None):
        parser.error("--shard-index and --shard-count must be used together")

    compare_script = os.path.abspath(os.path.expanduser(args.compare_script))
    generate_missing_hw_diffs(
        args.results_dir, args.output_dir, compare_script, shard_index=args.shard_index, shard_count=args.shard_count
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
