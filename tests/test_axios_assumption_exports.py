from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from surface_map.analyze import analyze
from surface_map.export import compare, contract, openapi, openapi_routes
from surface_map.rebuild import plan_rebuild
from surface_map.review import review


ASSUMPTION = "unverified_global_axios"
CAPTURE = [{"kind": "javascript", "text": 'fetch("/api/orders");'}]


def report(script='fetch("/api/orders");'):
    return analyze([{"kind": "javascript", "text": script}], javascript_engine="basic")


def assume(row):
    row["assumptions"] = [ASSUMPTION]
    for evidence in row["evidence"]:
        evidence["assumptions"] = [ASSUMPTION]
    return row


class AxiosAssumptionExportTests(unittest.TestCase):
    def test_contract_and_openapi_keep_route_and_evidence_assumptions(self):
        value = report()
        assume(value["routes"][0])
        original = deepcopy(value)
        compact = contract(value)["routes"][0]
        self.assertEqual(compact["assumptions"], [ASSUMPTION])
        self.assertEqual(compact["evidence"][0]["assumptions"], [ASSUMPTION])
        spec = openapi(value)
        operation = spec["paths"]["/api/orders"]["get"]
        self.assertEqual(operation["x-surface-map-assumptions"], [ASSUMPTION])
        self.assertEqual(operation["x-surface-map-evidence"][0]["assumptions"], [ASSUMPTION])
        self.assertIn("library identity and prior configuration were not verified", operation["description"])
        self.assertEqual(spec["x-surface-map-assumptions"], [ASSUMPTION])
        self.assertIn("library identity", spec["info"]["description"])
        self.assertEqual(value, original)

    def test_openapi_unions_assumptions_when_redacted_paths_merge_in_either_order(self):
        value = report('fetch("/api/orders/17");fetch("/api/orders/private-canary");')
        self.assertEqual(len(value["routes"]), 2)
        assume(value["routes"][1])
        original = deepcopy(value)
        for rows in (value["routes"], list(reversed(value["routes"]))):
            with self.subTest(first_path=rows[0]["path"]):
                merged = openapi_routes(rows)
                self.assertEqual(len(merged), 1)
                self.assertEqual(merged[0]["assumptions"], [ASSUMPTION])
                self.assertEqual(merged[0]["path"], "/api/orders/{segment_3}")
                self.assertEqual(openapi({**value, "routes": rows})["paths"]["/api/orders/{segment_3}"]["get"]
                                 ["x-surface-map-assumptions"], [ASSUMPTION])
        self.assertEqual(value, original)
        self.assertNotIn("private-canary", json.dumps(openapi(value)))

    def test_unresolved_target_assumption_survives_without_inventing_get(self):
        value = report('fetch("/api/orders", options);')
        assume(value["unresolved_operations"][0])
        self.assertEqual(value["routes"], [])
        self.assertEqual(contract(value)["unresolved_operations"][0]["assumptions"], [ASSUMPTION])
        spec = openapi(value)
        self.assertEqual(spec["paths"], {})
        self.assertEqual(spec["x-surface-map-unresolved-operations"][0]["assumptions"], [ASSUMPTION])
        self.assertEqual(spec["x-surface-map-assumptions"], [ASSUMPTION])
        self.assertIn("library identity", spec["info"]["description"])

    def test_compare_detects_known_assumption_gain_and_loss(self):
        plain = report()
        assumed = deepcopy(plain)
        assume(assumed["routes"][0])
        for before, after, old, new in ((plain, assumed, [], [ASSUMPTION]),
                                       (assumed, plain, [ASSUMPTION], [])):
            with self.subTest(old=old):
                difference = compare(before, after)
                self.assertFalse(difference["added"] or difference["removed"])
                change = difference["changed"][0]
                self.assertEqual(change["changed_fields"], ["assumptions"])
                self.assertEqual(change["before_assumptions"], old)
                self.assertEqual(change["assumptions"], new)

    def test_compare_detects_unresolved_assumption_gain_and_loss(self):
        plain = report('fetch("/api/orders", options);')
        assumed = deepcopy(plain)
        assume(assumed["unresolved_operations"][0])
        for before, after, old, new in ((plain, assumed, [], [ASSUMPTION]),
                                       (assumed, plain, [ASSUMPTION], [])):
            with self.subTest(old=old):
                change = compare(before, after)["unresolved_changed"][0]
                self.assertIsNone(change["method"])
                self.assertEqual(change["changed_fields"], ["assumptions"])
                self.assertEqual(change["before_assumptions"], old)
                self.assertEqual(change["assumptions"], new)

    def test_added_and_absent_rows_keep_aggregate_assumptions(self):
        empty = report("const value=1;")
        for script, added_key, removed_key in (('fetch("/api/orders");', "added", "removed"),
                                                ('fetch("/api/orders", options);', "unresolved_added", "unresolved_removed")):
            value = report(script)
            rows = value["routes"] or value["unresolved_operations"]
            assume(rows[0])
            for difference, key in ((compare(empty, value), added_key), (compare(value, empty), removed_key)):
                self.assertEqual(difference[key][0]["assumptions"], [ASSUMPTION])

    def test_unchanged_assumptions_do_not_create_changes(self):
        value = report()
        assume(value["routes"][0])
        self.assertEqual(compare(value, deepcopy(value))["changed"], [])
        unresolved = report('fetch("/api/orders", options);')
        assume(unresolved["unresolved_operations"][0])
        self.assertEqual(compare(unresolved, deepcopy(unresolved))["unresolved_changed"], [])

    def test_other_changes_still_keep_before_and_after_assumptions(self):
        before, after = report(), report('fetch("/api/orders?page=1");')
        for value in (before, after):
            assume(value["routes"][0])
        change = compare(before, after)["changed"][0]
        self.assertEqual(change["changed_fields"], ["query_fields"])
        self.assertEqual(change["before_assumptions"], [ASSUMPTION])
        self.assertEqual(change["assumptions"], [ASSUMPTION])

    def test_review_and_rebuild_preserve_aggregate_when_evidence_preview_drops_assumption(self):
        value = report()
        route = value["routes"][0]
        route["assumptions"] = [ASSUMPTION]
        route["evidence"] = [{"source": "source_1", "location": f"line {n}", "kind": "javascript"}
                             for n in range(1, 13)]
        route["evidence"][-1]["assumptions"] = [ASSUMPTION]
        original = deepcopy(value)
        compact = review(value)["routes_to_review"][0]
        self.assertEqual(len(compact["evidence"]), 3)
        self.assertEqual(compact["assumptions"], [ASSUMPTION])
        self.assertFalse(any(row.get("assumptions") for row in compact["evidence"]))
        with patch("surface_map.rebuild.analyze", return_value=value):
            operation = plan_rebuild(CAPTURE, javascript_engine="basic")["operations"][0]
        self.assertEqual(len(operation["evidence"]), 8)
        self.assertEqual(operation["assumptions"], [ASSUMPTION])
        self.assertFalse(any(row.get("assumptions") for row in operation["evidence"]))
        self.assertTrue(any("Axios identity and prior configuration" in text for text in operation["unknowns"]))
        self.assertEqual(value, original)

    def test_unresolved_review_and_rebuild_rows_retain_assumptions(self):
        value = report('fetch("/api/orders", options);')
        assume(value["unresolved_operations"][0])
        self.assertEqual(review(value)["routes_to_review"][0]["assumptions"], [ASSUMPTION])
        with patch("surface_map.rebuild.analyze", return_value=value):
            plan = plan_rebuild(CAPTURE, javascript_engine="basic")
        self.assertEqual(plan["unresolved"][0]["assumptions"], [ASSUMPTION])

    def test_ordinary_client_exports_do_not_manufacture_assumption_keys(self):
        value = report()
        for exported in (contract(value), openapi(value), review(value), compare(value, report('fetch("/api/orders?page=1");'))):
            self.assertNotIn("assumptions", json.dumps(exported))
        with patch("surface_map.rebuild.analyze", return_value=value):
            self.assertNotIn("assumptions", json.dumps(plan_rebuild(CAPTURE, javascript_engine="basic")))


if __name__ == "__main__":
    unittest.main()
