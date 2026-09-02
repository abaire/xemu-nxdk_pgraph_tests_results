#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    ),
)

from prune_data import (
    SemVer,
    execute_pruning,
    is_version_in_range,
    normalize_target,
    parse_version_range,
    plan_pruning,
)


class TestPruneData(unittest.TestCase):
    def test_semver_parse_and_compare(self):
        v1 = SemVer.parse("v0.6.30")
        v2 = SemVer.parse("0.8.100")
        v3 = SemVer.parse("xemu-0.8.136-master-abcdef")

        self.assertIsNotNone(v1)
        self.assertIsNotNone(v2)
        self.assertIsNotNone(v3)

        assert v1 is not None
        assert v2 is not None
        assert v3 is not None

        self.assertEqual(v1, SemVer(0, 6, 30))
        self.assertEqual(v2, SemVer(0, 8, 100))
        self.assertEqual(v3, SemVer(0, 8, 136))

        self.assertTrue(v1 < v2)
        self.assertTrue(v2 < v3)

    def test_parse_version_range(self):
        # min - max
        r1 = parse_version_range("v0.6.30 - v0.8.100")
        self.assertIsNotNone(r1)
        assert r1 is not None
        self.assertEqual(r1[0], SemVer(0, 6, 30))
        self.assertEqual(r1[1], SemVer(0, 8, 100))

        # inverted max - min should normalize to min <= max
        r2 = parse_version_range("v0.8.100 - v0.6.30")
        self.assertIsNotNone(r2)
        assert r2 is not None
        self.assertEqual(r2[0], SemVer(0, 6, 30))
        self.assertEqual(r2[1], SemVer(0, 8, 100))

        # dot notation
        r3 = parse_version_range("0.7.0..0.8.50")
        self.assertIsNotNone(r3)
        assert r3 is not None
        self.assertEqual(r3[0], SemVer(0, 7, 0))
        self.assertEqual(r3[1], SemVer(0, 8, 50))

    def test_is_version_in_range(self):
        min_v = SemVer(0, 7, 0)
        max_v = SemVer(0, 8, 50)

        self.assertTrue(is_version_in_range("xemu-0.7.98-master-123", min_v, max_v))
        self.assertTrue(is_version_in_range("xemu-0.7.0", min_v, max_v))
        self.assertTrue(is_version_in_range("xemu-0.8.50-rc1", min_v, max_v))
        self.assertFalse(is_version_in_range("xemu-0.6.99", min_v, max_v))
        self.assertFalse(is_version_in_range("xemu-0.8.51", min_v, max_v))

    def test_normalize_target(self):
        self.assertEqual(
            normalize_target(
                "https://example.com/results/xemu-0.8.101-master/Linux/index.html"
            ),
            "xemu-0.8.101-master/Linux",
        )
        self.assertEqual(
            normalize_target("results/xemu-0.8.101-master"),
            "xemu-0.8.101-master",
        )
        self.assertEqual(
            normalize_target("config-comparisons/slug-a--vs--slug-b"),
            "slug-a--vs--slug-b",
        )

    def test_plan_pruning_modes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res_dir = os.path.join(tmpdir, "results")
            comp_dir = os.path.join(tmpdir, "compare-results")
            config_dir = os.path.join(tmpdir, "config-comparisons")

            v1 = "xemu-0.7.98-master-aaa"
            v2 = "xemu-0.8.136-master-bbb"

            os.makedirs(os.path.join(res_dir, v1))
            os.makedirs(os.path.join(res_dir, v2))

            os.makedirs(os.path.join(comp_dir, v1))
            os.makedirs(os.path.join(comp_dir, v2))

            slug = f"{v1}__Linux--vs--{v2}__Linux"
            os.makedirs(os.path.join(config_dir, slug))

            remote_branches = [f"archive/{v1}", f"archive/{v2}"]

            # Test 1: Mode 'all' with range covering v1 only
            plan_all = plan_pruning(
                results_dir=res_dir,
                compare_results_dir=comp_dir,
                config_comparisons_dir=config_dir,
                target_mode="all",
                targets=[],
                version_range="v0.7.0 - v0.8.0",
                remote_branches=remote_branches,
            )
            self.assertEqual(plan_all.primary_results, [os.path.join(res_dir, v1)])
            self.assertEqual(plan_all.hw_comparisons, [os.path.join(comp_dir, v1)])
            self.assertEqual(
                plan_all.config_comparisons, [os.path.join(config_dir, slug)]
            )
            self.assertEqual(plan_all.archive_branches, [f"archive/{v1}"])

            # Test 2: Mode 'comparisons_only'
            plan_comp = plan_pruning(
                results_dir=res_dir,
                compare_results_dir=comp_dir,
                config_comparisons_dir=config_dir,
                target_mode="comparisons_only",
                targets=[v1],
                remote_branches=remote_branches,
            )
            self.assertEqual(plan_comp.primary_results, [])
            self.assertEqual(plan_comp.hw_comparisons, [os.path.join(comp_dir, v1)])
            self.assertEqual(
                plan_comp.config_comparisons, [os.path.join(config_dir, slug)]
            )
            self.assertEqual(plan_comp.archive_branches, [])

            # Test 3: Mode 'config_comparisons_only'
            plan_cfg = plan_pruning(
                results_dir=res_dir,
                compare_results_dir=comp_dir,
                config_comparisons_dir=config_dir,
                target_mode="config_comparisons_only",
                targets=[slug],
                remote_branches=remote_branches,
            )
            self.assertEqual(plan_cfg.primary_results, [])
            self.assertEqual(plan_cfg.hw_comparisons, [])
            self.assertEqual(
                plan_cfg.config_comparisons, [os.path.join(config_dir, slug)]
            )

            # Test 4: Dry-run execution
            self.assertEqual(
                execute_pruning(plan_all, dry_run=True, repo_root=tmpdir), 0
            )
            # Dirs should still exist
            self.assertTrue(os.path.exists(os.path.join(res_dir, v1)))

            # Test 5: Actual deletion
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                self.assertEqual(
                    execute_pruning(plan_all, dry_run=False, repo_root=tmpdir), 0
                )
                self.assertFalse(os.path.exists(os.path.join(res_dir, v1)))
                self.assertFalse(os.path.exists(os.path.join(comp_dir, v1)))
                self.assertFalse(os.path.exists(os.path.join(config_dir, slug)))
                # v2 should still exist
                self.assertTrue(os.path.exists(os.path.join(res_dir, v2)))
                # Check git push was called for archive branch
                mock_run.assert_called_with(
                    ["git", "push", "origin", "--delete", f"archive/{v1}"],
                    cwd=tmpdir,
                    check=True,
                )


if __name__ == "__main__":
    unittest.main()
