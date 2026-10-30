"""Public HTTPS capture tests use fake DNS, sockets and response bodies only."""

from contextlib import contextmanager
import io
import json
import os
import socket
import ssl
import threading
import time
import unittest
from unittest.mock import Mock, patch

from surface_map import url_capture as capture

CANARY = "PRIVATE" + "-URL-CANARY-68219"


def answer(address="1.1.1.1", family=socket.AF_INET):
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443))


class Response:
    def __init__(self, body=b"", *, status=200, content_type="text/html; charset=utf-8", headers=()):
        self.body = body.encode() if isinstance(body, str) else body
        self.status = status
        self.headers = [("Content-Type", content_type)] + list(headers) if content_type is not None else list(headers)
        self.offset = 0
        self.closed = False

    def getheaders(self):
        return self.headers

    def read1(self, size):
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class PublicCaptureTests(unittest.TestCase):
    def setUp(self):
        self.resolver = self.enterContext(patch.object(capture.socket, "getaddrinfo", return_value=[answer()]))
        self.calls = []
        self.responses = {}

        @contextmanager
        def opening(target, address, budget):
            self.calls.append((target.url, address))
            response = self.responses[target.url]
            if isinstance(response, Exception):
                raise response
            try:
                yield response
            finally:
                response.close()

        self.opening = self.enterContext(patch.object(capture, "_open_response", side_effect=opening))

    def page(self, text, url="https://example.com/"):
        self.responses[url] = Response(text)

    def assert_rejected(self, url, code=None):
        with self.assertRaises(capture.CaptureError) as caught:
            capture.capture_public_url(url)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertNotIn(CANARY, str(caught.exception))

    def test_rejects_non_public_or_ambiguous_urls_before_connection(self):
        for url in ("http://example.com/", "https://user:password@example.com/", "https://example.com:8443/",
                    "https://127.0.0.1/", "https://127.1/", "https://2130706433/", "https://0177.0.0.1/",
                    "https://169.254.169.254/", "https://100.64.0.1/", "https://168.63.129.16/",
                    "https://localhost/", "https://metadata.google.internal/", "https://device.local/",
                    "https://[::1]/", "https://[::ffff:8.8.8.8]/", "https://[64:ff9b::7f00:1]/",
                    "https://[2002:7f00:1::]/", "https://[2001::1]/", "https://[3fff::1]/",
                    "https://224.0.0.1/", "https://[ff0e::1]/", "https://[fe80::1%25eth0]/",
                    " https://example.com/", "https://example.com/\n" + CANARY,
                    "https://example.com\\@127.0.0.1/", "https://example.com../"):
            with self.subTest(url=url):
                self.assert_rejected(url)
        self.assertEqual(self.calls, [])
        self.resolver.assert_not_called()

    def test_all_dns_answers_are_checked_and_ipv4_is_preferred(self):
        self.page("<html></html>")
        self.resolver.return_value = [answer(), answer("127.0.0.1")]
        self.assert_rejected("https://example.com/", "blocked_target")
        self.assertEqual(self.calls, [])
        self.resolver.return_value = [answer("2606:4700:4700::1111", socket.AF_INET6), answer("8.8.8.8")]
        result = capture.capture_public_url("https://example.com/", False)
        self.assertEqual(self.calls[0][1], (socket.AF_INET, "8.8.8.8"))
        self.assertEqual(result["capture"]["fetched"], 1)

    def test_redirects_revalidate_dns_and_scripts_use_final_document_origin(self):
        self.responses["https://example.com/"] = Response(status=302, headers=[("Location", "https://www.example.com/pages/index")])
        self.page('<script src="../app.js"></script><script src="https://example.com/old.js"></script>', "https://www.example.com/pages/index")
        self.responses["https://www.example.com/app.js"] = Response("fetch('/api/orders');", content_type="text/javascript")
        self.resolver.side_effect = [[answer("1.1.1.1")], [answer("8.8.8.8")], [answer("9.9.9.9")]]
        result = capture.capture_public_url("https://example.com/")
        self.assertEqual(result["document_url"], "https://www.example.com/pages/index")
        self.assertEqual(len(result["artifacts"]), 2)
        self.assertEqual([address[1] for _, address in self.calls], ["1.1.1.1", "8.8.8.8", "9.9.9.9"])
        self.assertEqual(result["capture"]["redirects"], 1)
        self.assertEqual(result["capture"]["scripts_skipped"], 1)

    def test_private_redirect_and_rebinding_script_are_blocked(self):
        self.responses["https://example.com/"] = Response(status=302, headers=[("Location", "https://127.0.0.1/" + CANARY)])
        self.assert_rejected("https://example.com/", "blocked_target")
        self.assertEqual(len(self.calls), 1)
        self.calls.clear()
        self.page('<script src="/app.js"></script>')
        self.resolver.side_effect = [[answer()], [answer("10.0.0.1")]]
        result = capture.capture_public_url("https://example.com/")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["capture"]["scripts_fetched"], 0)
        self.assertIn(capture._WARNINGS["script_failed"], result["warnings"])

    def test_script_redirect_cannot_change_origin(self):
        self.page('<script src="/app.js"></script>')
        self.responses["https://example.com/app.js"] = Response(status=302, headers=[("Location", "https://other.example.com/app.js")])
        result = capture.capture_public_url("https://example.com/")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(result["capture"]["scripts_fetched"], 0)
        self.assertIn(capture._WARNINGS["script_failed"], result["warnings"])

    def test_base_scripts_document_order_selection_and_omissions(self):
        self.page('<base href="/assets/">\n<script src="one.js"></script>\n<script src="one.js"></script>'
                  '<script src="https://cdn.example.com/two.js"></script><script type="application/json" src="data.js"></script>'
                  '<script>fetch("/api/delete");</script><form action="/api/submit"></form><iframe src="/frame"></iframe>')
        self.responses["https://example.com/assets/one.js"] = Response("import('./later.js');", content_type="application/javascript")
        result = capture.capture_public_url("https://example.com/")
        self.assertEqual([url for url, _ in self.calls], ["https://example.com/", "https://example.com/assets/one.js"])
        self.assertEqual(result["capture"]["sources"][1]["location"], "line 2")
        self.assertEqual(result["capture"]["scripts_skipped"], 3)
        self.assertEqual(result["capture"]["inline_scripts_skipped"], 1)

    def test_html_url_attributes_trim_ascii_whitespace_before_resolution(self):
        self.page('<base href=" \t\n/assets/\r\f "><script src=" \t\none.js\r\f "></script>'
                  '<script src="one.js"></script><script src=" \t\n\f\r "></script>'
                  '<script src="bad\nname.js"></script><script src="\u00a0two.js\u00a0"></script>')
        self.responses["https://example.com/assets/one.js"] = Response("// first", content_type="text/javascript")
        self.responses["https://example.com/assets/%C2%A0two.js%C2%A0"] = Response("// second", content_type="text/javascript")
        result = capture.capture_public_url("https://example.com/")
        self.assertEqual([url for url, _ in self.calls], ["https://example.com/", "https://example.com/assets/one.js",
                                                       "https://example.com/assets/%C2%A0two.js%C2%A0"])
        self.assertEqual(result["capture"]["scripts_skipped"], 3)
        self.assertNotIn(capture._WARNINGS["base"], result["warnings"])

    def test_redirect_limit_does_not_skip_later_direct_scripts(self):
        self.page('<script src="/redirect0.js"></script><script src="/direct.js"></script>')
        for number in range(4):
            self.responses[f"https://example.com/redirect{number}.js"] = Response(
                status=302, headers=[("Location", f"/redirect{number + 1}.js")])
        self.responses["https://example.com/direct.js"] = Response("// direct", content_type="text/javascript")
        result = capture.capture_public_url("https://example.com/")
        self.assertEqual([url for url, _ in self.calls], ["https://example.com/"] +
                         [f"https://example.com/redirect{number}.js" for number in range(4)] + ["https://example.com/direct.js"])
        self.assertEqual(result["capture"]["redirects"], 3)
        self.assertEqual(result["capture"]["network_requests"], 6)
        self.assertEqual(result["capture"]["scripts_fetched"], 1)
        self.assertEqual(result["capture"]["scripts_skipped"], 1)
        self.assertIn(capture._WARNINGS["script_failed"], result["warnings"])
        self.assertNotIn(capture._WARNINGS["deadline"], result["warnings"])

    def test_displayed_code_and_template_content_never_select_script_or_base(self):
        self.page('<title><base href="https://other.example.com/"><script src="/fake1.js"></script></title>'
                  '<textarea><script src="/fake2.js"></script></textarea>'
                  '<template><base href="/wrong/"><template><script src="/fake3.js"></script></template></template>'
                  '<script src="/real.js"></script>')
        self.responses["https://example.com/real.js"] = Response("// real", content_type="text/javascript")
        result = capture.capture_public_url("https://example.com/")
        self.assertEqual(result["capture"]["external_scripts_seen"], 1)
        self.assertEqual([url for url, _ in self.calls], ["https://example.com/", "https://example.com/real.js"])

    def test_partial_script_failure_returns_fixed_warnings_and_generic_metadata(self):
        url = "https://example.com/?secret=" + CANARY
        self.page('<script src="/good.js"></script><script src="/bad.js"></script>', url)
        self.responses["https://example.com/good.js"] = Response("// " + CANARY, content_type="text/javascript")
        self.responses["https://example.com/bad.js"] = Response("private", status=403)
        result = capture.capture_public_url(url)
        self.assertEqual(result["capture"]["scripts_requested"], 2)
        self.assertEqual(result["capture"]["scripts_fetched"], 1)
        self.assertNotIn(CANARY, json.dumps({key: result[key] for key in ("capture", "warnings")}))
        self.assertEqual(result["capture"]["sources"][1]["id"], "source_2")
        self.assertIn(CANARY, result["document_url"])  # Internal analysis context only.
        self.assertIn(CANARY, result["artifacts"][1]["text"])

    def test_mime_encoding_duplicate_headers_and_utf8_are_strict(self):
        cases = [Response("html", content_type="text/plain"), Response("html", content_type=None),
                 Response("html", headers=[("Content-Encoding", "gzip")]),
                 Response("html", content_type="text/html; charset=iso-8859-1"),
                 Response(b"\xff", content_type="text/html"),
                 Response("html", headers=[("Content-Type", "text/html")]),
                 Response("html", headers=[("Content-Length", "4"), ("Content-Length", "4")]),
                 Response("html", headers=[("Content-Length", "4"), ("Transfer-Encoding", "chunked")]),
                 Response("html", headers=[("Content-Length", "100")]),
                 Response('<meta charset="shift_jis">'),
                 Response('<?xml version="1.0" encoding="iso-8859-1"?><html/>')]
        for response in cases:
            self.responses["https://example.com/"] = response
            self.assert_rejected("https://example.com/")

    def test_body_and_total_byte_limits_do_not_depend_on_content_length(self):
        self.page("x" * 21)
        with patch.object(capture, "MAX_HTML_BYTES", 20):
            self.assert_rejected("https://example.com/", "too_large")
        html = '<script src="/one.js"></script><script src="/two.js"></script>'
        self.page(html)
        self.responses["https://example.com/one.js"] = Response("a" * 6, content_type="text/javascript")
        self.responses["https://example.com/two.js"] = Response("b" * 6, content_type="text/javascript")
        with patch.object(capture, "MAX_TOTAL_BYTES", len(html) + 10):
            result = capture.capture_public_url("https://example.com/")
        self.assertEqual(result["capture"]["scripts_fetched"], 1)
        self.assertIn(capture._WARNINGS["script_failed"], result["warnings"])

    def test_script_and_redirect_request_budgets_are_finite(self):
        self.page("".join(f'<script src="/{n}.js"></script>' for n in range(8)))
        for number in range(8):
            self.responses[f"https://example.com/{number}.js"] = Response("// script", content_type="text/javascript")
        result = capture.capture_public_url("https://example.com/")
        self.assertEqual((len(self.calls), len(result["artifacts"])), (6, 6))
        self.assertEqual(result["capture"]["scripts_skipped"], 3)
        self.calls.clear()
        for number in range(5):
            self.responses[f"https://example.com/{number}"] = Response(status=302, headers=[("Location", f"/{number + 1}")])
        self.assert_rejected("https://example.com/0", "redirect_limit")
        self.assertEqual(len(self.calls), 4)

    def test_scripts_can_be_disabled_and_failures_never_echo_exceptions(self):
        self.page('<script src="/app.js"></script>')
        result = capture.capture_public_url("https://example.com/", False)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["capture"]["scripts_skipped"], 1)
        self.responses["https://example.com/"] = OSError(CANARY)
        self.assert_rejected("https://example.com/", "network")

    def test_cooperative_deadline_checked_between_body_reads(self):
        class SlowResponse(Response):
            def read1(self, size):
                time.sleep(0.025)
                return b"x"
        self.responses["https://example.com/"] = SlowResponse()
        with patch.object(capture, "CAPTURE_SECONDS", 0.04):
            self.assert_rejected("https://example.com/", "timeout")


class TransportBoundaryTests(unittest.TestCase):
    def test_tls_failures_are_sanitized_and_deadline_takes_precedence(self):
        for expired, expected in ((False, "tls_error"), (True, "timeout")):
            connection = Mock()
            connection.expired = expired
            connection.getresponse.side_effect = ssl.SSLError(CANARY)
            with patch.object(capture, "_PinnedHTTPSConnection", return_value=connection):
                with self.assertRaises(capture.CaptureError) as caught:
                    with capture._open_response(capture._target("https://example.com/"), (socket.AF_INET, "1.1.1.1"), capture._Budget()):
                        self.fail("TLS errors must fail closed")
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn(CANARY, str(caught.exception))

    def test_pinned_numeric_socket_preserves_original_tls_name_and_host_header(self):
        raw = Mock()
        secure = Mock()
        secure.makefile.return_value = io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        context = Mock()
        context.wrap_socket.return_value = secure
        with patch.object(capture.socket, "getaddrinfo", return_value=[answer("8.8.8.8")]) as resolver, \
             patch.object(capture.socket, "socket", return_value=raw), \
             patch.object(capture.ssl, "create_default_context", return_value=context), \
             patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:9999", "HTTP_PROXY": "http://127.0.0.1:9999"}):
            result = capture.capture_public_url("https://example.com/", False)
        self.assertEqual(result["capture"]["fetched"], 1)
        raw.connect.assert_called_once_with(("8.8.8.8", 443))
        context.wrap_socket.assert_called_once_with(raw, server_hostname="example.com", do_handshake_on_connect=False)
        secure.do_handshake.assert_called_once()
        resolver.assert_called_once()
        request = b"".join(call.args[0] for call in secure.sendall.call_args_list)
        self.assertIn(b"Host: example.com\r\n", request)
        self.assertIn(b"Accept-Encoding: identity\r\n", request)
        self.assertNotIn(b"Authorization:", request)
        self.assertNotIn(b"Cookie:", request)
        self.assertNotIn(b"127.0.0.1", request)

    def test_deadline_watchdog_interrupts_stalled_headers(self):
        released = threading.Event()

        class StalledConnection:
            def __init__(self, *args):
                self.expired = False
            def request(self, *args, **kwargs):
                pass
            def getresponse(self):
                released.wait(1)
                raise OSError(CANARY)
            def abort(self):
                self.expired = True
                released.set()
            def close(self):
                pass

        with patch.object(capture, "CAPTURE_SECONDS", 0.025), patch.object(capture, "_PinnedHTTPSConnection", StalledConnection):
            budget = capture._Budget()
            start = time.monotonic()
            with self.assertRaises(capture.CaptureError) as caught:
                with capture._open_response(capture._target("https://example.com/"), (socket.AF_INET, "1.1.1.1"), budget):
                    self.fail("stalled headers must fail")
            self.assertEqual(caught.exception.code, "timeout")
            self.assertNotIn(CANARY, str(caught.exception))
            self.assertLess(time.monotonic() - start, 0.5)

    def test_stalled_dns_keeps_single_slot_until_lookup_really_finishes(self):
        release = threading.Event()
        threads = []
        original_thread = threading.Thread

        def make_thread(*args, **kwargs):
            thread = original_thread(*args, **kwargs)
            threads.append(thread)
            return thread

        def stalled(*args, **kwargs):
            release.wait(1)
            return [answer()]

        with patch.object(capture, "_DNS_SLOT", threading.BoundedSemaphore(1)), \
             patch.object(capture, "DNS_SECONDS", 0.01), patch.object(capture.socket, "getaddrinfo", side_effect=stalled) as resolver, \
             patch.object(capture.threading, "Thread", side_effect=make_thread):
            try:
                with self.assertRaises(capture.CaptureError) as first:
                    capture._resolve("example.com", capture._Budget())
                self.assertEqual(first.exception.code, "dns_timeout")
                with self.assertRaises(capture.CaptureError) as second:
                    capture._resolve("other.example.com", capture._Budget())
                self.assertEqual(second.exception.code, "dns_busy")
                self.assertEqual(resolver.call_count, 1)
                self.assertTrue(threads[0].daemon)
            finally:
                release.set()
                for thread in threads:
                    thread.join(1)
            self.assertFalse(threads[0].is_alive())
            self.assertEqual(capture._resolve("example.com", capture._Budget()), [(socket.AF_INET, "1.1.1.1")])


if __name__ == "__main__":
    unittest.main()
