#!/bin/env python3

from __future__ import annotations

import argparse
import glob
import itertools
import os
import re
import urllib.parse
from collections import defaultdict
from typing import NamedTuple

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
            xemu_version=components[-1],
            platform_info=components[-4],
            gl_info=f"{components[-3]}:{components[-2]}",
            toc_md_file=toc_md_file,
        )


def generate_markdown_for_test_suite(results_dir: str, base_url: str) -> str | None:
    test_name_match = re.search(r"([^/]+)$", results_dir)  # Extract test name
    if not test_name_match:
        print(f"Warning: Could not extract test name from directory: {results_dir}")
        test_name = "Unknown"
    else:
        test_name = test_name_match.group(1)

    images = glob.glob(os.path.join(results_dir, "*.png"))

    if not images:
        return None

    def image_link(image_path: str) -> str:

        image_path = urllib.parse.quote(image_path)
        return f"![{os.path.basename(image_path)}]({base_url}/{image_path})"

    return "\n".join(
        [
            f"# Results - {test_name}",
            "",
            *[f"{image_link(image_file)}\n" for image_file in images],
        ]
    )


def make_md_filename(results_dir: str) -> str:
    sanitized_name = results_dir.replace("/", "__")
    return f"{sanitized_name}.md"


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
        "--base_url",
        default="https://raw.githubusercontent.com/abaire/xemu-nxdk_pgraph_tests_results/main",
        help="Base URL at which the contents of the repository may be publicly accessed",
    )

    args = parser.parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    all_result_md_files: dict[str, list[str]] = defaultdict(list)

    for root, dirnames, filenames in os.walk(args.results_dir):
        if dirnames:
            continue
        if filenames:
            # Github's wiki does not support pages in subdirs, so they are only used for image content. Test suites
            # should all have unique names so this should not cause issues.

            run_identifier = os.path.dirname(root)
            raw_md_filename = make_md_filename(root)
            all_result_md_files[run_identifier].append(raw_md_filename)

            output_filename = os.path.join(args.output_dir, raw_md_filename)

            with open(output_filename, "w") as output_file:
                content = generate_markdown_for_test_suite(root, args.base_url)
                output_file.write(content)

    xemu_version_to_results: dict[str, list[ResultsInfo]] = defaultdict(list)

    for run_identifier, results in all_result_md_files.items():
        raw_md_filename = make_md_filename(run_identifier)
        output_filename = os.path.join(args.output_dir, raw_md_filename)
        with open(output_filename, "w") as output_file:

            def friendly_page_name(result_md_file: str) -> str:
                return os.path.splitext(result_md_file[len(raw_md_filename) - 1 :])[0]

            output_file.writelines(
                [
                    f"{run_identifier}\n",
                    "===\n",
                    *[
                        f"- [[{friendly_page_name(result)}|{result[:-3]}]]\n"
                        for result in sorted(results)
                    ],
                ]
            )

        results_info = ResultsInfo.parse(run_identifier, raw_md_filename)
        xemu_version_to_results[results_info.xemu_version].append(results_info)

    _write_home_markdown(args.output_dir, xemu_version_to_results)


if __name__ == "__main__":
    main()
