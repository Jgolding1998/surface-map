import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from evals.diverse.run import EvaluationError, score
from evals.stress640.build import build, encoded, input_fingerprint
from evals.stress640.run import INPUT_FIELDS, case_composition, export_inputs, load_corpus, main, unresolved_metrics


class StressCorpusTests(unittest.TestCase):
    def test_frozen_cases_are_unique_balanced_and_reproducible(self):
        corpus = load_corpus()
        generated = build()
        self.assertEqual(len(corpus["cases"]), 640)
        self.assertEqual(len({case["family"] for case in corpus["cases"]}), 16)
        self.assertEqual(len({input_fingerprint(case) for case in corpus["cases"]}), 640)
        self.assertEqual(corpus["sha256"], hashlib.sha256(encoded(generated)).hexdigest())
        self.assertEqual(sum(case["expected"].get("rejected", False) for case in corpus["cases"]), 40)
        self.assertEqual(sum(case["task"] == "compare" for case in corpus["cases"]), 40)

    def _write(self, target, corpus):
        raw = encoded(corpus)
        (target / "corpus.json").write_bytes(raw)
        (target / "corpus.sha256").write_text(hashlib.sha256(raw).hexdigest(), encoding="ascii")

    def test_duplicate_inputs_fail_even_with_new_ids_and_valid_digest(self):
        corpus = build()
        corpus["cases"][1] = {**corpus["cases"][0], "id": "duplicate-with-new-name"}
        with tempfile.TemporaryDirectory() as directory:
            self._write(Path(directory), corpus)
            with self.assertRaises(EvaluationError):
                load_corpus(directory)

    def test_missing_expected_operations_fail_even_with_valid_digest(self):
        corpus = build()
        del corpus["cases"][0]["expected"]["unresolved"]
        with tempfile.TemporaryDirectory() as directory:
            self._write(Path(directory), corpus)
            with self.assertRaises(EvaluationError):
                load_corpus(directory)

    def test_input_mutation_requires_new_digest(self):
        corpus = build()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self._write(target, corpus)
            corpus["cases"][0]["artifacts"][0]["text"] += '<a href="/unexpected">Other</a>'
            (target / "corpus.json").write_bytes(encoded(corpus))
            with self.assertRaises(EvaluationError):
                load_corpus(target)

    def test_crlf_checkout_keeps_the_frozen_identity(self):
        folder = Path(__file__).resolve().parents[1] / "evals/stress640"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            target.joinpath("corpus.json").write_bytes(folder.joinpath("corpus.json").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            target.joinpath("corpus.sha256").write_bytes(folder.joinpath("corpus.sha256").read_bytes())
            self.assertEqual(load_corpus(target)["sha256"], load_corpus()["sha256"])

    def test_wrong_method_or_evidence_is_not_partial_credit(self):
        case = build()["cases"][0]
        truth = case["expected"]
        wrong = json.loads(json.dumps(truth))
        wrong["routes"][0][0] = "POST"
        self.assertIn("routes", score(case, wrong))
        wrong = json.loads(json.dumps(truth))
        wrong["routes"][0][3][0][2] = "har"
        self.assertIn("routes", score(case, wrong))

    def test_unresolved_metrics_count_missing_and_spurious_targets(self):
        corpus = {"cases": [
            {"id": "present", "task": "analyze", "expected": {"unresolved": [["/api/items", []]]}},
            {"id": "empty", "task": "analyze", "expected": {"unresolved": []}},
            {"id": "rejected", "task": "analyze", "expected": {"unresolved": [["/api/users", []]]}},
            {"id": "comparison", "task": "compare", "expected": {"unresolved_added": ["/api/tasks"]}},
        ]}
        results = {"results": [
            {"id": "present", "prediction": {"unresolved": [["/api/items", []]]}},
            {"id": "empty", "prediction": {"unresolved": [["/api/orders", []]]}},
            {"id": "rejected", "prediction": {"rejected": True}},
            {"id": "comparison", "prediction": {"unresolved_added": ["/api/tasks"]}},
        ]}
        metrics = unresolved_metrics(corpus, results)
        self.assertEqual({key: metrics[key] for key in ("tp", "fp", "fn")}, {"tp": 1, "fp": 1, "fn": 1})
        self.assertEqual(metrics["precision"], .5)
        self.assertEqual(metrics["recall"], .5)

    def test_unresolved_metrics_penalize_targets_on_invalid_input_and_internal_error(self):
        corpus = {"cases": [
            {"id": "invalid", "task": "analyze", "expected": {"rejected": True}},
            {"id": "error", "task": "analyze", "expected": {"unresolved": [["/api/items", []]]}},
        ]}
        results = {"results": [
            {"id": "invalid", "prediction": {"unresolved": [["/api/users", []]]}},
            {"id": "error", "prediction": {"error": "evaluation_error"}},
        ]}
        metrics = unresolved_metrics(corpus, results)
        self.assertEqual({key: metrics[key] for key in ("tp", "fp", "fn")}, {"tp": 0, "fp": 1, "fn": 1})
        self.assertEqual(metrics["precision"], 0)
        self.assertEqual(metrics["recall"], 0)

    def test_unresolved_metrics_empty_denominators_are_unavailable_not_perfect(self):
        corpus = {"cases": [{"id": "empty", "task": "analyze", "expected": {"unresolved": []}}]}
        metrics = unresolved_metrics(corpus, {"results": [{"id": "empty", "prediction": {"unresolved": []}}]})
        self.assertIsNone(metrics["precision"])
        self.assertIsNone(metrics["recall"])

    def test_composition_exposes_repeated_negative_outcomes_without_canary_padding(self):
        corpus = {"cases": [
            {"family": "empty", "task": "analyze", "expected": {"routes": [], "unresolved": [], "warnings": ["one"], "forbidden": ["a"]}},
            {"family": "empty", "task": "analyze", "expected": {"routes": [], "unresolved": [], "warnings": ["two"], "forbidden": ["b"]}},
            {"family": "rejected", "task": "analyze", "expected": {"rejected": True}},
            {"family": "rejected", "task": "analyze", "expected": {"rejected": True}},
        ]}
        summary = case_composition(corpus)
        self.assertEqual(summary["families"]["empty"]["distinct_expected_projections"], 1)
        self.assertEqual(summary["families"]["rejected"]["distinct_expected_projections"], 1)
        self.assertEqual(summary["totals"]["accepted_analysis_cases_without_targets"], 2)
        self.assertEqual(summary["totals"]["expected_rejections"], 2)

    def test_composition_counts_targets_and_preserves_signal_detail_and_comparison_differences(self):
        corpus = {"cases": [
            {"family": "mixed", "task": "analyze", "expected": {"routes": [["GET", "/api/items", "declared", []]], "unresolved": [["/api/users", []]]}},
            {"family": "signals", "task": "analyze", "expected": {"routes": [], "unresolved": [], "signals": []}},
            {"family": "signals", "task": "analyze", "expected": {"routes": [], "unresolved": [], "signals": [["marker", "line 1"]]}},
            {"family": "details", "task": "analyze", "expected": {"routes": [], "unresolved": [], "details": [{"method": "GET", "path": "/api/items", "statuses": [200]}]}},
            {"family": "details", "task": "analyze", "expected": {"routes": [], "unresolved": [], "details": [{"method": "GET", "path": "/api/items", "statuses": [404]}]}},
            {"family": "comparison", "task": "compare", "expected": {"added": [["GET", "/api/items"]]}},
            {"family": "comparison", "task": "compare", "expected": {"added": []}},
        ]}
        summary = case_composition(corpus)
        mixed = summary["families"]["mixed"]
        self.assertEqual(mixed["accepted_analysis_cases_with_known_operations"], 1)
        self.assertEqual(mixed["accepted_analysis_cases_with_unresolved_targets"], 1)
        self.assertEqual(mixed["accepted_analysis_cases_without_targets"], 0)
        for family in ("signals", "details", "comparison"):
            self.assertEqual(summary["families"][family]["distinct_expected_projections"], 2)
        self.assertEqual(summary["totals"]["comparison_cases"], 2)

    def test_export_has_opaque_ids_and_only_execution_inputs_in_original_order(self):
        corpus = load_corpus()
        before = json.dumps(corpus, sort_keys=True)
        bundle = export_inputs(corpus)
        self.assertEqual(len(bundle["cases"]), 640)
        for index, (original, exported) in enumerate(zip(corpus["cases"], bundle["cases"]), 1):
            self.assertEqual(exported["id"], f"case_{index:04d}")
            self.assertTrue(set(exported) <= {"id", *INPUT_FIELDS})
            self.assertEqual(input_fingerprint(original), input_fingerprint(exported))
            self.assertFalse({"family", "axes", "expected"} & exported.keys())
        self.assertEqual(json.dumps(corpus, sort_keys=True), before)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inputs.json"
            self.assertEqual(main(["--export-inputs", str(path)]), 0)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), bundle)


if __name__ == "__main__":
    unittest.main()
