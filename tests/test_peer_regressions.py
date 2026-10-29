import copy
import json
import socket
import threading
import unittest

from surface_map.analyze import ArtifactError, MAX_FILE_BYTES, analyze
from surface_map.export import openapi
from surface_map.server import make_server
from test_analysis import entry, har


class AnalysisRegressionTests(unittest.TestCase):
    def test_equivalent_redacted_templates_merge_without_losing_operations_or_evidence(self):
        report = analyze([har([entry("/api/orders/123?page=1", status=200, body={"id": 1}),
                               entry("/api/orders/abc?limit=2", status=201, body={"name": "value"}),
                               entry("/api/orders/ref-9", method="DELETE", status=204)])])
        before = copy.deepcopy(report)
        document = openapi(report)
        self.assertEqual(set(document["paths"]), {"/api/orders/{segment_3}"})
        methods = document["paths"]["/api/orders/{segment_3}"]
        self.assertEqual(set(methods), {"get", "delete"})
        operation = methods["get"]
        self.assertEqual(set(operation["responses"]), {"200", "201"})
        self.assertEqual(len(operation["x-surface-map-evidence"]), 2)
        self.assertEqual({item["name"] for item in operation["parameters"]}, {"page", "limit", "segment_3"})
        parameter = next(item for item in operation["parameters"] if item["in"] == "path")
        self.assertEqual(parameter["schema"], {"type": "string"})
        self.assertEqual(len(operation["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]), 2)
        self.assertEqual(report, before)

    def test_template_merge_preserves_numeric_types_at_unambiguous_positions(self):
        report = analyze([har([entry("/api/users/123/orders/456"), entry("/api/users/123/orders/abc")])])
        document = openapi(report)
        operation = document["paths"]["/api/users/{integer_3}/orders/{segment_5}"]["get"]
        self.assertEqual({item["name"]: item["schema"]["type"] for item in operation["parameters"]}, {"integer_3": "integer", "segment_5": "string"})

    def test_escaped_newline_does_not_shift_later_javascript_evidence(self):
        for newline in ("\n", "\r\n"):
            source = "const value='one\\" + newline + "two';" + newline + "fetch('/api/items');"
            report = analyze([{"kind": "javascript", "text": source}])
            self.assertEqual(report["routes"][0]["evidence"][0]["location"], "line 3")

    def test_escape_sequence_without_a_real_newline_does_not_increment_the_line(self):
        report = analyze([{"kind": "javascript", "text": "const value='one\\ntwo';\nfetch('/api/items');"}])
        self.assertEqual(report["routes"][0]["evidence"][0]["location"], "line 2")

    def test_forms_without_action_have_no_assumed_root_route(self):
        for action in ("", ' action=""', ' action="   "'):
            with self.subTest(action=action):
                report = analyze([{"kind": "html", "text": f'<form method="POST"{action}><input name="email"></form>'}])
                self.assertEqual(report["routes"], [])
                self.assertTrue(report["warnings"])
        report = analyze([{"kind": "html", "text": '<form action="/" method="POST"><input name="email"></form>'}])
        self.assertEqual([(route["method"], route["path"]) for route in report["routes"]], [("POST", "/")])

    def test_duplicate_html_attributes_use_the_first_browser_value(self):
        report = analyze([{"kind": "html", "text": '<a href="/dashboard" HREF="/admin">x</a><form action="/api/orders" action="/api/users" method="POST" method="DELETE"></form>'}])
        self.assertEqual({(route["method"], route["path"]) for route in report["routes"]}, {("GET", "/dashboard"), ("POST", "/api/orders")})

    def test_page_relative_urls_are_omitted_instead_of_rooted(self):
        html = ('<a href="#main">Skip</a><a href="?page=2">Next</a><a href="orders/5">Order</a><a href=" /dashboard ">Home</a>'
                '<form action="search"><input name="q"></form><script src="app.js"></script><link rel="stylesheet" href="../site.css">')
        report = analyze([{"kind": "html", "text": html}, {"kind": "javascript", "text": "fetch('api/notifications'); fetch('//cdn.example/api/messages');"}])
        self.assertEqual([(route["method"], route["path"]) for route in report["routes"]], [("GET", "/api/messages"), ("GET", "/dashboard")])
        self.assertEqual(report["assets"], [])
        self.assertEqual({item["source"] for item in report["warnings"] if "relative URL" in item["reason"]}, {"source_1", "source_2"})

    def test_forms_use_browser_submission_methods_and_encoding(self):
        html = ('<form action="/api/items/7" method="delete"><input name="id"></form>'
                '<form action="/api/search"><input name="q"><input name="q"><input name="page"></form>'
                '<form action="/api/orders" method="PoSt"><input name="sku"><input name="sku"></form>'
                '<form action="/api/close" method="dialog"><input name="note"></form>')
        report = analyze([{"kind": "html", "text": html}])
        routes = {(route["method"], route["path"]): route for route in report["routes"]}
        self.assertEqual(set(routes), {("GET", "/api/items/{integer_3}"), ("GET", "/api/search"), ("POST", "/api/orders")})
        self.assertEqual(routes[("GET", "/api/search")]["query_fields"], ["page", "q"])
        self.assertEqual(routes[("GET", "/api/search")]["request_shapes"], [])
        self.assertEqual(routes[("POST", "/api/orders")]["request_shapes"], [{"type": "object", "properties": {"sku": {"type": "string"}}}])
        operation = openapi(report)["paths"]["/api/search"]["get"]
        self.assertNotIn("requestBody", operation)
        self.assertEqual({item["name"] for item in operation["parameters"]}, {"page", "q"})

    def test_stylesheet_detection_uses_rel_tokens(self):
        report = analyze([{"kind": "html", "text": '<link rel="preload Stylesheet" href="/assets/app.css"><link rel="icon" href="/favicon.ico">'}])
        self.assertEqual([(item["kind"], item["path"]) for item in report["assets"]], [("link", "/favicon.ico"), ("stylesheet", "/assets/app.css")])

    def test_header_heavy_har_hits_the_node_limit_before_the_entry_limit(self):
        headers = [{"name": f"header-{index}", "value": "discarded"} for index in range(30)]
        entries = [entry(f"/api/orders/{index}") for index in range(150)]
        for item in entries:
            item["request"]["headers"] = headers
            item["response"]["headers"] = headers
        capture = har(entries)
        self.assertLess(len(capture["text"].encode()), MAX_FILE_BYTES)
        with self.assertRaises(ArtifactError) as error:
            analyze([capture])
        self.assertIn("JSON structure", str(error.exception))


class HTTPRegressionTests(unittest.TestCase):
    def test_invalid_versions_return_framed_fixed_json_with_security_headers(self):
        server = make_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        marker = b"PRIVATE_FIXTURE_559318"
        try:
            port = server.server_address[1]
            lines = [b"GET /" + marker + b" HTTP/1.x", b"GET /" + marker + b" HTTP/2.0", b"GET /" + marker + b" FOO/1.1",
                     b"GET /" + marker + b" HTTP/1.1 extra", b"GET /" + marker + b" HTTP/0.9", b"GET /" + marker]
            for line in lines:
                with self.subTest(line=line), socket.create_connection(("127.0.0.1", port), timeout=3) as client:
                    client.sendall(line + f"\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
                    data = b""
                    while block := client.recv(4096):
                        data += block
                    self.assertTrue(data.startswith((b"HTTP/1.0 400", b"HTTP/1.0 505")))
                    head, body = data.split(b"\r\n\r\n", 1)
                    self.assertIn(b"Content-Type: application/json", head)
                    self.assertIn(b"X-Content-Type-Options: nosniff", head)
                    self.assertIn(b"Content-Security-Policy:", head)
                    self.assertNotIn(b"Server:", head)
                    self.assertNotIn(marker, data)
                    self.assertEqual(json.loads(body), {"error": "Invalid HTTP request"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
