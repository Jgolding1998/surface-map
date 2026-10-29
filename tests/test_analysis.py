import json
import unittest
from unittest.mock import patch

from surface_map.analyze import ArtifactError, MAX_FILE_BYTES, analyze, js_tokens, safe_path, strict_json
from surface_map.export import compare, contract, openapi
from surface_map.samples import artifacts


CANARY = "PRIVATE" + "-CANARY-92837465"
EMAIL = "person" + chr(64) + "example.invalid"


def har(entries):
    return {"kind": "har", "text": json.dumps({"log": {"entries": entries}})}


def entry(path="/api/orders/123", method="GET", status=200, body=None):
    value = {"request": {"url": path, "method": method}, "response": {"status": status}}
    if body is not None:
        value["response"]["content"] = {"mimeType": "application/json", "text": json.dumps(body)}
    return value


class AnalysisTests(unittest.TestCase):
    def test_demo_has_separate_observed_declared_and_inferred_routes(self):
        report = analyze(artifacts())
        self.assertEqual(len(report["routes"]), 7)
        self.assertEqual({route["confidence"] for route in report["routes"]}, {"observed", "declared", "inferred"})
        self.assertEqual(len(report["assets"]), 2)
        route = next(item for item in report["routes"] if item["path"] == "/api/orders/{integer_3}")
        self.assertEqual(route["statuses"], [200])
        self.assertEqual(route["query_fields"], ["page"])
        self.assertEqual(route["evidence"], [{"source": "source_1", "location": "entry 1", "kind": "har"}])

    def test_no_sensitive_values_in_any_export(self):
        item = entry(f"https://{CANARY}.invalid/api/customers/{EMAIL}/{CANARY}?token={CANARY}&{EMAIL}={CANARY}", "POST", body={"email": EMAIL, "token": CANARY, EMAIL: CANARY, "nested_private_key": {"password": CANARY}})
        item["request"].update({"headers": [{"name": "Authorization", "value": CANARY}], "cookies": [{"name": CANARY, "value": CANARY}], "postData": {"mimeType": "application/json", "text": json.dumps({"password": CANARY, "__proto__": {"secret": CANARY}})}})
        inputs = [har([item]), {"kind": "html", "text": f'<a href="/api/customers/{EMAIL}">{EMAIL}</a><form action="/api/orders"><input name="{EMAIL}" value="{CANARY}"></form><script src="/{CANARY}.js"></script>'}, {"kind": "javascript", "text": f"const savedValue = '{CANARY}'; fetch('/api/orders/{CANARY}?token={CANARY}');"}]
        report = analyze(inputs)
        for model in [report, openapi(report), contract(report), compare(report, report)]:
            text = json.dumps(model)
            self.assertNotIn(CANARY, text)
            self.assertNotIn(EMAIL, text)
            self.assertNotIn("__proto__", text)
            self.assertNotIn("nested_private_key", text)
        self.assertIn("password", json.dumps(report))

    def test_safe_paths_redact_unknown_names_and_discard_origin_query_fragment(self):
        path, query = safe_path("https://example.invalid/api/customers/123/a-long-private-token?email=discarded#discarded")
        self.assertEqual(path, "/api/customers/{integer_3}/{segment_4}")
        self.assertEqual(query, ["email"])
        self.assertEqual(safe_path("/api/%63ustomers/person%40example.invalid")[0], "/api/customers/{segment_3}")

    def test_dangerous_schemes_traversal_and_excess_query_omitted(self):
        for value in ["javascript:alert(1)", "data:text/plain,test", "file:///private", "/../api", "/%2e%2e/api", "/api\n/private", "/api?" + "&".join("q=1" for _ in range(65))]:
            with self.subTest(value=value):
                self.assertIsNone(safe_path(value))

    def test_static_js_skips_comments_templates_and_concatenation(self):
        text = "// fetch('/api/private');\nconst s=\"fetch('/api/users')\";\nfetch(`/api/${id}`);\nfetch('/api/orders' + secret);\nfetch('/api/items');"
        report = analyze([{"kind": "javascript", "text": text}])
        self.assertEqual([item["path"] for item in report["routes"]], ["/api/items"])
        self.assertTrue(report["warnings"])

    def test_static_js_method_is_local_to_literal_options(self):
        report = analyze([{"kind": "javascript", "text": "fetch('/api/orders', {}); const other = {method: 'DELETE'}; fetch('/api/items', {method: 'POST'});"}])
        self.assertEqual({(item["path"], item["method"]) for item in report["routes"]}, {("/api/orders", "GET"), ("/api/items", "POST")})

    def test_dynamic_or_duplicate_js_method_is_omitted(self):
        for options in ["{method: choose()}", "{...options}", "{method:'PO'+'ST'}", "{method:'POST', method:'GET'}", "options"]:
            report = analyze([{"kind": "javascript", "text": f"fetch('/api/orders', {options});"}])
            with self.subTest(options=options):
                self.assertEqual(report["routes"], [])

    def test_shorthand_computed_accessor_and_inherited_methods_are_not_get(self):
        options = ["{method}", "{[method]: 'POST'}", "{['method']: 'POST'}", "{get method() {return 'POST'}}",
                   "{get [method]() {return 'POST'}}", "{method() {return 'POST'}}", "{__proto__: {method: 'POST'}}",
                   r"{'m\u0065thod': 'POST'}", r"{m\u0065thod: 'POST'}"]
        for value in options:
            with self.subTest(options=value):
                report = analyze([{"kind": "javascript", "text": f"const method='POST'; fetch('/api/orders', {value});"}])
                self.assertEqual(report["routes"], [])
                self.assertTrue(report["warnings"])

    def test_unescaped_literal_method_and_nested_unrelated_method_still_work(self):
        for options, expected in [("{method:'POST', headers:{method:'ignored'}}", "POST"),
                                  ("{headers:{method:'ignored'}, body:[1,2]}", "GET"),
                                  ("{mode:'method'}", "GET")]:
            with self.subTest(options=options):
                report = analyze([{"kind": "javascript", "text": f"fetch('/api/orders', {options});"}])
                self.assertEqual([(route["method"], route["path"]) for route in report["routes"]], [(expected, "/api/orders")])

    def test_escaped_url_literals_are_omitted_without_guessing_decoding(self):
        report = analyze([{"kind": "javascript", "text": r"fetch('\x2fapi\x2forders');"}])
        self.assertEqual(report["routes"], [])
        self.assertTrue(report["warnings"])

    def test_html_does_not_execute_or_export_text_and_values(self):
        report = analyze([{"kind": "html", "text": f'<script>throw "{CANARY}"; fetch("/api/private");</script><form action="/api/search" method="POST"><input name="q" value="{CANARY}"><input name="email" value="{EMAIL}"></form>'}])
        self.assertEqual(len(report["routes"]), 1)
        route = report["routes"][0]
        self.assertEqual(route["confidence"], "declared")
        self.assertEqual(route["request_shapes"][0]["properties"], {"q": {"type": "string"}, "email": {"type": "string"}})
        self.assertNotIn(CANARY, json.dumps(report))

    def test_body_shapes_do_not_infer_required_fields_or_constants(self):
        report = analyze([har([entry(body={"id": 23, "name": "Test", "enabled": True, "amount": 1.2, "items": [1, "a", None]})])])
        model = report["routes"][0]["response_shapes"][0]
        self.assertEqual(model["properties"]["id"], {"type": "integer"})
        self.assertEqual(model["properties"]["enabled"], {"type": "boolean"})
        self.assertEqual(len(model["properties"]["items"]["items"]["anyOf"]), 3)
        self.assertNotIn("required", model)
        self.assertNotIn("enum", json.dumps(model))

    def test_base64_and_invalid_json_bodies_are_not_decoded(self):
        item = entry()
        item["response"]["content"] = {"mimeType": "application/json", "encoding": "base64", "text": "private"}
        self.assertEqual(analyze([har([item])])["routes"][0]["response_shapes"], [])
        item["response"]["content"] = {"mimeType": "application/json", "text": "{bad}"}
        self.assertEqual(analyze([har([item])])["routes"][0]["response_shapes"], [])

    def test_status_types_and_nonstandard_methods(self):
        report = analyze([har([entry(status=True), entry(method="NOT-A-METHOD")])])
        self.assertEqual(len(report["routes"]), 1)
        self.assertEqual(report["routes"][0]["statuses"], [])
        self.assertTrue(report["warnings"])

    def test_invalid_har_method_types_are_controlled_errors(self):
        for method in [[], {}, None, 23, True]:
            with self.subTest(method=method), self.assertRaises(ArtifactError):
                analyze([har([entry(method=method)])])

    def test_invalid_unicode_har_body_omits_shape_and_preserves_request(self):
        item = entry()
        item["response"]["content"] = {"mimeType": "application/json", "text": "\ud800"}
        report = analyze([har([item])])
        self.assertEqual(len(report["routes"]), 1)
        self.assertEqual(report["routes"][0]["response_shapes"], [])

    def test_openapi_has_no_server_or_examples_and_declares_inferred_parameters(self):
        document = openapi(analyze(artifacts()))
        self.assertEqual(document["openapi"], "3.1.0")
        self.assertNotIn("servers", document)
        operation = document["paths"]["/api/orders/{integer_3}"]["get"]
        self.assertIn("x-surface-map-evidence", operation)
        self.assertEqual([p["name"] for p in operation["parameters"]], ["page", "integer_3"])
        self.assertNotIn('"example"', json.dumps(document))

    def test_capture_diff_tracks_added_absent_and_status_change(self):
        result = compare(analyze(artifacts()), analyze(artifacts(2)))
        self.assertEqual([route["path"] for route in result["added"]], ["/api/reports"])
        self.assertEqual([route["path"] for route in result["removed"]], ["/api/inventory"])
        self.assertEqual(result["changed"][0]["changed_fields"], ["statuses"])

    def test_scalar_value_changes_do_not_appear_in_diff(self):
        before = analyze([har([entry(body={"id": 1, "name": "before"})])])
        after = analyze([har([entry(body={"id": 2, "name": "after"})])])
        self.assertEqual(compare(before, after)["changed"], [])

    def test_schema_type_change_appears_in_diff(self):
        before = analyze([har([entry(body={"id": 1})])])
        after = analyze([har([entry(body={"id": "changed"})])])
        self.assertEqual(compare(before, after)["changed"][0]["changed_fields"], ["response_shapes"])

    def test_normalized_aliases_can_merge_distinct_sources(self):
        report = analyze([har([entry("https://one.invalid/api/private-label"), entry("https://two.invalid/api/another-label")])])
        self.assertEqual(len(report["routes"]), 1)
        self.assertIn("merge", report["privacy"]["scope"])

    def test_no_subprocess_or_network_used_by_analyzer(self):
        with patch("socket.create_connection", side_effect=AssertionError("Network forbidden")), patch("subprocess.Popen", side_effect=AssertionError("Execution forbidden")):
            self.assertEqual(len(analyze(artifacts())["routes"]), 7)


class LimitTests(unittest.TestCase):
    def test_invalid_formats_and_unicode_are_safe_errors(self):
        for value in [None, [], [{}], [{"kind": "zip", "text": CANARY}], [{"kind": [], "text": CANARY}], [{"kind": "html", "text": "\ud800"}], [{"kind": "html", "text": "", "filename": EMAIL}]]:
            with self.subTest(value=repr(value)), self.assertRaises(ArtifactError) as error:
                analyze(value)
            self.assertNotIn(CANARY, str(error.exception))
            self.assertNotIn(EMAIL, str(error.exception))

    def test_artifact_count_and_bytes(self):
        for value in [[{"kind": "html", "text": ""}] * 7, [{"kind": "html", "text": "a" * (MAX_FILE_BYTES + 1)}], [{"kind": "html", "text": "a" * MAX_FILE_BYTES}] * 3]:
            with self.assertRaises(ArtifactError):
                analyze(value)

    def test_json_depth_nodes_nonfinite_and_duplicates(self):
        for text in ["[" * 30 + "0" + "]" * 30, '{"a":1,"a":2}', '{"value": NaN}', '{"value":1e999}', '[0,' + ',0' * 1000 + ']']:
            with self.subTest(text=text[:30]), self.assertRaises(ArtifactError):
                strict_json(text)
        self.assertEqual(strict_json('{"text":"[[[[[[[[[[[[[[[[[[[[[[[[[["}')["text"], "[" * 26)

    def test_bad_har_shapes_are_rejected_without_source_text(self):
        for document in [{}, {"log": []}, {"log": {"entries": [CANARY]}}, {"log": {"entries": [{"request": {}, "response": []}]}}]:
            with self.assertRaises(ArtifactError) as error:
                analyze([{"kind": "har", "text": json.dumps(document)}])
            self.assertNotIn(CANARY, str(error.exception))

    def test_har_entry_limit(self):
        with self.assertRaises(ArtifactError):
            analyze([har([entry()] * 301)])

    def test_js_and_html_work_limits(self):
        with self.assertRaises(ArtifactError):
            analyze([{"kind": "javascript", "text": "a;" * 160000}])
        with self.assertRaises(ArtifactError):
            analyze([{"kind": "html", "text": "<b>" * 5001}])

    def test_typical_large_literal_fetch_bundles_are_accepted(self):
        unit = "fetch('/api/orders');\n"
        for kib in (128, 256, 500):
            source = unit * (kib * 1024 // len(unit))
            with self.subTest(kib=kib):
                report = analyze([{"kind": "javascript", "text": source}])
                self.assertEqual(len(report["routes"]), 1)
                self.assertEqual(report["routes"][0]["path"], "/api/orders")

    def test_tokenizer_work_deadline_is_enforced(self):
        with patch("surface_map.analyze.time.monotonic", side_effect=[0, 4]):
            with self.assertRaises(ArtifactError):
                js_tokens("a;" * 1000)


if __name__ == "__main__":
    unittest.main()
