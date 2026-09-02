#!/usr/bin/env python3
"""Generates perceptual differences between two xemu test runs for a given shard."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import subprocess
import sys

logger = logging.getLogger(__name__)

PIXELS_DIFF_RE = re.compile(r"(\d+)\s+pixels\s+are\s+different", re.IGNORECASE)


def run_perceptualdiff(
    source_img: str, target_img: str, output_diff_path: str
) -> tuple[bool, float]:
    """Runs perceptualdiff between source and target images.

    Returns (has_difference, diff_distance).
    If images match, output_diff_path is removed if perceptualdiff created it.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_diff_path)), exist_ok=True)
    cmd = [
        "perceptualdiff",
        source_img,
        target_img,
        "-output",
        output_diff_path,
        "-fov",
        "75",
        "-verbose",
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        output = proc.stdout + "\n" + proc.stderr

        if proc.returncode == 0:
            if os.path.exists(output_diff_path):
                try:
                    os.remove(output_diff_path)
                except OSError:
                    pass
            return False, 0.0

        match = PIXELS_DIFF_RE.search(output)
        diff_distance = float(match.group(1)) if match else math.inf
        return True, diff_distance

    except FileNotFoundError:
        logger.error("perceptualdiff executable not found on PATH.")
        return True, math.inf
    except Exception:
        logger.exception("Error running perceptualdiff")
        return True, math.inf


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate config diffs for assigned shard."
    )
    parser.add_argument(
        "--tasks-file", required=True, help="Path to config_diff_tasks.json"
    )
    parser.add_argument(
        "--shard-index",
        type=int,
        required=True,
        help="Index of current shard (0-based)",
    )
    parser.add_argument(
        "--shard-count", type=int, required=True, help="Total number of shards"
    )
    parser.add_argument(
        "--stage-dir", required=True, help="Directory to stage shard diff outputs"
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    with open(args.tasks_file, encoding="utf-8") as f:
        plan = json.load(f)

    slug = plan["slug"]
    all_tasks = plan["tasks"]
    shard_tasks = all_tasks[args.shard_index :: args.shard_count]

    logger.info(
        "Shard %d/%d executing %d of %d task(s) for comparison '%s'",
        args.shard_index,
        args.shard_count,
        len(shard_tasks),
        len(all_tasks),
        slug,
    )

    stage_comp_dir = os.path.join(args.stage_dir, "config-comparisons", slug)
    os.makedirs(stage_comp_dir, exist_ok=True)

    tests_with_differences: dict[str, float] = {}
    tests_matching_target: list[str] = []

    for task in shard_tasks:
        suite = task["suite"]
        test_name = task["test_name"]
        fq_name = f"{suite}:{test_name}"
        src_img = task["source_image"]
        tgt_img = task["target_image"]

        stage_diff_path = os.path.join(stage_comp_dir, suite, f"{test_name}-diff.png")

        has_diff, diff_distance = run_perceptualdiff(src_img, tgt_img, stage_diff_path)
        if has_diff:
            tests_with_differences[fq_name] = diff_distance
        else:
            tests_matching_target.append(fq_name)

    # Write partial shard summary
    partial_summary = {
        "shard_index": args.shard_index,
        "tests_with_differences": tests_with_differences,
        "tests_matching_target": tests_matching_target,
    }
    partial_summary_file = os.path.join(
        stage_comp_dir, f".shard_{args.shard_index}_summary.json"
    )
    with open(partial_summary_file, "w", encoding="utf-8") as f:
        json.dump(partial_summary, f, indent=2)

    # If no diff images were generated, keep stage dir from being empty
    placeholder = os.path.join(args.stage_dir, "KEEP_ARTIFACT")
    if not os.path.exists(placeholder):
        with open(placeholder, "w", encoding="utf-8") as f:
            f.write("")

    logger.info(
        "Shard %d finished: %d diff(s) found, %d test(s) matched target.",
        args.shard_index,
        len(tests_with_differences),
        len(tests_matching_target),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
