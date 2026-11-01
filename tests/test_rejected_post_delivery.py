"""Complete rejection delivery to delayed upload peers, using real sockets."""
import http.client
import json
import re
import select
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch

from surface_map import server as server_module


CANARY = "PRIVATE_REJECTED_POST_CANARY_9284"


class RejectedPostDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = server_module.make_server(0)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.port = cls.server.server_address[1]
        cls.host = "127.0.0.1:" + str(cls.port)
        cls.origin = "http://" + cls.host
        client = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=3)
        try:
            client.request("GET", "/")
            cls.token = re.search(rb'"([0-9a-f]{64})"', client.getresponse().read()).group(1).decode()
        finally:
            client.close()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(2)

    @staticmethod
    def _body():
        return json.dumps({"private": CANARY}).encode()

    def _headers(self, method="POST", path="/api/rebuild", *, overrides=None, length=None, extra=()):
        headers = {"Host": self.host, "Origin": self.origin, "X-Surface-Token": self.token,
                   "Content-Type": "application/json", "Content-Length": str(length if length is not None else len(self._body()))}
        for key, value in (overrides or {}).items():
            if value is None:
                headers.pop(key, None)
            else:
                headers[key] = value
        lines = [method + " " + path + " HTTP/1.1", *(key + ": " + value for key, value in headers.items()), *extra]
        return ("\r\n".join(lines) + "\r\n\r\n").encode()

    def _late_request(self, method="POST", path="/api/rebuild", *, overrides=None, length=None, extra=(), gap=0.025):
        """Keep the arrived response unread so a late-body RST cannot be hidden."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as client:
            client.sendall(self._headers(method, path, overrides=overrides, length=length, extra=extra))
            self.assertTrue(select.select([client], [], [], 2)[0], "No rejection arrived")
            time.sleep(gap)
            client.sendall(self._body())
            time.sleep(0.02)
            chunks, size = [], 0
            while True:
                block = client.recv(4096)
                if not block:
                    break
                size += len(block)
                self.assertLessEqual(size, 65536)
                chunks.append(block)
        response = b"".join(chunks)
        head, separator, body = response.partition(b"\r\n\r\n")
        self.assertEqual(separator, b"\r\n\r\n")
        lines = head.split(b"\r\n")
        headers = dict(line.decode("ascii").split(": ", 1) for line in lines[1:])
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertEqual(headers["Connection"], "close")
        self.assertNotIn(CANARY.encode(), response)
        return lines[0], body

    def _assert_json(self, response, status, reason, error):
        line, body = response
        self.assertEqual(line, ("HTTP/1.0 " + str(status) + " " + reason).encode())
        self.assertEqual(json.loads(body), {"error": error})

    def _health(self):
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            client.request("GET", "/health")
            response = client.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), {"ok": True})
        finally:
            client.close()

    def test_delayed_security_rejections_are_complete_and_never_parse(self):
        variants = (({"X-Surface-Token": ""}, "Missing or invalid request token"),
                    ({"X-Surface-Token": "invalid"}, "Missing or invalid request token"),
                    ({"Host": CANARY + ".invalid"}, "Unrecognized host"),
                    ({"Origin": "https://" + CANARY + ".invalid"}, "Cross-origin request rejected"),
                    ({"Origin": None}, "Cross-origin request rejected"),
                    ({"Sec-Fetch-Site": "cross-site"}, "Cross-origin request rejected"))
        with patch.object(self.server.RequestHandlerClass, "body", side_effect=AssertionError("Unauthorized body parsed")) as parse, \
                patch.object(server_module, "analyze", side_effect=AssertionError("Unauthorized analysis")) as analyze:
            for iteration in range(2):
                for overrides, error in variants:
                    with self.subTest(iteration=iteration, guard=list(overrides)):
                        self._assert_json(self._late_request(overrides=overrides), 403, "Forbidden", error)
            parse.assert_not_called()
            analyze.assert_not_called()

    def test_delayed_put_and_get_bodies_keep_exact_error_responses(self):
        with patch.object(self.server.RequestHandlerClass, "body", side_effect=AssertionError("Rejected body parsed")) as parse:
            for iteration in range(3):
                with self.subTest(iteration=iteration, method="PUT"):
                    self._assert_json(self._late_request("PUT"), 405, "Method Not Allowed", "Method unsupported")
                with self.subTest(iteration=iteration, method="GET"):
                    self._assert_json(self._late_request("GET", "/" + CANARY), 404, "Not Found", "Not found")
            parse.assert_not_called()

    def test_delayed_invalid_framing_and_unknown_path_keep_rejection(self):
        with patch.object(server_module, "strict_json", side_effect=AssertionError("Rejected JSON decoded")) as decode, \
                patch.object(server_module, "analyze", side_effect=AssertionError("Rejected analysis")) as analyze:
            self._assert_json(self._late_request(length=server_module.MAX_REQUEST + 1), 422, http.client.responses[422],
                              "Selection rejected: unsupported, malformed or over limit. No input text or paths are returned.")
            self._assert_json(self._late_request(path="/unknown"), 404, "Not Found", "Not found")
            self._assert_json(self._late_request(extra=("Host: " + self.host,)), 400, "Bad Request", "Ambiguous HTTP headers")
            decode.assert_not_called()
            analyze.assert_not_called()

    def test_delayed_admission_rejection_uses_the_same_close_path(self):
        with patch.object(self.server, "connections", threading.BoundedSemaphore(0)), \
                patch.object(self.server.RequestHandlerClass, "body", side_effect=AssertionError("Unadmitted body parsed")) as parse:
            line, body = self._late_request()
            self.assertEqual(line, b"HTTP/1.0 503 Service Unavailable")
            self.assertEqual(body, b"")
            parse.assert_not_called()

    def test_slow_sender_cannot_extend_connection_past_linger_budget(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as client:
            client.sendall(self._headers("PUT", length=9999999999))
            response = http.client.HTTPResponse(client)
            response.begin()
            self.assertEqual(response.status, 405)
            self.assertEqual(json.loads(response.read()), {"error": "Method unsupported"})
            start, stopped = time.monotonic(), False
            while time.monotonic() - start < 1:
                try:
                    client.sendall(b"x")
                except OSError:
                    stopped = True
                    break
                time.sleep(0.02)
            response.close()
            self.assertTrue(stopped, "Trickling input kept the closed connection writable for a second")
            self.assertLess(time.monotonic() - start, 1)
        self._health()

    def test_larger_than_discard_cap_cannot_wedge_workers_or_invoke_analysis(self):
        with patch.object(self.server.RequestHandlerClass, "body", side_effect=AssertionError("Unauthorized body parsed")) as parse, \
                patch.object(server_module, "analyze", side_effect=AssertionError("Unauthorized analysis")) as analyze:
            start = time.monotonic()
            for _ in range(4):
                with socket.create_connection(("127.0.0.1", self.port), timeout=2) as client:
                    client.sendall(self._headers(overrides={"X-Surface-Token": ""}, length=65536))
                    self.assertTrue(select.select([client], [], [], 2)[0])
                    time.sleep(0.02)
                    try:
                        client.sendall(b"x" * 65536)
                    except OSError:
                        # Above the documented cap delivery is not guaranteed.
                        # Assert health, never accept a reset as a delivered 403.
                        pass
            self._health()
            self.assertLess(time.monotonic() - start, 2)
            parse.assert_not_called()
            analyze.assert_not_called()

    def test_sixteen_stalled_uploads_release_workers_within_the_fixed_budget(self):
        clients = []
        try:
            for _ in range(16):
                client = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                clients.append(client)
                client.sendall(self._headers(overrides={"X-Surface-Token": ""}, length=9999999999))
            for client in clients:
                response = http.client.HTTPResponse(client)
                response.begin()
                self.assertEqual(response.status, 403)
                self.assertNotIn(CANARY.encode(), response.read())
                response.close()
            acquired = []
            start = time.monotonic()
            try:
                for _ in range(16):
                    self.assertTrue(self.server.connections.acquire(timeout=0.5))
                    acquired.append(True)
            finally:
                for _ in acquired:
                    self.server.connections.release()
            self.assertLess(time.monotonic() - start, 1)
        finally:
            for client in clients:
                client.close()

    def test_pipelined_request_is_never_processed(self):
        first = self._headers(overrides={"X-Surface-Token": ""}, length=2) + b"{}"
        second = self._headers(path="/api/analyze", length=2) + b"{}"
        with patch.object(self.server.RequestHandlerClass, "body", side_effect=AssertionError("Body parsed")) as parse, \
                patch.object(server_module, "analyze", side_effect=AssertionError("Followup analyzed")) as analyze:
            with socket.create_connection(("127.0.0.1", self.port), timeout=3) as client:
                client.sendall(first + second)
                response = http.client.HTTPResponse(client)
                response.begin()
                self.assertEqual(response.status, 403)
                self.assertEqual(json.loads(response.read()), {"error": "Missing or invalid request token"})
                response.close()
                self.assertEqual(client.recv(1), b"")
            parse.assert_not_called()
            analyze.assert_not_called()


class RawDiscardBoundsTests(unittest.TestCase):
    def test_discard_half_closes_then_reads_only_16_kib(self):
        request = Mock()
        request.recv.side_effect = lambda size: b"x" * size
        with patch.object(server_module.time, "monotonic", return_value=0):
            server_module._linger_discard(request)
        request.shutdown.assert_called_once_with(socket.SHUT_WR)
        self.assertEqual(sum(call.args[0] for call in request.recv.call_args_list), 16 * 1024)
        self.assertTrue(all(call.args[0] <= 4096 for call in request.recv.call_args_list))
        self.assertTrue(all(call.args[0] == 0.1 for call in request.settimeout.call_args_list))
        self.assertEqual(request.method_calls[0].args, (socket.SHUT_WR,))

    def test_discard_timeout_tracks_remaining_total_budget(self):
        request, elapsed = Mock(), [0.0]

        def receive(size):
            elapsed[0] += 0.04
            return b"x"

        request.recv.side_effect = receive
        with patch.object(server_module.time, "monotonic", side_effect=lambda: elapsed[0]):
            server_module._linger_discard(request)
        self.assertEqual(request.recv.call_count, 3)
        self.assertEqual(len(request.settimeout.call_args_list), 3)
        for actual, expected in zip(request.settimeout.call_args_list, (0.1, 0.06, 0.02)):
            self.assertAlmostEqual(actual.args[0], expected)

    def test_discard_does_not_retry_eof_timeout_or_socket_errors(self):
        for failure in (b"", TimeoutError(), ConnectionResetError()):
            with self.subTest(failure=type(failure).__name__):
                request = Mock()
                if isinstance(failure, bytes):
                    request.recv.return_value = failure
                else:
                    request.recv.side_effect = failure
                with patch.object(server_module.time, "monotonic", return_value=0):
                    server_module._linger_discard(request)
                request.recv.assert_called_once_with(4096)
        request = Mock()
        request.shutdown.side_effect = OSError()
        server_module._linger_discard(request)
        request.recv.assert_not_called()


if __name__ == "__main__":
    unittest.main()
