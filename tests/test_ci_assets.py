from copy import deepcopy
import json
import unittest

from surface_map.analyze import analyze
from surface_map.ci_assets import FIXED_NOTE, compare_assets


CANARY = "PRIVATE_ASSET_CANARY_7961"


def html(text):
    return analyze([{"kind": "html", "text": text}], javascript_engine="basic")


class CIAssetComparisonTests(unittest.TestCase):
    def test_new_literal_script_keeps_generic_source_evidence(self):
        result = compare_assets(html("<p>Before</p>"),
                                html('\n<script src="/assets/app.js"></script>'))
        self.assertEqual(result["added"], [{"kind": "script", "path": "/assets/app.js",
                                          "evidence": [{"source": "source_1", "location": "line 2"}]}])
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["unchanged_count"], 0)

    def test_absent_literal_script_keeps_before_evidence_without_deletion_claim(self):
        result = compare_assets(html('<script src="/assets/main.js"></script>'),
                                html("<p>After</p>"))
        self.assertEqual(result["removed"], [{"kind": "script", "path": "/assets/main.js",
                                            "evidence": [{"source": "source_1", "location": "line 1"}]}])
        self.assertEqual(result["added"], [])
        self.assertIn("not proven deleted", result["scope"])

    def test_reordering_and_line_moves_are_unchanged(self):
        before = html('<script src="/assets/app.js"></script>\n'
                      '<link rel="stylesheet" href="/assets/app.css">')
        after = html('\n\n<link rel="stylesheet" href="/assets/app.css">\n'
                     '<script src="/assets/app.js"></script>')
        result = compare_assets(before, after)
        self.assertEqual((result["added"], result["removed"], result["unchanged_count"]),
                         ([], [], 2))

    def test_stylesheet_link_and_embedded_document_kinds_are_retained(self):
        after = html('<link rel="stylesheet" href="/assets/main.css">'
                     '<link rel="preload" href="/assets/app.js">'
                     '<iframe src="/dashboard"></iframe>')
        result = compare_assets(html("<p>Before</p>"), after)
        self.assertEqual([row["kind"] for row in result["added"]],
                         ["embedded-document", "link", "stylesheet"])
        self.assertEqual([row["path"] for row in result["added"]],
                         ["/dashboard", "/assets/app.js", "/assets/main.css"])

    def test_same_path_domain_swap_is_invisible_and_explicitly_scoped(self):
        before = html('<script src="https://first.invalid/assets/app.js"></script>')
        after = html('<script src="https://second.invalid/assets/app.js"></script>')
        result = compare_assets(before, after)
        self.assertEqual((result["added"], result["removed"], result["unchanged_count"]),
                         ([], [], 1))
        self.assertEqual(result["coverage"], "normalized_asset_references_only")
        self.assertIn("domain-only swaps", result["scope"])
        self.assertNotIn("first.invalid", json.dumps(result))
        self.assertNotIn("second.invalid", json.dumps(result))

    def test_aliased_filename_rename_is_not_reported_as_observed_change(self):
        before = html('<script src="/assets/private-original-name.js"></script>')
        after = html('<script src="/assets/another-private-name.js"></script>')
        result = compare_assets(before, after)
        self.assertEqual((result["added"], result["removed"], result["unchanged_count"]),
                         ([], [], 1))
        self.assertIn("aliased path renames can be invisible", result["scope"])

    def test_secret_domains_query_values_and_source_filenames_are_not_returned(self):
        after = html('<script src="https://' + CANARY + '.invalid/assets/' + CANARY
                     + '.js?token=' + CANARY + '"></script><!-- ' + CANARY + '.html -->')
        result = compare_assets(html("<p>Before</p>"), after)
        self.assertEqual(len(result["added"]), 1)
        serialized = json.dumps(result)
        self.assertNotIn(CANARY, serialized)
        self.assertNotIn("https://", serialized)
        self.assertNotIn("token=", serialized)
        self.assertEqual(result["added"][0]["evidence"],
                         [{"source": "source_1", "location": "line 1"}])

    def test_inline_scripts_and_dynamic_loaders_do_not_become_asset_evidence(self):
        before = html('<script>import("/assets/app.js");</script>')
        after = html('<script>const s=document.createElement("script");'
                     's.src="/assets/main.js";document.head.appendChild(s);</script>')
        result = compare_assets(before, after)
        self.assertEqual((result["added"], result["removed"], result["unchanged_count"]),
                         ([], [], 0))
        self.assertIn("Inline scripts and dynamic loaders", result["scope"])

    def test_same_path_kind_change_is_an_added_and_absent_reference(self):
        before = html('<link rel="preload" href="/assets/app.js">')
        after = html('<script src="/assets/app.js"></script>')
        result = compare_assets(before, after)
        self.assertEqual(result["added"][0]["kind"], "script")
        self.assertEqual(result["removed"][0]["kind"], "link")
        self.assertEqual(result["unchanged_count"], 0)

    def test_projection_drops_unretained_metadata_and_does_not_mutate_inputs(self):
        before = html("<p>Before</p>")
        after = html('<script src="/assets/app.js"></script>')
        after["assets"][0]["original"] = CANARY
        after["assets"][0]["evidence"][0]["filename"] = CANARY
        original = deepcopy(after)
        result = compare_assets(before, after)
        self.assertEqual(after, original)
        self.assertNotIn(CANARY, json.dumps(result))
        result["added"][0]["evidence"][0]["location"] = "line 999"
        self.assertEqual(after, original)

    def test_out_of_boundary_inventory_and_non_generic_evidence_are_rejected(self):
        report = html('<script src="/assets/app.js"></script>')
        too_many = deepcopy(report)
        too_many["assets"] *= 201
        invalid_evidence = deepcopy(report)
        invalid_evidence["assets"][0]["evidence"][0]["source"] = CANARY + ".html"
        for invalid in (too_many, invalid_evidence):
            with self.subTest(case=len(invalid["assets"])):
                with self.assertRaisesRegex(ValueError, "Invalid normalized asset report"):
                    compare_assets(report, invalid)

    def test_empty_inventory_still_reports_coverage_limits(self):
        result = compare_assets(html("<p>Before</p>"), html("<p>After</p>"))
        self.assertEqual(result, {"added": [], "removed": [], "unchanged_count": 0,
                                  "coverage": "normalized_asset_references_only", "scope": FIXED_NOTE})


if __name__ == "__main__":
    unittest.main()
