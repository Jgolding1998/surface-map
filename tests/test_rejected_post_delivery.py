"""Real socket coverage for early POST rejection and bounded unread-body discard."""
import http.client
import json
import re
import socket
import threading
import time
import unittest
from unittest.mock import patch

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

    def request(self, path="/api/rebuild", body=None, **overrides):
        data = body if body is not None else json.dumps({"private": CANARY}).encode()
        headers = {"Host": self.host, "Origin": self.origin, "X-Surface-Token": self.token,
                   "Content-Type": "application/json", **overrides}
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            client.request("POST", path, data, headers)
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            client.close()

    def assert_rejection(self, result, status=403):
        code, headers, body = result
        self.assertEqual(code, status)
        self.assertEqual(headers["Connection"], "close")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertNotIn(CANARY.encode(), body)
        self.assertIn("error", json.loads(body))

    def test_pending_small_body_receives_complete_forbidden_response(self):
        """Put body bytes in the socket after header parsing, before rejection write."""
        ready, release, discarded = threading.Event(), threading.Event(), threading.Event()
        original = self.server.RequestHandlerClass.reply
        original_setup = self.server.RequestHandlerClass.setup

        class CountDiscarded:
            def __init__(self, stream):
                self.stream = stream

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def read1(self, size):
                block = self.stream.read1(size)
                if block:
                    discarded.set()
                return block

        def setup(handler):
            original_setup(handler)
            handler.rfile = CountDiscarded(handler.rfile)

        def wait_to_reply(handler, code, value, *args, **kwargs):
            if handler.command == "POST":
                ready.set()
                if not release.wait(2):
                    raise AssertionError("Client did not release the rejection response")
            return original(handler, code, value, *args, **kwargs)

        body = json.dumps({"private": CANARY}).encode()
        headers = ("POST /api/rebuild HTTP/1.1\r\nHost: " + self.host + "\r\nOrigin: " + self.origin
                   + "\r\nX-Surface-Token: \r\nContent-Type: application/json\r\nContent-Length: "
                   + str(len(body)) + "\r\n\r\n").encode()
        with patch.object(self.server.RequestHandlerClass, "reply", wait_to_reply), \
                patch.object(self.server.RequestHandlerClass, "setup", setup), \
                patch.object(self.server.RequestHandlerClass, "body", side_effect=AssertionError("Unauthorized body parsed")) as parse, \
                patch.object(server_module, "analyze", side_effect=AssertionError("Unauthorized analysis")) as analyze:
            client = socket.create_connection(("127.0.0.1", self.port), timeout=3)
            try:
                client.sendall(headers)
                self.assertTrue(ready.wait(2))
                client.sendall(body)
                release.set()
                response = http.client.HTTPResponse(client)
                response.begin()
                self.assert_rejection((response.status, dict(response.getheaders()), response.read()))
                response.close()
                self.assertTrue(discarded.wait(0.5), "Rejected body remained unread at socket closure")
            finally:
                release.set()
                client.close()
            parse.assert_not_called()
            analyze.assert_not_called()

    def test_repeated_security_rejections_keep_complete_responses_and_never_parse(self):
        variants = ({"X-Surface-Token": ""}, {"Host": CANARY + ".invalid"},
                    {"Origin": "https://" + CANARY + ".invalid"}, {"Sec-Fetch-Site": "cross-site"})
        with patch.object(self.server.RequestHandlerClass, "body", side_effect=AssertionError("Unauthorized body parsed")) as parse, \
                patch.object(server_module, "analyze", side_effect=AssertionError("Unauthorized analysis")) as analyze:
            for iteration in range(8):
                for path in ("/api/rebuild", "/api/project", "/api/analyze"):
                    for headers in variants:
                        with self.subTest(iteration=iteration, path=path, headers=list(headers)):
                            self.assert_rejection(self.request(path, **headers))
            parse.assert_not_called()
            analyze.assert_not_called()

    def test_stalled_large_unauthorized_bodies_release_connection_workers(self):
        """Sixteen idle upload peers must not retain all workers for body deadlines."""
        headers = ("POST /api/analyze HTTP/1.1\r\nHost: " + self.host + "\r\nOrigin: " + self.origin
                   + "\r\nX-Surface-Token: \r\nContent-Type: application/json\r\nContent-Length: 9999999999\r\n\r\n").encode()
        clients = []
        start = time.monotonic()
        with patch.object(self.server.RequestHandlerClass, "body", side_effect=AssertionError("Unauthorized body parsed")) as parse, \
                patch.object(server_module, "analyze", side_effect=AssertionError("Unauthorized analysis")) as analyze:
            try:
                for _ in range(16):
                    client = socket.create_connection(("127.0.0.1", self.port), timeout=3)
                    clients.append(client)
                    client.sendall(headers)
                for client in clients:
                    response = http.client.HTTPResponse(client)
                    response.begin()
                    self.assert_rejection((response.status, dict(response.getheaders()), response.read()))
                    response.close()
                # Completion is observed through the actual admission semaphore,
                # not an arbitrary sleep or accepting a reset as success.
                acquired = []
                try:
                    for _ in range(16):
                        self.assertTrue(self.server.connections.acquire(timeout=0.5))
                        acquired.append(True)
                finally:
                    for _ in acquired:
                        self.server.connections.release()
                self.assertLess(time.monotonic() - start, 3.0)
                parse.assert_not_called()
                analyze.assert_not_called()
            finally:
                for client in clients:
                    client.close()

    def test_rejected_upload_discard_has_byte_bound_and_flushes_before_half_close(self):
        events, timeouts, reads = [], [], []

        class Connection:
            def shutdown(self, how):
                events.append(("shutdown", how))

            def settimeout(self, value):
                timeouts.append(value)

        class Writer:
            def flush(self):
                events.append("flush")

        class Reader:
            def read1(self, size):
                events.append("read")
                reads.append(size)
                return b"x" * size

        handler = object.__new__(self.server.RequestHandlerClass)
        handler.connection, handler.wfile, handler.rfile = Connection(), Writer(), Reader()
        handler.close_connection = False
        with patch.object(server_module.time, "monotonic", return_value=10.0):
            handler._finish_rejected_upload()
        self.assertEqual(events[:2], ["flush", ("shutdown", socket.SHUT_WR)])
        self.assertEqual(sum(reads), 16 * 1024)
        self.assertTrue(all(size <= 4096 for size in reads))
        self.assertTrue(timeouts and all(0 < value <= 0.05 for value in timeouts))
        self.assertTrue(handler.close_connection)

    def test_rejected_upload_discard_stops_at_deadline_and_tolerates_stalled_peer(self):
        from unittest.mock import Mock

        handler = object.__new__(self.server.RequestHandlerClass)
        handler.connection, handler.wfile, handler.rfile = Mock(), Mock(), Mock()
        handler.rfile.read1.return_value = b"x"
        handler.close_connection = False
        with patch.object(server_module.time, "monotonic", side_effect=[10.0, 10.01, 10.11]):
            handler._finish_rejected_upload()
        handler.rfile.read1.assert_called_once_with(4096)
        handler.connection.settimeout.assert_called_once_with(0.05)
        self.assertTrue(handler.close_connection)
        handler.rfile.read1.reset_mock()
        handler.rfile.read1.side_effect = TimeoutError()
        with patch.object(server_module.time, "monotonic", return_value=10.0):
            handler._finish_rejected_upload()
        handler.rfile.read1.assert_called_once_with(4096)

    def test_consumed_authorized_invalid_json_is_not_read_again(self):
        with patch.object(self.server.RequestHandlerClass, "_finish_rejected_upload") as discard:
            self.assert_rejection(self.request(body=("not-json " + CANARY).encode()), status=422)
            discard.assert_not_called()

    def test_authorized_unknown_route_is_rejected_before_body_parsing(self):
        with patch.object(self.server.RequestHandlerClass, "body", side_effect=AssertionError("Body parsed")) as parse:
            self.assert_rejection(self.request("/not-a-route"), status=404)
            parse.assert_not_called()

    def test_unread_pipelined_request_is_discarded_and_never_processed(self):
        first = ("POST /api/rebuild HTTP/1.1\r\nHost: " + self.host + "\r\nOrigin: " + self.origin
                 + "\r\nX-Surface-Token: \r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}").encode()
        second = ("POST /api/analyze HTTP/1.1\r\nHost: " + self.host + "\r\nOrigin: " + self.origin
                  + "\r\nX-Surface-Token: " + self.token
                  + "\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}").encode()
        with patch.object(self.server.RequestHandlerClass, "body", side_effect=AssertionError("Body parsed")) as parse, \
                patch.object(server_module, "analyze", side_effect=AssertionError("Followup analyzed")) as analyze:
            client = socket.create_connection(("127.0.0.1", self.port), timeout=3)
            try:
                client.sendall(first + second)
                response = http.client.HTTPResponse(client)
                response.begin()
                self.assert_rejection((response.status, dict(response.getheaders()), response.read()))
                response.close()
                self.assertEqual(client.recv(1), b"")
            finally:
                client.close()
            parse.assert_not_called()
            analyze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
