"""User-requested PR review scenarios through real, local Git commits.

These are transparent fixtures, not customer trials or an accuracy estimate.
They deliberately cover two changes privacy normalization cannot distinguish.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from surface_map import ci, git_compare


CANARY = "PRIVATE_PR_REVIEW_CANARY_56918"
PRIVATE_DOMAIN = "private-pr-review-canary.invalid"
PRIVATE_FILE = "private_" + CANARY + ".html"


class PRFeedbackCaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "fixture"
        self.repo.mkdir()
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.name", "Synthetic PR Reviewer")
        self.git("config", "user.email", "reviewer@example.invalid")
        self.git("config", "core.autocrlf", "false")

    def git(self, *arguments):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_SYSTEM=os.devnull)
        completed = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false",
             "-c", "core.hooksPath=" + os.devnull, "-C", str(self.repo), *arguments],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=True, timeout=15, shell=False)
        return completed.stdout.decode("utf-8").strip()

    def commit(self, text, *, name=PRIVATE_FILE):
        (self.repo / name).write_text(text, encoding="utf-8")
        self.git("add", "--", name)
        self.git("commit", "--quiet", "--allow-empty", "-m", "Synthetic review scenario")
        return self.git("rev-parse", "HEAD")

    def compare_text(self, before, after, *, name=PRIVATE_FILE, explicit=False):
        base = self.commit(before, name=name)
        head = self.commit(after, name=name)
        options = {"files": [name]} if explicit else {}
        report = git_compare.compare_git(self.repo, base, head,
                                         javascript_engine="basic", **options)
        self.assertTrue(report["analysis_performed"])
        self.assertEqual(report["base_commit"], base)
        self.assertEqual(report["head_commit"], head)
        output = ci.json_output(report) + git_compare.markdown(report)
        for raw in (CANARY, PRIVATE_DOMAIN, name, str(self.repo)):
            self.assertNotIn(raw, output)
        self.assertIn("not proven bugs", report["scope"])
        return report

    @staticmethod
    def operations(rows):
        return {(row["method"], row["path"]) for row in rows}

    def assert_no_review_change(self, report):
        evidence = report["evidence_comparison"]
        delta = evidence["comparison"]
        for key in ("added", "removed", "changed", "unresolved_added",
                    "unresolved_removed", "unresolved_changed"):
            self.assertEqual(delta.get(key, []), [], key)
        for key in ("added", "removed"):
            self.assertEqual(evidence["asset_comparison"][key], [], key)

    def test_literal_form_method_change_is_visible(self):
        report = self.compare_text(
            '<form action="/api/orders" method="get"><input name="order_id"></form>',
            '<form action="/api/orders" method="post"><input name="order_id"></form>')
        delta = report["evidence_comparison"]["comparison"]
        self.assertEqual(self.operations(delta["added"]), {("POST", "/api/orders")})
        self.assertEqual(self.operations(delta["removed"]), {("GET", "/api/orders")})

    def test_literal_form_target_change_is_visible(self):
        prefix = '<form action="https://' + PRIVATE_DOMAIN
        suffix = '?token=' + CANARY + '" method="post"><input name="email"></form>'
        report = self.compare_text(prefix + '/api/login' + suffix,
                                   prefix + '/api/register' + suffix)
        delta = report["evidence_comparison"]["comparison"]
        self.assertEqual(self.operations(delta["added"]), {("POST", "/api/register")})
        self.assertEqual(self.operations(delta["removed"]), {("POST", "/api/login")})

    def test_added_hidden_email_field_changes_declared_shape_without_value_leak(self):
        before = '<form action="/api/orders" method="post"><input name="order_id"></form>'
        after = before.replace('</form>', '<input type="hidden" name="email" value="'
                               + CANARY + '"></form>')
        report = self.compare_text(before, after)
        delta = report["evidence_comparison"]["comparison"]
        self.assertEqual(self.operations(delta["changed"]), {("POST", "/api/orders")})
        self.assertIn("request_shapes", delta["changed"][0]["changed_fields"])
        self.assertEqual(delta["added"], [])
        self.assertEqual(delta["removed"], [])

    def test_missing_static_navigation_is_review_evidence_not_proven_deletion(self):
        report = self.compare_text('<a href="/dashboard">Dashboard</a><p>Body</p>',
                                   '<p>Body</p>')
        absent = report["evidence_comparison"]["comparison"]["removed"]
        self.assertEqual(self.operations(absent), {("GET", "/dashboard")})
        self.assertEqual(absent[0]["absence_context"], ["not_seen_in_supplied_capture"])
        self.assertIn("absent evidence does not prove removal", report["scope"])

    def test_missing_literal_fetch_is_reported_without_running_it(self):
        report = self.compare_text("fetch('/api/events');", "const enabled = true;",
                                   name="private_" + CANARY + ".js")
        absent = report["evidence_comparison"]["comparison"]["removed"]
        self.assertEqual(self.operations(absent), {("GET", "/api/events")})
        self.assertIn("Running pages and server behavior are never checked", report["scope"])

    def test_new_normalized_script_reference_is_reported(self):
        report = self.compare_text('<p>Body</p>', '<p>Body</p>\n<script src="https://'
                                   + PRIVATE_DOMAIN + '/assets/app.js?token=' + CANARY
                                   + '"></script>')
        assets = report["evidence_comparison"]["asset_comparison"]
        self.assertEqual([(r["kind"], r["path"]) for r in assets["added"]],
                         [("script", "/assets/app.js")])
        self.assertEqual(assets["added"][0]["evidence"],
                         [{"source": "source_1", "location": "line 2"}])

    def test_missing_normalized_script_reference_is_reported(self):
        report = self.compare_text('<script src="/assets/app.js"></script><p>Body</p>',
                                   '<p>Body</p>')
        assets = report["evidence_comparison"]["asset_comparison"]
        self.assertEqual([(r["kind"], r["path"]) for r in assets["removed"]],
                         [("script", "/assets/app.js")])
        self.assertIn("not proven deleted", assets["scope"])

    def test_unchanged_explicit_selection_is_quiet(self):
        source = '<a href="/dashboard">Dashboard</a><script src="/assets/app.js"></script>'
        report = self.compare_text(source, source, explicit=True)
        self.assert_no_review_change(report)
        self.assertEqual(report["policy"]["status"], "advisory")

    def test_whitespace_only_change_is_quiet(self):
        report = self.compare_text(
            '<form action="/api/orders" method="post"><input name="email"></form>'
            '<script src="/assets/app.js"></script>',
            '\n<form action="/api/orders" method="post">\n  <input name="email">\n</form>\n'
            '<script src="/assets/app.js"></script>\n')
        self.assert_no_review_change(report)

    def test_unknown_field_rename_is_invisible_and_must_not_be_claimed_detected(self):
        template = '<form action="/api/orders" method="post"><input name="{}"></form>'
        report = self.compare_text(template.format("private_first_" + CANARY),
                                   template.format("private_second_" + CANARY))
        self.assert_no_review_change(report)
        self.assertIn("Privacy normalization can hide route or field renames", report["scope"])

    def test_same_path_script_domain_swap_is_invisible_and_explicitly_scoped(self):
        report = self.compare_text(
            '<script src="https://' + PRIVATE_DOMAIN + '/assets/app.js"></script>',
            '<script src="https://second-' + PRIVATE_DOMAIN + '/assets/app.js"></script>')
        self.assert_no_review_change(report)
        assets = report["evidence_comparison"]["asset_comparison"]
        self.assertEqual(assets["unchanged_count"], 1)
        self.assertIn("domain-only swaps", assets["scope"])


if __name__ == "__main__":
    unittest.main()
