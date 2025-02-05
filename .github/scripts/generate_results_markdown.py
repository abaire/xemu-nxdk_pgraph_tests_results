#!/bin/env python3

from __future__ import annotations

import argparse
import glob
import hashlib
import itertools
import json
import logging
import os
import urllib.parse
from collections import defaultdict
from typing import NamedTuple, Any

from frozendict import frozendict, deepfreeze

logger = logging.getLogger(__name__)

# Fully qualified comparison elements may be very long. This value is used to cap their length, switching to an MD5 if
# needed.
_MAX_NAME_COMPONENT_LENGTH = 24


class RunIdentifier(NamedTuple):
    """Holds components of a run identifier."""

    run_identifier: tuple[str, ...]
    xemu_version: str
    platform_info: str
    gl_info: str

    def minimal_identifier(self) -> RunIdentifier:
        """Returns a RunIdentifier that omits any extraneous components of the run_identifier member."""
        return RunIdentifier(
            run_identifier=(self.xemu_version, self.platform_info, self.gl_info),
            xemu_version=self.xemu_version,
            platform_info=self.platform_info,
            gl_info=self.gl_info,
        )

    @classmethod
    def parse(cls, run_identifier: str) -> RunIdentifier:
        # results/Linux_foo/gl_version/glsl_version/xemu_version
        components = run_identifier.split("/")
        return cls(
            run_identifier=tuple(components),
            xemu_version=components[-4],
            platform_info=components[-3],
            gl_info=f"{components[-2]}:{components[-1]}",
        )


class ComparisonInfo(NamedTuple):
    identifier: RunIdentifier
    golden_identifier_component: str
    golden_identifier: str
    summary: frozendict[str, Any]

    @classmethod
    def parse(cls, run_identifier: str, summary: dict[str, Any]) -> ComparisonInfo:
        components = run_identifier.split("/")

        return cls(
            identifier=RunIdentifier(
                run_identifier=tuple(components),
                xemu_version=components[-4],
                platform_info=components[-3],
                gl_info=components[-2],
            ),
            golden_identifier_component=os.path.basename(run_identifier),
            golden_identifier=summary.get("golden_identifier", "UNKNOWN"),
            summary=deepfreeze(summary),
        )


class ResultsInfo(NamedTuple):
    """Contains information about the results for a specific emu+machine+driver."""

    identifier: RunIdentifier
    toc_md_file: str

    @classmethod
    def parse(cls, run_identifier: str, toc_md_file: str) -> ResultsInfo:
        return cls(
            identifier=RunIdentifier.parse(run_identifier),
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


def short_name(name_string: str) -> str:
    """Shortens the given name, if necessary."""
    if len(name_string) < _MAX_NAME_COMPONENT_LENGTH:
        return name_string

    hash = hashlib.md5()
    hash.update(name_string.encode("utf-8"))
    return hash.hexdigest()


class ComparisonWriter:
    """Generates markdown describing a comparison between a pair of raw results."""

    def __init__(
        self,
        comparison_dir: str,
        output_dir: str,
        base_url: str,
    ) -> None:
        self.comparison_dir = comparison_dir
        self.output_dir = output_dir
        self.base_url = base_url

    def generate_markdown_for_test_suite(
        self, test_suite_dir: str, run_info: dict[str, Any]
    ) -> tuple[str, str] | None:
        """Generates the markdown for the contents of a test suite page.

        :return tuple[suite_name, markdown_content]
        """
        suite_name = os.path.basename(test_suite_dir)

        images = glob.glob(os.path.join(test_suite_dir, "*.png"))

        if not images:
            return None

        ret = [
            f"Diffs - {suite_name} vs {run_info['golden_identifier']}",
            "===",
        ]

        distance_dict = run_info["tests_with_differences"]

        for image_file in sorted(images):
            test_name = os.path.splitext(os.path.basename(image_file))[0]
            fq_name = f"{suite_name}:{test_name}"
            distance_name = fq_name[:-5]  # Drop "-diff" suffix
            distance = distance_dict.get(distance_name, "UNKNOWN")
            ret.append(f"## {test_name} - {distance}")
            ret.append(make_image_link(self.base_url, image_file))

        return suite_name, "\n".join(ret)

    def _create_test_suite_files(
        self, run_identifier_to_summary: dict[str, dict[str, Any]]
    ) -> dict[ComparisonInfo, list[tuple[str, str]]]:
        """Writes markdown files for each test suite.

        :return {ComparisonInfo: list[tuple[page_title, md_filename]]}
        """
        comparison_to_named_pages: dict[ComparisonInfo, list[tuple[str, str]]] = (
            defaultdict(list)
        )
        for root, dirnames, filenames in os.walk(self.comparison_dir):
            if dirnames:
                continue

            if not filenames:
                continue

            run_identifier = os.path.dirname(root)
            run_info = run_identifier_to_summary[run_identifier]

            active_name = short_name(run_info["result_identifier"])
            golden_name = short_name(run_info["golden_identifier"])
            suite_suffix = make_md_filename(os.path.basename(root))
            raw_md_filename = f"cmp_{active_name}_{golden_name}_{suite_suffix}"

            comparison_info = ComparisonInfo.parse(run_identifier, run_info)

            output_filename = os.path.join(self.output_dir, raw_md_filename)

            suite_name, content = self.generate_markdown_for_test_suite(root, run_info)
            if content:
                with open(output_filename, "w") as output_file:
                    output_file.write(content)
                comparison_to_named_pages[comparison_info].append(
                    (suite_name, raw_md_filename)
                )

        return comparison_to_named_pages

    def _process_summaries(self) -> dict[str, dict[str, Any]]:
        """Discovers summary.json files, loads them, and returns a map of directory path to their content."""
        summary_files = glob.glob(
            "**/summary.json", root_dir=self.comparison_dir, recursive=True
        )

        def load_summary(subpath: str) -> tuple[str, dict[str, Any]]:
            full_path = os.path.join(self.comparison_dir, subpath)
            with open(full_path) as infile:
                return os.path.dirname(full_path), json.load(infile)

        return {
            key: value
            for key, value in [
                load_summary(summary_file) for summary_file in summary_files
            ]
        }

    def process(
        self,
    ) -> dict[RunIdentifier, list[tuple[ComparisonInfo, list[tuple[str, str]]]]]:
        """Processes the comparison directory and generates markdown files.

        :return {xemu_version: list[tuple[ComparisonInfo, list[tuple[title, md_filename]]]} describing the generated pages.
        """
        run_identifier_to_summary = self._process_summaries()
        comparison_to_files = self._create_test_suite_files(run_identifier_to_summary)

        ret: dict[RunIdentifier, list[tuple[ComparisonInfo, list[tuple[str, str]]]]] = (
            defaultdict(list)
        )
        for comparison, files in comparison_to_files.items():
            ret[comparison.identifier.minimal_identifier()].append((comparison, files))
        return ret


class ResultsWriter:
    """Generates markdown describing raw results."""

    def __init__(
        self,
        results_dir: str,
        output_dir: str,
        base_url: str,
        run_identifier_to_comparison_results: dict[
            RunIdentifier, list[tuple[ComparisonInfo, list[tuple[str, str]]]]
        ],
    ) -> None:
        self.results_dir = results_dir
        self.output_dir = output_dir
        self.base_url = base_url
        self.run_identifier_to_comparison_results = run_identifier_to_comparison_results

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

        return "\n".join(ret)

    def generate_markdown_for_machine_info(self, run_result_dir: str) -> str:
        ret = []
        machine_info_file = os.path.join(run_result_dir, "machine_info.txt")
        if os.path.isfile(machine_info_file):
            ret.append("")
            ret.append("# Machine info")
            ret.append("```")
            with open(machine_info_file, "r") as infile:
                ret.append(infile.read())
            ret.append("```")
        return "\n".join(ret)

    def generate_markdown_for_comparisons(self, result_info: ResultsInfo) -> str:
        result_key = result_info.identifier.minimal_identifier()
        comparisons = self.run_identifier_to_comparison_results.get(result_key, [])
        if not comparisons:
            return ""

        def bulletted_fq_names(fq_names: list[str]) -> list[str]:
            return [f"* {name.replace(':', ' : ')}" for name in sorted(fq_names)]

        ret = ["", "# Comparisons"]
        for comparison, md_files in comparisons:
            ret.append(f"## {comparison.golden_identifier.replace(':', ' ')}")

            for page_title, md_file in sorted(md_files, key=lambda x: x[0]):
                ret.append(f"- [[{page_title}|{md_file[:-3]}]]\n")

            missing_goldens = comparison.summary.get("tests_without_goldens")
            if missing_goldens:
                ret.extend(
                    [
                        "### Results present without a golden to compare to",
                        *bulletted_fq_names(missing_goldens),
                    ]
                )

            missing_results = comparison.summary.get("goldens_without_results")
            if missing_results:
                ret.extend(
                    ["### Missing results", *bulletted_fq_names(missing_results)]
                )

        return "\n".join(ret)

    def _create_run_overview_file(
        self, result_dir_to_markdown_files: dict[str, list[str]]
    ) -> dict[str, list[ResultsInfo]]:
        """Creates the overview file for the run (links to each test suite page and provides top level info"""
        xemu_version_to_results: dict[str, list[ResultsInfo]] = defaultdict(list)

        for run_result_dir, results in result_dir_to_markdown_files.items():
            raw_md_filename = make_md_filename(run_result_dir)
            results_info = ResultsInfo.parse(run_result_dir, raw_md_filename)
            output_filename = os.path.join(self.output_dir, raw_md_filename)
            with open(output_filename, "w") as output_file:

                def friendly_page_name(result_md_file: str) -> str:
                    return os.path.splitext(result_md_file[len(raw_md_filename) - 1 :])[
                        0
                    ]

                machine_info = self.generate_markdown_for_machine_info(run_result_dir)
                if machine_info:
                    output_file.write(machine_info)

                output_file.writelines(
                    [
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

                comparisons = self.generate_markdown_for_comparisons(results_info)
                if comparisons:
                    output_file.write(comparisons)

            xemu_version_to_results[results_info.identifier.xemu_version].append(
                results_info
            )
        return xemu_version_to_results

    def process(self) -> dict[str, list[ResultsInfo]]:
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
                    xemu_version_to_results[xemu_version],
                    key=lambda x: x.identifier.platform_info,
                )
            }

            for platform in sorted(by_platform.keys()):
                output_file.write(f"## {platform}\n")

                by_gl_info = {
                    gl_info: list(group)
                    for gl_info, group in itertools.groupby(
                        by_platform[platform], key=lambda x: x.identifier.gl_info
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
        "--verbose",
        "-v",
        help="Enables verbose logging information",
        action="store_true",
    )
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

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level)

    os.makedirs(args.output_dir, exist_ok=True)

    if args.comparison_dir:
        run_identifier_to_comparison_results = ComparisonWriter(
            args.comparison_dir, args.output_dir, args.base_url
        ).process()
    else:
        run_identifier_to_comparison_results = {}

    logger.debug("Comparison files: %s", run_identifier_to_comparison_results)

    results_writer = ResultsWriter(
        args.results_dir,
        args.output_dir,
        args.base_url,
        run_identifier_to_comparison_results,
    )

    xemu_version_to_results = results_writer.process()
    _write_home_markdown(args.output_dir, xemu_version_to_results)


if __name__ == "__main__":
    main()
