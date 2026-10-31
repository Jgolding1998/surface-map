import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from surface_map.__main__ import main as cli_main

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("git_ci_action", ROOT / "scripts" / "git_ci_action.py")
ACTION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACTION)
CANARY = "PRIVATE_GIT_ACTION_CANARY_5028"


class GitActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.repo = self.workspace / "app"
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Synthetic Tester")
        self.git("config", "user.email", "tester@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.base = self.commit('<form action="/api/orders" method="get"><input name="order_id"></form>')
        self.head = self.commit('<form action="/api/orders" method="post"><input type="hidden" name="email" value="'
                                + CANARY + '"></form><a href="/reports">Reports</a>')
        self.env = {"GITHUB_WORKSPACE": str(self.workspace), "SURFACE_MAP_REPOSITORY": "app",
                    "SURFACE_MAP_BASE": self.base, "SURFACE_MAP_HEAD": self.head,
                    "SURFACE_MAP_ENGINE": "basic", "SURFACE_MAP_OUTPUT_DIRECTORY": "reports",
                    "GITHUB_OUTPUT": str(self.workspace / "outputs"),
                    "GITHUB_STEP_SUMMARY": str(self.workspace / "summary")}

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *arguments):
        env = dict(os.environ)
        env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
        result = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + os.devnull,
                                 "-C", str(self.repo), *arguments], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, check=True, timeout=15)
        return result.stdout.decode().strip()

    def commit(self, html):
        (self.repo / "page.html").write_text(html, encoding="utf-8")
        self.git("add", "--", "page.html")
        self.git("commit", "--quiet", "--allow-empty", "-m", "synthetic change")
        return self.git("rev-parse", "HEAD")

    def invoke(self, **overrides):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = ACTION.main({**self.env, **overrides})
        return code, stdout.getvalue(), stderr.getvalue()

    def test_real_git_action_writes_private_reports_and_advisory_summary(self):
        code, stdout, stderr = self.invoke()
        self.assertEqual((code, stderr), (0, ""))
        result = json.loads((self.workspace / "reports/comparison.json").read_text())
        self.assertTrue(result["analysis_performed"])
        self.assertEqual(result["policy"]["status"], "advisory")
        self.assertTrue(result["evidence_comparison"]["comparison"]["added"])
        summary = (self.workspace / "summary").read_text()
        self.assertIn("Effective merge base", summary)
        self.assertIn("Static asset references", summary)
        self.assertIn("domain-only swaps", summary)
        outputs = (self.workspace / "outputs").read_text()
        self.assertIn("analysis-performed=true\n", outputs)
        self.assertIn("policy-passed=\n", outputs)
        self.assertNotIn(CANARY, stdout + stderr + summary + json.dumps(result) + outputs)
        self.assertNotIn("page.html", summary + json.dumps(result))

    def test_selected_policy_fails_after_reports_are_written(self):
        code, _, stderr = self.invoke(SURFACE_MAP_FAIL_ON="added")
        self.assertEqual((code, stderr), (1, ""))
        self.assertTrue((self.workspace / "reports/comparison.json").is_file())
        self.assertIn("policy-status=failed", (self.workspace / "outputs").read_text())

    def test_no_supported_changes_is_explicit_not_applicable(self):
        code, _, stderr = self.invoke(SURFACE_MAP_BASE=self.head)
        self.assertEqual((code, stderr), (0, ""))
        result = json.loads((self.workspace / "reports/comparison.json").read_text())
        self.assertFalse(result["analysis_performed"])
        self.assertIsNone(result["policy"]["passed"])
        self.assertEqual(result["policy"]["status"], "not_applicable")
        self.assertIn("not an all-clear", (self.workspace / "summary").read_text())
        self.assertIn("analysis-performed=false", (self.workspace / "outputs").read_text())

    def test_invalid_references_and_repository_escape_do_not_leak_or_write(self):
        for overrides in ({"SURFACE_MAP_BASE": CANARY}, {"SURFACE_MAP_REPOSITORY": "../" + CANARY},
                          {"SURFACE_MAP_FILES": "page.html\n\n"}, {"SURFACE_MAP_FAIL_ON": CANARY}):
            with self.subTest(overrides=list(overrides)):
                code, stdout, stderr = self.invoke(**overrides)
                self.assertEqual((code, stdout), (2, ""))
                self.assertNotIn(CANARY, stderr)
                self.assertNotIn(str(self.workspace), stderr)
                self.assertFalse((self.workspace / "reports").exists())
                self.assertFalse((self.workspace / "summary").exists())

    def test_existing_destination_is_never_overwritten(self):
        target = self.workspace / "reports"
        target.mkdir()
        (target / "sentinel").write_text(CANARY)
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual((target / "sentinel").read_text(), CANARY)
        self.assertFalse((self.workspace / "outputs").exists())

    def test_explicit_selection_analyzes_unchanged_source(self):
        code, _, stderr = self.invoke(SURFACE_MAP_BASE=self.head, SURFACE_MAP_FILES="page.html")
        self.assertEqual((code, stderr), (0, ""))
        result = json.loads((self.workspace / "reports/comparison.json").read_text())
        self.assertTrue(result["analysis_performed"])
        self.assertEqual(result["evidence_comparison"]["comparison"]["added"], [])

    def test_registered_cli_reads_real_git_and_rejects_private_reference(self):
        out, err = io.StringIO(), io.StringIO()
        arguments = ["compare-git", "--repo", str(self.repo), "--base", self.base,
                     "--head", self.head, "--javascript-engine", "basic", "--format", "markdown"]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(arguments)
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertIn("static PR evidence", out.getvalue())
        self.assertNotIn(CANARY, out.getvalue())
        arguments[arguments.index(self.base)] = CANARY
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(arguments)
        self.assertEqual(code, 2)
        self.assertNotIn(CANARY, err.getvalue())

    def test_origin_file_classifies_without_exporting_origin_in_any_channel(self):
        base = self.commit('<script src="/static/app.js"></script>')
        head = self.commit('<script src="https://cdn-' + CANARY.lower() + '.invalid/static/app.js"></script>')
        metadata = self.workspace / "origin.txt"
        metadata.write_text(" https://first-" + CANARY.lower() + ".invalid:443\n", encoding="utf-8")
        code, stdout, stderr = self.invoke(SURFACE_MAP_BASE=base, SURFACE_MAP_HEAD=head,
                                           SURFACE_MAP_SITE_ORIGIN_FILE="origin.txt")
        self.assertEqual((code, stderr), (0, ""))
        result = json.loads((self.workspace / "reports/comparison.json").read_text())
        self.assertTrue(result["review_signals"]["coverage"]["site_origin_supplied"])
        script = result["review_signals"]["comparison"]["script"]["added"][0]
        self.assertEqual(script["attributes"][0]["origin_relation"], "cross_origin")
        channels = stdout + stderr + "".join(path.read_text() for path in (
            self.workspace / "reports/comparison.json", self.workspace / "reports/comparison.md",
            self.workspace / "outputs", self.workspace / "summary"))
        self.assertNotIn(CANARY.lower(), channels.lower())
        self.assertNotIn("origin.txt", channels)

    def test_domain_only_swap_triggers_policy_when_legacy_view_is_quiet(self):
        template = '<script src="https://{}.invalid/static/app.js"></script>'
        base, head = self.commit(template.format("first")), self.commit(template.format("second"))
        code, _, stderr = self.invoke(SURFACE_MAP_BASE=base, SURFACE_MAP_HEAD=head, SURFACE_MAP_FAIL_ON="absent")
        self.assertEqual((code, stderr), (1, ""))
        result = json.loads((self.workspace / "reports/comparison.json").read_text())
        self.assertEqual(result["evidence_comparison"]["policy"]["matched"], [])
        self.assertEqual(result["policy"]["matched"], ["absent"])
        self.assertIn("Combined policy status: **failed**", (self.workspace / "summary").read_text())

    def test_origin_file_rejects_private_bad_contents_without_outputs(self):
        for contents in ("https://user:" + CANARY + "@site.invalid", "https://site.invalid/" + CANARY,
                         CANARY * 300, "https://site.invalid\n" + CANARY, ""):
            with self.subTest(length=len(contents)):
                (self.workspace / "origin.txt").write_text(contents, encoding="utf-8")
                code, stdout, stderr = self.invoke(SURFACE_MAP_SITE_ORIGIN_FILE="origin.txt")
                self.assertEqual((code, stdout), (2, ""))
                self.assertNotIn(CANARY, stderr)
                for target in ("reports", "summary", "outputs"):
                    self.assertFalse((self.workspace / target).exists())

    def test_origin_file_refuses_escape_missing_and_directory(self):
        (self.workspace / "directory").mkdir()
        for name in ("../" + CANARY, str(self.workspace / CANARY), "missing", "directory"):
            with self.subTest(kind=name[:10]):
                code, stdout, stderr = self.invoke(SURFACE_MAP_SITE_ORIGIN_FILE=name)
                self.assertEqual((code, stdout), (2, ""))
                self.assertNotIn(CANARY, stderr)
                self.assertFalse((self.workspace / "reports").exists())

    def test_origin_file_refuses_symbolic_link(self):
        target = self.workspace / "origin.txt"
        target.write_text("https://" + CANARY.lower() + ".invalid", encoding="utf-8")
        link = self.workspace / "linked.txt"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("Symbolic link creation unavailable")
        code, stdout, stderr = self.invoke(SURFACE_MAP_SITE_ORIGIN_FILE="linked.txt")
        self.assertEqual((code, stdout), (2, ""))
        self.assertNotIn(CANARY.lower(), stderr.lower())
        self.assertFalse((self.workspace / "reports").exists())

    def test_supplemental_failure_retains_report_and_cannot_pass(self):
        with patch("surface_map.pr_html_signals.collect_html", side_effect=ValueError(CANARY)):
            code, stdout, stderr = self.invoke()
        self.assertEqual((code, stderr), (1, ""))
        result = json.loads((self.workspace / "reports/comparison.json").read_text())
        self.assertTrue(result["evidence_comparison"])
        self.assertEqual(result["policy"]["matched"], ["incomplete"])
        self.assertTrue(result["review_signals"]["coverage"]["incomplete_sources"])
        channels = stdout + stderr + (self.workspace / "summary").read_text() + json.dumps(result)
        self.assertNotIn(CANARY, channels)


if __name__ == "__main__":
    unittest.main()
