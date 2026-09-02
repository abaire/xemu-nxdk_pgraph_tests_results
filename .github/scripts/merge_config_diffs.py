#!/usr/bin/env python3
# ruff: noqa: BLE001
"""Merges partial shard comparison summaries into unified summary.json for a configuration comparison."""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)


def merge_config_diff_summaries(comparison_dir: str, slug: str, tasks_file: str) -> str:
    """Merges all .shard_*_summary.json files in comparison_dir/slug into summary.json."""
    slug_dir = os.path.join(comparison_dir, slug)
    if not os.path.isdir(slug_dir):
        msg = f"Comparison directory '{slug_dir}' does not exist."
        raise FileNotFoundError(msg)

    with open(tasks_file, encoding="utf-8") as f:
        plan = json.load(f)

    source_run = plan.get("source_run", "")
    target_run = plan.get("target_run", "")
    tests_matching_hw = plan.get("tests_matching_hw", [])
    tests_without_match = plan.get("tests_without_match", [])

    tests_with_differences: dict[str, float] = {}
    tests_matching_target_set: set[str] = set()

    shard_summary_files = glob.glob(os.path.join(slug_dir, ".shard_*_summary.json"))
    logger.info(
        "Found %d shard summary file(s) in %s", len(shard_summary_files), slug_dir
    )

    for shard_file in shard_summary_files:
        try:
            with open(shard_file, encoding="utf-8") as f:
                shard_data = json.load(f)
            tests_with_differences.update(shard_data.get("tests_with_differences", {}))
            tests_matching_target_set.update(
                shard_data.get("tests_matching_target", [])
            )
            os.remove(shard_file)
        except Exception as e:
            logger.warning("Failed processing shard file %s: %s", shard_file, e)

    # Sort keys for deterministic output
    final_summary = {
        "slug": slug,
        "source_run": source_run,
        "target_run": target_run,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "diff_count": len(tests_with_differences),
        "tests_with_differences": dict(sorted(tests_with_differences.items())),
        "tests_matching_target": sorted(tests_matching_target_set),
        "tests_matching_hw": sorted(tests_matching_hw),
        "tests_without_match": sorted(tests_without_match),
    }

    summary_path = os.path.join(slug_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(final_summary, f, indent=2)

    logger.info(
        "Successfully merged comparison summary for '%s': %d difference(s) recorded at %s",
        slug,
        len(tests_with_differences),
        summary_path,
    )
    return summary_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge shard diff summaries for config comparison."
    )
    parser.add_argument(
        "--comparison-dir",
        default="config-comparisons",
        help="Root directory for configuration comparisons",
    )
    parser.add_argument(
        "--slug", required=True, help="Slug of the comparison being merged"
    )
    parser.add_argument(
        "--tasks-file", required=True, help="Path to config_diff_tasks.json"
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    merge_config_diff_summaries(args.comparison_dir, args.slug, args.tasks_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
