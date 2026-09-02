#!/usr/bin/env python3
# ruff: noqa: BLE001
"""Plans configuration comparison tasks between two specific xemu test runs."""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import sys
import urllib.parse
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

HW_GOLDEN_DIR_NAME = "Xbox__Xbox__DirectX__nv2a"


def normalize_run_input(raw_input: str) -> str:
    """Normalizes a user run input (URL or path) into a relative results path.

    Accepts:
    - Full or relative URLs: https://.../results/xemu-0.8.../Linux_.../gl_.../index.html
    - Paths with or without results/: results/xemu-0.8.../Linux_.../gl_...
    - Unencoded or URL-encoded strings
    """
    raw = raw_input.strip().strip("'\"")
    if not raw:
        return ""

    # Parse URL if applicable
    parsed = urllib.parse.urlparse(raw)
    path = parsed.path if parsed.path else raw

    # Decode any %20, %2B, etc.
    path = urllib.parse.unquote(path)

    # Normalize backslashes to forward slashes
    path = path.replace("\\", "/").strip("/")

    # Strip index.html or other trailing file names
    if path.endswith("/index.html"):
        path = path[: -len("/index.html")]
    elif path.endswith(".html"):
        path = os.path.dirname(path)

    # If 'results/' is in the path, take whatever follows 'results/'
    parts = path.split("/")
    if "results" in parts:
        idx = parts.index("results")
        parts = parts[idx + 1 :]

    return "/".join(p for p in parts if p)


def make_comparison_slug(source_run: str, target_run: str) -> str:
    """Generates a unique, filesystem-safe slug for the comparison pair."""
    safe_source = re.sub(
        r"[^A-Za-z0-9_.-]", "_", source_run.replace("/", "__").replace(":", "__")
    )
    safe_target = re.sub(
        r"[^A-Za-z0-9_.-]", "_", target_run.replace("/", "__").replace(":", "__")
    )
    return f"{safe_source}--vs--{safe_target}"


def load_hw_diff_summary(
    compare_results_dir: str, run_path: str
) -> dict[str, float] | None:
    """Finds and loads the tests_with_differences map from the HW diff summary.json if present."""
    run_comp_dir = os.path.join(compare_results_dir, run_path)
    if not os.path.isdir(run_comp_dir):
        return None

    summary_file = os.path.join(run_comp_dir, HW_GOLDEN_DIR_NAME, "summary.json")
    if not os.path.isfile(summary_file):
        summaries = glob.glob(
            os.path.join(run_comp_dir, "**/summary.json"), recursive=True
        )
        if summaries:
            summary_file = summaries[0]
        else:
            return None

    try:
        with open(summary_file, encoding="utf-8") as f:
            data = json.load(f)
            return data.get("tests_with_differences", {})
    except Exception as e:
        logger.warning("Could not read HW summary for %s: %s", run_path, e)
        return None


@dataclass
class ConfigDiffTask:
    suite: str
    test_name: str
    source_image: str
    target_image: str
    diff_output_image: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def plan_config_diffs(
    source_run: str,
    target_run: str,
    results_dir: str = "results",
    compare_results_dir: str = "compare-results",
    output_dir: str = "config-comparisons",
    max_shards: int = 16,
) -> tuple[str, list[ConfigDiffTask], list[str], list[str]]:
    """Plans comparison tasks between source_run and target_run.

    Returns (slug, tasks, tests_matching_hw, tests_without_match)
    """
    slug = make_comparison_slug(source_run, target_run)
    source_full_dir = os.path.join(results_dir, source_run)
    target_full_dir = os.path.join(results_dir, target_run)

    if not os.path.isdir(source_full_dir):
        msg = f"Source run directory '{source_full_dir}' not found."
        raise FileNotFoundError(msg)
    if not os.path.isdir(target_full_dir):
        msg = f"Target run directory '{target_full_dir}' not found."
        raise FileNotFoundError(msg)

    # Discover images in source
    source_images: dict[str, str] = {}
    for root, _dirs, files in os.walk(source_full_dir):
        for f in files:
            if f.endswith(".png") and not f.endswith("-diff.png"):
                suite = os.path.basename(root)
                test_name = os.path.splitext(f)[0]
                source_images[f"{suite}:{test_name}"] = os.path.join(root, f)

    # Discover images in target
    target_images: dict[str, str] = {}
    for root, _dirs, files in os.walk(target_full_dir):
        for f in files:
            if f.endswith(".png") and not f.endswith("-diff.png"):
                suite = os.path.basename(root)
                test_name = os.path.splitext(f)[0]
                target_images[f"{suite}:{test_name}"] = os.path.join(root, f)

    # HW diff summaries
    source_hw_diffs = load_hw_diff_summary(compare_results_dir, source_run)
    target_hw_diffs = load_hw_diff_summary(compare_results_dir, target_run)

    tasks: list[ConfigDiffTask] = []
    tests_matching_hw: list[str] = []
    tests_without_match: list[str] = []

    comparison_target_dir = os.path.join(output_dir, slug)

    all_test_keys = sorted(set(source_images.keys()) | set(target_images.keys()))

    for key in all_test_keys:
        suite, test_name = key.split(":", 1)
        src_img = source_images.get(key)
        tgt_img = target_images.get(key)

        if not src_img or not tgt_img:
            tests_without_match.append(key)
            continue

        # Check if both match HW
        if source_hw_diffs is not None and target_hw_diffs is not None:
            src_differs_hw = key in source_hw_diffs
            tgt_differs_hw = key in target_hw_diffs
            if not src_differs_hw and not tgt_differs_hw:
                tests_matching_hw.append(key)
                continue

        diff_output = os.path.join(
            comparison_target_dir, suite, f"{test_name}-diff.png"
        )
        tasks.append(
            ConfigDiffTask(
                suite=suite,
                test_name=test_name,
                source_image=src_img,
                target_image=tgt_img,
                diff_output_image=diff_output,
            )
        )

    logger.info("Total paired tests: %d", len(all_test_keys))
    logger.info(
        "Tests where both match HW golden (excluded): %d", len(tests_matching_hw)
    )
    logger.info("Tests without pair: %d", len(tests_without_match))
    logger.info("Diff tasks scheduled: %d", len(tasks))

    return slug, tasks, tests_matching_hw, tests_without_match


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan configuration comparison between two xemu runs."
    )
    parser.add_argument("--source-run", required=True, help="Path or URL to source run")
    parser.add_argument("--target-run", required=True, help="Path or URL to target run")
    parser.add_argument(
        "--results-dir", default="results", help="Directory containing test results"
    )
    parser.add_argument(
        "--compare-results-dir",
        default="compare-results",
        help="Directory containing HW diffs",
    )
    parser.add_argument(
        "--output-dir",
        default="config-comparisons",
        help="Output directory for comparisons",
    )
    parser.add_argument(
        "--max-shards", type=int, default=16, help="Maximum number of parallel shards"
    )
    parser.add_argument(
        "--output-plan-file",
        default="config_diff_tasks.json",
        help="Path to write the tasks plan JSON",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    source_run = normalize_run_input(args.source_run)
    target_run = normalize_run_input(args.target_run)

    if not source_run:
        logger.error("Invalid or empty --source-run provided: '%s'", args.source_run)
        return 1
    if not target_run:
        logger.error("Invalid or empty --target-run provided: '%s'", args.target_run)
        return 1

    slug, tasks, tests_matching_hw, tests_without_match = plan_config_diffs(
        source_run=source_run,
        target_run=target_run,
        results_dir=args.results_dir,
        compare_results_dir=args.compare_results_dir,
        output_dir=args.output_dir,
        max_shards=args.max_shards,
    )

    diff_count = len(tasks)
    shard_count = min(diff_count, args.max_shards) if diff_count > 0 else 0
    shards = list(range(shard_count)) if shard_count > 0 else []
    matrix = {"shard": shards}
    matrix_json = json.dumps(matrix)

    plan_data = {
        "slug": slug,
        "source_run": source_run,
        "target_run": target_run,
        "tests_matching_hw": tests_matching_hw,
        "tests_without_match": tests_without_match,
        "tasks": [t.to_dict() for t in tasks],
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.output_plan_file)), exist_ok=True)
    with open(args.output_plan_file, "w", encoding="utf-8") as f:
        json.dump(plan_data, f, indent=2)

    logger.info(
        "Saved plan with %d tasks across %d shards to %s",
        diff_count,
        shard_count,
        args.output_plan_file,
    )

    print(f"slug={slug}")
    print(f"source_run={source_run}")
    print(f"target_run={target_run}")
    print(f"diff_count={diff_count}")
    print(f"shard_count={shard_count}")
    print(f"matrix={matrix_json}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"slug={slug}\n")
            f.write(f"source_run={source_run}\n")
            f.write(f"target_run={target_run}\n")
            f.write(f"diff_count={diff_count}\n")
            f.write(f"shard_count={shard_count}\n")
            f.write(f"matrix={matrix_json}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
