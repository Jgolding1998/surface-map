"""Independent real-Git checks for static browser navigation evidence.

Fixtures are data. The comparator must not execute them, open a destination,
publish their private values, or mistake an inspection gap for a lost link.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

from surface_map import ci, git_compare, js_parser
from surface_map.__main__ import main as cli_main


@unittest.skipUnless(js_parser.available()[0], "Pinned syntax parser unavailable")
class NavigationGitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name) / "application"
        self.repo.mkdir()
        self.canary = "private_" + uuid.uuid4().hex
        self.script = self.canary + ".js"
        self.page = self.canary + ".html"
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.name", "Synthetic Navigation Reviewer")
        self.git("config", "user.email", "reviewer@example.invalid")
        self.git("config", "core.autocrlf", "false")

    def git(self, *arguments):
        environment = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                           GIT_CONFIG_SYSTEM=os.devnull)
        completed = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false",
             "-c", "core.hooksPath=" + os.devnull, "-C", str(self.repo), *arguments],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, check=True, timeout=15)
        return completed.stdout.decode("utf-8").strip()

    def commit(self, contents):
        for name, text in contents.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Synthetic navigation fixture")
        return self.git("rev-parse", "HEAD")

    def pair(self, before, after, *, engine="enhanced", **options):
        base, head = self.commit(before), self.commit(after)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            report = git_compare.compare_git(self.repo, base, head,
                                             javascript_engine=engine, **options)
            markdown = git_compare.markdown(report)
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))
        self.assertTrue(report["analysis_performed"])
        self.assertIsNotNone(report["evidence_comparison"])
        encoded = ci.json_output(report)
        for private in (self.canary, str(self.repo), self.script, self.page):
            self.assertNotIn(private, encoded + markdown)
        return report, base, head

    @staticmethod
    def navigation(report):
        return report["review_signals"]["comparison"]["navigation"]

    def assert_no_navigation_changes(self, report):
        group = self.navigation(report)
        for change in ("added", "removed", "changed"):
            self.assertEqual(group[change], [], change)

    def assert_gap_cannot_pass(self, report):
        coverage = report["review_signals"]["coverage"]
        self.assertTrue(coverage["warnings"] or coverage["incomplete_sources"])
        self.assertEqual(report["policy"]["status"], "failed")
        self.assertFalse(report["policy"]["passed"])

    def test_four_literal_navigation_forms_disappear_without_inventing_endpoints(self):
        before = "\n".join((
            "location.assign('/assign_" + self.canary + "');",
            "location.replace('/replace_" + self.canary + "');",
            "window.location.href='/href_" + self.canary + "';",
            "window.open('https://external-" + self.canary + ".invalid/open');",
        ))
        report, _, _ = self.pair({self.script: before}, {self.script: "const ready=true;"},
                                  fail_on=("absent",))
        group = self.navigation(report)
        self.assertEqual((len(group["added"]), len(group["removed"])), (0, 4))
        self.assertEqual({row["evidence"][0]["location"] for row in group["removed"]},
                         {"line 1", "line 2", "line 3", "line 4"})
        self.assertTrue(all(row["confidence"] == "inferred" for row in group["removed"]))
        self.assertTrue(all("unverified_global_navigation" in row["assumptions"]
                            for row in group["removed"]))
        endpoints = report["review_signals"]["comparison"]["endpoint"]
        self.assertEqual((endpoints["added"], endpoints["removed"]), ([], []))
        self.assertIn("absent", report["policy"]["matched"])

    def test_same_path_domain_swap_stays_visible_and_private(self):
        template = "window.open('https://{}-" + self.canary + ".invalid/settings?token=" + self.canary + "');"
        report, _, _ = self.pair({self.script: template.format("first")},
                                  {self.script: template.format("second")},
                                  site_origin="https://owner.invalid")
        group = self.navigation(report)
        self.assertEqual((len(group["added"]), len(group["removed"])), (1, 1))
        added, removed = group["added"][0], group["removed"][0]
        self.assertEqual(added["path_id"], removed["path_id"])
        self.assertNotEqual(added["origin_id"], removed["origin_id"])
        self.assertEqual(added["attributes"][0]["origin_relation"], "cross_origin")

    def test_reformat_query_value_and_fragment_changes_are_quiet(self):
        before = "location.assign('/settings?token=" + self.canary + "#before');"
        after = "// formatting only\nlocation.assign( '/settings?token=other#after' );"
        report, _, _ = self.pair({self.script: before}, {self.script: after})
        self.assert_no_navigation_changes(report)
        self.assertEqual(self.navigation(report)["unchanged_count"], 1)
        self.assertEqual(report["review_signals"]["coverage"]["incomplete_sources"], [])

    def test_inline_disappearance_keeps_html_evidence_and_original_line(self):
        before = ('<a href="/dashboard">Home</a>\n<form action="/submit"><input name="email"></form>\n'
                  '<script>\nlocation.replace("/next_' + self.canary + '");\n</script>')
        after = ('<a href="/dashboard">Home</a>\n<form action="/submit"><input name="email"></form>\n'
                 '<script>const ready=true;</script>')
        report, _, _ = self.pair({self.page: before}, {self.page: after})
        group = self.navigation(report)
        self.assertEqual(len(group["removed"]), 1)
        self.assertEqual(group["removed"][0]["evidence"], [{"source": "source_1", "location": "line 4"}])
        self.assertEqual(group["unchanged_count"], 1)
        self.assertEqual(report["review_signals"]["comparison"]["form_field"]["unchanged_count"], 1)

    def test_comments_strings_and_shadowed_objects_do_not_create_lost_links(self):
        before = ('const example="location.assign(\'/fake_' + self.canary + '\')";\n'
                  '// window.open("/comment");\n'
                  'function local(location,window){location.assign("/local");window.open("/local2");'
                  'window.location.href="/local3";}')
        report, _, _ = self.pair({self.script: before}, {self.script: "const ready=true;"})
        self.assert_no_navigation_changes(report)

    def test_dynamic_destination_cannot_quietly_pass_warning_policy(self):
        before = "location.assign(window.userDestination);"
        after = "location.assign(window.otherDestination);"
        report, _, _ = self.pair({self.script: before}, {self.script: after}, fail_on=("warnings",))
        self.assert_no_navigation_changes(report)
        self.assert_gap_cannot_pass(report)

    def test_dynamic_default_is_advisory_and_readonly_location_uses_keep_literal_hints(self):
        dynamic = "location.assign(window.userDestination);window.open(window.otherDestination);"
        report, _, _ = self.pair({self.script: dynamic}, {self.script: dynamic}, files=[self.script])
        coverage = report["review_signals"]["coverage"]
        self.assertEqual(report["policy"]["status"], "advisory")
        self.assertTrue(coverage["warnings"])
        self.assertTrue(coverage["omissions"])
        self.assertEqual(coverage["incomplete_sources"], [])
        self.assert_no_navigation_changes(report)
        endpoints = report["review_signals"]["comparison"]["endpoint"]
        self.assertEqual((endpoints["added"], endpoints["removed"], endpoints["changed"]), ([], [], []))

        readonly = ('const {pathname}=window.location;\n'
                    'const parsed=new URL(window.location);\n'
                    'location.hash="#fragment_' + self.canary + '";\n')
        before = readonly + 'window.open("/settings_' + self.canary + '");'
        report, _, _ = self.pair({self.script: before}, {self.script: readonly}, fail_on=("warnings",))
        self.assertEqual(report["policy"]["status"], "passed")
        self.assertEqual(report["review_signals"]["coverage"]["warnings"], [])
        self.assertEqual(report["review_signals"]["coverage"]["incomplete_sources"], [])
        self.assertEqual(len(self.navigation(report)["removed"]), 1)

    def test_same_and_cross_file_global_mutation_cannot_fabricate_disappearance(self):
        for mutation in ("window.open=customOpen;", "open=customOpen;"):
            for cross_file in (False, True):
                with self.subTest(mutation=mutation, cross_file=cross_file):
                    call = "window.open('/private_" + self.canary + "');"
                    if cross_file:
                        before = {"a_" + self.script: call, "b_" + self.script: mutation}
                        after = {"a_" + self.script: "const ready=true;", "b_" + self.script: mutation}
                        files = sorted(before)
                    else:
                        before, after = {self.script: mutation + call}, {self.script: mutation}
                        files = [self.script]
                    report, _, _ = self.pair(before, after, files=files, fail_on=("warnings",))
                    self.assertEqual(self.navigation(report)["removed"], [])
                    self.assert_gap_cannot_pass(report)

    def test_page_local_call_loss_is_not_hidden_by_a_surviving_peer(self):
        first, second = "a_" + self.script, "b_" + self.script
        call = "window.location.href='/settings';"
        report, _, _ = self.pair({first: call, second: call},
                                  {first: "const ready=true;", second: call}, files=[first, second])
        group = self.navigation(report)
        self.assertEqual((len(group["removed"]), group["unchanged_count"]), (1, 1))
        self.assertEqual(group["removed"][0]["evidence"][0]["source"], "source_1")

    def test_non_web_destination_and_basic_mode_have_explicit_coverage(self):
        for text, engine in (("location.assign('javascript:alert(\"" + self.canary + "\")');", "enhanced"),
                             ("window.open('/settings');", "basic")):
            with self.subTest(engine=engine):
                report, _, _ = self.pair({self.script: text}, {self.script: "const ready=true;"},
                                          engine=engine, fail_on=("warnings",))
                self.assert_no_navigation_changes(report)
                self.assert_gap_cannot_pass(report)

    def test_source_is_never_executed_and_cli_channels_remain_private(self):
        sentinel = self.repo.parent / (self.canary + "_must_not_exist")
        never_execute = "require('node:fs').writeFileSync(" + json.dumps(str(sentinel)) + ",'unexpected');"
        before = never_execute + "\nwindow.open('https://" + self.canary + ".invalid/path?secret=" + self.canary + "');"
        report, base, head = self.pair({self.script: before}, {self.script: never_execute})
        self.assertFalse(sentinel.exists())
        self.assertEqual(len(self.navigation(report)["removed"]), 1)
        for output_format in ("json", "markdown"):
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli_main(["compare-git", "--repo", str(self.repo), "--base", base, "--head", head,
                                 "--javascript-engine", "enhanced", "--format", output_format, "--fail-on", "absent"])
            self.assertEqual((code, stderr.getvalue()), (1, ""))
            self.assertNotIn(self.canary, stdout.getvalue() + stderr.getvalue())
            self.assertNotIn(str(sentinel), stdout.getvalue() + stderr.getvalue())
            self.assertFalse(sentinel.exists())


if __name__ == "__main__":
    unittest.main()
