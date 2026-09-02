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

try:
    from generate_results_site import (
        ConfigComparisonScanner,
        PagesWriter,
        _xemu_version_sort_filter,
    )
    from jinja2 import Environment, FileSystemLoader

    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


@unittest.skipUnless(
    HAS_DEPS, "Site generator dependencies (requests, jinja2, frozendict) not installed"
)
class TestGenerateResultsSiteConfigComparisons(unittest.TestCase):
    def _setup_config_dir(self, tmpdir: str) -> tuple[str, str, str]:
        config_dir = os.path.join(tmpdir, "config-comparisons")
        output_dir = os.path.join(tmpdir, "output")
        slug = "xemu-A__Linux--vs--xemu-B__Linux"
        slug_dir = os.path.join(config_dir, slug)
        os.makedirs(slug_dir)

        summary_data = {
            "slug": slug,
            "source_run": "xemu-A/Linux/gl",
            "target_run": "xemu-B/Linux/gl",
            "timestamp": "2026-09-02T12:00:00Z",
            "diff_count": 1,
            "tests_with_differences": {
                "suite1:diff_test": 15.0,
            },
            "tests_matching_target": [
                "suite1:identical_test",
            ],
            "tests_matching_hw": [],
            "tests_without_match": [],
        }
        with open(os.path.join(slug_dir, "summary.json"), "w") as f:
            json.dump(summary_data, f)

        return config_dir, output_dir, slug

    def test_config_comparison_scanner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir, output_dir, slug = self._setup_config_dir(tmpdir)
            scanner = ConfigComparisonScanner(
                config_comparisons_dir=config_dir,
                output_dir=output_dir,
                base_url="https://example.com",
                hw_golden_base_url="https://example.com/hw",
                hw_comparison_dir=None,
                source_image_index={},
                results={},
            )
            config_comps = scanner.process()
            self.assertEqual(len(config_comps), 1)
            self.assertEqual(config_comps[0].slug, slug)
            self.assertEqual(config_comps[0].diff_count, 1)
            self.assertEqual(config_comps[0].matching_count, 1)
            self.assertIn("suite1", config_comps[0].results_by_suite)
            items = config_comps[0].results_by_suite["suite1"]
            self.assertEqual(len(items), 2)
            item_map = {it.test_name: it for it in items}
            self.assertTrue(item_map["identical_test"].is_identical)
            self.assertFalse(item_map["diff_test"].is_identical)
            self.assertEqual(item_map["diff_test"].diff_distance, 15.0)

    def test_config_comparison_site_generation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir, output_dir, slug = self._setup_config_dir(tmpdir)
            templates_dir = os.path.abspath(
                os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    ".github",
                    "scripts",
                    "site-templates",
                )
            )
            jinja_env = Environment(loader=FileSystemLoader(templates_dir))
            jinja_env.filters["version_sort"] = _xemu_version_sort_filter
            jinja_env.globals["sidenav_width"] = 48
            jinja_env.globals["sidenav_icon_width"] = 32

            scanner = ConfigComparisonScanner(
                config_comparisons_dir=config_dir,
                output_dir=output_dir,
                base_url="https://example.com",
                hw_golden_base_url="https://example.com/hw",
                hw_comparison_dir=None,
                source_image_index={},
                results={},
            )
            config_comps = scanner.process()

            writer = PagesWriter(
                results={},
                env=jinja_env,
                output_dir=output_dir,
                result_images_base_url="https://example.com",
                hw_golden_images_base_url="https://example.com/hw",
                test_source_base_url="",
                hw_golden_browser_base_url="",
                config_comparisons=config_comps,
            )
            writer.write()

            # Verify files were generated
            comp_index_file = os.path.join(output_dir, "comparisons", "index.html")
            comp_detail_file = os.path.join(
                output_dir, "comparisons", slug, "index.html"
            )
            root_index_file = os.path.join(output_dir, "index.html")

            self.assertTrue(os.path.isfile(comp_index_file))
            self.assertTrue(os.path.isfile(comp_detail_file))
            self.assertTrue(os.path.isfile(root_index_file))

            # Verify contents of detail page
            with open(comp_detail_file) as f:
                content = f.read()
                # Check topnav
                self.assertIn("topnav", content)
                self.assertIn("Configuration Comparisons", content)
                # Check hide identical checkbox
                self.assertIn("hide-identical-toggle", content)
                self.assertIn('data-identical="true"', content)
                # Check image-group for 3-way cycling
                self.assertIn("image-group", content)
                self.assertIn('data-state="source"', content)
                self.assertIn('data-state="golden-target"', content)
                self.assertIn('data-state="golden-hw"', content)


if __name__ == "__main__":
    unittest.main()
