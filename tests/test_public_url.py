import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from surface_map import mcp_server, public_url
from surface_map.analyze import analyze
from surface_map.rebuild import SCOPE, plan_rebuild
from surface_map.url_capture import CaptureError


CANARY = "PRIVATE_PUBLIC_CAPTURE_83651"


def captured(*, scripts=True):
    artifacts = [{"kind": "html", "text":
                  '<a href="/dashboard">Home</a><form action="/api/orders?token=' + CANARY + '" method="post"><input name="order_id" value="' + CANARY + '"></form>'}]
    if scripts:
        artifacts.append({"kind": "javascript", "text": 'fetch("/api/items");'})
    return {"artifacts": artifacts, "document_url": "https://public.example/" + CANARY,
            "capture": {"mode": "public_https_static", "fetched": len(artifacts),
                        "scripts_fetched": int(scripts), "inline_scripts_skipped": 1},
            "warnings": ["Inline JavaScript was not extracted or executed."]}


class PublicURLServiceTests(unittest.TestCase):
    def inspect(self, capture=None, **options):
        capture = captured() if capture is None else capture
        with patch.object(public_url, "capture_public_url", return_value=capture) as fetch:
            result = public_url.inspect_public_url("https://public.example/", javascript_engine="basic", **options)
        fetch.assert_called_once_with("https://public.example/", include_scripts=options.get("include_scripts", True))
        return result

    def test_fetch_and_analysis_run_once_for_report_and_plan(self):
        with patch.object(public_url, "capture_public_url", return_value=captured()) as fetch, \
                patch.object(public_url, "analyze", wraps=analyze) as analysis, \
                patch("surface_map.rebuild.analyze", side_effect=AssertionError("Do not reanalyze the capture")):
            result = public_url.inspect_public_url("https://public.example/", javascript_engine="basic")
        fetch.assert_called_once()
        analysis.assert_called_once()
        report, plan = result["report"], result["plan"]
        self.assertEqual({(row["method"], row["path"], row["confidence"]) for row in plan["operations"]}, {
            ("GET", "/api/items", "inferred"), ("POST", "/api/orders", "declared")})
        for row in plan["operations"]:
            origin = next(item for item in report["routes"] if item["id"] == row["id"])
            self.assertEqual(row["evidence"], origin["evidence"])
        self.assertEqual(plan["coverage"]["capture"], report["capture"])
        self.assertEqual(plan["coverage"]["capture_warnings"], report["capture_warnings"])
        self.assertEqual(plan["privacy"], report["privacy"])

    def test_output_is_minimized_and_scope_discloses_actual_fetch(self):
        result = self.inspect()
        output = json.dumps(result)
        self.assertNotIn(CANARY, output)
        self.assertNotIn("public.example", output)
        self.assertNotIn('"artifacts"', output)
        self.assertNotIn('"document_url"', output)
        self.assertNotIn('"files"', output)
        self.assertEqual(result["plan"]["scope"], public_url.PUBLIC_PLAN_SCOPE)
        self.assertIn("page and selected scripts were fetched", result["plan"]["scope"])
        self.assertNotIn("no discovered address is contacted", result["plan"]["scope"])
        self.assertEqual(result["plan"]["candidate_references"]["candidates"], [])
        self.assertFalse(result["plan"]["candidate_references"]["limits"]["inventory_performed"])
        self.assertEqual(result["plan"]["coverage"]["reference_inventory_omission"]["source_count"], 2)

    def test_public_plan_does_not_run_a_weaker_reference_scan(self):
        with patch("surface_map.rebuild.inventory_references", side_effect=AssertionError("No lexical rescan")):
            result = self.inspect()
        self.assertIn("were not inventoried", result["plan"]["candidate_references"]["scope"])

    def test_partial_script_analysis_keeps_original_source_ids(self):
        capture = captured(scripts=False)
        capture["artifacts"].extend([
            {"kind": "javascript", "text": "a;" * 200000},
            {"kind": "javascript", "text": 'fetch("/api/items");'},
        ])
        capture["capture"]["scripts_fetched"] = 2
        result = self.inspect(capture)
        report, plan = result["report"], result["plan"]
        self.assertTrue(report["capture"]["analysis_partial"])
        self.assertEqual(report["capture"]["scripts_analyzed"], 1)
        self.assertEqual([row["source"] for row in report["script_omissions"]], ["source_2"])
        route = next(row for row in plan["operations"] if row["path"] == "/api/items")
        self.assertEqual(route["evidence"][0]["source"], "source_3")
        self.assertEqual(plan["coverage"]["script_omissions"], report["script_omissions"])
        self.assertTrue(any("omitted from analysis" in item for item in plan["coverage"]["capture_warnings"]))

    def test_option_validation_precedes_capture(self):
        invalid = ({"url": None}, {"url": []}, {"url": ""}, {"url": "x" * 2049},
                   {"url": "https://public.example/", "include_scripts": 1},
                   {"url": "https://public.example/", "include_scripts": None},
                   {"url": "https://public.example/", "javascript_engine": []},
                   {"url": "https://public.example/", "javascript_engine": "unexpected"})
        with patch.object(public_url, "capture_public_url") as fetch:
            for options in invalid:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    public_url.inspect_public_url(**options)
        fetch.assert_not_called()

    def test_capture_failure_does_not_start_analysis(self):
        with patch.object(public_url, "capture_public_url", side_effect=CaptureError("blocked_target")), \
                patch.object(public_url, "analyze") as analysis, self.assertRaises(CaptureError):
            public_url.inspect_public_url("https://127.0.0.1/", javascript_engine="basic")
        analysis.assert_not_called()

    def test_analysis_budget_starts_after_capture(self):
        real_capture = captured()
        real_report = analyze(real_capture["artifacts"], document_url=real_capture["document_url"], javascript_engine="basic",
                              tolerate_script_errors=True)
        finished = []
        def fetch(*args, **kwargs):
            finished.append(time.monotonic())
            return real_capture
        with patch.object(public_url, "capture_public_url", side_effect=fetch), \
                patch.object(public_url, "analyze", return_value=real_report) as analysis:
            public_url.inspect_public_url("https://public.example/", javascript_engine="basic")
        deadline = analysis.call_args.kwargs["deadline"]
        self.assertGreaterEqual(deadline, finished[0] + public_url.ANALYSIS_SECONDS)
        self.assertLess(deadline, finished[0] + public_url.ANALYSIS_SECONDS + 1)

    def test_mcp_uses_the_shared_capture_analysis_without_sdk_import(self):
        capture = captured()
        expected = public_url.analyze_public_capture(capture, javascript_engine="basic")
        with patch.object(mcp_server, "capture_public_url", return_value=captured()) as fetch:
            actual = mcp_server._execute("analyze_url", {"url": "https://public.example/", "include_scripts": False,
                                                       "javascript_engine": "basic"})
        fetch.assert_called_once_with("https://public.example/", include_scripts=False)
        self.assertEqual(actual, expected)

    def test_partial_report_gets_a_fresh_bounded_plan_budget_after_analysis(self):
        capture = captured()
        capture["artifacts"][1]["text"] = "a;" * 200000
        partial = analyze(capture["artifacts"], document_url=capture["document_url"],
                          javascript_engine="basic", tolerate_script_errors=True)
        self.assertTrue(partial["script_omissions"])
        start = time.monotonic()
        clock = SimpleNamespace(monotonic=Mock(side_effect=[start, start + public_url.ANALYSIS_SECONDS + 1]))
        with patch.object(public_url, "capture_public_url", return_value=capture), \
                patch.object(public_url, "analyze", return_value=partial) as analysis, \
                patch.object(public_url, "time", clock), \
                patch.object(public_url, "_plan_from_report", wraps=public_url._plan_from_report) as projection:
            result = public_url.inspect_public_url("https://public.example/", javascript_engine="basic")
        self.assertEqual(analysis.call_args.kwargs["deadline"], start + public_url.ANALYSIS_SECONDS)
        self.assertEqual(projection.call_args.kwargs["deadline"],
                         start + public_url.ANALYSIS_SECONDS + 1 + public_url.PLAN_SECONDS)
        self.assertTrue(result["report"]["capture"]["analysis_partial"])
        self.assertEqual(result["plan"]["coverage"]["script_omissions"], partial["script_omissions"])

    def test_saved_plan_scope_and_reference_inventory_are_unchanged(self):
        plan = plan_rebuild([{"kind": "javascript", "text": 'fetch("/api/items");'}], javascript_engine="basic")
        self.assertEqual(plan["scope"], SCOPE)
        self.assertTrue(plan["candidate_references"]["candidates"])
        self.assertNotIn("reference_inventory_omission", plan["coverage"])


if __name__ == "__main__":
    unittest.main()
