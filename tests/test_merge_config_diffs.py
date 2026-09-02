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

from merge_config_diffs import merge_config_diff_summaries


class TestMergeConfigDiffs(unittest.TestCase):
    def test_merge_config_diff_summaries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            comp_dir = os.path.join(tmpdir, "config-comparisons")
            slug = "runA--vs--runB"
            slug_dir = os.path.join(comp_dir, slug)
            os.makedirs(slug_dir)

            # Write plan file
            plan_file = os.path.join(tmpdir, "plan.json")
            with open(plan_file, "w") as f:
                json.dump(
                    {
                        "source_run": "runA",
                        "target_run": "runB",
                        "tests_matching_hw": ["suite1:hw_match"],
                        "tests_without_match": ["suite1:extra_in_A"],
                    },
                    f,
                )

            # Write two shard files
            shard0 = os.path.join(slug_dir, ".shard_0_summary.json")
            with open(shard0, "w") as f:
                json.dump(
                    {
                        "shard_index": 0,
                        "tests_with_differences": {"suite1:test1": 10.0},
                        "tests_matching_target": ["suite1:match_target_1"],
                    },
                    f,
                )

            shard1 = os.path.join(slug_dir, ".shard_1_summary.json")
            with open(shard1, "w") as f:
                json.dump(
                    {
                        "shard_index": 1,
                        "tests_with_differences": {"suite2:test2": 25.5},
                        "tests_matching_target": ["suite2:match_target_2"],
                    },
                    f,
                )

            summary_file = merge_config_diff_summaries(comp_dir, slug, plan_file)
            self.assertTrue(os.path.isfile(summary_file))

            # Shard files should have been removed
            self.assertFalse(os.path.exists(shard0))
            self.assertFalse(os.path.exists(shard1))

            with open(summary_file) as f:
                summary = json.load(f)

            self.assertEqual(summary["slug"], slug)
            self.assertEqual(summary["source_run"], "runA")
            self.assertEqual(summary["target_run"], "runB")
            self.assertEqual(summary["diff_count"], 2)
            self.assertEqual(
                summary["tests_with_differences"],
                {"suite1:test1": 10.0, "suite2:test2": 25.5},
            )
            self.assertEqual(
                summary["tests_matching_target"],
                ["suite1:match_target_1", "suite2:match_target_2"],
            )
            self.assertEqual(summary["tests_matching_hw"], ["suite1:hw_match"])
            self.assertEqual(summary["tests_without_match"], ["suite1:extra_in_A"])


if __name__ == "__main__":
    unittest.main()
