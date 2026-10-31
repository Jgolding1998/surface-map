import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from surface_map import ci
from surface_map.__main__ import main
from surface_map.analyze import ArtifactError


CANARY = "PRIVATE_CI_CANARY_7629"


def html_capture(text, **context):
    return {"artifacts": [{"kind": "html", "text": text}], **context}


def js_capture(text):
    return {"artifacts": [{"kind": "javascript", "text": text}]}


class OfflineCITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def capture(self, document, name="capture.json"):
        path = self.directory / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path)

    def invoke(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def compare(self, before, after, *extra):
        return self.invoke(["compare", "--before", self.capture(before, "before.json"),
                            "--after", self.capture(after, "after.json"),
                            "--javascript-engine", "basic", *extra])

    def test_analyze_offline_redacts_values_origins_paths_and_never_fetches(self):
        document = html_capture(
            '<form action="https://' + CANARY + '.invalid/api/orders?token=' + CANARY + '" method="post">'
            '<input name="email" value="' + CANARY + '"></form><!-- ' + CANARY + ' -->')
        with patch("socket.create_connection", side_effect=AssertionError(CANARY)), \
                patch("urllib.request.urlopen", side_effect=AssertionError(CANARY)):
            code, stdout, stderr = self.invoke(["analyze", "--capture", self.capture(document, CANARY + ".json")])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["routes"][0]["method"], "POST")
        self.assertNotIn(CANARY, stdout + stderr)
        self.assertNotIn(str(self.directory), stdout + stderr)

    def test_analysis_engine_defaults_to_required_enhanced(self):
        path = self.capture(html_capture("<p>Saved content</p>"))
        with patch.object(ci, "analyze", return_value={"routes": []}) as backend:
            self.assertEqual(self.invoke(["analyze", "--capture", path])[0], 0)
        self.assertEqual(backend.call_args.kwargs["javascript_engine"], "enhanced")

    def test_advisory_emits_real_additions_and_absences_with_zero_exit(self):
        code, stdout, stderr = self.compare(html_capture('<a href="/dashboard">Home</a>'),
                                            html_capture('<a href="/login">Sign in</a>'))
        self.assertEqual((code, stderr), (0, ""))
        report = json.loads(stdout)
        self.assertEqual(report["comparison"]["added"][0]["path"], "/login")
        self.assertEqual(report["comparison"]["removed"][0]["path"], "/dashboard")
        self.assertEqual(report["policy"], {"fail_on": [], "matched": [], "passed": True, "status": "advisory"})
        self.assertIn("does not establish equivalent capture coverage", report["scope"])

    def test_selected_policies_exit_one_and_unmatched_policies_pass(self):
        before = html_capture('<a href="/dashboard">Home</a>')
        after = html_capture('<a href="/login">Sign in</a>')
        code, stdout, _ = self.compare(before, after, "--fail-on", "absent,added,added")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["policy"]["matched"], ["added", "absent"])
        self.assertEqual(self.compare(before, after, "--fail-on", "changed")[0], 0)

    def test_changed_query_fields_trigger_changed_but_line_moves_do_not(self):
        before = html_capture('<a href="/api/orders?page=1">Orders</a>')
        moved = html_capture('\n\n<a href="/api/orders?page=1">Orders</a>')
        self.assertEqual(self.compare(before, moved, "--fail-on", "changed,added,absent")[0], 0)
        code, stdout, _ = self.compare(before, html_capture('<a href="/api/orders?limit=1">Orders</a>'),
                                       "--fail-on", "changed")
        self.assertEqual(code, 1)
        self.assertIn("query_fields", json.loads(stdout)["comparison"]["changed"][0]["changed_fields"])

    def test_unchanged_unknown_method_is_preserved_and_explicit_policy_matches(self):
        document = js_capture("fetch('/api/orders', options);")
        code, stdout, stderr = self.compare(document, document, "--fail-on", "unresolved")
        self.assertEqual(code, 1, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["after"]["unresolved_operations"][0]["method"], None)
        self.assertEqual(report["policy"]["matched"], ["unresolved"])
        self.assertEqual(report["comparison"].get("unresolved_added"), [])
        self.assertIn("/api/orders", ci.markdown(report))

    def test_added_absent_and_changed_policies_cover_unknown_method_targets(self):
        empty = js_capture("const value = 1;")
        unresolved = js_capture("fetch('/api/orders', options);")
        changed = js_capture("fetch('/api/orders?page=1', options);")
        for before, after, policy in ((empty, unresolved, "added"), (unresolved, empty, "absent"),
                                      (unresolved, changed, "changed")):
            with self.subTest(policy=policy):
                code, stdout, stderr = self.compare(before, after, "--fail-on", policy)
                self.assertEqual(code, 1, stderr)
                self.assertEqual(json.loads(stdout)["policy"]["matched"], [policy])
        self.assertEqual(self.compare(unresolved, unresolved, "--fail-on", "added,absent,changed")[0], 0)

    def test_warnings_policy_matches_either_capture_and_advisory_still_passes(self):
        before = html_capture('<a href="relative">Unknown base</a>')
        after = html_capture("<p>No links</p>")
        code, stdout, _ = self.compare(before, after, "--fail-on", "warnings")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout)["policy"]["matched"], ["warnings"])
        self.assertEqual(self.compare(before, after)[0], 0)

    def test_coverage_kind_counts_and_document_context_must_match(self):
        before = html_capture('<a href="/dashboard">Home</a>')
        mismatches = [
            js_capture("fetch('/dashboard');"),
            {"artifacts": before["artifacts"] * 2},
            html_capture('<a href="/dashboard">Home</a>', document_url="https://example.invalid/"),
        ]
        for after in mismatches:
            with self.subTest(after=after):
                code, stdout, stderr = self.compare(before, after)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertIn("coverage", stderr)

    def test_different_preview_origins_are_permitted_with_matching_document_context(self):
        before = html_capture('<a href="orders">Orders</a>', document_url="https://base.invalid/api/")
        after = html_capture('<a href="orders">Orders</a>', document_url="https://pr.invalid/api/")
        code, stdout, stderr = self.compare(before, after, "--fail-on", "changed,added,absent")
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("base.invalid", stdout)
        self.assertNotIn("pr.invalid", stdout)

    def test_normalization_limit_is_explicit_instead_of_claiming_endpoint_identity(self):
        before = html_capture('<a href="/api/private-one">One</a>')
        after = html_capture('<a href="/api/private-two">Two</a>')
        code, stdout, _ = self.compare(before, after, "--fail-on", "changed,added,absent")
        report = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertIn("hide changes to unknown names", report["scope"])

    def test_evidence_retention_truncation_is_not_a_passing_comparison(self):
        document = html_capture("\n".join('<a href="/dashboard">Home</a>' for _ in range(40)))
        report = ci.analyze_capture(document, javascript_engine="basic")
        self.assertTrue(report["evidence_limits"]["truncated"])
        code, stdout, stderr = self.compare(document, document)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("incomplete", stderr)

    def test_effective_parser_mismatch_is_rejected(self):
        before = ci.analyze_capture(html_capture("<p>One</p>"), javascript_engine="basic")
        after = deepcopy(before)
        before["javascript_analysis"] = [{"source": "source_1", "engine": "basic"}]
        after["javascript_analysis"] = [{"source": "source_1", "engine": "enhanced"}]
        with patch.object(ci, "analyze", side_effect=[before, after]):
            with self.assertRaises(ci.CIError):
                ci.compare_captures(html_capture(""), html_capture(""), javascript_engine="auto")

    def test_unknown_keys_duplicate_keys_and_invalid_values_are_private_errors(self):
        invalid = [
            '{"artifacts":[],"artifacts":[]}',
            json.dumps({"artifacts": [], CANARY: CANARY}),
            json.dumps({"artifacts": [{"kind": "html", "text": "", CANARY: CANARY}]}),
            json.dumps({"artifacts": [{"kind": "html", "text": CANARY}], "document_url": None}),
            '{"artifacts":[{"kind":"html","text":"\\ud800"}]}',
            '{' + CANARY,
        ]
        path = self.directory / (CANARY + ".json")
        for text in invalid:
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                code, stdout, stderr = self.invoke(["analyze", "--capture", str(path)])
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertNotIn(CANARY, stderr)
                self.assertNotIn(str(path), stderr)
                self.assertNotIn("Traceback", stderr)

    def test_invalid_policy_is_not_silently_advisory(self):
        document = html_capture("<p>Saved page</p>")
        for value in (CANARY, "added,", "all", "ADDED"):
            with self.subTest(value=value):
                code, stdout, stderr = self.compare(document, document, "--fail-on", value)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertNotIn(CANARY, stderr)

    def test_parser_unavailable_and_unexpected_failure_are_minimized(self):
        path = self.capture(js_capture("fetch('/api/orders');"), CANARY + ".json")
        for failure in (ArtifactError(CANARY), RuntimeError(CANARY)):
            with self.subTest(failure=type(failure).__name__), patch.object(ci, "analyze", side_effect=failure):
                code, stdout, stderr = self.invoke(["analyze", "--capture", path])
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertNotIn(CANARY, stderr)
            self.assertNotIn("Traceback", stderr)

    def test_real_required_parser_unavailability_never_falls_back(self):
        from surface_map import js_parser
        with patch.object(js_parser, "available", return_value=(False, "unavailable")):
            with self.assertRaises(ci.CIError):
                ci.analyze_capture(js_capture("fetch('/api/orders');"))

    def test_shared_analysis_deadline_is_preserved(self):
        document = html_capture("<p>Saved page</p>")
        with patch.object(ci, "ANALYSIS_SECONDS", -1):
            code, stdout, stderr = self.compare(document, document)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn("Traceback", stderr)

    def test_output_bound_rejects_before_stdout_for_json_and_markdown(self):
        document = html_capture('<a href="/dashboard">Home</a>')
        with patch.object(ci, "MAX_OUTPUT_BYTES", 32):
            for arguments in (["analyze", "--capture", self.capture(document)],
                              ["compare", "--before", self.capture(document, "before.json"),
                               "--after", self.capture(document, "after.json"), "--format", "markdown"]):
                with self.subTest(arguments=arguments):
                    code, stdout, stderr = self.invoke(arguments)
                    self.assertEqual(code, 2)
                    self.assertEqual(stdout, "")
                    self.assertIn("byte limit", stderr)
        result = ci.compare_captures(document, document, javascript_engine="basic")
        with patch.object(ci, "MAX_OUTPUT_BYTES", 32), self.assertRaises(ci.CIError):
            ci.markdown(result)

    def test_json_bound_counts_escaping_and_final_newline(self):
        with patch.object(ci, "MAX_OUTPUT_BYTES", 8):
            self.assertEqual(ci.json_output("12345"), '"12345"')
            with self.assertRaises(ci.CIError):
                ci.json_output("123456")
            with self.assertRaises(ci.CIError):
                ci.json_output("\u20ac\u20ac")

    def test_capture_size_limit_is_applied_before_decoding_or_analysis(self):
        path = self.capture(html_capture("<p>Saved page</p>"))
        with patch.object(ci, "MAX_CAPTURE_BYTES", 8), patch.object(ci, "analyze") as backend:
            code, stdout, _ = self.invoke(["analyze", "--capture", path])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        backend.assert_not_called()

    def test_markdown_has_counts_changed_fields_evidence_and_escaped_cells(self):
        before = html_capture('<a href="/api/orders?page=1">Orders</a>')
        after = html_capture('<a href="/api/orders?limit=1">Orders</a>')
        code, stdout, stderr = self.compare(before, after, "--format", "markdown", "--fail-on", "changed")
        self.assertEqual(code, 1, stderr)
        self.assertIn("Changed evidence | 1", stdout)
        self.assertIn("query\\_fields", stdout)
        self.assertIn("source\\_1", stdout)
        result = ci.compare_captures(before, after, javascript_engine="basic")
        malicious = '<script>alert(1)</script>|[click](https://evil.invalid)\n# injected'
        result["comparison"]["changed"][0]["path"] = malicious
        result["after"]["warnings"] = [{"source": malicious, "reason": malicious}]
        rendered = ci.markdown(result)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("[click](", rendered)
        self.assertNotIn("\n# injected", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("\\|", rendered)

    def test_markdown_explicitly_counts_summary_rows_it_omits(self):
        result = ci.compare_captures(html_capture(""), html_capture(""), javascript_engine="basic")
        result["comparison"]["added"] = [{"method": "GET", "path": "/api/orders", "evidence": []}] * 3
        with patch.object(ci, "MAX_MARKDOWN_ROWS", 1):
            rendered = ci.markdown(result)
        self.assertIn("Added evidence | 3", rendered)
        self.assertIn("2 additional rows are omitted", rendered)


if __name__ == "__main__":
    unittest.main()
