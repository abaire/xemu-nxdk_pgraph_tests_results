#!/usr/bin/env python3
# ruff: noqa: BLE001
"""Prunes test results, hardware comparisons, and configuration comparisons.

Supports dry-run, semver range targeting, remote archive branch deletion,
and git-filter-repo history rewriting to allow GitHub GC to reclaim space.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
from typing import NamedTuple

logger = logging.getLogger(__name__)

VERSION_RE = re.compile(r"xemu-(\d+)\.(\d+)\.(\d+)(?:[-._].*)?", re.IGNORECASE)
SEMVER_TAG_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)


class SemVer(NamedTuple):
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> SemVer | None:
        m = SEMVER_TAG_RE.search(text.strip())
        if m:
            return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return None


def parse_version_range(range_str: str) -> tuple[SemVer, SemVer] | None:
    """Parses ranges like 'v0.6.30-v0.8.100', '0.8.100..0.6.30', etc."""
    cleaned = range_str.strip()
    if ".." in cleaned:
        parts = cleaned.split("..", 1)
    elif "-" in cleaned:
        # Check if dash is between versions
        parts = [p.strip() for p in re.split(r"\s+-\s+|\s*-\s*", cleaned) if p.strip()]
    else:
        return None

    if len(parts) != 2:
        return None

    v1 = SemVer.parse(parts[0])
    v2 = SemVer.parse(parts[1])
    if not v1 or not v2:
        return None

    min_v = min(v1, v2)
    max_v = max(v2, v1)
    return min_v, max_v


def is_version_in_range(version_dir: str, min_v: SemVer, max_v: SemVer) -> bool:
    """Checks if a directory like 'xemu-0.8.101-master-...' falls within min_v..max_v inclusive."""
    v = SemVer.parse(version_dir)
    if not v:
        return False
    return min_v <= v <= max_v


def normalize_target(target: str) -> str:
    """Normalizes target string (URL, path, or slug)."""
    t = target.strip().strip("'\"")
    if not t:
        return ""
    # Strip URL prefix up to results/ or comparisons/
    for marker in ("/results/", "/config-comparisons/", "/compare-results/"):
        if marker in t:
            t = t.split(marker, 1)[1]
            break
    t = t.removeprefix("results/")
    t = t.removeprefix("config-comparisons/")
    t = t.removeprefix("compare-results/")
    if t.endswith("/index.html"):
        t = t[: -len("/index.html")]
    elif t.endswith(".html"):
        t = os.path.dirname(t)
    return t.strip("/")


class PrunePlan(NamedTuple):
    primary_results: list[str]
    hw_comparisons: list[str]
    config_comparisons: list[str]
    archive_branches: list[str]


def plan_pruning(
    results_dir: str,
    compare_results_dir: str,
    config_comparisons_dir: str,
    target_mode: str,
    targets: list[str],
    version_range: str = "",
    remote_branches: list[str] | None = None,
) -> PrunePlan:
    """Computes paths to delete across directories according to mode and filters."""
    matched_versions: set[str] = set()
    matched_run_paths: set[str] = set()
    matched_config_slugs: set[str] = set()

    semver_range = parse_version_range(version_range) if version_range else None

    # Scan available primary versions
    available_versions = []
    if os.path.isdir(results_dir):
        available_versions = [
            d
            for d in os.listdir(results_dir)
            if os.path.isdir(os.path.join(results_dir, d)) and not d.startswith(".")
        ]

    # Filter by range
    if semver_range:
        min_v, max_v = semver_range
        for v in available_versions:
            if is_version_in_range(v, min_v, max_v):
                matched_versions.add(v)

    # Filter by explicit targets
    for raw_target in targets:
        clean = normalize_target(raw_target)
        if not clean:
            continue
        # Check if target is a version
        if clean in available_versions or any(
            clean == v.split("-")[1] for v in available_versions if "-" in v
        ):
            matched_versions.add(clean)
            continue
        # Check if target starts with a version (specific run)
        first_comp = clean.split("/")[0]
        if first_comp in available_versions:
            if "/" in clean:
                matched_run_paths.add(clean)
            else:
                matched_versions.add(clean)
            continue
        # Check if target is a config comparison slug
        if "--vs--" in clean or "__vs__" in clean:
            matched_config_slugs.add(clean)
            continue

        # Substring / pattern match on version
        for v in available_versions:
            if clean in v:
                matched_versions.add(v)

    # Now compute directories to prune based on mode
    primary_to_delete: list[str] = []
    hw_to_delete: list[str] = []
    config_to_delete: list[str] = []
    archive_branches_to_delete: list[str] = []

    # Config comparisons to delete
    available_configs = []
    if os.path.isdir(config_comparisons_dir):
        available_configs = [
            d
            for d in os.listdir(config_comparisons_dir)
            if os.path.isdir(os.path.join(config_comparisons_dir, d))
            and not d.startswith(".")
        ]

    for c in available_configs:
        # Check if explicitly matched
        if c in matched_config_slugs:
            config_to_delete.append(os.path.join(config_comparisons_dir, c))
            continue
        # If any matched version is in slug
        for v in matched_versions:
            v_slug = v.replace("/", "__").replace(":", "__")
            if v_slug in c:
                config_to_delete.append(os.path.join(config_comparisons_dir, c))
                break
        # If any matched run path is in slug
        for rp in matched_run_paths:
            rp_slug = rp.replace("/", "__").replace(":", "__")
            if (
                rp_slug in c
                and os.path.join(config_comparisons_dir, c) not in config_to_delete
            ):
                config_to_delete.append(os.path.join(config_comparisons_dir, c))

    if target_mode in ("all", "comparisons_only") and os.path.isdir(
        compare_results_dir
    ):
        for v in matched_versions:
            v_dir = os.path.join(compare_results_dir, v)
            if os.path.exists(v_dir):
                hw_to_delete.append(v_dir)
        for rp in matched_run_paths:
            rp_dir = os.path.join(compare_results_dir, rp)
            if os.path.exists(rp_dir) and rp_dir not in hw_to_delete:
                hw_to_delete.append(rp_dir)

    if target_mode == "all":
        # Primary results in results/
        if os.path.isdir(results_dir):
            for v in matched_versions:
                v_dir = os.path.join(results_dir, v)
                if os.path.exists(v_dir):
                    primary_to_delete.append(v_dir)
            for rp in matched_run_paths:
                rp_dir = os.path.join(results_dir, rp)
                if os.path.exists(rp_dir) and rp_dir not in primary_to_delete:
                    primary_to_delete.append(rp_dir)

        # Archive branches
        if remote_branches:
            for v in matched_versions:
                branch_name = f"archive/{v}"
                if branch_name in remote_branches:
                    archive_branches_to_delete.append(branch_name)

    return PrunePlan(
        primary_results=sorted(primary_to_delete),
        hw_comparisons=sorted(hw_to_delete),
        config_comparisons=sorted(config_to_delete),
        archive_branches=sorted(archive_branches_to_delete),
    )


def execute_pruning(
    plan: PrunePlan,
    dry_run: bool = True,
    rewrite_history: bool = False,
    repo_root: str = ".",
) -> int:
    """Executes the pruning plan, deleting files and optionally rewriting history."""
    all_dirs = plan.primary_results + plan.hw_comparisons + plan.config_comparisons

    logger.info("=== PRUNING PLAN ===")
    logger.info(
        "Mode: %s", "DRY-RUN (no changes will be applied)" if dry_run else "EXECUTE"
    )
    logger.info("Primary result directories to remove: %d", len(plan.primary_results))
    for p in plan.primary_results:
        logger.info("  [-] Primary: %s", p)

    logger.info("HW comparison directories to remove: %d", len(plan.hw_comparisons))
    for h in plan.hw_comparisons:
        logger.info("  [-] HW Comp: %s", h)

    logger.info(
        "Config comparison directories to remove: %d", len(plan.config_comparisons)
    )
    for c in plan.config_comparisons:
        logger.info("  [-] Config Comp: %s", c)

    logger.info("Remote archive branches to delete: %d", len(plan.archive_branches))
    for b in plan.archive_branches:
        logger.info("  [-] Branch: %s", b)

    if dry_run:
        logger.info("Dry-run complete. Exiting without modifying repository.")
        return 0

    # Delete directories from disk
    for d in all_dirs:
        if os.path.exists(d):
            logger.info("Deleting directory: %s", d)
            shutil.rmtree(d, ignore_errors=True)

    # Delete remote archive branches
    for branch in plan.archive_branches:
        logger.info("Deleting remote branch: %s", branch)
        try:
            subprocess.run(
                ["git", "push", "origin", "--delete", branch], cwd=repo_root, check=True
            )
        except Exception as e:
            logger.warning("Failed to delete remote branch %s: %s", branch, e)

    # History rewriting with git-filter-repo if requested
    if rewrite_history and all_dirs:
        logger.info(
            "Rewriting git history with git-filter-repo to excise deleted paths..."
        )
        filter_args = ["git", "filter-repo", "--force", "--invert-paths"]
        for d in all_dirs:
            rel = os.path.relpath(d, repo_root)
            filter_args.extend(["--path", rel])
        try:
            subprocess.run(filter_args, cwd=repo_root, check=True)
            logger.info("Successfully rewritten git history.")
        except Exception as e:
            logger.error("Failed running git-filter-repo: %s", e)
            return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune stale results and comparisons.")
    parser.add_argument(
        "--target-mode",
        choices=["all", "comparisons_only", "config_comparisons_only"],
        default="all",
        help="What to prune",
    )
    parser.add_argument(
        "--targets",
        default="",
        help="Comma-separated list of target URLs, paths, version strings, or slugs",
    )
    parser.add_argument(
        "--version-range",
        default="",
        help="Semantic version range, e.g. 'v0.6.30-v0.8.100' or '0.8.100..0.6.30'",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview actions without deleting",
    )
    parser.add_argument(
        "--rewrite-history",
        action="store_true",
        default=False,
        help="Use git-filter-repo to expunge objects from git history",
    )
    parser.add_argument(
        "--results-dir", default="results", help="Primary results directory"
    )
    parser.add_argument(
        "--compare-results-dir", default="compare-results", help="HW diff directory"
    )
    parser.add_argument(
        "--config-comparisons-dir",
        default="config-comparisons",
        help="Config diff directory",
    )
    parser.add_argument("--repo-root", default=".", help="Repository root directory")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    targets_list = [t.strip() for t in args.targets.split(",") if t.strip()]

    # Fetch remote branches if git is available
    remote_branches: list[str] = []
    try:
        raw_branches = (
            subprocess.check_output(["git", "branch", "-r"], cwd=args.repo_root)
            .decode()
            .split()
        )
        remote_branches = [
            b.split("origin/")[1]
            for b in raw_branches
            if b.startswith("origin/archive/")
        ]
    except Exception as e:
        logger.debug("Could not inspect remote archive branches: %s", e)

    plan = plan_pruning(
        results_dir=args.results_dir,
        compare_results_dir=args.compare_results_dir,
        config_comparisons_dir=args.config_comparisons_dir,
        target_mode=args.target_mode,
        targets=targets_list,
        version_range=args.version_range,
        remote_branches=remote_branches,
    )

    return execute_pruning(
        plan=plan,
        dry_run=args.dry_run,
        rewrite_history=args.rewrite_history,
        repo_root=args.repo_root,
    )


if __name__ == "__main__":
    sys.exit(main())
