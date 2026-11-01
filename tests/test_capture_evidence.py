"""Public MCP/HTTP evidence behavior, with independent synthetic expectations."""
import base64
from copy import deepcopy
import json
import time
import unittest
from unittest.mock import patch

from surface_map.analyze import ArtifactError, analyze
from surface_map.capture_compare import compare_saved
from surface_map.evidence import inspect_evidence, page_report
from surface_map.export import compare, contract, openapi
from surface_map.mcp_server import _execute, tool_specs
from surface_map.review import review


def html(text):
    return {"kind": "html", "text": text}


def har_entry(path="/api/orders", method="POST", body=None, response=None):
    request = {"method": method, "url": "https://private.invalid" + path}
    if body is not None:
        request["postData"] = body
    return {"request": request, "response": {"status": 200, "content": response or
            {"mimeType": "application/json", "text": '{"success":true}'}}}


def har(entries):
    return {"kind": "har", "text": json.dumps({"log": {"entries": entries}})}


OLD = '<form action="/api/orders" method="post"><input name="private_old" type="hidden"></form><script src="https://old.invalid/widget.js"></script>'
NEW = OLD.replace('private_old', 'private_new').replace('old.invalid', 'new.invalid')


class CaptureSignalTests(unittest.TestCase):
    def test_mcp_reports_private_field_and_domain_changes_without_names(self):
        result = _execute("compare_captures", {"before": [html(OLD)], "after": [html(NEW)], "javascript_engine": "basic"})
        self.assertEqual(result["comparison"]["changed"], [])
        self.assertEqual(result["comparison"]["added"], [])
        signals = result["review_signals"]
        self.assertEqual(signals["status"], "available")
        for category in ("form_field", "script", "origin"):
            self.assertEqual(len(signals["comparison"][category]["added"]), 1)
            self.assertEqual(len(signals["comparison"][category]["removed"]), 1)
        serialized = json.dumps(result)
        for secret in ("private_old", "private_new", "old.invalid", "new.invalid"):
            self.assertNotIn(secret, serialized)

    def test_legacy_projection_and_explicit_opt_out(self):
        old, new = [html(OLD)], [html(NEW)]
        result = compare_saved(old, new, javascript_engine="basic", include_review_signals=False)
        self.assertNotIn("review_signals", result)
        self.assertEqual(result["comparison"], compare(analyze(old, javascript_engine="basic"), analyze(new, javascript_engine="basic")))

    def test_http_encoded_uploads_receive_the_same_rich_signals(self):
        encoded = lambda text: {"kind": "html", "text_b64": base64.b64encode(text.encode()).decode()}
        result = compare_saved([encoded(OLD)], [encoded(NEW)], javascript_engine="basic")
        self.assertEqual(result["review_signals"]["status"], "available")
        self.assertEqual(len(result["review_signals"]["comparison"]["form_field"]["added"]), 1)
        self.assertNotIn("private_old", json.dumps(result))

    def test_uninspected_inline_handler_cannot_be_an_available_comparison(self):
        result = compare_saved([html('<button onclick="posthog.capture(\'private_old\')">A</button>')],
                               [html('<button>A</button>')], javascript_engine="basic")
        self.assertEqual(result["review_signals"]["status"], "partial")
        self.assertTrue(result["review_signals"]["coverage"]["warnings"])

    def test_mixed_har_keeps_original_source_numbers(self):
        network = har([har_entry()])
        result = compare_saved([network, html(OLD)], [network, html(NEW)], javascript_engine="basic")
        signals = result["review_signals"]
        self.assertEqual(signals["coverage"]["excluded_sources"][0]["source"], "source_1")
        for category in ("form_field", "script"):
            for row in signals["comparison"][category]["added"]:
                self.assertEqual({entry["source"] for entry in row["evidence"]}, {"source_2"})
        self.assertIn("har_body_analysis", result["before"])

    def test_har_only_and_mismatched_selections_are_not_static_all_clears(self):
        network = har([har_entry()])
        result = compare_saved([network], [network], javascript_engine="basic")
        self.assertEqual(result["review_signals"]["reason"], "har_only_capture")
        result = compare_saved([html(OLD)], [html(NEW), html('<a href="/api/items">A</a>')], javascript_engine="basic")
        self.assertEqual(result["review_signals"]["status"], "not_applicable")
        self.assertEqual(result["review_signals"]["reason"], "unpaired_artifact_kinds")

    def test_per_side_context_and_incomplete_context(self):
        result = compare_saved([html('<form action="orders"></form>')], [html('<form action="orders"></form>')],
                               javascript_engine="basic", before_document_url="https://old.invalid/api/",
                               after_document_url="https://new.invalid/api/")
        self.assertTrue(result["review_signals"]["comparison"]["origin"]["added"])
        result = compare_saved([html('<form action="orders"></form>')], [html('<form action="orders"></form>')],
                               javascript_engine="basic", before_document_url="https://old.invalid/api/")
        self.assertEqual(result["review_signals"]["status"], "unavailable")

    def test_basic_partial_javascript_keeps_independent_html_changes(self):
        old = [html('<form action="/api/orders"></form><script>fetch("/api/items")</script>')]
        new = [html('<form action="/api/payments"></form><script>fetch("/api/users")</script>')]
        result = compare_saved(old, new, javascript_engine="basic")
        self.assertEqual(result["review_signals"]["status"], "partial")
        self.assertTrue(result["review_signals"]["comparison"]["endpoint"]["added"])

    def test_no_network_or_source_execution(self):
        with patch("socket.create_connection", side_effect=AssertionError("network")):
            result = compare_saved([html(OLD)], [html(NEW)], javascript_engine="basic")
            self.assertEqual(result["review_signals"]["status"], "available")

    def test_invalid_signal_flag_is_rejected(self):
        for value in (1, "yes", None, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compare_saved([html(OLD)], [html(NEW)], include_review_signals=value)

    def test_expired_deadline_is_not_a_successful_report(self):
        with self.assertRaises(ArtifactError):
            compare_saved([html(OLD)], [html(NEW)], deadline=time.monotonic() - 1)


class EvidencePageTests(unittest.TestCase):
    def setUp(self):
        self.capture = [har([har_entry(body={"mimeType": "application/x-www-form-urlencoded", "text": "quantity=VALUE_CANARY&quantity=VALUE_CANARY"}),
                             har_entry("/api/items", "GET"), har_entry("/api/users", "GET")])]

    def test_pages_preserve_totals_and_next_offset(self):
        pages = [inspect_evidence(self.capture, javascript_engine="basic", limit=1, offset=index) for index in range(3)]
        self.assertTrue(all(page["counts"]["retained"] == 3 and page["counts"]["matching"] == 3 for page in pages))
        self.assertEqual([page["page"]["next_offset"] for page in pages], [1, 2, None])
        self.assertEqual(len({page["items"][0]["id"] for page in pages}), 3)
        self.assertTrue(all("har_body_analysis" in page and "warnings" in page for page in pages))

    def test_mcp_filter_and_detail_preserve_form_counts_without_values(self):
        result = _execute("inspect_evidence", {"artifacts": self.capture, "javascript_engine": "basic",
            "method": "POST", "confidence": "observed", "source_id": "source_1", "evidence_kind": "har", "detail": True})
        self.assertEqual(result["counts"]["matching"], 1)
        fields = result["items"][0]["request_fields"][0]["fields"]
        self.assertEqual(fields, [{"name": "quantity", "occurrences": 2}])
        self.assertNotIn("VALUE_CANARY", json.dumps(result))
        self.assertNotIn("private.invalid", json.dumps(result))

    def test_summary_omits_shapes_and_detail_keeps_them(self):
        summary = inspect_evidence(self.capture, javascript_engine="basic")
        detail = inspect_evidence(self.capture, javascript_engine="basic", detail=True)
        self.assertNotIn("response_shapes", summary["items"][0])
        self.assertIn("response_shapes_count", summary["items"][0])
        self.assertIn("response_shapes", detail["items"][0])

    def test_body_pages_disclose_retained_row_omissions(self):
        capture = [har([har_entry() for _ in range(70)])]
        page = inspect_evidence(capture, javascript_engine="basic", category="har_bodies", body_state="omitted", limit=1)
        coverage = page["har_body_analysis"][0]
        self.assertEqual(coverage["counts"]["bodies"], 140)
        self.assertEqual(coverage["omitted_body_records"], 12)
        self.assertNotIn("bodies", coverage)
        self.assertEqual(page["counts"]["retained"], 128)
        self.assertEqual(page["counts"]["matching"], 64)
        self.assertEqual(page["items"][0]["reason"], "not_present")

    def test_source_and_kind_must_match_the_same_evidence(self):
        report = analyze(self.capture, javascript_engine="basic")
        report["routes"][0]["evidence"].append({"source": "source_2", "kind": "form", "location": "line 1"})
        page = page_report(report, source_id="source_1", evidence_kind="form")
        self.assertEqual(page["counts"]["matching"], 0)

    def test_assets_filter_uses_asset_kind_with_source_evidence(self):
        result = inspect_evidence([html('<script src="/app.js"></script><link rel="stylesheet" href="/app.css">')],
                                  javascript_engine="basic", category="assets", source_id="source_1", evidence_kind="script")
        self.assertEqual(result["counts"]["matching"], 1)
        self.assertEqual(result["items"][0]["kind"], "script")
        embedded = inspect_evidence([html('<iframe src="/api/items"></iframe>')], javascript_engine="basic",
                                    category="assets", evidence_kind="embedded-document")
        self.assertEqual(embedded["counts"]["matching"], 1)

    def test_offset_beyond_end_is_empty_with_real_counts(self):
        result = inspect_evidence(self.capture, javascript_engine="basic", offset=99)
        self.assertEqual(result["counts"], {"retained": 3, "matching": 3, "returned": 0, "remaining": 0})
        self.assertIsNone(result["page"]["next_offset"])

    def test_invalid_filters_are_rejected(self):
        for selection in ({"limit": True}, {"limit": 101}, {"offset": -1}, {"method": "TRACE"},
                          {"source_id": "../private"}, {"confidence": "safe"}, {"detail": "yes"},
                          {"category": "assets", "method": "POST"}, {"body_state": "inspected"}):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                inspect_evidence(self.capture, javascript_engine="basic", **selection)

    def test_projection_does_not_mutate_the_report(self):
        report = analyze(self.capture, javascript_engine="basic")
        before = deepcopy(report)
        page_report(report, category="har_bodies", detail=True)
        self.assertEqual(report, before)

    def test_ninth_tool_is_bounded_and_read_only_in_schema(self):
        specs = tool_specs()
        self.assertEqual(len(specs), 9)
        schema = specs["inspect_evidence"][1]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["limit"]["maximum"], 100)


class ExportBodyEvidenceTests(unittest.TestCase):
    def test_rebuild_plan_keeps_observed_forms_and_body_coverage(self):
        from surface_map.rebuild import plan_rebuild
        capture = [har([har_entry(body={"mimeType": "application/x-www-form-urlencoded", "text": "quantity=VALUE_CANARY"})])]
        plan = plan_rebuild(capture, javascript_engine="basic")
        self.assertEqual(plan["operations"][0]["request_fields"][0]["fields"][0]["name"], "quantity")
        self.assertEqual(plan["coverage"]["har_body_analysis"][0]["counts"]["inspected"], 2)
        self.assertNotIn("bodies", plan["coverage"]["har_body_analysis"][0])
        self.assertNotIn("VALUE_CANARY", json.dumps(plan))

    def test_form_fields_and_body_coverage_survive_all_exports(self):
        artifacts = [har([har_entry(body={"mimeType": "multipart/form-data", "params": [
            {"name": "quantity", "value": "VALUE_CANARY"}, {"name": "quantity", "fileName": "FILE_CANARY"}]})])]
        report = analyze(artifacts, javascript_engine="basic")
        self.assertIn("har_body_analysis", review(report))
        exported = contract(report)
        self.assertEqual(exported["routes"][0]["request_fields"][0]["fields"][0]["occurrences"], 2)
        api = openapi(report)
        operation = api["paths"]["/api/orders"]["post"]
        self.assertIn("x-surface-map-observed-form-fields", operation)
        self.assertIn("x-surface-map-har-body-analysis", api)
        self.assertNotIn("requestBody", operation)
        for result in (report, exported, api):
            self.assertNotIn("VALUE_CANARY", json.dumps(result))
            self.assertNotIn("FILE_CANARY", json.dumps(result))

    def test_form_occurrence_changes_are_visible(self):
        old = analyze([har([har_entry(body={"mimeType": "application/x-www-form-urlencoded", "text": "quantity=1"})])], javascript_engine="basic")
        new = analyze([har([har_entry(body={"mimeType": "application/x-www-form-urlencoded", "text": "quantity=1&quantity=2"})])], javascript_engine="basic")
        result = compare(old, new)
        self.assertEqual(result["changed"][0]["changed_fields"], ["request_fields"])

    def test_form_capture_reordering_does_not_invent_a_shape_change(self):
        body = {"mimeType": "application/x-www-form-urlencoded", "text": "quantity=1&quantity=2"}
        first, second = har_entry(body=body), har_entry("/api/items", "GET")
        old = analyze([har([first, second])], javascript_engine="basic")
        new = analyze([har([second, first])], javascript_engine="basic")
        self.assertEqual(compare(old, new)["changed"], [])

    def test_unread_body_does_not_look_like_verified_schema_removal(self):
        old = analyze([har([har_entry()])], javascript_engine="basic")
        new = analyze([har([har_entry(response={"mimeType": "application/json", "encoding": "unknown", "text": "opaque"})])], javascript_engine="basic")
        result = compare(old, new)
        self.assertIn("capture_bodies_uninspected_after", result["changed"][0]["context"])
        self.assertEqual(result["after_coverage"]["har_bodies"]["not_present"], 1)


if __name__ == "__main__":
    unittest.main()
