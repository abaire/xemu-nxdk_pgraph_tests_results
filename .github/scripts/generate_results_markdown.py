#!/bin/env python3

from __future__ import annotations

import argparse
import glob
import itertools
import json
import os
import urllib.parse
from collections import defaultdict
from typing import NamedTuple, Any


class ResultsInfo(NamedTuple):
    run_identifier: list[str]
    xemu_version: str
    platform_info: str
    gl_info: str
    toc_md_file: str

    @classmethod
    def parse(cls, run_identifier: str, toc_md_file: str) -> ResultsInfo:
        # results/Linux_foo/gl_version/glsl_version/xemu_version
        components = run_identifier.split("/")
        return cls(
            run_identifier=components,
            xemu_version=components[-4],
            platform_info=components[-3],
            gl_info=f"{components[-2]}:{components[-1]}",
            toc_md_file=toc_md_file,
        )


def make_md_filename(results_dir: str) -> str:
    """Converts a results path into a unique markdown filename."""
    sanitized_name = results_dir.replace("/", "__")
    return f"{sanitized_name}.md"


def make_image_link(base_url: str, image_path: str) -> str:
    """Generates wiki markdown to display an inline image."""
    image_path = urllib.parse.quote(image_path)
    return f"![{os.path.basename(image_path)}]({base_url}/{image_path})"


class ResultsWriter:
    def __init__(
        self,
        results_dir: str,
        output_dir: str,
        base_url: str,
        xemu_version_to_comparison_results: dict[str, list[ResultsInfo]],
    ) -> None:
        self.results_dir = results_dir
        self.output_dir = output_dir
        self.base_url = base_url
        self.xemu_version_to_comparison_results = xemu_version_to_comparison_results

    def generate_markdown_for_test_suite(self, test_suite_dir: str) -> str | None:
        """Generates the markdown for the contents of a test suite page."""
        suite_name = os.path.basename(test_suite_dir)

        images = glob.glob(os.path.join(test_suite_dir, "*.png"))

        if not images:
            return None

        ret = [
            f"Results - {suite_name}",
            "===",
        ]

        for image_file in sorted(images):
            ret.append(f"## {os.path.splitext(os.path.basename(image_file))[0]}")
            ret.append(make_image_link(self.base_url, image_file))

        return "\n".join(ret)

    def _create_test_suite_files(self) -> dict[str, list[str]]:
        """Writes markdown files for each test suite.

        :return {run_results_directory: list[generated_markdown_filename]}
        """
        all_result_md_files: dict[str, list[str]] = defaultdict(list)
        for root, dirnames, filenames in os.walk(self.results_dir):
            if dirnames:
                continue

            if not filenames:
                continue

            # Github's wiki does not support pages in subdirs, so all files are written directly in the output_dir. Test
            # suites should all have unique names so this should not cause issues.
            run_identifier = os.path.dirname(root)
            raw_md_filename = make_md_filename(root)

            all_result_md_files[run_identifier].append(raw_md_filename)

            output_filename = os.path.join(self.output_dir, raw_md_filename)

            with open(output_filename, "w") as output_file:
                content = self.generate_markdown_for_test_suite(root)
                if content:
                    output_file.write(content)

        return all_result_md_files

    def generate_markdown_for_failures(self, run_result_dir: str) -> str:
        ret = []

        result_description_file = os.path.join(run_result_dir, "results.json")
        if os.path.isfile(result_description_file):
            with open(result_description_file, "r") as infile:
                summary = json.load(infile)

                failed_tests = summary.get("failed", {})
                flaky_tests = summary.get("flaky", {})

        def generate_failure_info(result_dict: dict[str, Any]):
            for fq_name in sorted(result_dict.keys()):
                ret.append(f"## {fq_name}")
                for failure in result_dict[fq_name].get("failures", []):
                    ret.extend(["```", failure, "```"])

        if failed_tests:
            ret.extend(["", f"# Failed tests ({len(failed_tests)})"])
            generate_failure_info(failed_tests)

        if flaky_tests:
            ret.extend(["", f"# Flaky tests ({len(flaky_tests)})"])
            generate_failure_info(flaky_tests)

        machine_info_file = os.path.join(run_result_dir, "machine_info.txt")
        if os.path.isfile(machine_info_file):
            ret.append("")
            ret.append("# Machine info")
            ret.append("```")
            with open(machine_info_file, "r") as infile:
                ret.append(infile.read())
            ret.append("```")

        return "\n".join(ret)

    def _create_run_overview_file(
        self, result_dir_to_markdown_files: dict[str, list[str]]
    ) -> dict[str, list[ResultsInfo]]:
        """Creates the"""
        xemu_version_to_results: dict[str, list[ResultsInfo]] = defaultdict(list)

        for run_result_dir, results in result_dir_to_markdown_files.items():
            raw_md_filename = make_md_filename(run_result_dir)
            output_filename = os.path.join(self.output_dir, raw_md_filename)
            with open(output_filename, "w") as output_file:

                def friendly_page_name(result_md_file: str) -> str:
                    return os.path.splitext(result_md_file[len(raw_md_filename) - 1 :])[
                        0
                    ]

                output_file.writelines(
                    [
                        f"{run_result_dir}\n",
                        "===\n",
                        "# Results\n",
                        *[
                            f"- [[{friendly_page_name(result)}|{result[:-3]}]]\n"
                            for result in sorted(results)
                        ],
                    ]
                )

                failure_info = self.generate_markdown_for_failures(run_result_dir)
                if failure_info:
                    output_file.write(failure_info)

            results_info = ResultsInfo.parse(run_result_dir, raw_md_filename)
            xemu_version_to_results[results_info.xemu_version].append(results_info)
        return xemu_version_to_results

    def process_results(self) -> dict[str, list[ResultsInfo]]:
        """Processes the results directory and generates markdown files.

        :return {xemu_version: list[ResultInfo]} describing the generated pages.
        """
        run_result_dir_to_markdown_files = self._create_test_suite_files()
        return self._create_run_overview_file(run_result_dir_to_markdown_files)


def _write_home_markdown(
    output_dir: str, xemu_version_to_results: dict[str, list[ResultsInfo]]
) -> None:
    with open(os.path.join(output_dir, "Home.md"), "w") as output_file:
        output_file.writelines(
            [
                "nxdk_pgraph_tests results for the [xemu](https://xemu.app) emulator\n",
                "===\n",
            ]
        )

        for xemu_version in sorted(xemu_version_to_results.keys()):
            output_file.write(f"# {xemu_version}\n")

            by_platform = {
                platform: list(group)
                for platform, group in itertools.groupby(
                    xemu_version_to_results[xemu_version], key=lambda x: x.platform_info
                )
            }

            for platform in sorted(by_platform.keys()):
                output_file.write(f"## {platform}\n")

                by_gl_info = {
                    gl_info: list(group)
                    for gl_info, group in itertools.groupby(
                        by_platform[platform], key=lambda x: x.gl_info
                    )
                }

                for gl_info in sorted(by_gl_info.keys()):
                    if len(by_gl_info[gl_info]) > 1:
                        # Results are uniquely identified by platform/gl_info/xemu, so there should never be more than
                        # one TOC file
                        msg = (
                            f"Found {len(by_gl_info[gl_info])} result infos, expected 1"
                        )
                        raise ValueError(msg)

                    output_file.write(
                        f"[[{gl_info}|{by_gl_info[gl_info][0].toc_md_file[:-3]}]]\n\n"
                    )


def _process_comparisons(
    comparison_dir: str, output_dir: str, base_url: str
) -> dict[str, list[ResultsInfo]]:
    # TODO: Implement me.
    return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "results_dir",
        help="Directory including test outputs that will be processed",
    )
    parser.add_argument(
        "output_dir",
        help="Directory into which markdown files will be generated",
    )
    parser.add_argument(
        "--base-url",
        "-u",
        default="https://raw.githubusercontent.com/abaire/xemu-nxdk_pgraph_tests_results/main",
        help="Base URL at which the contents of the repository may be publicly accessed",
    )
    parser.add_argument(
        "--comparison-dir",
        "-c",
        help="Directory containing diff results that should be processed.",
    )

    args = parser.parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    if args.comparison_dir:
        xemu_version_to_comparison_results = _process_comparisons(
            args.comparison_dir, args.output_dir, args.base_url
        )
    else:
        xemu_version_to_comparison_results = {}

    results_writer = ResultsWriter(
        args.results_dir,
        args.output_dir,
        args.base_url,
        xemu_version_to_comparison_results,
    )

    xemu_version_to_results = results_writer.process_results()
    _write_home_markdown(args.output_dir, xemu_version_to_results)


if __name__ == "__main__":
    main()
