from pathlib import Path
import tempfile
import unittest
import json
import hashlib

from evals.diverse.run import EvaluationError, evaluate, load_corpus, project_comparison, run_case, score
from evals.diverse.compare_agent import (canonical_prediction, compare_baseline, index_predictions,
                                        load_baseline, parse_response, score_predictions)


class DiverseEvaluationTests(unittest.TestCase):
    def test_corpus_identity_and_inputs(self):
        corpus = load_corpus()
        self.assertEqual(len(corpus["cases"]), 64)
        self.assertGreaterEqual(len({case["family"] for case in corpus["cases"]}), 12)
        self.assertEqual(len({case["id"] for case in corpus["cases"]}), 64)
        self.assertEqual(sum(case["expected"].get("rejected", False) for case in corpus["cases"]), 3)

    def test_changed_truth_requires_a_new_digest(self):
        folder = Path(__file__).resolve().parents[1] / "evals/diverse"
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            (target / "corpus.sha256").write_bytes((folder / "corpus.sha256").read_bytes())
            data = (folder / "corpus.json").read_bytes().replace(b"navigation-lines", b"navigation-false")
            (target / "corpus.json").write_bytes(data)
            with self.assertRaises(EvaluationError):
                load_corpus(target)

    def test_crlf_checkout_has_the_same_identity(self):
        folder = Path(__file__).resolve().parents[1] / "evals/diverse"
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            (target / "corpus.sha256").write_bytes((folder / "corpus.sha256").read_bytes())
            data = (folder / "corpus.json").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            (target / "corpus.json").write_bytes(data)
            self.assertEqual(load_corpus(target)["sha256"], load_corpus()["sha256"])

    def test_a_valid_digest_does_not_allow_missing_unresolved_expectations(self):
        folder = Path(__file__).resolve().parents[1] / "evals/diverse"
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            corpus = json.loads((folder / "corpus.json").read_text(encoding="utf-8"))
            del corpus["cases"][0]["expected"]["unresolved"]
            raw = json.dumps(corpus).encode("utf-8")
            (target / "corpus.json").write_bytes(raw)
            (target / "corpus.sha256").write_text(hashlib.sha256(raw).hexdigest(), encoding="ascii")
            with self.assertRaises(EvaluationError):
                load_corpus(target)

    def test_unexpected_routes_are_not_filtered(self):
        case = {"expected": {"routes": [], "unresolved": []}}
        observed = {"routes": [["GET", "/unexpected", "inferred", []]], "unresolved": []}
        self.assertEqual(score(case, observed), ["routes"])
        observed = {"routes": [], "unresolved": [["/unexpected", []]]}
        self.assertEqual(score(case, observed), ["unresolved"])

    def test_right_route_with_wrong_evidence_is_a_failure(self):
        route = ["GET", "/api/items", "declared", [["source_1", "line 2", "link"]]]
        case = {"expected": {"routes": [route], "unresolved": []}}
        for wrong in (["GET", "/api/items", "observed", route[3]],
                      ["GET", "/api/items", "declared", [["source_2", "line 2", "link"]]],
                      ["GET", "/api/items", "declared", [["source_1", "line 1", "link"]]]):
            self.assertIn("routes", score(case, {"routes": [wrong], "unresolved": []}))

    def test_warnings_details_privacy_and_opt_in_are_scored(self):
        case = {"expected": {"routes": [], "unresolved": [], "signals": None,
                             "warnings": ["missing coverage"],
                             "details": [{"method": "GET", "path": "/items", "statuses": [403]}]}}
        observed = {"routes": [], "unresolved": [], "signals": [], "warnings": [],
                    "details": [{"method": "GET", "path": "/items", "statuses": [200]}],
                    "privacy_failure": True}
        self.assertEqual(score(case, observed), ["privacy_canary", "required_warning", "route_details", "signals"])

    def test_correct_rejection_and_analyzer_failure_are_separate(self):
        case = {"expected": {"rejected": True}}
        self.assertEqual(score(case, {"rejected": True}), [])
        self.assertEqual(score(case, {"error": "evaluation_error"}), ["expected_rejection"])
        normal = {"expected": {"routes": [], "unresolved": []}}
        self.assertEqual(score(normal, {"rejected": True}), ["unexpected_rejection"])

    def test_repeat_instability_fails_even_when_first_answer_was_correct(self):
        calls = []
        def analyze(*args, **kwargs):
            calls.append(True)
            return {"routes": [], "warnings": [] if len(calls) == 1 else [{"reason": "changed"}]}
        case = {"id": "stable", "family": "negative", "task": "analyze", "artifacts": [],
                "expected": {"routes": [], "unresolved": []}}
        result = evaluate({"cases": [case], "sha256": "test", "scope": "test"}, "basic", 3,
                          analyze=analyze, compare=lambda a,b: {}, artifact_error=ValueError)
        self.assertEqual(result["passed"], 0)
        self.assertEqual(result["stable_cases"], 0)
        self.assertEqual(result["results"][0]["failures"], ["repeat_inconsistency"])

    def test_comparison_retains_new_unknown_targets_and_evidence_changes(self):
        projected = project_comparison({"added": [], "removed": [],
            "changed": [{"method": "GET", "path": "/items", "changed_fields": ["evidence_kinds"]}],
            "unresolved_added": [{"path": "/search"}],
            "unresolved_changed": [{"path": "/users", "changed_fields": ["query_fields"]}]})
        self.assertEqual(projected["unresolved_added"], ["/search"])
        self.assertEqual(projected["unresolved_changed"], [["/users", ["query_fields"]]])
        self.assertEqual(projected["changed"], [["GET", "/items", ["evidence_kinds"]]])

    def test_comparison_uses_each_document_url_and_checks_both_reports_for_canaries(self):
        calls = []
        def analyze(artifacts, **kwargs):
            calls.append(kwargs.get("document_url"))
            return {"routes": [], "private_value": "COMPARE-CANARY" if len(calls) == 1 else ""}
        case = {"task": "compare", "before": [], "after": [],
                "before_document_url": "https://before.example/docs/",
                "after_document_url": "https://after.example/users/",
                "expected": {"added": [], "removed": [], "changed": [],
                             "forbidden": ["COMPARE-CANARY"]}}
        result = run_case(case, "basic", analyze=analyze,
                          compare=lambda a,b: {"added": [], "removed": [], "changed": []}, artifact_error=ValueError)
        self.assertEqual(calls, [case["before_document_url"], case["after_document_url"]])
        self.assertIn("privacy_canary", score(case, result))

    def test_missing_observed_operation_list_is_not_an_empty_success(self):
        case = {"expected": {"routes": [], "unresolved": []}}
        self.assertEqual(score(case, {"routes": []}), ["unresolved"])

    def test_non_script_cases_are_exercised_without_optional_parser(self):
        from surface_map.analyze import ArtifactError, analyze
        from surface_map.export import compare
        corpus = load_corpus()
        def has_scripts(case):
            return any(a["kind"] in {"javascript", "typescript"}
                       for key in ("artifacts", "before", "after") for a in case.get(key, []))
        corpus["cases"] = [case for case in corpus["cases"] if not has_scripts(case)]
        report = evaluate(corpus, "basic", 1, analyze=analyze, compare=compare, artifact_error=ArtifactError)
        self.assertEqual(report["passed"], report["cases"],
                         [(r["id"], r["failures"]) for r in report["results"] if not r["passed"]])


class AgentComparisonTests(unittest.TestCase):
    def sample(self):
        return {"id": "sample", "routes": [["GET", "/items", "declared", [
                    ["source_1", "line 1", "link"], ["source_2", "line 3", "javascript"]]]], "unresolved": []}

    def test_fences_and_evidence_order_do_not_change_the_answer(self):
        sample = self.sample()
        expected = canonical_prediction(sample)
        sample["routes"][0][3].reverse()
        sample["routes"][0][3].append(sample["routes"][0][3][0])
        text = "```json\n" + json.dumps({"results": [sample]}) + "\n```"
        self.assertEqual(index_predictions(parse_response(text), ["sample"])["sample"], expected)

    def test_missing_duplicate_unknown_and_malformed_predictions_fail(self):
        sample = self.sample()
        for rows in ([], [sample, sample], [{**sample, "id": "unknown"}],
                     [{**sample, "routes": [["GET", "/items", "declared", []]]}],
                     [{**sample, "unresolved": [["/items", [["source_1", "line 1", "har"]]]]}],
                     [{**sample, "extra": "unexpected"}]):
            with self.subTest(rows=rows), self.assertRaises(EvaluationError):
                index_predictions(rows, ["sample"])

    def test_duplicate_json_keys_and_explanatory_text_fail(self):
        for text in ('{"results":[],"results":[]}', 'Explanation\n{"results":[]}', '{"results": NaN}'):
            with self.subTest(text=text), self.assertRaises(EvaluationError):
                parse_response(text)

    def test_false_positive_and_wrong_confidence_remain_failures(self):
        sample = self.sample()
        truth = index_predictions([sample], ["sample"])
        wrong = {**sample, "routes": [["GET", "/items", "observed", sample["routes"][0][3]],
                                     ["POST", "/unexpected", "inferred", [["source_1", "line 1", "javascript"]]]]}
        result = score_predictions(truth, index_predictions([wrong], ["sample"]))
        self.assertEqual(result["passed"], 0)
        self.assertEqual(result["known_operations"]["fp"], 1)
        self.assertEqual(result["results"]["sample"]["failures"], ["routes"])

    def test_frozen_baseline_reproduces_the_paired_tie(self):
        manifest, truth, tool, trials = load_baseline()
        report = compare_baseline(manifest, truth, tool, trials)
        self.assertEqual(report["unique_cases"], 45)
        self.assertEqual(report["stable_agent_cases"], 45)
        self.assertEqual(report["tool"]["passed"], 45)
        self.assertEqual(len(report["trials"]), 3)
        for trial in report["trials"]:
            self.assertEqual(trial["agent"]["passed"], 45)
            self.assertEqual(trial["paired"], {"both_correct": 45, "both_incorrect": 0, "tool_only": 0, "agent_only": 0})
        folder = Path(__file__).resolve().parents[1] / "evals/diverse/agent_baseline"
        self.assertEqual(len(manifest["batch_receipts"]), 12)
        for receipt in manifest["batch_receipts"]:
            self.assertEqual(hashlib.sha256((folder / receipt["raw_response_file"]).read_bytes()).hexdigest(),
                             receipt["raw_response_sha256"])


if __name__ == "__main__":
    unittest.main()
