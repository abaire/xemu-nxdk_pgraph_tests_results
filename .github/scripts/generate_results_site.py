#!/usr/bin/env python3

# ruff: noqa: C416 Unnecessary dict comprehension
# ruff: noqa: C414 Unnecessary list call
# ruff: noqa: S701: By default, jinja2 sets `autoescape` to `False`.

from __future__ import annotations

import argparse
import glob
import json
import logging
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, NamedTuple

from frozendict import deepfreeze, frozendict
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

# Fully qualified comparison elements may be very long. This value is used to cap their length, switching to an MD5 if
# needed.
_MAX_NAME_COMPONENT_LENGTH = 24

# Run identifier used for comparisons against the nxdk_pgraph_tests_golden_results from Xbox hardware.
HW_GOLDEN_IDENTIFIER = "Xbox_Hardware"

COMPARE_SUBDIR = "compare"
RESULTS_SUBDIR = "results"


class TestSuiteDescriptor(NamedTuple):
    """Describes one of the nxdk_pgraph_tests test suites."""

    suite_name: str
    class_name: str
    description: list[str]
    source_file: str
    source_file_line: int
    test_descriptions: dict[str, str]

    @classmethod
    def from_obj(cls, obj: dict[str, Any]) -> TestSuiteDescriptor:
        return cls(
            suite_name=obj.get("suite", "").replace(" ", "_"),
            class_name=obj.get("class", ""),
            description=obj.get("description", []),
            source_file=obj.get("source_file", ""),
            source_file_line=obj.get("source_file_line", -1),
            test_descriptions=obj.get("test_descriptions", {}),
        )


def _fuzzy_lookup_suite_descriptor(
    descriptors: dict[str, TestSuiteDescriptor], suite_name: str
) -> TestSuiteDescriptor | None:
    """Attempts a permissive lookup of the given suite_name in the given set of `TestSuiteDescriptor`s"""

    # Check for a perfect match.
    ret = descriptors.get(suite_name)
    if ret:
        return ret

    # Descriptor keys are generally of the form TestSuiteTests whereas the suite names tend to be "Test_suite".
    camel_cased = "".join(element.title() for element in suite_name.split("_"))
    ret = descriptors.get(camel_cased)
    if ret:
        return ret

    return descriptors.get(f"{camel_cased}Tests")


class TestSuiteDescriptorLoader:
    """Loads test suite descriptors from the nxdk_pgraph_tests project."""

    def __init__(self, registry_url: str):
        self.registry_url = registry_url

    def _load_registry(self) -> dict[str, Any] | None:
        import requests

        try:
            response = requests.get(self.registry_url, timeout=30)
            response.raise_for_status()
            return json.loads(response.content)
        except requests.exceptions.RequestException:
            logger.exception("Failed to load descriptor from '%s'", self.registry_url)
            return None

    def process(self) -> dict[str, TestSuiteDescriptor]:
        """Loads the test suite descriptors from the nxdk_pgraph_tests project."""

        registry = self._load_registry()
        if not registry:
            return {}

        return {
            descriptor.suite_name: descriptor
            for descriptor in [TestSuiteDescriptor.from_obj(item) for item in registry.get("test_suites", [])]
        }


class RunIdentifier(NamedTuple):
    """Holds components of a run identifier."""

    run_identifier: tuple[str, ...]
    xemu_version: str
    platform_info: str
    gl_info: str

    @property
    def path(self) -> str:
        return str(os.path.join(*self.run_identifier))

    @property
    def minimal_path(self) -> str:
        """Returns a path consisting of 'xemu/platform/gl'"""
        return self.minimal_identifier().path

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


class TestCaseComparisonInfo(NamedTuple):
    """Encapsulates information about differences in results for a single test case."""

    test_name: str
    source_image_url: str
    golden_image_url: str
    diff_image_url: str
    diff_distance: float


class TestSuiteComparisonInfo(NamedTuple):
    """Encapsulates information about differences in results for tests within a single test suite."""

    suite_name: str
    test_cases: tuple[TestCaseComparisonInfo, ...]
    descriptor: TestSuiteDescriptor | None


class ComparisonInfo(NamedTuple):
    """"""

    identifier: RunIdentifier
    golden_identifier_component: str
    golden_identifier: str
    summary: frozendict[str, Any]
    results: tuple[TestSuiteComparisonInfo, ...]

    @classmethod
    def parse(
        cls,
        run_identifier: str,
        summary: dict[str, Any],
        results: tuple[TestSuiteComparisonInfo, ...],
    ) -> ComparisonInfo:
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
            results=results,
        )


class ComparisonScanner:
    """Scans and categorizes differences between test runs."""

    def __init__(
        self,
        comparison_dir: str,
        output_dir: str,
        base_url: str,
        results_dir: str,
        hw_golden_base_url: str,
        test_suite_descriptors: dict[str, TestSuiteDescriptor],
        golden_results_dir: str = "",
    ) -> None:
        self.comparison_dir = comparison_dir
        self.output_dir = output_dir
        self.base_url = base_url
        self.results_dir = results_dir
        self.golden_results_dir = golden_results_dir if golden_results_dir else results_dir
        self.hw_golden_base_url = hw_golden_base_url
        self.test_suite_descriptors = test_suite_descriptors

    def _process_test_case_artifacts(
        self,
        test_suite_dir: str,
        suite_name: str,
        run_info: dict[str, Any],
        golden_base_url: str,
    ) -> list[TestCaseComparisonInfo]:
        """Processes the given test suite comparison results dir and generates TestCaseComparisonInfo for each diff."""

        images = glob.glob(os.path.join(test_suite_dir, "*.png"))

        if not images:
            return []

        # Restore the paths of the original images that were used to produce the diff image.
        # TODO: Store this as metadata instead of relying on consistent locations.
        results_base_path = os.path.join(self.results_dir, run_info["result_identifier"].replace(":", "/"))
        golden_base_path = (
            ""
            if run_info["golden_identifier"] == HW_GOLDEN_IDENTIFIER
            else os.path.join(self.golden_results_dir, run_info["golden_identifier"].replace(":", "/"))
        )

        ret: list[TestCaseComparisonInfo] = []

        for image_file in images:
            test_name = os.path.basename(image_file).replace("-diff.png", "")
            fq_name = f"{suite_name}:{test_name}"

            original_image_subpath = fq_name.split(":")
            source_image_url = "/".join([self.base_url, results_base_path, *original_image_subpath])
            golden_image_url = "/".join([golden_base_url, golden_base_path, *original_image_subpath])

            ret.append(
                TestCaseComparisonInfo(
                    test_name=test_name,
                    source_image_url=f"{source_image_url}.png",
                    golden_image_url=f"{golden_image_url}.png",
                    diff_image_url=f"{self.base_url}/{image_file}",
                    diff_distance=run_info["tests_with_differences"].get(fq_name, math.inf),
                )
            )

        return ret

    def _process_test_suite(self, test_suite_dir: str, run_info: dict[str, Any]) -> TestSuiteComparisonInfo | None:
        golden_base_url = (
            self.hw_golden_base_url if run_info["golden_identifier"] == HW_GOLDEN_IDENTIFIER else self.base_url
        )

        suite_name = os.path.basename(test_suite_dir)

        test_artifacts = self._process_test_case_artifacts(test_suite_dir, suite_name, run_info, golden_base_url)
        if test_artifacts:
            return TestSuiteComparisonInfo(
                suite_name=suite_name,
                test_cases=tuple(test_artifacts),
                descriptor=_fuzzy_lookup_suite_descriptor(self.test_suite_descriptors, suite_name),
            )
        return None

    def _process_comparison_artifacts(
        self, run_identifier_to_summary: dict[str, dict[str, Any]]
    ) -> list[ComparisonInfo]:
        """Processes the results for each comparison between pairs of results."""

        run_identifier_to_suits: dict[str, list[TestSuiteComparisonInfo]] = defaultdict(list)
        for run_root, run_info in run_identifier_to_summary.items():
            for item in os.listdir(run_root):
                suite_path = os.path.join(run_root, item)
                if os.path.isdir(suite_path):
                    result = self._process_test_suite(suite_path, run_info)
                    if result:
                        run_identifier_to_suits[run_root].append(result)

        ret: list[ComparisonInfo] = []
        for run_identifier, test_suites in run_identifier_to_suits.items():
            run_info = run_identifier_to_summary[run_identifier]
            ret.append(ComparisonInfo.parse(run_identifier, run_info, tuple(test_suites)))

        return ret

    def _process_summaries(self) -> dict[str, dict[str, Any]]:
        """Discovers summary.json files, loads them, and returns a map of directory path to their content."""
        summary_files = glob.glob("**/summary.json", root_dir=self.comparison_dir, recursive=True)

        def load_summary(subpath: str) -> tuple[str, dict[str, Any]]:
            full_path = os.path.join(self.comparison_dir, subpath)
            with open(full_path) as infile:
                return os.path.dirname(full_path), json.load(infile)

        return {key: value for key, value in [load_summary(summary_file) for summary_file in summary_files]}

    def process(
        self,
    ) -> dict[RunIdentifier, list[ComparisonInfo]]:
        """Processes the comparison directory into ComparisonInfo instances keyed by their emulator+platform+gl_info.

        Results that have been compared to multiple goldens will map to a list of comparisons in arbitrary order.
        """
        run_identifier_to_summary = self._process_summaries()
        ret: dict[RunIdentifier, list[ComparisonInfo]] = defaultdict(list)

        for comparison in self._process_comparison_artifacts(run_identifier_to_summary):
            ret[comparison.identifier.minimal_identifier()].append(comparison)

        return ret


# List of raw output from xemu related to machine information (xemu version, CPU, GL_VERSION, etc...)
MachineInfo = list[str]

# Dict of information about the renderer pipeline used when executing xemu.
RendererInfo = dict[str, str]

# Dict of results information output by the test executor.
ResultsSummary = dict[str, Any]


@dataclass
class TestResult:
    """Contains information about the results of a specific test within a suite."""

    name: str
    artifact_url: str
    info: frozendict[str, Any]


@dataclass
class SuiteResults:
    """Contains information about the results of a specific suite within a run."""

    name: str
    test_results: tuple[TestResult, ...]
    flaky_tests: frozendict[str, Any]
    failed_tests: frozendict[str, Any]
    descriptor: TestSuiteDescriptor | None


class ResultsInfo(NamedTuple):
    """Contains information about the results for a specific emu+machine+driver."""

    identifier: RunIdentifier
    machine_info: MachineInfo
    renderer_info: RendererInfo
    results: tuple[SuiteResults, ...]
    comparisons: list[ComparisonInfo]

    def get_machine_info_dict(self) -> dict[str, str]:
        """Parses machine_info into a dict."""
        ret: dict[str, str] = {}
        for line in self.machine_info:
            key, value = line.split(":", 1)
            ret[key] = value.strip()

        return ret


class ResultsScanner:
    """Scans and categorizes test results."""

    def __init__(
        self,
        results_dir: str,
        output_dir: str,
        base_url: str,
        run_identifier_to_comparison_results: dict[RunIdentifier, list[ComparisonInfo]],
        test_suite_descriptors: dict[str, TestSuiteDescriptor],
    ) -> None:
        self.results_dir = results_dir
        self.output_dir = output_dir
        self.base_url = base_url
        self.run_identifier_to_comparison_results = run_identifier_to_comparison_results
        self.test_suite_descriptors = test_suite_descriptors

    def _process_test_case_artifacts(
        self, test_suite_dir: str, suite_name: str, result_summary: ResultsSummary
    ) -> list[TestResult]:
        """Processes the given test suite results dir and generates TestResult for each artifact."""

        images = glob.glob(os.path.join(test_suite_dir, "*.png"))

        if not images:
            return []

        ret: list[TestResult] = []

        for image_file in images:
            test_name = os.path.splitext(os.path.basename(image_file))[0]
            fq_name = f"{suite_name}::{test_name}"
            test_info = result_summary.get("passed", {}).get(fq_name)
            if not test_info:
                test_info = result_summary.get("flaky", {}).get(fq_name)

            ret.append(
                TestResult(
                    name=test_name,
                    artifact_url=f"{self.base_url}/{image_file}",
                    info=deepfreeze(test_info),
                )
            )

        return ret

    def _get_suite_descriptor(self, suite_name: str) -> TestSuiteDescriptor | None:
        return _fuzzy_lookup_suite_descriptor(self.test_suite_descriptors, suite_name)

    def _process_suite(
        self, artifacts_path: str, suite_name: str, results_summary: ResultsSummary
    ) -> SuiteResults | None:
        test_artifacts = self._process_test_case_artifacts(artifacts_path, suite_name, results_summary)
        if test_artifacts:
            fq_prefix = f"{suite_name}::"
            flaky_tests = {
                key: value for key, value in results_summary.get("flaky", {}).items() if key.startswith(fq_prefix)
            }
            failed_tests = {
                key: value for key, value in results_summary.get("failed", {}).items() if key.startswith(fq_prefix)
            }

            return SuiteResults(
                name=suite_name,
                test_results=tuple(test_artifacts),
                flaky_tests=deepfreeze(flaky_tests),
                failed_tests=deepfreeze(failed_tests),
                descriptor=self._get_suite_descriptor(suite_name),
            )
        return None

    def _process_results(self, run_id: str, machine_info: MachineInfo, results_summary: ResultsSummary) -> ResultsInfo:
        suite_results: dict[str, SuiteResults] = {}

        for root, dirnames, filenames in os.walk(run_id):
            if dirnames:
                continue

            if not filenames:
                continue

            suite_name = os.path.basename(root)

            result = self._process_suite(root, suite_name, results_summary)
            if result:
                suite_results[suite_name] = result

        for fqname, failure in results_summary.get("failed", {}).items():
            suite, test = fqname.split("::")
            if suite not in suite_results:
                suite_results[suite] = SuiteResults(
                    name=suite,
                    test_results=(),
                    failed_tests=deepfreeze({fqname: failure}),
                    flaky_tests=frozendict(),
                    descriptor=self._get_suite_descriptor(suite),
                )

        run_identifier = RunIdentifier.parse(run_id)

        return ResultsInfo(
            identifier=run_identifier,
            machine_info=machine_info,
            renderer_info=results_summary.get("renderer_info"),
            results=tuple(list(suite_results.values())),
            comparisons=self.run_identifier_to_comparison_results.get(run_identifier.minimal_identifier(), []),
        )

    def _process_summaries(self) -> dict[str, tuple[MachineInfo, ResultsSummary]]:
        """Discovers results.json and machine_info.txt files and returns a map of directory path to their contents."""
        results_files = glob.glob("**/results.json", root_dir=self.results_dir, recursive=True)

        def load_results(subpath: str) -> tuple[str, ResultsSummary]:
            full_path = os.path.join(self.results_dir, subpath)
            with open(full_path) as infile:
                return os.path.dirname(full_path), json.load(infile)

        run_id_to_results: dict[str, ResultsSummary] = {
            key: value for key, value in [load_results(filename) for filename in results_files]
        }

        for run_id, results_summary in run_id_to_results.items():
            renderer_info_file = os.path.join(run_id, "renderer.json")
            if os.path.isfile(renderer_info_file):
                with open(renderer_info_file) as infile:
                    results_summary["renderer_info"] = json.load(infile)
            else:
                results_summary["renderer_info"] = {"vulkan": False}

        machine_info_files = glob.glob("**/machine_info.txt", root_dir=self.results_dir, recursive=True)

        def load_machine_info(subpath: str) -> tuple[str, MachineInfo]:
            full_path = os.path.join(self.results_dir, subpath)
            with open(full_path) as infile:
                content = infile.read()
                return os.path.dirname(full_path), content.split("\n")

        run_id_to_machine_info: dict[str, MachineInfo] = {
            key: value for key, value in [load_machine_info(filename) for filename in machine_info_files]
        }

        ret: dict[str, tuple[MachineInfo, ResultsSummary]] = {}
        for run_id, machine_info in run_id_to_machine_info.items():
            results_summary = run_id_to_results[run_id]
            ret[run_id] = (machine_info, results_summary)

        return ret

    def process(self) -> dict[str, ResultsInfo]:
        """Processes the results directory into {run_identifier: ResultsInfo}."""
        run_identifier_to_summary = self._process_summaries()

        return {
            run_id: self._process_results(run_id, *info_and_summary)
            for run_id, info_and_summary in run_identifier_to_summary.items()
        }


class PrettyMachineInfo(NamedTuple):
    """Returns the nicest possible human-readable components for a ResultsInfo."""

    platform: str
    gl: str
    glsl: str
    renderer: str

    @property
    def flat_name(self) -> str:
        return f"{self.platform} {self.renderer} {self.gl} {self.glsl}"

    @property
    def gl_info(self) -> str:
        return f"{self.gl} - GLSL version {self.glsl}"

    @classmethod
    def parse(cls, results_info: ResultsInfo) -> PrettyMachineInfo:
        machine_info_dict = results_info.get_machine_info_dict()

        cpu = machine_info_dict.get("CPU").replace("/", "-")
        os = machine_info_dict.get("OS_Version").replace("/", "-")
        gl_vendor = machine_info_dict.get("GL_VENDOR").replace("/", "-")
        gl_renderer = machine_info_dict.get("GL_RENDERER").replace("/", "-")
        gl_version = machine_info_dict.get("GL_VERSION").replace("/", "-")
        glsl_version = machine_info_dict.get("GL_SHADING_LANGUAGE_VERSION").replace("/", "-")

        run_identifier = results_info.identifier
        platform = f"{os} - {cpu}" if cpu and os else run_identifier.platform_info
        gl = (
            f"{gl_vendor} - {gl_renderer} - {gl_version}"
            if gl_vendor and gl_renderer and gl_version
            else run_identifier.gl_info.split(":")[0]
        )
        if not glsl_version:
            glsl_version = run_identifier.gl_info.split(":")[1]
        renderer = "Vulkan" if results_info.renderer_info.get("vulkan") else "OpenGL"

        return cls(platform=platform, gl=gl, glsl=glsl_version, renderer=renderer)


class PagesWriter:
    """Generates HTML output suitable for GitHub pages."""

    def __init__(
        self,
        results: dict[str, ResultsInfo],
        env: Environment,
        output_dir: str,
        result_images_base_url: str,
        hw_golden_images_base_url: str,
        test_source_base_url: str,
    ) -> None:
        self.results = results
        self.env = env
        self.output_dir = output_dir.rstrip("/")
        self.css_output_dir = output_dir.rstrip("/")
        self.js_output_dir = output_dir.rstrip("/")
        self.images_base_url = result_images_base_url.rstrip("/")
        self.hw_images_base_url = hw_golden_images_base_url.rstrip("/")
        self.test_source_base_url = test_source_base_url.rstrip("/")

    @staticmethod
    def _comparison_suite_url(comparison: ComparisonInfo, suite_result: TestSuiteComparisonInfo) -> str:
        return os.path.join(COMPARE_SUBDIR, comparison.identifier.minimal_path, f"{suite_result.suite_name}.html")

    def _home_url(self, output_dir) -> str:
        return f"{os.path.relpath(self.output_dir, output_dir)}/index.html"

    def _write_comparison_suite_page(
        self,
        comparison: ComparisonInfo,
        suite_result: TestSuiteComparisonInfo,
        results: list[TestCaseComparisonInfo],
        navigate_up_url: str,
    ) -> None:
        """Generates a page that renders all diffs between a result set and golden for a particular test suite."""
        index_template = self.env.get_template("suite_comparison_result.html.j2")
        output_dir = os.path.join(self.output_dir, COMPARE_SUBDIR, comparison.identifier.minimal_path)
        os.makedirs(output_dir, exist_ok=True)

        with open(os.path.join(output_dir, f"{suite_result.suite_name}.html"), "w") as outfile:
            outfile.write(
                index_template.render(
                    source_identifier=comparison.summary["result_identifier"],
                    golden_identifier=comparison.summary["golden_identifier"],
                    suite_name=suite_result.suite_name,
                    results=results,
                    css_dir=os.path.relpath(self.css_output_dir, output_dir),
                    js_dir=os.path.relpath(self.js_output_dir, output_dir),
                    home_url=self._home_url(output_dir),
                    navigate_up_url=navigate_up_url,
                    descriptor=self._pack_descriptor(suite_result.descriptor),
                )
            )

    @staticmethod
    def _comparison_url(comparison: ComparisonInfo) -> str:
        return os.path.join(COMPARE_SUBDIR, comparison.identifier.minimal_path, "index.html")

    def _write_comparisons_page(self, comparison: ComparisonInfo, golden_base_url: str) -> None:
        """Generates a page that renders all diffs between a pair of results, with links to per-suite diff pages."""

        index_template = self.env.get_template("comparison_result.html.j2")
        output_subdir = os.path.join(COMPARE_SUBDIR, comparison.identifier.minimal_path)
        output_dir = os.path.join(self.output_dir, output_subdir)
        os.makedirs(output_dir, exist_ok=True)

        navigate_up_url = f"{os.path.relpath(self.output_dir, output_dir)}/{RESULTS_SUBDIR}/{comparison.identifier.minimal_path}/index.html#{comparison.golden_identifier}"

        suite_to_results: dict[str, list[TestCaseComparisonInfo]] = defaultdict(
            list,
            {result.suite_name: list(result.test_cases) for result in comparison.results},
        )

        for fqname in comparison.summary.get("goldens_without_results", []):
            suite_name, test_name = self.split_fq_name(fqname)
            info = TestCaseComparisonInfo(
                test_name=test_name,
                source_image_url="",
                golden_image_url=self.golden_url_for_fqtest(fqname, golden_base_url),
                diff_image_url="",
                diff_distance=math.inf,
            )
            suite_to_results[suite_name].append(info)

        for fqname in comparison.summary.get("tests_without_goldens", []):
            suite_name, test_name = self.split_fq_name(fqname)
            info = TestCaseComparisonInfo(
                test_name=test_name,
                source_image_url=self.results_url_for_fqtest(comparison.identifier, fqname),
                golden_image_url="",
                diff_image_url="",
                diff_distance=math.inf,
            )
            suite_to_results[suite_name].append(info)

        with open(os.path.join(output_dir, "index.html"), "w") as outfile:
            outfile.write(
                index_template.render(
                    source_identifier=comparison.summary["result_identifier"],
                    golden_identifier=comparison.summary["golden_identifier"],
                    results={
                        suite.suite_name: {
                            "url": os.path.relpath(
                                self._comparison_suite_url(comparison, suite),
                                output_subdir,
                            ),
                            "test_results": suite_to_results[suite.suite_name],
                            "descriptor": suite.descriptor,
                        }
                        for suite in comparison.results
                    },
                    css_dir=os.path.relpath(self.css_output_dir, output_dir),
                    js_dir=os.path.relpath(self.js_output_dir, output_dir),
                    home_url=self._home_url(output_dir),
                    navigate_up_url=navigate_up_url,
                )
            )

        for suite_results in comparison.results:
            self._write_comparison_suite_page(
                comparison, suite_results, suite_to_results[suite_results.suite_name], navigate_up_url
            )

    @staticmethod
    def split_fq_name(fully_qualified_test_name: str) -> tuple[str, str]:
        """Splits a fully qualified test name into (suite, test_case)."""
        split = fully_qualified_test_name.split(":", 1)
        return split[0], split[1]

    @staticmethod
    def golden_url_for_fqtest(fully_qualified_test_name: str, golden_base_url: str) -> str:
        path = "/".join([golden_base_url, *PagesWriter.split_fq_name(fully_qualified_test_name)])
        return f"{path}.png"

    def results_url_for_fqtest(self, run: RunIdentifier, fully_qualified_test_name: str) -> str:
        path = "/".join(
            [
                self.images_base_url,
                RESULTS_SUBDIR,
                run.minimal_path.replace(":", "/"),
                *self.split_fq_name(fully_qualified_test_name),
            ]
        )
        return f"{path}.png"

    @staticmethod
    def _suite_result_url(run: ResultsInfo, suite: SuiteResults) -> str:
        return os.path.join(RESULTS_SUBDIR, run.identifier.minimal_path, suite.name, "index.html")

    def _suite_source_url(self, source_file_path: str, source_line: int) -> str:
        if self.test_source_base_url and source_file_path:
            if source_line >= 0:
                return f"{self.test_source_base_url}/{source_file_path}#L{source_line}"
            return f"{self.test_source_base_url}/{source_file_path}"
        return ""

    def _pack_descriptor(self, descriptor: TestSuiteDescriptor | None) -> dict[str, Any] | None:
        if not descriptor:
            return None
        return {
            "description": descriptor.description,
            "source_file": descriptor.source_file,
            "source_url": self._suite_source_url(descriptor.source_file, descriptor.source_file_line),
            "test_descriptions": descriptor.test_descriptions,
        }

    def _write_test_suite_results_page(self, run: ResultsInfo, suite: SuiteResults) -> None:
        """Generates a page for all of the test case results within a single test suite."""
        index_template = self.env.get_template("test_suite_results.html.j2")
        output_subdir = os.path.join(RESULTS_SUBDIR, run.identifier.minimal_path, suite.name)
        output_dir = os.path.join(self.output_dir, output_subdir)
        os.makedirs(output_dir, exist_ok=True)

        pretty_machine_info = PrettyMachineInfo.parse(run)
        result_infos: dict[str, dict[str, str]] = {}
        for result in suite.test_results:
            result_infos[result.name] = {"url": result.artifact_url}
        for info in suite.flaky_tests.values():
            result_infos.get(info["name"], {})["failures"] = info["failures"]
        for info in suite.failed_tests.values():
            result_infos[info["name"]] = {"url": None, "failures": info["failures"]}

        with open(os.path.join(output_dir, "index.html"), "w") as outfile:
            outfile.write(
                index_template.render(
                    run_identifier=run.identifier,
                    pretty_machine_info=pretty_machine_info,
                    suite_name=suite.name,
                    results=result_infos,
                    css_dir=os.path.relpath(self.css_output_dir, output_dir),
                    js_dir=os.path.relpath(self.js_output_dir, output_dir),
                    descriptor=self._pack_descriptor(suite.descriptor),
                    home_url=self._home_url(output_dir),
                    navigate_up_url="../index.html",
                )
            )

    def _write_run_results_pages(self, run: ResultsInfo) -> None:
        """Generates a page containing links to all of the suites and comparisons for a specific xemu/platform/gl."""
        index_template = self.env.get_template("test_run_results.html.j2")
        output_subdir = os.path.join(RESULTS_SUBDIR, run.identifier.minimal_path)
        output_dir = os.path.join(self.output_dir, output_subdir)
        os.makedirs(output_dir, exist_ok=True)

        result_urls = {
            suite.name: os.path.relpath(self._suite_result_url(run, suite), output_subdir) for suite in run.results
        }

        all_failed_tests: dict[str, list[str]] = {}
        all_flaky_tests: dict[str, list[str]] = {}
        for suite in run.results:
            self._write_test_suite_results_page(run, suite)
            for name, info in suite.failed_tests.items():
                all_failed_tests[name] = info.get("failures", [])
            for name, info in suite.flaky_tests.items():
                all_flaky_tests[name] = info.get("failures", [])

        comparisons: dict[str, dict[str, str]] = {}
        for comparison in run.comparisons:
            golden_base_url = (
                self.hw_images_base_url
                if comparison.golden_identifier == HW_GOLDEN_IDENTIFIER
                else self.images_base_url
            )

            missing_tests: dict[str, str] = {
                fqname.replace(":", " :: "): self.golden_url_for_fqtest(fqname, golden_base_url)
                for fqname in comparison.summary.get("goldens_without_results", [])
            }
            extra_tests: dict[str, str] = {
                fqname.replace(":", " "): self.results_url_for_fqtest(run.identifier, fqname)
                for fqname in comparison.summary.get("tests_without_goldens", [])
            }

            comparisons[comparison.golden_identifier] = {
                "comparison_page": os.path.relpath(self._comparison_url(comparison), output_subdir),
                "results": {
                    suite_result.suite_name: os.path.relpath(
                        self._comparison_suite_url(comparison, suite_result),
                        output_subdir,
                    )
                    for suite_result in comparison.results
                },
                "missing_tests": missing_tests,
                "extra_tests": extra_tests,
                "golden_identifier": comparison.golden_identifier,
            }

            self._write_comparisons_page(comparison, golden_base_url)

        home_url = self._home_url(output_dir)
        with open(os.path.join(output_dir, "index.html"), "w") as outfile:
            pretty_machine_info = PrettyMachineInfo.parse(run)
            outfile.write(
                index_template.render(
                    run_identifier=run.identifier,
                    machine_info=run.machine_info,
                    pretty_machine_info=pretty_machine_info,
                    comparisons=comparisons,
                    test_suites=result_urls,
                    failed_tests=all_failed_tests,
                    flaky_tests=all_flaky_tests,
                    css_dir=os.path.relpath(self.css_output_dir, output_dir),
                    js_dir=os.path.relpath(self.js_output_dir, output_dir),
                    home_url=home_url,
                    navigate_up_url=home_url,
                )
            )

    def _write_top_level_index(self) -> None:
        run_identifier_keyed_results = {run.identifier: run for run in self.results.values()}

        index_template = self.env.get_template("index.html.j2")
        output_dir = self.output_dir

        with open(os.path.join(output_dir, "index.html"), "w") as outfile:
            emulator_grouped_pages = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
            for run_identifier, run in run_identifier_keyed_results.items():
                pretty_machine_info = PrettyMachineInfo.parse(run)
                emulator_grouped_pages[run_identifier.xemu_version][pretty_machine_info.platform][
                    pretty_machine_info.renderer
                ].append(
                    {
                        "results_url": f"{RESULTS_SUBDIR}/{run_identifier.minimal_path}/index.html",
                        "machine_info": pretty_machine_info,
                    }
                )
            outfile.write(
                index_template.render(
                    emulator_grouped_results=emulator_grouped_pages,
                    css_dir=os.path.relpath(self.css_output_dir, output_dir),
                    js_dir=os.path.relpath(self.js_output_dir, output_dir),
                )
            )

    def _write_css(self) -> None:
        css_template = self.env.get_template("site.css.j2")
        with open(os.path.join(self.css_output_dir, "site.css"), "w") as outfile:
            outfile.write(
                css_template.render(
                    comparison_golden_outline_size=6,
                    title_bar_height=40,
                )
            )

    def _write_js(self) -> None:
        css_template = self.env.get_template("script.js.j2")
        with open(os.path.join(self.js_output_dir, "script.js"), "w") as outfile:
            outfile.write(css_template.render())

    def write(self) -> int:
        os.makedirs(self.output_dir, exist_ok=True)
        self._write_css()
        self._write_js()
        self._write_top_level_index()
        for run in self.results.values():
            self._write_run_results_pages(run)

        return 0


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
        "--hw-golden-base-url",
        default="https://raw.githubusercontent.com/abaire/nxdk_pgraph_tests_golden_results/main/results",
        help="Base URL at which the contents of the golden images from Xbox hardware may be publicly accessed.",
    )
    parser.add_argument(
        "--comparison-dir",
        "-c",
        help="Directory containing diff results that should be processed.",
    )
    parser.add_argument(
        "--templates-dir",
        help="Directory containing the templates used to render the site.",
    )
    parser.add_argument(
        "--golden-results-dir",
        help="Overrides the directory containing non-hardware golden results. Defaults to <results_dir>.",
    )
    parser.add_argument(
        "--test-descriptor-registry-url",
        default="https://raw.githubusercontent.com/abaire/nxdk_pgraph_tests/pages_doxygen/xml/nxdk_pgraph_tests_registry.json",
        help="URL at which the JSON test suite registry for nxdk_pgraph_tests may be publicly accessed.",
    )
    parser.add_argument(
        "--test-source-browser-base-url",
        default="https://github.com/abaire/nxdk_pgraph_tests/blob/pages_doxygen",
        help="Base URL from which the test suite source files may be publicly accessed.",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level)

    os.makedirs(args.output_dir, exist_ok=True)

    test_suite_descriptors = (
        TestSuiteDescriptorLoader(args.test_descriptor_registry_url).process()
        if args.test_descriptor_registry_url
        else {}
    )

    if args.comparison_dir:
        run_identifier_to_comparison_results = ComparisonScanner(
            args.comparison_dir,
            args.output_dir,
            args.base_url,
            args.results_dir,
            args.hw_golden_base_url,
            test_suite_descriptors,
            args.golden_results_dir,
        ).process()
    else:
        run_identifier_to_comparison_results = {}

    results = ResultsScanner(
        args.results_dir,
        args.output_dir,
        args.base_url,
        run_identifier_to_comparison_results,
        test_suite_descriptors,
    ).process()

    if not args.templates_dir:
        args.templates_dir = os.path.join(os.path.dirname(__file__), "site-templates")

    jinja_env = Environment(loader=FileSystemLoader(args.templates_dir))
    jinja_env.globals["sidenav_width"] = 48
    jinja_env.globals["sidenav_icon_width"] = 32

    return PagesWriter(
        results, jinja_env, args.output_dir, args.base_url, args.hw_golden_base_url, args.test_source_browser_base_url
    ).write()


if __name__ == "__main__":
    sys.exit(main())
