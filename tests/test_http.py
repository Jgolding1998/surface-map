import http.client
import json
import re
import socket
import threading
import time
import unittest
from unittest.mock import Mock, call, patch

from surface_map.server import MAX_REQUEST, make_server
from surface_map import js_parser
from test_analysis import CANARY, EMAIL, entry, har


class HTTPTests(unittest.TestCase):
    def test_engine_selection_is_validated_before_analysis(self):
        for engine in (None, [], "unexpected"):
            response = self.request("/api/analyze", "POST", {"artifacts": [], "javascript_engine": engine})
            self.assertEqual(response[0], 422)
            self.assertIn("engine", json.loads(response[2])["error"])
        response = self.request("/api/analyze", "POST", {"artifacts": [{"kind": "javascript", "text": 'fetch("/api/orders")'}], "javascript_engine": "basic"})
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[2])["report"]["javascript_analysis"][0]["engine"], "basic")
        response = self.request("/api/demo", "POST", {"javascript_engine": "basic"})
        self.assertEqual(response[0], 200)
        self.assertEqual(json.loads(response[2])["report"]["javascript_analysis"][0]["engine"], "basic")

    @unittest.skipUnless(js_parser.available()[0], "Optional parser and supported Node required")
    def test_real_http_enhanced_unknown_method_and_source_imports(self):
        response = self.request("/api/analyze", "POST", {"artifacts": [{"kind": "typescript", "text": 'const path:string="/api/orders"; fetch(path,{method:chosen})'}], "javascript_engine": "enhanced"})
        self.assertEqual(response[0], 200)
        value = json.loads(response[2])
        self.assertEqual(value["report"]["routes"], [])
        self.assertEqual(value["report"]["unresolved_operations"][0]["method"], None)
        self.assertEqual(value["openapi"]["paths"], {})
        response = self.request("/api/source", "POST", {"artifacts": [{"kind": "javascript", "content": 'import("react"); function f(require){require("axios")};'}], "javascript_engine": "enhanced"})
        self.assertEqual(response[0], 200)
        self.assertEqual([row["name"] for row in json.loads(response[2])["inventory"]["dependencies"]], ["react"])

    def setUp(self):
        self.server = make_server(0)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = re.search(rb'name="surface-token" content="([a-f0-9]+)"', self.request("/")[2]).group(1).decode()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, method="GET", value=None, headers=None, raw=None):
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        body = raw if raw is not None else json.dumps(value).encode() if value is not None else None
        config = {"Origin": self.origin, "X-Surface-Token": self.token if hasattr(self, "token") else "", "Content-Type": "application/json"}
        config.update(headers or {})
        client.request(method, path, body, config)
        response = client.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        client.close()
        return result

    def test_second_server_cannot_share_an_active_listener_port(self):
        duplicate = None
        try:
            duplicate = make_server(self.port)
        except OSError:
            pass
        else:
            self.fail("An active listener port must be exclusive")
        finally:
            if duplicate is not None:
                duplicate.server_close()

    @unittest.skipUnless(hasattr(socket, "SO_EXCLUSIVEADDRUSE"), "Windows exclusive socket option")
    def test_windows_exclusive_listener_rejects_reuse_address_binding(self):
        self.assertEqual(self.server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
        with socket.socket() as contender:
            contender.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with self.assertRaises(OSError):
                contender.bind(("127.0.0.1", self.port))

    def test_demo_and_fixed_routes(self):
        result = self.request("/api/demo", "POST", {})
        self.assertEqual(result[0], 200)
        self.assertEqual(len(json.loads(result[2])["report"]["routes"]), 7)
        for path in ["/app.js", "/styles.css", "/health"]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 200)
        for path in ["/../server.py", "/%2e%2e/server.py", "/server.py", "/?private=value", "/api/demo"]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_public_analyze_response_and_exports_never_include_planted_values(self):
        capture = entry(f"https://{CANARY}.invalid/api/customers/{EMAIL}?token={CANARY}", body={"token": CANARY, "email": EMAIL})
        capture["request"]["headers"] = [{"name": "Authorization", "value": CANARY}]
        status, _, body = self.request("/api/analyze", "POST", {"artifacts": [har([capture])]})
        self.assertEqual(status, 200)
        self.assertNotIn(CANARY, body.decode())
        self.assertNotIn(EMAIL, body.decode())
        self.assertEqual(set(json.loads(body)), {"report", "contract", "openapi"})

    def test_error_response_does_not_echo_bad_source_or_filenames(self):
        for payload in [{"artifacts": [{"kind": "har", "text": '{"private":"' + CANARY + '"'}]}, {"artifacts": [{"kind": "zip", "text": CANARY, "filename": EMAIL}]}, {"artifacts": [har([entry(method=[])])]}]:
            status, _, body = self.request("/api/analyze", "POST", payload)
            self.assertEqual(status, 422)
            self.assertNotIn(CANARY, body.decode())
            self.assertNotIn(EMAIL, body.decode())

    def test_security_headers_and_no_cors(self):
        _, headers, _ = self.request("/")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertNotIn("Server", headers)

    def test_total_header_deadline_covers_request_line_and_header_drip(self):
        for prefix in [b"GET /", f"GET /health HTTP/1.0\r\nHost: 127.0.0.1:{self.port}\r\nX-Pad: ".encode()]:
            with self.subTest(prefix=prefix), patch("surface_map.server.HEADER_TIMEOUT", .2):
                with socket.create_connection(("127.0.0.1", self.port), timeout=1) as client:
                    started = time.monotonic()
                    client.sendall(prefix)
                    for _ in range(15):
                        try:
                            client.sendall(b"x")
                        except OSError:
                            break
                        time.sleep(.03)
                    try:
                        response = client.recv(4096)
                    except ConnectionError:
                        response = b""
                    self.assertEqual(response, b"")
                    self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.request("/health")[0], 200)

    def test_export_amplification_is_rejected_before_response_write(self):
        captures = [entry(status=status, body={"id": 42, "name": CANARY}) for status in range(200, 300)]
        with patch("surface_map.server.MAX_RESPONSE", 8192):
            status, _, body = self.request("/api/analyze", "POST", {"artifacts": [har(captures)]})
        self.assertEqual(status, 413)
        self.assertLess(len(body), 200)
        self.assertNotIn(CANARY, body.decode())

    def test_host_origin_and_csrf_are_required(self):
        for headers in [{"Host": "attacker.invalid"}, {"Origin": "https://attacker.invalid"}, {"Origin": "null"}, {"X-Surface-Token": ""}, {"X-Surface-Token": chr(233)}, {"Sec-Fetch-Site": "cross-site"}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/demo", "POST", {}, headers)[0], 403)

    def test_malformed_duplicate_host_header(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.putrequest("GET", "/health", skip_host=True)
        connection.putheader("Host", f"127.0.0.1:{self.port}")
        connection.putheader("Host", f"127.0.0.1:{self.port}")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        connection.close()

    def test_framing_content_type_and_unicode(self):
        for config in [{"Content-Length": str(MAX_REQUEST + 1)}, {"Content-Length": "-1"}, {"Transfer-Encoding": "chunked"}, {"Content-Type": "text/plain"}]:
            with self.subTest(config=config):
                self.assertEqual(self.request("/api/demo", "POST", {}, config)[0], 422)
        self.assertEqual(self.request("/api/analyze", "POST", raw=b"\xff")[0], 422)

    def test_comparison_has_real_added_and_absent_evidence(self):
        from surface_map.samples import artifacts
        status, _, body = self.request("/api/compare", "POST", {"baseline": artifacts(), "candidate": artifacts(2)})
        self.assertEqual(status, 200)
        diff = json.loads(body)["diff"]
        self.assertEqual(len(diff["added"]), 1)
        self.assertEqual(len(diff["removed"]), 1)
        self.assertEqual(len(diff["changed"]), 1)

    def test_bad_http_request_does_not_echo_path(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as client:
            client.sendall(f"INVALID BAD /{CANARY} HTTP/1.1\r\n\r\n".encode())
            response = b""
            while True:
                block = client.recv(4096)
                if not block:
                    break
                response += block
        self.assertNotIn(CANARY.encode(), response)
        self.assertIn(b"Invalid HTTP request", response)

    def test_busy_analysis_rejects_before_reading_another_body(self):
        held = []
        try:
            for _ in range(2):
                client = socket.create_connection(("127.0.0.1", self.port), timeout=3)
                held.append(client)
                headers = f"POST /api/analyze HTTP/1.0\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: {self.origin}\r\nX-Surface-Token: {self.token}\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{{"
                client.sendall(headers.encode())
            time.sleep(.1)
            started = time.monotonic()
            self.assertEqual(self.request("/api/demo", "POST", {})[0], 503)
            self.assertLess(time.monotonic() - started, 1)
        finally:
            for client in held:
                client.close()

    def test_connection_overload_is_visible_to_ordinary_get_and_post_clients(self):
        held = 0
        try:
            for _ in range(16):
                self.assertTrue(self.server.connections.acquire(timeout=1))
                held += 1
            document = {"artifacts": [{"kind": "javascript", "text": "const note='" + "x" * 8192 + "'; fetch('/api/items');"}]}
            self.assertEqual(len(json.dumps(document).encode()), 8278)
            for _ in range(8):
                status, headers, body = self.request("/health")
                self.assertEqual((status, body), (503, b""))
                self.assertEqual(headers["Connection"], "close")
            for _ in range(8):
                status, headers, body = self.request("/api/analyze", "POST", document)
                self.assertEqual((status, body), (503, b""))
                self.assertEqual(headers["Content-Length"], "0")
        finally:
            for _ in range(held):
                self.server.connections.release()
        self.assertEqual(self.request("/health")[0], 200)


class AdmissionDrainTests(unittest.TestCase):
    def setUp(self):
        self.server = make_server(0)
        self.addCleanup(self.server.server_close)
        self.server.connections = Mock()
        self.server.connections.acquire.return_value = False

    def test_busy_response_half_closes_then_drains_at_most_16_kib(self):
        request = Mock(spec=socket.socket)
        request.recv.return_value = b"x" * 4096
        events = Mock()
        events.attach_mock(request, "socket")
        with patch.object(self.server, "shutdown_request") as finish, patch("surface_map.server.time.monotonic", return_value=0):
            events.attach_mock(finish, "finish")
            self.server.process_request(request, ("127.0.0.1", 0))
            finish.assert_called_once_with(request)
        self.assertEqual(events.mock_calls[:3], [
            call.socket.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"),
            call.socket.shutdown(socket.SHUT_WR), call.socket.settimeout(0.05),
        ])
        self.assertEqual(request.recv.call_args_list, [call(4096)] * 4)
        self.assertEqual(events.mock_calls[-1], call.finish(request))
        self.server.connections.release.assert_not_called()

    def test_busy_drain_stops_at_the_deadline_with_a_fixed_read_timeout(self):
        request = Mock(spec=socket.socket)
        request.recv.return_value = b"x"
        with patch.object(self.server, "shutdown_request") as finish, patch("surface_map.server.time.monotonic", side_effect=[0, 0.01, 0.06, 0.11]):
            self.server.process_request(request, ("127.0.0.1", 0))
            finish.assert_called_once_with(request)
        self.assertEqual(request.recv.call_count, 2)
        request.settimeout.assert_called_once_with(0.05)

    def test_eof_and_socket_failure_still_finish_the_rejected_connection(self):
        for result in (b"", socket.timeout("idle")):
            with self.subTest(result=type(result).__name__):
                request = Mock(spec=socket.socket)
                request.recv.side_effect = [result]
                with patch.object(self.server, "shutdown_request") as finish, patch("surface_map.server.time.monotonic", return_value=0):
                    self.server.process_request(request, ("127.0.0.1", 0))
                    finish.assert_called_once_with(request)
                self.assertEqual(request.recv.call_count, 1)


if __name__ == "__main__":
    unittest.main()
