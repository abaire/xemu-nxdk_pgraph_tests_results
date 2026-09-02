#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    ),
)

from plan_config_diffs import (
    make_comparison_slug,
    normalize_run_input,
    plan_config_diffs,
)


class TestPlanConfigDiffs(unittest.TestCase):
    def test_normalize_run_input(self):
        # URL with index.html
        url1 = "https://abaire.github.io/xemu-nxdk_pgraph_tests_results/results/xemu-0.8.136/Linux_x86_64/gl_NVIDIA/index.html"
        self.assertEqual(
            normalize_run_input(url1), "xemu-0.8.136/Linux_x86_64/gl_NVIDIA"
        )

        # URL without index.html and with trailing slash
        url2 = "http://example.com/results/xemu-0.8.135/Darwin_arm64/gl_Apple/"
        self.assertEqual(
            normalize_run_input(url2), "xemu-0.8.135/Darwin_arm64/gl_Apple"
        )

        # URL encoded characters
        url3 = "https://example.com/results/xemu-0.8.136/Linux_x86_64/gl_NVIDIA%20GeForce/index.html"
        self.assertEqual(
            normalize_run_input(url3), "xemu-0.8.136/Linux_x86_64/gl_NVIDIA GeForce"
        )

        # Relative path with results/
        path1 = "results/xemu-0.8.136/Linux_x86_64/gl_NVIDIA"
        self.assertEqual(
            normalize_run_input(path1), "xemu-0.8.136/Linux_x86_64/gl_NVIDIA"
        )

        # Relative path without results/
        path2 = "xemu-0.8.136/Linux_x86_64/gl_NVIDIA"
        self.assertEqual(
            normalize_run_input(path2), "xemu-0.8.136/Linux_x86_64/gl_NVIDIA"
        )

        # Empty
        self.assertEqual(normalize_run_input(""), "")

    def test_make_comparison_slug(self):
        slug = make_comparison_slug(
            "xemu-0.8.136/Linux_x86_64/gl_NVIDIA",
            "xemu-0.8.135/Linux_x86_64/gl_NVIDIA",
        )
        self.assertEqual(
            slug,
            "xemu-0.8.136__Linux_x86_64__gl_NVIDIA--vs--xemu-0.8.135__Linux_x86_64__gl_NVIDIA",
        )

    def test_plan_config_diffs_hw_matching(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            results_dir = os.path.join(tmpdir, "results")
            compare_results_dir = os.path.join(tmpdir, "compare-results")

            source_run = "xemu-A/Linux/gl"
            target_run = "xemu-B/Linux/gl"

            # Create test images in source and target
            # suite1:
            #   test_both_match_hw (neither in tests_with_differences) -> should be excluded
            #   test_source_differs_hw (in source tests_with_differences) -> should be included
            #   test_both_differ_hw (in both tests_with_differences) -> should be included
            src_suite = os.path.join(results_dir, source_run, "suite1")
            tgt_suite = os.path.join(results_dir, target_run, "suite1")
            os.makedirs(src_suite)
            os.makedirs(tgt_suite)

            for t in [
                "test_both_match_hw",
                "test_source_differs_hw",
                "test_both_differ_hw",
            ]:
                with open(os.path.join(src_suite, f"{t}.png"), "w") as f:
                    f.write("src")
                with open(os.path.join(tgt_suite, f"{t}.png"), "w") as f:
                    f.write("tgt")

            # Create HW summaries
            src_hw_dir = os.path.join(
                compare_results_dir, source_run, "Xbox__Xbox__DirectX__nv2a"
            )
            tgt_hw_dir = os.path.join(
                compare_results_dir, target_run, "Xbox__Xbox__DirectX__nv2a"
            )
            os.makedirs(src_hw_dir)
            os.makedirs(tgt_hw_dir)

            with open(os.path.join(src_hw_dir, "summary.json"), "w") as f:
                json.dump(
                    {
                        "tests_with_differences": {
                            "suite1:test_source_differs_hw": 12.0,
                            "suite1:test_both_differ_hw": 45.0,
                        }
                    },
                    f,
                )

            with open(os.path.join(tgt_hw_dir, "summary.json"), "w") as f:
                json.dump(
                    {
                        "tests_with_differences": {
                            "suite1:test_both_differ_hw": 45.0,
                        }
                    },
                    f,
                )

            slug, tasks, tests_matching_hw, tests_without_match = plan_config_diffs(
                source_run=source_run,
                target_run=target_run,
                results_dir=results_dir,
                compare_results_dir=compare_results_dir,
                output_dir=os.path.join(tmpdir, "config-comparisons"),
            )

            self.assertEqual(slug, make_comparison_slug(source_run, target_run))
            self.assertEqual(tests_without_match, [])
            # test_both_match_hw should be in tests_matching_hw and NOT in tasks
            self.assertIn("suite1:test_both_match_hw", tests_matching_hw)
            task_names = [t.test_name for t in tasks]
            self.assertNotIn("test_both_match_hw", task_names)
            self.assertIn("test_source_differs_hw", task_names)
            self.assertIn("test_both_differ_hw", task_names)
            self.assertEqual(len(tasks), 2)


if __name__ == "__main__":
    unittest.main()
