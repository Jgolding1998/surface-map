"""Evaluator contract tests use injected analyzers, never Node, workers or fixture execution."""
import contextlib
import builtins
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from evals import run


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.corpus = run.load_corpus()
        self.by_text = {case["text"]: case for case in self.corpus["cases"]}

    def report(self, case, mode="enhanced"):
        engine = {"engine": mode}
        if mode == "enhanced":
            engine["parser"] = "mock-parser"
        return {"routes": [{"method": method, "path": path} for method, path in case["known"]],
                "unresolved_operations": [{"method": None, "path": path} for path in case["unresolved"]],
                "javascript_analysis": [engine]}

    def analyzer(self, artifacts, *, javascript_engine):
        self.assertEqual(len(artifacts), 1)
        case = self.by_text[artifacts[0]["text"]]
        self.assertEqual(artifacts[0]["kind"], case["kind"])
        return self.report(case, javascript_engine)

    def evaluate(self, analyzer=None, trials=1, mode="enhanced"):
        close = Mock()
        result = run.evaluate(self.corpus, mode, trials, analyze=analyzer or self.analyzer, close=close)
        self.assertEqual(close.call_count, trials + 1)
        return result

    def test_corpus_contract_and_frozen_minimized_truth(self):
        cases = self.corpus["cases"]
        self.assertEqual(len(cases), 16)
        self.assertEqual(sum(bool(case["known"]) for case in cases), 10)
        self.assertEqual(sum(bool(case["unresolved"]) for case in cases), 1)
        self.assertEqual(sum(not case["known"] and not case["unresolved"] for case in cases), 5)
        self.assertEqual(len({case["id"] for case in cases}), 16)
        from surface_map.analyze import safe_path
        for path in run.PATHS:
            self.assertEqual(safe_path(path), (path, []))
        self.assertEqual(self.corpus["aggregate_sha256"], "c1249df44653f85a4d8cb841a702229a76bac50bdd64e96c55354357ffeec7c8")

    def test_changed_fixture_content_is_rejected(self):
        original = run._bytes
        def changed(path, limit):
            data = original(path, limit)
            return data + b"// changed\n" if path.name == "literal-get.js" else data
        with patch.object(run, "_bytes", changed), self.assertRaisesRegex(run.EvaluationError, "checksum"):
            run.load_corpus()

    def test_crlf_checkout_has_same_fixture_hashes(self):
        original = run._bytes
        with patch.object(run, "_bytes", lambda path, limit: original(path, limit).replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")):
            self.assertEqual(run.load_corpus()["aggregate_sha256"], self.corpus["aggregate_sha256"])

    def test_traversal_remote_and_duplicate_fixture_id_are_rejected(self):
        original = run._bytes
        for replacement in ("../outside.js", "https://example.invalid/fixture.js", "cases/../outside.js"):
            def changed(path, limit):
                data = original(path, limit)
                if path.name == "corpus.json":
                    corpus = json.loads(data); corpus["cases"][0]["file"] = replacement
                    return json.dumps(corpus).encode()
                return data
            with self.subTest(replacement=replacement), patch.object(run, "_bytes", changed), self.assertRaises(run.EvaluationError):
                run.load_corpus()
        def duplicate(path, limit):
            data = original(path, limit)
            if path.name == "corpus.json":
                corpus = json.loads(data); corpus["cases"][1]["id"] = corpus["cases"][0]["id"]
                return json.dumps(corpus).encode()
            return data
        with patch.object(run, "_bytes", duplicate), self.assertRaises(run.EvaluationError):
            run.load_corpus()

    def test_tp_fp_fn_and_undefined_rates(self):
        self.assertEqual(run.metrics({1, 2}, {2, 3}), {"tp": 1, "fp": 1, "fn": 1, "precision": .5, "recall": .5, "f1": .5})
        self.assertIsNone(run.metrics({1}, set())["precision"])
        self.assertIsNone(run.metrics(set(), {1})["recall"])
        self.assertEqual(run.metrics(set(), set())["f1"], 1)

    def test_perfect_mock_results_use_correct_public_artifact_kinds_and_explicit_mode(self):
        analyzer = Mock(side_effect=self.analyzer)
        result = self.evaluate(analyzer, trials=2)
        self.assertEqual(analyzer.call_count, 64)
        self.assertTrue(result["passed"])
        self.assertEqual(result["exact_cases"], {"passed": 16, "total": 16})
        self.assertEqual(result["negative_cases"], {"passed": 5, "total": 5})
        self.assertEqual(result["metrics"]["typed_operations"]["tp"], 11)
        self.assertEqual(result["latency"]["cold_pass"]["samples"], 2)
        self.assertEqual(result["latency"]["warm_pass"]["case_calls_per_sample"], 16)
        self.assertNotIn('"text"', json.dumps(result))
        self.assertIn("Exact cases | 16/16", run.table(result))

    def test_unknown_method_is_not_defaulted_to_get(self):
        def guessed(artifacts, *, javascript_engine):
            case = self.by_text[artifacts[0]["text"]]
            report = self.report(case)
            if case["unresolved"]:
                report["routes"] = [{"method": "GET", "path": "/api/search"}]
                report["unresolved_operations"] = []
            return report
        result = self.evaluate(guessed)
        self.assertFalse(result["passed"])
        self.assertEqual(result["metrics"]["typed_operations"]["fp"], 1)
        self.assertEqual(result["metrics"]["typed_operations"]["fn"], 1)
        self.assertEqual(result["metrics"]["targets_ignoring_method"]["tp"], 11)
        self.assertEqual(result["exact_cases"]["passed"], 15)

    def test_unexpected_paths_are_not_filtered_and_fallback_is_rejected(self):
        report = self.report({"known": [["GET", "/api/unexpected"]], "unresolved": []})
        self.assertEqual(run.projection(report, "enhanced")[0], {("GET", "/api/unexpected")})
        with self.assertRaisesRegex(run.EvaluationError, "parser mode"):
            run.projection(self.report({"known": [], "unresolved": []}, "basic"), "enhanced")

    def test_operation_envelopes_require_routes_and_allow_only_optional_unresolved_list(self):
        valid = self.report({"known": [], "unresolved": []})
        optional = {key: value for key, value in valid.items() if key != "unresolved_operations"}
        self.assertEqual(run.projection(optional, "enhanced")[:2], (set(), set()))
        malformed = [{key: value for key, value in valid.items() if key != "routes"}]
        malformed += [{**valid, "routes": value} for value in (None, {}, "not a list")]
        malformed += [{**valid, "unresolved_operations": value} for value in (None, {}, "not a list")]
        malformed += [{**valid, "javascript_analysis": [value]} for value in (None, [], "not an object")]
        for report in malformed:
            with self.subTest(report=report), self.assertRaises(run.EvaluationError):
                run.projection(report, "enhanced")

    def test_analysis_error_on_negative_case_cannot_be_a_correct_empty_result(self):
        def fail(artifacts, *, javascript_engine):
            if self.by_text[artifacts[0]["text"]]["id"] == "unknown-target":
                raise ValueError("synthetic failure")
            return self.analyzer(artifacts, javascript_engine=javascript_engine)
        result = self.evaluate(fail)
        self.assertFalse(result["passed"])
        self.assertEqual(result["analysis_errors_all_passes"], 2)
        self.assertEqual(result["negative_cases"]["passed"], 4)

    def test_error_rows_preserve_only_allowlisted_codes_never_exception_text(self):
        from surface_map.js_parser import JsParserError
        for error, code in ((JsParserError("timeout"), "timeout"),
                            (ValueError("PRIVATE-ERROR-CONTENT"), "analysis_failed")):
            def fail(artifacts, *, javascript_engine):
                if self.by_text[artifacts[0]["text"]]["id"] == "unknown-target":
                    raise error
                return self.analyzer(artifacts, javascript_engine=javascript_engine)
            with self.subTest(code=code):
                result = self.evaluate(fail)
                row = next(item for item in result["cases"] if item["id"] == "unknown-target")
                self.assertEqual(row["error_code"], code)
                self.assertNotIn("PRIVATE-ERROR-CONTENT", json.dumps(result))
        error = ValueError("PRIVATE-ERROR-CONTENT")
        error.code = "PRIVATE-CODE-CONTENT"
        result = self.evaluate(Mock(side_effect=error))
        self.assertTrue(all(row["error_code"] == "analysis_failed" for row in result["cases"]))
        self.assertNotIn("PRIVATE-CODE-CONTENT", json.dumps(result))

    def test_repeated_pass_disagreement_fails_without_inflating_accuracy_sample(self):
        seen = 0
        def unstable(artifacts, *, javascript_engine):
            nonlocal seen
            seen += 1
            report = self.analyzer(artifacts, javascript_engine=javascript_engine)
            if seen == 17:
                report["routes"] = []
            return report
        result = self.evaluate(unstable)
        self.assertFalse(result["stable_predictions"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["exact_cases"]["total"], 16)
        self.assertEqual(result["metrics"]["typed_operations"]["tp"], 11)

    def test_interruption_still_closes_workers(self):
        close = Mock()
        with self.assertRaises(KeyboardInterrupt):
            run.evaluate(self.corpus, "enhanced", 1, analyze=Mock(side_effect=KeyboardInterrupt), close=close)
        self.assertEqual(close.call_count, 2)

    def test_invalid_mode_or_trial_count_closes_without_analysis(self):
        for mode, trials in (("auto", 1), ("basic", 0), ("enhanced", 11)):
            analyze, close = Mock(), Mock()
            with self.subTest(mode=mode, trials=trials), self.assertRaises(run.EvaluationError):
                run.evaluate(self.corpus, mode, trials, analyze=analyze, close=close)
            analyze.assert_not_called(); close.assert_called_once()

    def test_main_fixture_failure_always_closes_and_returns_controlled_json(self):
        from surface_map import js_parser
        output = io.StringIO()
        with patch.object(run, "load_corpus", side_effect=ValueError("private details")), patch.object(js_parser, "close") as close, contextlib.redirect_stdout(output):
            self.assertEqual(run.main(["--mode", "enhanced", "--check"]), 2)
        self.assertEqual(json.loads(output.getvalue())["error"], "Evaluation could not complete")
        close.assert_called_once()

    def test_main_import_failures_return_fixed_json_and_conditionally_close(self):
        from surface_map import js_parser
        original = builtins.__import__
        for broken_name, closes in (("surface_map", 0), ("surface_map.analyze", 1)):
            def imported(name, *args, **kwargs):
                if name == broken_name:
                    raise ImportError("PRIVATE-IMPORT-DETAILS")
                return original(name, *args, **kwargs)
            output = io.StringIO()
            with self.subTest(broken_name=broken_name), patch.object(builtins, "__import__", imported), \
                 patch.object(js_parser, "close") as close, contextlib.redirect_stdout(output):
                self.assertEqual(run.main(["--mode", "enhanced"]), 2)
                self.assertEqual(close.call_count, closes)
            self.assertEqual(json.loads(output.getvalue())["error"], "Evaluation could not complete")
            self.assertNotIn("PRIVATE-IMPORT", output.getvalue())

    def test_main_keyboard_interrupt_is_controlled_but_system_exit_propagates(self):
        from surface_map import js_parser
        output = io.StringIO()
        with patch.object(run, "evaluate", side_effect=KeyboardInterrupt), patch.object(js_parser, "close") as close, contextlib.redirect_stdout(output):
            self.assertEqual(run.main(["--mode", "enhanced"]), 2)
        close.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())["error"], "Evaluation could not complete")
        with patch.object(run, "evaluate", side_effect=SystemExit(7)), patch.object(js_parser, "close") as close, self.assertRaises(SystemExit) as raised:
            run.main(["--mode", "enhanced"])
        self.assertEqual(raised.exception.code, 7)
        close.assert_called_once()

    def test_main_cleanup_interrupt_returns_fixed_exit_two(self):
        from surface_map import js_parser
        output = io.StringIO()
        with patch.object(run, "evaluate", return_value=self.evaluate()), patch.object(js_parser, "close", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(output):
            self.assertEqual(run.main(["--mode", "enhanced"]), 2)
        self.assertEqual(json.loads(output.getvalue())["error"], "Evaluation could not complete")

    def test_main_accuracy_and_analysis_error_exit_codes(self):
        from surface_map import js_parser
        pristine = self.evaluate()
        for passed, errors, check, expected in ((True, 0, True, 0), (False, 0, False, 0),
                                               (False, 0, True, 1), (False, 1, False, 2)):
            result = {**pristine, "passed": passed, "analysis_errors_all_passes": errors}
            with self.subTest(passed=passed, errors=errors, check=check), \
                 patch.object(run, "load_corpus", return_value=self.corpus), \
                 patch.object(run, "evaluate", return_value=result), patch.object(js_parser, "close") as close, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run.main(["--mode", "enhanced", *(["--check"] if check else [])]), expected)
                close.assert_called_once()

    def test_latency_units_and_sample_counts_from_injected_clock(self):
        clock = iter((0.0, .012, 2.0, 2.020))
        result = run.evaluate(self.corpus, "basic", 1, analyze=self.analyzer, close=Mock(), clock=lambda: next(clock))
        cold, warm = result["latency"]["cold_pass"], result["latency"]["warm_pass"]
        self.assertAlmostEqual(cold["median"], 12)
        self.assertAlmostEqual(warm["median"], 20)
        self.assertEqual((cold["samples"], warm["samples"], cold["case_calls_per_sample"]), (1, 1, 16))
        self.assertEqual(cold["unit"], "milliseconds per complete corpus pass")


if __name__ == "__main__":
    unittest.main()
