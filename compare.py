#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from typing import NamedTuple

from git import Repo
import lpips

logger = logging.getLogger(__name__)

_HW_GOLDEN_GIT_URL = "https://github.com/abaire/nxdk_pgraph_tests_golden_results.git"


class ResultsInfo(NamedTuple):
    result_path: str
    xemu_version: str
    platform_info: str
    gl_info: str
    test_suites: dict[str, dict[str, str]]

    @property
    def run_identifier(self):
        return f"{self.xemu_version}:{self.platform_info}:{self.gl_info}"

    @property
    def output_subdirectory(self) -> str:
        return os.path.join(self.xemu_version, self.platform_info, self.gl_info)

    @property
    def run_identifier_subdirectory(self) -> str:
        return self.run_identifier.replace(":", "__")

    def get_flattened_tests(self) -> set[str]:
        """Return a flattened set of test_suite::test_case."""
        ret = set()
        for suite_name, test_cases in self.test_suites.items():
            suite_name = suite_name.replace(" ", "_")
            for test_case in test_cases.keys():
                ret.add(f"{suite_name}:{test_case}")
        return ret

    def find_result_images(self) -> ResultsInfo:
        """Walks the result_path to find all png images."""
        for root, dirnames, filenames in os.walk(self.result_path):
            if os.path.basename(root).startswith("."):
                dirnames.clear()
                continue

            if dirnames:
                continue

            test_suite = os.path.basename(root)
            if test_suite in {"perceptualdiff", "scripts"}:
                continue

            for filename in filenames:
                test_case = os.path.splitext(filename)[0]
                self.test_suites[test_suite][test_case] = os.path.join(root, filename)
        return self

    @classmethod
    def parse(cls, result_path: str) -> ResultsInfo:
        # results/Linux_foo/gl_version/glsl_version/xemu_version
        components = result_path.split("/")
        return cls(
            result_path=result_path,
            xemu_version=components[-4],
            platform_info=components[-3],
            gl_info=f"{components[-2]}:{components[-1]}",
            test_suites=defaultdict(dict),
        ).find_result_images()


class Difference(NamedTuple):
    """Encapsulates the lpips difference between a result and its golden output."""

    test_suite: str
    test_case: str
    result_artifact: str
    golden_artifact: str
    distance: float

    @property
    def fully_qualified_test_name(self) -> str:
        return f"{self.test_suite}:{self.test_case}"

    @property
    def difference_filename(self) -> str:
        return f"{os.path.join(self.test_suite, self.test_case)}-diff.png"

    def generate_difference_image(self, perceptualdiff: str, output_path: str) -> None:
        """Generates a diff image in the given output_path using perceptualdiff."""
        target_filename = os.path.join(output_path, self.difference_filename)
        target_dir = os.path.dirname(target_filename)
        os.makedirs(target_dir, exist_ok=True)
        subprocess.run(
            [
                perceptualdiff,
                "-output",
                target_filename,
                self.result_artifact,
                self.golden_artifact,
            ],
            check=False,
            capture_output=True,
        )


def _ensure_path(path: str) -> str:
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(path, exist_ok=True)
    return path


def _ensure_cache_path(cache_path: str) -> str:
    if not cache_path:
        msg = "cache_path may not be empty"
        raise ValueError(msg)
    return _ensure_path(cache_path)


def _fetch_hw_goldens(output_dir: str):
    logger.info("Cloning from %s", _HW_GOLDEN_GIT_URL)
    Repo.clone_from(_HW_GOLDEN_GIT_URL, output_dir, depth=1)


def _compare(
    results_info: ResultsInfo, golden_info: ResultsInfo, diff_threshold: float
) -> tuple[set[str], set[str], list[Difference]]:
    loss_fn = lpips.LPIPS(net="alex")

    results_tests = results_info.get_flattened_tests()
    golden_tests = golden_info.get_flattened_tests()

    only_results = results_tests - golden_tests
    only_goldens = golden_tests - results_tests

    differences: list[Difference] = []

    logger.info("Comparing image files (this may take some time)...")
    for test_suite, test_cases in results_info.test_suites.items():
        golden_suite = golden_info.test_suites.get(test_suite, {})
        for test_case, artifact in test_cases.items():
            golden_artifact = golden_suite.get(test_case)
            if not golden_artifact:
                continue

            # Load images
            artifact_image = lpips.im2tensor(lpips.load_image(artifact))
            golden_image = lpips.im2tensor(lpips.load_image(golden_artifact))

            distance = loss_fn(artifact_image, golden_image)
            distance_value = distance.item()
            logger.debug(
                "LPIPS distance between %s and %s = %f",
                artifact,
                golden_artifact,
                distance_value,
            )
            if distance_value <= diff_threshold:
                continue

            differences.append(
                Difference(
                    test_suite, test_case, artifact, golden_artifact, distance_value
                )
            )

    return only_results, only_goldens, differences


def perform_comparison(
    results_path: str,
    golden_path: str,
    output_dir: str,
    perceptualdiff: str,
    diff_threshold: float,
) -> None:
    results_info = ResultsInfo.parse(results_path)

    if "nxdk_pgraph_tests_golden_results" in golden_path:
        golden_info = ResultsInfo(
            xemu_version="Xbox",
            platform_info="Xbox",
            gl_info="DirectX:nv2a",
            result_path=golden_path,
            test_suites=defaultdict(dict),
        ).find_result_images()
        against_name = "Xbox_Hardware"
    else:
        golden_info = ResultsInfo.parse(golden_path)
        against_name = golden_info.run_identifier

    only_results, only_golden, diffs = _compare(
        results_info, golden_info, diff_threshold
    )

    if not (only_results or only_golden or diffs):
        return

    comparison_output_directory = os.path.join(
        output_dir,
        results_info.output_subdirectory,
        golden_info.run_identifier_subdirectory,
    )
    if os.path.isdir(comparison_output_directory):
        shutil.rmtree(comparison_output_directory)
    os.makedirs(comparison_output_directory, exist_ok=True)

    summary = {
        "result_identifier": results_info.run_identifier,
        "golden_identifier": against_name,
        "tests_without_goldens": sorted(only_results),
        "goldens_without_results": sorted(only_golden),
        "tests_with_differences": {
            diff.fully_qualified_test_name: diff.distance for diff in diffs
        },
    }
    with open(
        os.path.join(comparison_output_directory, "summary.json"), "w", encoding="utf-8"
    ) as outfile:
        json.dump(summary, outfile, ensure_ascii=True, indent=2, sort_keys=True)

    for diff in diffs:
        logger.info("Generating diff image for %s", diff.fully_qualified_test_name)
        diff.generate_difference_image(perceptualdiff, comparison_output_directory)


def _process_arguments_and_run():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verbose",
        "-v",
        help="Enables verbose logging information",
        action="store_true",
    )
    parser.add_argument(
        "results",
        help="Path to the root of the results to compare against the golden results.",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        metavar="path_to_output_directory",
        default="compare-results",
        help="Path to directory into which diff artifacts will be written.",
    )
    parser.add_argument(
        "--against",
        "-a",
        help="Path to the root of the results to consider golden. Omit to test against the HW results repo.",
    )
    parser.add_argument(
        "--cache-path", "-C", default="cache", help="Path to persistent cache area."
    )
    parser.add_argument(
        "--perceptualdiff",
        default="perceptualdiff",
        help="Path to the perceptualdiff binary.",
    )
    parser.add_argument(
        "--diff-threshold",
        "-t",
        type=float,
        default=0.0,
        help="LPIPS distance threshold below which images are considered equal.",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level)

    if not os.path.isdir(args.results):
        logger.error("Source directory '%s' does not exist", args.results)
        return 1

    if not args.against:
        cache_path = _ensure_cache_path(args.cache_path)
        hw_golden_root = os.path.join(cache_path, "nxdk_pgraph_tests_golden_results")
        if not os.path.isdir(hw_golden_root):
            _fetch_hw_goldens(hw_golden_root)
        golden_dir = hw_golden_root
    else:
        golden_dir = args.against

    if not os.path.isdir(golden_dir):
        logger.error("Comparison directory '%s' does not exist", golden_dir)
        return 1

    os.makedirs(args.output_dir, exist_ok=True)

    perform_comparison(
        args.results,
        golden_dir,
        args.output_dir,
        args.perceptualdiff,
        args.diff_threshold,
    )

    return 0


if __name__ == "__main__":
    sys.exit(_process_arguments_and_run())
