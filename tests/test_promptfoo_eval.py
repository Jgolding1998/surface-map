"""Objective integration checks; no npm install, AI account or MCP SDK needed."""
import copy
from contextlib import redirect_stdout
from io import StringIO
import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from evals.diverse.run import load_corpus
from evals.promptfoo import assertions, prepare, provider
from evals.promptfoo.run import finish_run, validate_export
from evals.promptfoo.viewer import ReportHandler
from surface_map.analyze import analyze
from surface_map.export import compare


class PromptfooIntegrationTests(unittest.TestCase):
    @staticmethod
    def successful_export(mode="basic"):
        cases = json.loads(prepare.generated_files()[f"tests-{mode}.json"])
        identities = [case["vars"]["case_id"] for case in cases]
        rows = [{"vars": {"case_id": case_id}, "repeatIndex": repeat, "success": True,
                 "response": {}, "promptIdx": 0, "gradingResult": {"pass": True},
                 "namedScores": {"exact_case": 1}}
                for case_id in identities for repeat in range(3)]
        groups = [{"description": case_id, "repetitions": 3, "passed": 3, "failed": 0,
                   "errors": 0, "cached": 0, "unstable": False} for case_id in identities]
        return {"shareableUrl": None, "results": {"results": rows,
                "stats": {"successes": len(rows), "failures": 0, "errors": 0},
                "repeatStability": {"totalGroups": len(groups), "unstableGroups": 0,
                                    "groupsWithErrors": 0, "cachedResults": 0, "groups": groups}}}

    def test_actual_provider_output_passes_published_form_rubric(self):
        result = provider.call_api("post-form-fields", {"config": {"engine": "basic"}},
                                   {"vars": {"case_id": "post-form-fields"}})
        self.assertTrue(assertions.get_assert(result["output"], {"vars": {"case_id": "post-form-fields"}})["pass"])
        self.assertEqual(result["metadata"]["condition"], "direct-python")
        self.assertNotIn("artifacts", result["metadata"])

    def test_changed_source_evidence_is_a_failure(self):
        result = provider.call_api("post-form-fields", {}, {"vars": {"case_id": "post-form-fields"}})
        observed = json.loads(result["output"])
        observed["routes"][0][3][0][1] = "line 999"
        score = assertions.get_assert(json.dumps(observed), {"vars": {"case_id": "post-form-fields"}})
        self.assertFalse(score["pass"])
        self.assertIn("routes", score["reason"])

    def test_direct_provider_never_accepts_arbitrary_prompt_or_engine(self):
        with self.assertRaises(ValueError):
            provider.call_api("/private/input.html", {}, {"vars": {"case_id": "post-form-fields"}})
        with self.assertRaises(ValueError):
            provider.call_api("post-form-fields", {"config": {"engine": "auto"}},
                              {"vars": {"case_id": "post-form-fields"}})

    def test_malformed_or_unknown_case_fails_instead_of_disappearing(self):
        for output, case_id in (("not JSON", "post-form-fields"), ("[]", "post-form-fields"),
                                ("{}", "not-a-case"), ("{}", "post-form-fields")):
            with self.subTest(output=output, case_id=case_id):
                self.assertFalse(assertions.get_assert(output, {"vars": {"case_id": case_id}})["pass"])

    def test_rejection_counts_as_success_only_for_expected_rejection(self):
        result = provider.call_api("invalid-har-object", {}, {"vars": {"case_id": "invalid-har-object"}})
        self.assertEqual(json.loads(result["output"]), {"rejected": True})
        self.assertTrue(assertions.get_assert(result["output"], {"vars": {"case_id": "invalid-har-object"}})["pass"])
        self.assertFalse(assertions.get_assert(result["output"], {"vars": {"case_id": "post-form-fields"}})["pass"])

    def test_mcp_raw_reports_and_comparisons_use_same_rubric(self):
        for case_id in prepare.MCP_CASE_IDS:
            _, case = provider.case_by_id(case_id)
            if case["task"] == "compare":
                reports = [analyze(case[key], javascript_engine="basic") for key in ("before", "after")]
                raw = {"comparison": compare(*reports)}
            else:
                raw = analyze(case["artifacts"], javascript_engine="basic",
                              include_page_signals=case.get("include_page_signals", False))
            result = assertions.get_assert(json.dumps(raw), {"vars": {"case_id": case_id, "transport": "mcp"}})
            with self.subTest(case_id=case_id):
                self.assertTrue(result["pass"], result)

    def test_raw_mcp_privacy_leak_is_scored_even_outside_projection(self):
        _, case = provider.case_by_id("har-credentials-discarded")
        raw = analyze(case["artifacts"], javascript_engine="basic")
        self.assertTrue(case["expected"]["forbidden"])
        raw["unexpected_debug"] = case["expected"]["forbidden"][0]
        score = assertions.get_assert(json.dumps(raw), {"vars": {"case_id": case["id"], "transport": "mcp"}})
        self.assertFalse(score["pass"])
        self.assertIn("privacy_canary", score["reason"])

    def test_generated_cases_match_verified_corpus_without_exposing_gold(self):
        files = prepare.generated_files()
        enhanced = json.loads(files["tests-enhanced.json"])
        basic = json.loads(files["tests-basic.json"])
        self.assertEqual(len(enhanced), 64)
        self.assertEqual(len(basic), 46)
        self.assertEqual(len(json.loads(files["tests-mcp.json"])), 4)
        for name, rendered in files.items():
            self.assertEqual((prepare.FOLDER / name).read_text(encoding="utf-8"), rendered)
            for case in json.loads(rendered):
                self.assertNotIn("expected", case["vars"])
                self.assertEqual(case["metadata"]["corpus_sha256"], load_corpus()["sha256"])

    def test_report_viewer_blocks_history_paths_and_unexpected_hosts(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ReportHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            for path, host, status in (("/", None, 200), ("/.promptfoo/promptfoo.db", None, 404),
                                       ("/../../requirements-mcp.txt", None, 404),
                                       ("/", "unrelated.example", 403)):
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                try:
                    connection.request("GET", path, headers={"Host": host} if host else {})
                    response = connection.getresponse()
                    self.assertEqual(response.status, status)
                    response.read()
                finally:
                    connection.close()
        finally:
            server.shutdown()
            worker.join(timeout=2)
            server.server_close()

    def test_postflight_requires_all_declared_cases_and_repetitions(self):
        for mode, count in (("basic", 138), ("enhanced", 192), ("mcp", 12)):
            with self.subTest(mode=mode):
                self.assertEqual(validate_export(self.successful_export(mode), mode), count)
        incomplete = self.successful_export()
        incomplete["results"]["results"].pop()
        incomplete["results"]["stats"]["successes"] -= 1
        with self.assertRaises(ValueError):
            validate_export(incomplete, "basic")
        duplicate = self.successful_export()
        duplicate["results"]["results"][-1] = copy.deepcopy(duplicate["results"]["results"][0])
        with self.assertRaises(ValueError):
            validate_export(duplicate, "basic")

    def test_postflight_rejects_cached_or_contradictory_successes(self):
        original = self.successful_export()
        mutations = [
            lambda doc: doc["results"]["stats"].update(errors=1),
            lambda doc: doc["results"]["results"][0].update(success=False),
            lambda doc: doc["results"]["results"][0]["response"].update(cached=True),
            lambda doc: doc["results"]["results"][0]["gradingResult"].update({"pass": False}),
            lambda doc: doc["results"]["repeatStability"].update(cachedResults=1),
            lambda doc: doc["results"]["repeatStability"]["groups"][0].update(repetitions=2),
            lambda doc: doc["results"]["repeatStability"]["groups"][0].update(unstable=True),
        ]
        for index, mutate in enumerate(mutations):
            document = copy.deepcopy(original)
            mutate(document)
            with self.subTest(mutation=index), self.assertRaises(ValueError):
                validate_export(document, "basic")

    def test_postflight_preserves_vendor_exit_and_rejects_missing_export(self):
        with TemporaryDirectory() as folder, redirect_stdout(StringIO()):
            result_path = Path(folder) / "missing.json"
            self.assertEqual(finish_run(37, result_path, "basic"), 37)
            self.assertEqual(finish_run(0, result_path, "basic"), 1)
            result_path.write_text(json.dumps(self.successful_export()), encoding="utf-8")
            self.assertEqual(finish_run(0, result_path, "basic"), 0)


if __name__ == "__main__":
    unittest.main()
