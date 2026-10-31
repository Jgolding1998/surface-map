import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from surface_map.analyze import ArtifactError, analyze
from surface_map.rebuild import MAX_OPERATIONS, plan_rebuild
from surface_map.project import inspect_project


class RebuildTests(unittest.TestCase):
    def test_reviewed_mime_noise_and_private_filename_variants(self):
        capture = [{"kind": "html", "text": ""}, {"kind": "javascript", "text":
            'const a="application/json", b="text/html", c="image/png", d="./api/orders";'}]
        plan = plan_rebuild(capture, document_url="https://demo.invalid/", javascript_engine="basic")
        refs = plan["candidate_references"]
        self.assertEqual([row["path"] for row in refs["candidates"]], ["/api/orders"])
        self.assertEqual(refs["sources_not_scanned"], 1)
        for name in ("my_secrets.py", "app.credentials.js", "prod-token.ts"):
            with self.subTest(name=name), self.assertRaises(ArtifactError):
                inspect_project([{"logical_path": name, "kind": "python" if name.endswith("py") else "javascript" if name.endswith("js") else "typescript", "content": ""}])

    def test_links_candidates_and_unknown_methods_never_become_operations(self):
        capture = [{"kind": "html", "text": '<a href="/dashboard">Home</a><form action="/api/orders" method="post"></form>'},
                   {"kind": "javascript", "text": 'const unused="/api/inventory"; fetch("/api/export", options);'}]
        plan = plan_rebuild(capture, javascript_engine="basic")
        self.assertEqual([(r["method"], r["path"]) for r in plan["operations"]], [("POST", "/api/orders")])
        self.assertEqual(plan["pages"][0]["path"], "/dashboard")
        self.assertTrue(any(r["path"] == "/api/inventory" for r in plan["candidate_references"]["candidates"]))
        self.assertEqual(plan["unresolved"][0]["path"], "/api/export")
        self.assertTrue(plan["unknowns"])

    def test_canary_values_and_capture_code_do_not_enter_plan(self):
        canary = "PERSONAL_CANARY_52847"
        capture = [{"kind": "javascript", "text": f'fetch("https://{canary}.invalid/api/orders?token={canary}"); const secret="{canary}";'}]
        plan = plan_rebuild(capture, javascript_engine="basic")
        self.assertNotIn(canary, json.dumps(plan))
        self.assertEqual(plan["operations"][0]["query_fields"], ["token"])

    def test_observation_is_preserved_but_mock_is_not_a_successful_backend(self):
        capture = [{"kind": "har", "text": json.dumps({"log": {"entries": [{
            "request": {"method": "GET", "url": "https://demo.invalid/api/orders"},
            "response": {"status": 200, "content": {"mimeType": "application/json", "text": '{"id":22}'}}
        }]}})}]
        operation = plan_rebuild(capture, javascript_engine="basic")["operations"][0]
        self.assertEqual(operation["confidence"], "observed")
        self.assertEqual(operation["statuses"], [200])
        self.assertEqual(operation["stub"], "not_implemented")
        self.assertTrue(operation["response_shapes"])

    def test_cross_origin_only_group_is_excluded_from_mock(self):
        capture = [{"kind": "html", "text": '<form action="https://other.invalid/api/orders" method="post"></form>'}]
        plan = plan_rebuild(capture, document_url="https://demo.invalid/", javascript_engine="basic")
        self.assertEqual(plan["operations"][0]["stub"], "none")
        self.assertEqual(plan["coverage"]["cross_origin_groups_without_stubs"], 1)

    def test_unknown_and_mixed_origin_scope_stays_explicit(self):
        capture = [{"kind": "javascript", "text": 'fetch("/api/orders");'}]
        plan = plan_rebuild(capture, javascript_engine="basic")
        self.assertFalse(plan["coverage"]["origin_classification_available"])
        self.assertIn("unclassified", plan["operations"][0]["unknowns"][0])
        capture.insert(0, {"kind": "html", "text": ""})
        capture[1]["text"] += ' fetch("https://other.invalid/api/orders");'
        plan = plan_rebuild(capture, document_url="https://demo.invalid/", javascript_engine="basic")
        self.assertEqual(plan["operations"][0]["origin_relations"], ["cross_origin", "same_origin"])
        self.assertIn("Confirm origin scope before including this operation.", plan["operations"][0]["unknowns"])

    def test_plan_limits_are_explicit_and_default_analysis_is_unchanged(self):
        capture = [{"kind": "javascript", "text": '\n'.join(f'fetch("/api/orders/{n}/items");' for n in range(60))}]
        before = analyze(capture, javascript_engine="basic")
        plan_rebuild(capture, javascript_engine="basic")
        self.assertEqual(analyze(capture, javascript_engine="basic"), before)
        routes = []
        for n in range(MAX_OPERATIONS + 3):
            routes.append({"id": f"route_{n+1}", "method": "GET", "path": "/api/orders", "confidence": "inferred",
                           "evidence": [], "statuses": [], "query_fields": [], "request_shapes": [], "response_shapes": []})
        with patch("surface_map.rebuild.analyze", return_value={"routes": routes, "privacy": {}, "warnings": []}):
            plan = plan_rebuild(capture, javascript_engine="basic")
        self.assertEqual(len(plan["operations"]), MAX_OPERATIONS)
        self.assertEqual(plan["coverage"]["omitted_operations"], 3)

    def test_expired_invalid_deadlines_and_bad_inputs_are_rejected(self):
        capture = [{"kind": "html", "text": ""}]
        for deadline in (time.monotonic() - 1, True, float("nan"), float("inf"), "later"):
            with self.subTest(deadline=str(deadline)), self.assertRaises(ArtifactError):
                plan_rebuild(capture, deadline=deadline)
        with self.assertRaises(ArtifactError):
            plan_rebuild([])

    def test_claude_synthetic_demo(self):
        root = Path(__file__).resolve().parents[1] / "examples/rebuild-demo"
        capture = [{"kind": kind, "text": (root / filename).read_text(encoding="utf-8")}
                   for filename, kind in (("page.html", "html"), ("app.js", "javascript"), ("session.har", "har"))]
        plan = plan_rebuild(capture, javascript_engine="basic")
        operations = {(r["method"], r["path"]): r for r in plan["operations"]}
        self.assertEqual(operations[("GET", "/api/projects")]["confidence"], "observed")
        self.assertEqual(operations[("POST", "/api/projects")]["statuses"], [201])
        self.assertEqual(operations[("GET", "/api/projects/{integer_3}")]["statuses"], [404])
        self.assertEqual(operations[("GET", "/search")]["confidence"], "declared")
        self.assertEqual({r["path"] for r in plan["pages"]}, {"/dashboard", "/settings"})
        self.assertTrue(any(r["path"] == "/api/export" for r in plan["unresolved"]))


if __name__ == "__main__":
    unittest.main()
