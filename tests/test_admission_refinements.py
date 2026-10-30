"""Regression tests for shared analysis deadlines, malformed markup and the HTTP envelope.

All inputs are synthetic. Nothing here contacts a network service.
"""
import http.client
import json
import re
import threading
import time
import unittest
from unittest.mock import patch

from surface_map import analyze as analysis
from surface_map import js_parser, server
from surface_map.analyze import ArtifactError, HTML, SEGMENTS, Builder, analyze, strict_json

CANARY = "CANARYMARKEDSECTION7Q"
WORDS = sorted(SEGMENTS)[:21]
PAIRS = [f"/{first}/{second}" for first in WORDS for second in WORDS]


class SharedDeadlineTests(unittest.TestCase):
    def test_expired_shared_deadline_rejects_before_any_artifact_is_parsed(self):
        with patch.object(analysis, "HTML", wraps=HTML) as parser:
            with self.assertRaises(ArtifactError) as error:
                analyze([{"kind": "html", "text": '<a href="/api/users">People</a>'}], deadline=time.monotonic() - 1)
        self.assertIn("shared request deadline", str(error.exception))
        parser.assert_not_called()

    def test_html_parser_budget_never_exceeds_the_shared_deadline(self):
        soon = time.monotonic() + 0.25
        self.assertEqual(HTML("source_1", Builder("basic"), deadline=soon).deadline, soon)
        started = time.monotonic()
        later = HTML("source_1", Builder("basic"), deadline=started + 60).deadline
        self.assertLessEqual(later, time.monotonic() + analysis.HTML_WORK_SECONDS)

    def test_deadline_is_checked_after_each_artifact(self):
        clock = [0.0]

        def slow_har(text, source, builder):
            clock[0] = 20.0

        artifacts = [{"kind": "har", "text": "{}"}, {"kind": "html", "text": '<a href="/api/users">People</a>'}]
        with patch.object(analysis.time, "monotonic", side_effect=lambda: clock[0]), patch.object(analysis, "parse_har", side_effect=slow_har), \
                patch.object(analysis, "HTML", wraps=HTML) as parser:
            with self.assertRaises(ArtifactError) as error:
                analyze(artifacts, deadline=10.0)
        self.assertTrue(str(error.exception).startswith("source_1: "))
        self.assertIn("shared request deadline", str(error.exception))
        parser.assert_not_called()

    def test_one_deadline_spans_both_comparison_sides(self):
        clock = [0.0]
        page = [{"kind": "html", "text": '<a href="/api/users">People</a>'}]
        with patch.object(analysis.time, "monotonic", side_effect=lambda: clock[0]):
            analyze(page, javascript_engine="basic", deadline=10.0)
            clock[0] = 10.0
            with self.assertRaises(ArtifactError):
                analyze(page, javascript_engine="basic", deadline=10.0)

    def test_html_deadline_expiring_inside_the_parser_rejects_the_artifact(self):
        clock = [0.0]
        original = HTML.handle_starttag

        def advancing(parser, tag, attributes):
            clock[0] += 1.0
            return original(parser, tag, attributes)

        page = "".join(f'<a href="{path}">x</a>' for path in PAIRS[:20])
        with patch.object(analysis.time, "monotonic", side_effect=lambda: clock[0]), patch.object(HTML, "handle_starttag", advancing):
            with self.assertRaises(ArtifactError) as error:
                analyze([{"kind": "html", "text": page}], javascript_engine="basic", deadline=5.0)
        self.assertIn("deadline", str(error.exception))


class MalformedMarkupTests(unittest.TestCase):
    def test_parser_assertions_become_fixed_errors_without_source_text(self):
        for method in ("feed", "close"):
            with self.subTest(method=method), patch.object(HTML, method, side_effect=AssertionError(f"unknown status keyword {CANARY!r}")):
                with self.assertRaises(ArtifactError) as error:
                    analyze([{"kind": "html", "text": "<p>ok</p>"}])
            self.assertEqual(str(error.exception), "source_1: Malformed or unsupported HTML markup")

    def test_real_marked_sections_parse_or_fail_without_reflection(self):
        # Older parsers raise AssertionError for these; newer parsers treat them as bogus comments.
        for markup in (f"<![{CANARY}[ hidden ]]>", f"<![ {CANARY}", f"<!{CANARY}[", "<![if !supportLists]><![endif]>"):
            page = markup + '<a href="/api/users">People</a>'
            with self.subTest(markup=markup.replace(CANARY, "canary")):
                try:
                    report = analyze([{"kind": "html", "text": page}], javascript_engine="basic")
                except ArtifactError as error:
                    self.assertNotIn(CANARY, str(error))
                else:
                    self.assertNotIn(CANARY, json.dumps(report))


class NumericCapTests(unittest.TestCase):
    def test_route_cap_is_exact_and_names_the_limit(self):
        self.assertGreater(len(PAIRS), analysis.MAX_ROUTES)
        links = lambda count: "".join(f'<a href="{path}">x</a>' for path in PAIRS[:count])
        self.assertEqual(len(analyze([{"kind": "html", "text": links(analysis.MAX_ROUTES)}], javascript_engine="basic")["routes"]), analysis.MAX_ROUTES)
        with self.assertRaises(ArtifactError) as error:
            analyze([{"kind": "html", "text": links(analysis.MAX_ROUTES + 1)}], javascript_engine="basic")
        self.assertIn(f"{analysis.MAX_ROUTES}-route limit", str(error.exception))

    def test_asset_cap_is_exact_and_names_the_limit(self):
        # Paths use only literal segment words so normalization keeps every asset distinct.
        scripts = lambda count: "".join(f'<script src="{path}"></script>' for path in PAIRS[:count])
        self.assertEqual(len(analyze([{"kind": "html", "text": scripts(200)}], javascript_engine="basic")["assets"]), 200)
        with self.assertRaises(ArtifactError) as error:
            analyze([{"kind": "html", "text": scripts(201)}], javascript_engine="basic")
        self.assertIn("200-asset limit", str(error.exception))


class EnvelopeTests(unittest.TestCase):
    def test_wide_envelope_stops_before_the_object_decoder_allocates_containers(self):
        text = '[' + ','.join(['{}'] * 10000) + ']'
        with patch.object(analysis.json, 'loads', side_effect=AssertionError('Decoder must stay idle')) as decoder:
            with self.assertRaises(ArtifactError):
                strict_json(text, depth=6, nodes=100, prescan=False)
        decoder.assert_not_called()

    def test_large_escaped_string_is_one_value_in_the_envelope_guard(self):
        value = ('\\"[]{}:,\n' * 160000)
        document = json.dumps({'artifacts':[{'kind':'html','text':value}]})
        self.assertEqual(strict_json(document, depth=6, nodes=100, prescan=False)['artifacts'][0]['text'], value)

    def test_envelope_structure_is_checked_without_the_character_prescan(self):
        cases = {
            "deep": '{"artifacts":' + "[" * 7 + "]" * 7 + "}",
            "very deep": '{"artifacts":' + "[" * 100000 + "]" * 100000 + "}",
            "duplicate": '{"artifacts":[],"artifacts":[]}',
            "nan": '{"artifacts":[],"javascript_engine":NaN}',
            "infinity": '{"artifacts":[-Infinity]}',
            "overflow": '{"artifacts":[1e999]}',
            "nodes": '{"artifacts":[' + ",".join(["0"] * 120) + "]}",
            "wide": '{"artifacts":[' + ",".join(["0"] * 1001) + "]}",
        }
        for name, text in cases.items():
            with self.subTest(name=name), self.assertRaises(ArtifactError):
                strict_json(text, depth=6, nodes=100, prescan=False)
        self.assertEqual(strict_json('{"artifacts":[{"kind":"html","text":"[[[[[[[[[[["}]}', depth=6, nodes=100, prescan=False),
                         {"artifacts": [{"kind": "html", "text": "[" * 11}]})


class HTTPRefinementTests(unittest.TestCase):
    def setUp(self):
        self.server = server.make_server(0)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = re.search(rb'name="surface-token" content="([a-f0-9]+)"', self.request("/")[1]).group(1).decode()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, method="GET", value=None, raw=None):
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = raw.encode() if raw is not None else json.dumps(value).encode() if value is not None else None
        headers = {"Origin": self.origin, "X-Surface-Token": getattr(self, "token", ""), "Content-Type": "application/json"}
        try:
            client.request(method, path, body, headers)
            response = client.getresponse()
            return response.status, response.read()
        finally:
            client.close()

    def test_limits_publish_a_browser_timeout_that_covers_the_server_budgets(self):
        status, body = self.request("/api/limits")
        self.assertEqual(status, 200)
        limits = json.loads(body)
        upload_seconds = 8
        worst_single_artifact = max(analysis.HTML_WORK_SECONDS, analysis.JS_WORK_SECONDS, js_parser.TIMEOUT + 3.0)
        self.assertGreater(limits["request_timeout_ms"], (upload_seconds + server.ANALYSIS_BUDGET + worst_single_artifact) * 1000)
        self.assertGreaterEqual(limits["request_bytes"], 2 * 4 * ((limits["web"]["total_bytes"] + 2) // 3))
        self.assertLessEqual(limits["web"]["html_bytes"], limits["web"]["total_bytes"])

    def test_envelope_rejections_use_fixed_messages_and_no_prescan(self):
        bodies = ['{"artifacts":' + "[" * 7 + "]" * 7 + "}", '{"artifacts":' + "[" * 50000 + "]" * 50000 + "}",
                  '{"artifacts":[],"artifacts":[]}', '{"artifacts":[],"javascript_engine":NaN}', '{"artifacts":[1e999]}']
        with patch.object(server, "strict_json", wraps=strict_json) as checked:
            for raw in bodies:
                with self.subTest(size=len(raw)):
                    status, body = self.request("/api/analyze", "POST", raw=raw)
                    self.assertEqual(status, 422)
                    self.assertIn("JSON", json.loads(body)["error"])
        self.assertTrue(checked.call_args_list)
        self.assertTrue(all(call.kwargs.get("prescan") is False for call in checked.call_args_list))

    def test_compare_shares_one_deadline_and_reports_expiry(self):
        page = [{"kind": "html", "text": '<a href="/api/users">People</a>'}]
        with patch.object(server, "analyze", wraps=analyze) as wrapped:
            status, _ = self.request("/api/compare", "POST", {"baseline": page, "candidate": page, "javascript_engine": "basic"})
        self.assertEqual(status, 200)
        deadlines = [call.kwargs["deadline"] for call in wrapped.call_args_list]
        self.assertEqual(len(deadlines), 2)
        self.assertEqual(deadlines[0], deadlines[1])
        with patch.object(server, "ANALYSIS_BUDGET", -1.0):
            status, body = self.request("/api/compare", "POST", {"baseline": page, "candidate": page, "javascript_engine": "basic"})
            self.assertEqual(status, 422)
            self.assertIn("shared request deadline", json.loads(body)["error"])
            self.assertEqual(self.request("/api/demo", "POST", {"javascript_engine": "basic"})[0], 200)

    def test_malformed_markup_over_http_is_controlled_and_not_reflected(self):
        with patch.object(HTML, "feed", side_effect=AssertionError(f"unknown status keyword {CANARY!r}")):
            status, body = self.request("/api/analyze", "POST", {"artifacts": [{"kind": "html", "text": "<p>x</p>"}], "javascript_engine": "basic"})
        self.assertEqual(status, 422)
        self.assertNotIn(CANARY.encode(), body)
        status, body = self.request("/api/analyze", "POST", {"artifacts": [{"kind": "html", "text": f"<![{CANARY}[ x ]]><a href=\"/api/users\">x</a>"}], "javascript_engine": "basic"})
        self.assertIn(status, (200, 422))
        self.assertNotIn(CANARY.encode(), body)


if __name__ == "__main__":
    unittest.main()
