import json
import unittest

from surface_map.analyze import Builder, FIELDS, analyze


def omissions(report):
    return {(item["source"], item["category"]): item
            for item in report.get("evidence_limits", {}).get("omissions", [])}


class EvidenceLimitsTests(unittest.TestCase):
    def test_html_retains_routes_and_discloses_lost_source_locations(self):
        text = "\n".join('<a href="/items">Items</a>' for _ in range(35))
        report = analyze([{"kind": "html", "text": text}], javascript_engine="basic")
        self.assertEqual(len(report["routes"]), 1)
        self.assertEqual(len(report["routes"][0]["evidence"]), 32)
        self.assertEqual(omissions(report)[("source_1", "route_evidence")], {
            "source": "source_1", "category": "route_evidence", "limit": 32,
            "omitted_occurrences": 3})
        self.assertTrue(report["evidence_limits"]["truncated"])
        self.assertIn("not distinct values", report["evidence_limits"]["scope"])
        self.assertTrue(any("retention limit" in row["reason"] for row in report["warnings"]))

    def test_retained_duplicates_do_not_count_as_lost_evidence(self):
        builder = Builder("basic")
        for index in range(32):
            builder.add("/items", "GET", "link", "source_1", f"line {index + 1}")
        for _ in range(20):
            builder.add("/items", "GET", "link", "source_1", "line 1")
        self.assertNotIn("evidence_limits", builder.finish())

    def test_discarded_duplicates_are_counted_as_occurrences_without_unbounded_memory(self):
        builder = Builder("basic")
        for index in range(32):
            builder.add("/items", "GET", "link", "source_1", f"line {index + 1}")
        for _ in range(10):
            builder.add("/items", "GET", "link", "source_1", "line 99")
        report = builder.finish()
        self.assertEqual(omissions(report)[("source_1", "route_evidence")]["omitted_occurrences"], 10)
        self.assertEqual(len(report["warnings"]), 1)

    def test_route_status_and_shape_limits_are_reported_separately(self):
        builder = Builder("basic")
        for index in range(65):
            builder.add("/items", "POST", "har", "source_1", "entry 1", status=200 + index,
                        request={"type": "object", "properties": {"items": {"minItems": index}}},
                        response={"type": "array", "minItems": index})
        report = builder.finish()
        route = report["routes"][0]
        for field in ("statuses", "request_shapes", "response_shapes"):
            self.assertEqual(len(route[field]), 64)
            self.assertEqual(omissions(report)[("source_1", "route_" + field)]["omitted_occurrences"], 1)
        self.assertEqual(len(report["warnings"]), 1)

    def test_query_names_are_bounded_after_merging_distinct_captures(self):
        builder = Builder("basic")
        names = sorted(FIELDS)[:65]
        for name in names:
            builder.add("/items?" + name + "=discarded", "GET", "har", "source_1", "entry 1")
            builder.unresolved_operation("/orders?" + name + "=discarded", "source_2", "line 1")
        report = builder.finish()
        self.assertEqual(len(report["routes"][0]["query_fields"]), 64)
        self.assertEqual(len(report["unresolved_operations"][0]["query_fields"]), 64)
        self.assertEqual(omissions(report)[("source_1", "route_query_fields")]["omitted_occurrences"], 1)
        self.assertEqual(omissions(report)[("source_2", "unresolved_query_fields")]["omitted_occurrences"], 1)

    def test_unresolved_targets_retain_their_own_evidence_limit(self):
        builder = Builder("basic")
        for index in range(33):
            builder.unresolved_operation("/items", "source_1", f"line {index + 1}")
        builder.unresolved_operation("/items", "source_1", "line 1")
        report = builder.finish()
        self.assertEqual(len(report["unresolved_operations"][0]["evidence"]), 32)
        self.assertEqual(omissions(report)[("source_1", "unresolved_evidence")]["omitted_occurrences"], 1)

    def test_asset_evidence_deduplicates_before_counting_omissions(self):
        builder = Builder("basic")
        for index in range(17):
            builder.asset("/assets/app.js", "script", "source_1", f"line {index + 1}")
        builder.asset("/assets/app.js", "script", "source_1", "line 1")
        report = builder.finish()
        self.assertEqual(len(report["assets"][0]["evidence"]), 16)
        self.assertEqual(omissions(report)[("source_1", "asset_evidence")]["omitted_occurrences"], 1)

    def test_form_field_limit_never_copies_discarded_names(self):
        canary = "synthetic_private_field_never_emit"
        fields = "".join(f'<input name="field_{index}">' for index in range(32))
        text = '<form method="post" action="/orders">' + fields + f'<input name="{canary}"><input name="field_0"></form>'
        report = analyze([{"kind": "html", "text": text}], javascript_engine="basic")
        self.assertEqual(len(report["routes"][0]["request_shapes"][0]["properties"]), 32)
        self.assertEqual(omissions(report)[("source_1", "form_fields")]["omitted_occurrences"], 1)
        self.assertNotIn(canary, json.dumps(report))

    def test_warning_capacity_cannot_hide_the_fact_that_evidence_was_lost(self):
        builder = Builder("basic")
        for index in range(32):
            builder.warn("source_1", f"Synthetic warning {index}")
        builder.warn("source_1", "Synthetic warning 0")
        for index in range(33):
            builder.add("/items", "GET", "link", "source_1", f"line {index + 1}")
        report = builder.finish()
        self.assertEqual(len(report["warnings"]), 32)
        self.assertEqual(omissions(report)[("source_1", "warnings")]["omitted_occurrences"], 1)
        self.assertEqual(omissions(report)[("source_1", "route_evidence")]["omitted_occurrences"], 1)

    def test_uncapped_report_does_not_claim_complete_coverage(self):
        report = analyze([{"kind": "html", "text": '<a href="/items">Items</a>'}], javascript_engine="basic")
        self.assertNotIn("evidence_limits", report)
        self.assertIn("Evidence is incomplete", report["privacy"]["scope"])


if __name__ == "__main__":
    unittest.main()
