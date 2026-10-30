import copy
import json
import unittest

from surface_map.analyze import Builder, analyze
from surface_map.export import compare


class ComparisonEvidenceTests(unittest.TestCase):
    def report(self, *artifacts):
        return analyze(list(artifacts), javascript_engine="basic")

    def har(self):
        return {"kind": "har", "text": json.dumps({"log": {"entries": [{
            "request": {"method": "GET", "url": "https://example.com/api/items"},
            "response": {"status": 200, "content": {}},
        }]}})}

    def unresolved(self, url):
        builder = Builder()
        builder.unresolved_operation(url, "source_1", "line 1")
        return builder.finish()

    def test_declared_evidence_loss_is_visible_even_when_har_confidence_stays_observed(self):
        before = self.report(self.har(), {"kind": "html", "text": '<a href="/api/items">Items</a>'})
        after = self.report(self.har())
        original = copy.deepcopy((before, after))
        change = compare(before, after)["changed"][0]
        self.assertEqual(change["changed_fields"], ["evidence_kinds"])
        self.assertEqual({row["kind"] for row in change["before_evidence"]}, {"har", "link"})
        self.assertEqual({row["kind"] for row in change["evidence"]}, {"har"})
        self.assertEqual((before, after), original)

    def test_line_moves_duplicate_links_and_artifact_reordering_are_not_service_changes(self):
        before = self.report(self.har(), {"kind": "html", "text": '<a href="/api/items">Items</a>'})
        after = self.report({"kind": "html", "text": '\n\n<a href="/api/items">Items</a><a href="/api/items">Again</a>'}, self.har())
        self.assertEqual(compare(before, after)["changed"], [])

    def test_unresolved_method_query_change_is_not_silently_lost(self):
        before = self.unresolved("/api/items?q=hidden")
        after = self.unresolved("/api/items?page=hidden")
        result = compare(before, after)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["unresolved_changed"][0]["changed_fields"], ["query_fields"])
        self.assertIsNone(result["unresolved_changed"][0]["method"])
        self.assertNotIn("hidden", json.dumps(result))

    def test_same_unknown_method_target_has_no_change(self):
        report = self.unresolved("/api/items")
        self.assertEqual(compare(report, report)["unresolved_changed"], [])


if __name__ == "__main__":
    unittest.main()
