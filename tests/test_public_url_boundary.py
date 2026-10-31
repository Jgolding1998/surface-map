"""Adversarial HTTP integration checks; all public transport is simulated."""
from contextlib import contextmanager
import http.client
import json
import re
import socket
import threading
import unittest
from unittest.mock import patch

from surface_map.analyze import ArtifactError, parse_js
from surface_map.server import MAX_RESPONSE, make_server
from surface_map.url_capture import CaptureError


CANARY = "PRIVATE_PUBLIC_URL_BOUNDARY_5831"
URL = f"https://example.com/{CANARY}?token={CANARY}"


class Response:
    def __init__(self, body, content_type="text/html; charset=utf-8"):
        self.body = body.encode("utf-8")
        self.offset = 0
        self.status = 200
        self.content_type = content_type

    def getheaders(self):
        return [("Content-Type", self.content_type)]

    def read1(self, size):
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


class PublicURLBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.server = make_server(0)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = re.search(rb'name="surface-token" content="([a-f0-9]+)"',
                               self.request("/", method="GET")[2]).group(1).decode()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path="/api/public-url", value=None, *, method="POST", headers=None, raw=None, timeout=5):
        config = {"Origin": self.origin, "X-Surface-Token": getattr(self, "token", ""),
                  "Content-Type": "application/json"}
        config.update(headers or {})
        config = {key: item for key, item in config.items() if item is not None}
        body = raw if raw is not None else json.dumps(value).encode("utf-8") if value is not None else None
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        connection.request(method, path, body, config)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    @contextmanager
    def transport(self, *, html=None):
        page = html if html is not None else (
            '<title>' + CANARY + '</title><form method="post" action="/api/orders">'
            '<input name="order_id" value="' + CANARY + '"></form>'
            '<script src="/bad.js"></script><script src="/good.js"></script>')
        responses = {
            URL: (page, "text/html; charset=utf-8"),
            "https://example.com/bad.js": ('function ( {', "text/javascript"),
            "https://example.com/good.js": (
                'fetch("/api/items?token=' + CANARY + '");'
                'fetch("https://other.example.com/api/users");', "text/javascript"),
        }
        calls = []

        @contextmanager
        def opening(target, address, budget):
            calls.append(target.url)
            body, media = responses[target.url]
            yield Response(body, media)

        with patch("surface_map.url_capture._resolve", return_value=[(socket.AF_INET, "1.1.1.1")]), \
                patch("surface_map.url_capture._open_response", side_effect=opening):
            yield calls

    def test_token_origin_and_method_gate_network_access(self):
        with patch("surface_map.server.inspect_public_url") as inspect:
            for headers in ({"X-Surface-Token": "a" * 64}, {"Origin": None},
                            {"Origin": "https://foreign.example"}, {"Sec-Fetch-Site": "cross-site"}):
                with self.subTest(headers=headers):
                    self.assertEqual(self.request(value={"url": URL}, headers=headers)[0], 403)
            self.assertEqual(self.request(value=None, method="GET")[0], 404)
            self.assertEqual(self.request(value={"url": URL}, method="PUT")[0], 405)
            inspect.assert_not_called()

    def test_rejected_shapes_options_and_encodings_never_reach_dns(self):
        invalid = [None, [], {}, {"url": URL, "artifacts": []}, {"url": URL, "report": {}},
                   {"url": URL, "include_scaffold": True}, {"url": 1}, {"url": []},
                   {"url": ""}, {"url": "https://example.com/" + "x" * 2048},
                   *({"url": URL, "include_scripts": value} for value in (None, 1, "true", [])),
                   *({"url": URL, "javascript_engine": value} for value in (None, 1, [], "invalid"))]
        with patch("surface_map.url_capture._resolve") as resolve, \
                patch("surface_map.url_capture._open_response") as opening:
            for payload in invalid:
                with self.subTest(payload=payload):
                    status, _, body = self.request(value=payload)
                    self.assertEqual(status, 422)
                    self.assertNotIn(CANARY, body.decode())
            for raw in (b'{"url":"https://example.com/","url":"https://127.0.0.1/"}',
                        b'{"url":', b'\xff', json.dumps({"url": "x" * 17000}).encode()):
                with self.subTest(raw_size=len(raw)):
                    self.assertEqual(self.request(raw=raw)[0], 422)
            resolve.assert_not_called()
            opening.assert_not_called()

    def test_private_and_non_https_targets_are_rejected_before_transport(self):
        with patch("surface_map.url_capture._resolve") as resolve, \
                patch("surface_map.url_capture._open_response") as opening:
            for url in ("http://example.com/", "file:///etc/passwd", "https://127.0.0.1/",
                        "https://localhost/", "https://169.254.169.254/", "https://[::1]/",
                        "https://user:password@example.com/", "https://example.com:8443/"):
                with self.subTest(url=url):
                    status, _, body = self.request(value={"url": url})
                    self.assertEqual(status, 422)
                    self.assertIn(json.loads(body)["code"], {"invalid_url", "blocked_target"})
            resolve.assert_not_called()
            opening.assert_not_called()

    def test_real_partial_capture_preserves_ids_privacy_and_all_export_context(self):
        def parse(text, source, builder, **kwargs):
            if source == "source_2":
                raise ArtifactError(CANARY)
            return parse_js(text, source, builder, **kwargs)

        with self.transport() as outgoing, patch("surface_map.analyze.parse_js", side_effect=parse):
            status, headers, body = self.request(value={"url": URL, "javascript_engine": "basic"})
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(set(result), {"report", "plan", "openapi", "contract"})
        self.assertNotIn(CANARY, body.decode())
        self.assertNotIn("example.com", body.decode())
        self.assertNotIn("artifacts", result)
        self.assertNotIn("document_url", result)
        self.assertEqual(outgoing, [URL, "https://example.com/bad.js", "https://example.com/good.js"])
        report, plan = result["report"], result["plan"]
        self.assertTrue(report["capture"]["analysis_partial"])
        self.assertEqual(report["capture"]["scripts_fetched"], 2)
        self.assertEqual(report["capture"]["scripts_analyzed"], 1)
        self.assertEqual(report["script_omissions"], [{"source": "source_2", "code": "unsupported_or_limit"}])
        self.assertEqual(next(row for row in report["sources"] if row["id"] == "source_2")["status"], "omitted")
        items = next(row for row in plan["operations"] if row["path"] == "/api/items")
        self.assertEqual({row["source"] for row in items["evidence"]}, {"source_3"})
        self.assertEqual(next(row for row in plan["operations"] if row["path"] == "/api/users")["stub"], "none")
        self.assertEqual({(row["method"], row["path"]) for row in report["routes"]},
                         {(row["method"], row["path"]) for row in plan["operations"]})
        self.assertEqual(plan["coverage"]["capture"], report["capture"])
        self.assertEqual(plan["coverage"]["script_omissions"], report["script_omissions"])
        self.assertFalse(plan["candidate_references"]["limits"]["inventory_performed"])
        self.assertTrue(plan["coverage"]["reference_inventory_omission"])
        self.assertIn("were fetched", plan["scope"])
        for name in ("openapi", "contract"):
            for key in ("capture", "capture_warnings", "script_omissions", "sources", "javascript_analysis"):
                self.assertEqual(result[name]["x-surface-map-" + key.replace("_", "-")], report[key])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])

    def test_disabled_script_capture_does_not_fetch_discovered_scripts(self):
        with self.transport() as outgoing:
            status, _, body = self.request(value={"url": URL, "include_scripts": False, "javascript_engine": "basic"})
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(outgoing, [URL])
        self.assertEqual(result["report"]["capture"]["scripts_fetched"], 0)
        self.assertTrue(any("disabled" in warning.lower() for warning in result["report"]["capture_warnings"]))
        self.assertEqual(len(result["plan"]["operations"]), 1)

    def test_unexpected_and_capture_failures_return_fixed_private_errors(self):
        for error, expected in ((RuntimeError(URL + CANARY), 500), (CaptureError("network"), 422)):
            with self.subTest(error=type(error).__name__), \
                    patch("surface_map.server.inspect_public_url", side_effect=error):
                status, _, body = self.request(value={"url": URL})
            self.assertEqual(status, expected)
            self.assertNotIn(CANARY, body.decode())
            self.assertNotIn("example.com", body.decode())
            self.assertNotIn("Traceback", body.decode())
        self.assertEqual(self.request("/health", method="GET")[0], 200)

    def test_public_limits_leave_offline_timeout_unchanged(self):
        status, _, body = self.request("/api/limits", method="GET")
        self.assertEqual(status, 200)
        limits = json.loads(body)
        self.assertEqual(limits["request_timeout_ms"], 35000)
        self.assertEqual(limits["public_url"]["request_timeout_ms"], 45000)
        self.assertLess(limits["public_url"]["request_bytes"], limits["source_request_bytes"])
        self.assertLessEqual(limits["public_url"]["capture_seconds"] + limits["public_url"]["analysis_seconds"], 30)

    def test_one_public_request_leaves_capacity_for_offline_analysis_and_releases_its_slot(self):
        with self.transport(html='<form method="post" action="/api/orders"></form>'):
            from surface_map.public_url import inspect_public_url
            public = inspect_public_url(URL, include_scripts=False, javascript_engine="basic")
        entered, release = threading.Event(), threading.Event()
        first_result, first_errors = [], []

        def hold_capture(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Blocked fake capture was not released")
            return public

        def first_request():
            try:
                first_result.append(self.request(value={"url": URL, "javascript_engine": "basic"}))
            except Exception as error:
                first_errors.append(error)

        with patch("surface_map.server.inspect_public_url", side_effect=hold_capture) as inspect:
            first = threading.Thread(target=first_request, daemon=True)
            first.start()
            try:
                self.assertTrue(entered.wait(2), "The first public request should enter capture")
                # Finishes while the first capture is still blocked; a waiting URL queue
                # would hit this timeout rather than returning the expected busy response.
                status, _, body = self.request(value={"url": URL}, timeout=2)
                self.assertEqual(status, 503)
                self.assertNotIn(CANARY, body.decode())
                self.assertEqual(inspect.call_count, 1)
                status, _, body = self.request("/api/demo", {"javascript_engine": "basic"})
                self.assertEqual(status, 200)
                self.assertTrue(json.loads(body)["report"]["routes"])
                self.assertFalse(release.is_set())
            finally:
                release.set()
                first.join(timeout=3)
            self.assertFalse(first.is_alive())
            self.assertEqual(first_errors, [])
            self.assertEqual(first_result[0][0], 200)
        with patch("surface_map.server.inspect_public_url", side_effect=RuntimeError(CANARY)):
            self.assertEqual(self.request(value={"url": URL})[0], 500)
        with patch("surface_map.server.inspect_public_url", return_value=public) as inspect:
            self.assertEqual(self.request(value={"url": URL})[0], 200)
            inspect.assert_called_once()

    def test_combined_public_response_still_obeys_output_limit(self):
        with self.transport(html='<form method="post" action="/api/orders"></form>'):
            from surface_map.public_url import inspect_public_url
            public = inspect_public_url(URL, include_scripts=False, javascript_engine="basic")
        public["report"]["capture_warnings"].append("x" * MAX_RESPONSE)
        with patch("surface_map.server.inspect_public_url", return_value=public):
            status, _, body = self.request(value={"url": URL})
        self.assertEqual(status, 413)
        self.assertNotIn(CANARY, body.decode())
        self.assertLess(len(body), 256)


if __name__ == "__main__":
    unittest.main()
