"""Independent real-Git acceptance cases for Rik's five review requests.

These verify static source evidence and privacy, not runtime behavior or user
benefit. They do not execute any of the selected application source.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

from surface_map import ci, git_compare, js_parser
from surface_map.__main__ import main as cli_main


ROOT = Path(__file__).resolve().parents[1]
READY = js_parser.available()[0]


class RikAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "application"
        self.repo.mkdir()
        self.canary = "private_" + uuid.uuid4().hex
        self.page = self.canary + ".html"
        self.script = self.canary + ".js"
        self.report_sequence = 0
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.name", "Synthetic Reviewer")
        self.git("config", "user.email", "reviewer@example.invalid")
        self.git("config", "core.autocrlf", "false")

    def git(self, *arguments):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_SYSTEM=os.devnull)
        result = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false",
                                 "-c", "core.hooksPath=" + os.devnull, "-C", str(self.repo), *arguments],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=True, shell=False, timeout=15)
        return result.stdout.decode("utf-8").strip()

    def commit(self, files):
        for name, value in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if value is None:
                path.unlink()
            elif type(value) is bytes:
                path.write_bytes(value)
            else:
                path.write_text(value, encoding="utf-8")
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Synthetic acceptance case")
        return self.git("rev-parse", "HEAD")

    def pair(self, before, after, *, engine="basic", **options):
        base, head = self.commit(before), self.commit(after)
        out, err = io.StringIO(), io.StringIO()
        analysis_start = time.monotonic()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            report = git_compare.compare_git(self.repo, base, head, javascript_engine=engine, **options)
            rendered = git_compare.markdown(report)
        self.last_analysis_seconds = time.monotonic() - analysis_start
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))
        self.assertEqual(report["format"], "surface-map-git-comparison-v2")
        self.assertTrue(report["analysis_performed"])
        self.assertEqual(report["head_commit"], head)
        encoded = ci.json_output(report)
        for private in (self.canary, self.page, self.script, str(self.repo)):
            self.assertNotIn(private, encoded + rendered)
        output = os.environ.get("SURFACE_RIK_ACCEPTANCE_REPORT_DIR")
        if output:
            folder = Path(output)
            folder.mkdir(parents=True, exist_ok=True)
            self.report_sequence += 1
            name = self.id().rsplit(".", 1)[-1] + "_comparison_" + str(self.report_sequence)
            (folder / (name + ".json")).write_text(encoded, encoding="utf-8")
            (folder / (name + ".md")).write_text(rendered, encoding="utf-8")
        return report, base, head

    @staticmethod
    def group(report, category):
        return report["review_signals"]["comparison"][category]

    def assert_quiet(self, report, categories=None):
        categories = categories or report["review_signals"]["comparison"]
        for category in categories:
            for key in ("added", "removed", "changed"):
                self.assertEqual(self.group(report, category)[key], [], (category, key))

    def assert_incomplete(self, report, source="source_1", categories=()):
        coverage = report["review_signals"]["coverage"]
        selected = [item for item in coverage["incomplete_sources"] if item["source"] == source]
        self.assertTrue(selected)
        inspected = set().union(*(set(item["categories"]) for item in selected))
        self.assertTrue(set(categories).issubset(inspected))
        self.assertTrue(coverage["warnings"])
        self.assertIsNotNone(report["evidence_comparison"])
        self.assertFalse(report["policy"]["passed"])
        self.assertEqual(report["policy"]["status"], "failed")
        self.assertIn("incomplete", report["policy"]["matched"])

    def test_r1_form_method_and_origin_only_destination_changes(self):
        template = '<form action="https://{}/api/orders?token={}" method="{}"><input name="email"></form>'
        report, _, _ = self.pair({self.page: template.format("first-" + self.canary + ".invalid", self.canary, "get")},
                                  {self.page: template.format("second-" + self.canary + ".invalid", "other", "post")})
        group = self.group(report, "endpoint")
        self.assertEqual((len(group["added"]), len(group["removed"])), (1, 1))
        old, new = group["removed"][0], group["added"][0]
        self.assertEqual(old["path_id"], new["path_id"])
        self.assertNotEqual(old["origin_id"], new["origin_id"])
        self.assertEqual(old["attributes"][0]["method"], "GET")
        self.assertEqual(new["attributes"][0]["method"], "POST")
        fields = self.group(report, "form_field")
        self.assertEqual((len(fields["added"]), len(fields["removed"]), len(fields["changed"])), (0, 0, 1))
        self.assertIn("associations", fields["changed"][0]["changed_fields"])
        self.assertNotEqual(fields["changed"][0]["before_associations"], fields["changed"][0]["associations"])

    def test_r2_hidden_field_added_removed_and_renamed(self):
        empty = '<form action="/api/orders" method="post"></form>'
        before = empty.replace("</form>", '<input name="old_' + self.canary + '" type="hidden" value="' + self.canary + '"></form>')
        after = before.replace("old_" + self.canary, "new_" + self.canary)
        added, _, _ = self.pair({self.page: empty}, {self.page: before})
        self.assertEqual(len(self.group(added, "form_field")["added"]), 1)
        renamed, _, _ = self.pair({self.page: before}, {self.page: after})
        group = self.group(renamed, "form_field")
        self.assertEqual((len(group["added"]), len(group["removed"])), (1, 1))
        self.assertNotEqual(group["added"][0]["field_id"], group["removed"][0]["field_id"])
        removed, _, _ = self.pair({self.page: after}, {self.page: empty})
        self.assertEqual(len(self.group(removed, "form_field")["removed"]), 1)

    def test_r2_text_to_hidden_is_one_change(self):
        template = '<form action="/api/orders"><input name="email" type="{}"></form>'
        report, _, _ = self.pair({self.page: template.format("text")}, {self.page: template.format("hidden")})
        group = self.group(report, "form_field")
        self.assertEqual((len(group["added"]), len(group["removed"]), len(group["changed"])), (0, 0, 1))
        self.assertFalse(group["changed"][0]["before_attributes"][0]["hidden"])
        self.assertTrue(group["changed"][0]["attributes"][0]["hidden"])

    def test_r2_implicit_current_page_form_keeps_hidden_field_removal_and_type_change(self):
        before = ('<form><input type="hidden" name="alpha_' + self.canary + '" value="' + self.canary + '">'
                  '<input type="hidden" name="beta_' + self.canary + '"></form>')
        after = '<form><input type="text" name="beta_' + self.canary + '"></form>'
        report, _, _ = self.pair({self.page: before}, {self.page: after})
        fields = self.group(report, "form_field")
        self.assertEqual((len(fields["added"]), len(fields["removed"]), len(fields["changed"])), (0, 1, 1))
        self.assertTrue(fields["removed"][0]["attributes"][0]["hidden"])
        self.assertTrue(fields["changed"][0]["before_attributes"][0]["hidden"])
        self.assertFalse(fields["changed"][0]["attributes"][0]["hidden"])

    def test_r2_hidden_fields_swapped_between_existing_destinations_are_changes(self):
        forms = [
            '<form action="/api/orders" method="post"><input type="hidden" name="alpha_' + self.canary + '"><input name="email"></form>',
            '<form action="/api/reports" method="post"><input type="hidden" name="beta_' + self.canary + '"><input name="password"></form>',
        ]
        before = "\n".join(forms)
        swapped = before.replace("alpha_" + self.canary, "temporary").replace("beta_" + self.canary, "alpha_" + self.canary).replace("temporary", "beta_" + self.canary)
        report, _, _ = self.pair({self.page: before}, {self.page: swapped})
        fields = self.group(report, "form_field")
        self.assertEqual((len(fields["added"]), len(fields["removed"]), len(fields["changed"])), (0, 0, 2))
        for row in fields["changed"]:
            self.assertIn("associations", row["changed_fields"])
            self.assertNotEqual(row["before_associations"], row["associations"])
        self.assert_quiet(report, ("endpoint",))
        # Form and field order do not change where the same data is sent.
        reordered = '\n'.join(reversed(forms))
        reordered = reordered.replace('<input type="hidden" name="alpha_' + self.canary + '"><input name="email">',
                                      '<input name="email"><input type="hidden" name="alpha_' + self.canary + '">')
        reordered = reordered.replace('<input type="hidden" name="beta_' + self.canary + '"><input name="password">',
                                      '<input name="password"><input type="hidden" name="beta_' + self.canary + '">')
        control, _, _ = self.pair({self.page: before}, {self.page: reordered})
        self.assert_quiet(control)

    def test_r2_duplicate_hidden_controls_and_per_destination_redistribution(self):
        field = '<input type="hidden" name="' + self.canary + '">'
        def forms(first, second):
            return ('<form action="/api/orders" method="post">' + field * first + '</form>'
                    '<form action="/api/reports" method="post">' + field * second + '</form>')
        # Adding a second same-name control still submits another field occurrence.
        added, _, _ = self.pair({self.page: forms(1, 0)}, {self.page: forms(2, 0)})
        row = self.group(added, "form_field")["changed"][0]
        self.assertEqual((row["before_count"], row["count"]), (1, 2))
        self.assertIn("occurrences", row["changed_fields"])
        # Total occurrences stay three, but their destinations redistribute.
        moved, _, _ = self.pair({self.page: forms(2, 1)}, {self.page: forms(1, 2)})
        fields = self.group(moved, "form_field")
        self.assertEqual((len(fields["added"]), len(fields["removed"]), len(fields["changed"])), (0, 0, 1))
        row = fields["changed"][0]
        self.assertEqual((row["before_count"], row["count"]), (3, 3))
        self.assertEqual(row["changed_fields"], ["associations"])
        before_counts = {item["target_id"]: item["count"] for item in row["before_associations"]}
        after_counts = {item["target_id"]: item["count"] for item in row["associations"]}
        self.assertEqual(set(before_counts), set(after_counts))
        self.assertTrue(all(before_counts[key] != after_counts[key] for key in before_counts))
        self.assert_quiet(moved, ("endpoint",))

    def test_r3_domain_swap_and_new_third_party_are_not_normalized_away(self):
        before = '<script src="https://first-' + self.canary + '.invalid/assets/app.js"></script>'
        after = '<script src="https://second-' + self.canary + '.invalid/assets/app.js?secret=' + self.canary + '"></script>'
        report, _, _ = self.pair({self.page: before}, {self.page: after}, site_origin="https://site.invalid")
        scripts = self.group(report, "script")
        self.assertEqual((len(scripts["added"]), len(scripts["removed"])), (1, 1))
        self.assertEqual(scripts["added"][0]["path_id"], scripts["removed"][0]["path_id"])
        self.assertNotEqual(scripts["added"][0]["origin_id"], scripts["removed"][0]["origin_id"])
        self.assertEqual(scripts["added"][0]["attributes"][0]["origin_relation"], "cross_origin")
        self.assertEqual(len(self.group(report, "origin")["added"]), 1)

    def test_r3_same_origin_is_not_mislabeled_third_party(self):
        report, _, _ = self.pair({self.page: "<p>body</p>"},
                                  {self.page: '<script src="https://site.invalid:443/assets/app.js"></script>'},
                                  site_origin="https://site.invalid")
        self.assertEqual(self.group(report, "script")["added"][0]["attributes"][0]["origin_relation"], "same_origin")

    def test_r4_page_local_loss_is_not_hidden_by_a_link_on_another_page(self):
        link = '<a href="/dashboard">Home</a>'
        second = "second_" + self.page
        report, _, _ = self.pair({self.page: link, second: link}, {self.page: "<p>body</p>"},
                                  files=[self.page, second])
        group = self.group(report, "navigation")
        self.assertEqual(len(group["removed"]), 1)
        self.assertEqual(group["unchanged_count"], 1)

    def test_r4_parent_relative_navigation_disappearance_is_retained(self):
        report, _, _ = self.pair({self.page: '<a href="../account/settings">Settings</a>'},
                                  {self.page: '<p>Settings moved</p>'})
        self.assertEqual(len(self.group(report, "navigation")["removed"]), 1)

    def test_non_web_anchor_schemes_do_not_become_navigation_changes(self):
        before = ('<a href="mailto:' + self.canary + '@example.invalid">Email</a>'
                  '<a href="javascript:alert(\'' + self.canary + '\')">Open</a>')
        report, _, _ = self.pair({self.page: before}, {self.page: '<p>Contact</p>'})
        self.assert_quiet(report, ("navigation", "endpoint", "origin"))

    def test_identical_realistic_html_is_quiet_under_warning_policy(self):
        html = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Account</title><link rel="stylesheet" href="/assets/site.css">'
                '<script src="/assets/app.js" defer></script>'
                '<script>window.bootstrap={loggedIn:false};</script></head><body><header>'
                '<a href="/dashboard">Dashboard</a></header><main><h1>Sign in</h1>'
                '<form action="/api/login" method="post"><label>Email<input type="email" name="email"></label>'
                '<label>Password<input type="password" name="password"></label>'
                '<button type="submit">Continue</button></form></main></body></html>')
        report, _, _ = self.pair({self.page: html}, {self.page: html}, engine="enhanced", files=[self.page], fail_on=("warnings",))
        self.assert_quiet(report)
        self.assertEqual(report["review_signals"]["coverage"]["warnings"], [])
        self.assertEqual(report["policy"]["matched"], [])
        self.assertTrue(report["policy"]["passed"])

    def test_claude_followup_fragment_form_action_retains_hidden_field_removal(self):
        before = '<form action="#"><input type="hidden" name="' + self.canary + '"></form>'
        report, _, _ = self.pair({self.page: before}, {self.page: '<form action="#"></form>'})
        fields = self.group(report, "form_field")
        self.assertEqual((len(fields["added"]), len(fields["removed"])), (0, 1))
        self.assertTrue(fields["removed"][0]["attributes"][0]["hidden"])
        self.assert_quiet(report, ("endpoint",))

    def test_claude_followup_non_web_form_targets_keep_controls_and_warn(self):
        actions = ["mailto:" + self.canary + "@example.invalid", "javascript:alert('" + self.canary + "')"]
        for action in actions:
            with self.subTest(scheme=action.split(":", 1)[0]):
                form = '<form action="' + action + '">'
                before = form + '<input type="hidden" name="' + self.canary + '"></form>'
                report, _, _ = self.pair({self.page: before}, {self.page: form + '</form>'}, fail_on=("warnings",))
                self.assertEqual(len(self.group(report, "form_field")["removed"]), 1)
                self.assertTrue(report["review_signals"]["coverage"]["warnings"])
                self.assertFalse(report["policy"]["passed"])
                self.assertIn("warnings", report["policy"]["matched"])
                self.assertFalse(any("form_field" in item["categories"] for item in report["review_signals"]["coverage"]["incomplete_sources"]))
                self.assert_quiet(report, ("endpoint", "origin"))

    def test_claude_followup_relative_document_base_change_is_visible(self):
        before = '<base href="/app/' + self.canary + '/"><a href="settings">Settings</a>'
        after = before.replace('/app/', '/other/')
        report, _, _ = self.pair({self.page: before}, {self.page: after}, fail_on=("added", "absent"))
        bases = self.group(report, "document_base")
        self.assertEqual((len(bases["added"]), len(bases["removed"])), (1, 1))
        self.assertNotEqual(bases["added"][0]["target_id"], bases["removed"][0]["target_id"])
        self.assertFalse(report["policy"]["passed"])
        self.assertEqual(set(report["policy"]["matched"]), {"added", "absent"})

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_claude_followup_global_analytics_overwrite_aliases_cannot_claim_unchanged(self):
        shadow = "z_shadow_" + self.script
        overwrites = ["this.posthog={capture:function(){}};",
                      "(function(root){root.posthog={capture:function(){}};})(this);",
                      "document.defaultView.posthog={capture:function(){}};"]
        for index, overwrite in enumerate(overwrites):
            with self.subTest(alias=index):
                before = {self.script: "posthog.capture('" + self.canary + "');", shadow: "const ready=true;"}
                after = {self.script: before[self.script], shadow: overwrite}
                report, _, _ = self.pair(before, after, engine="enhanced", files=[self.script, shadow], fail_on=("warnings",))
                self.assert_incomplete(report, categories=("tracking",))
                self.assert_quiet(report, ("tracking",))
                self.assertEqual(self.group(report, "tracking")["unchanged_count"], 0)

    def test_quiet_controls_get_default_whitespace_reordering_and_values(self):
        before = '<form action="/api/orders?token=' + self.canary + '#old"><input name="email" value="' + self.canary + '"></form><a href="/reports">R</a>'
        after = '<a href="/reports">R</a>\n<form method="get" action="/api/orders?token=other#new">\n<input value="other" name="email">\n</form>'
        report, _, _ = self.pair({self.page: before}, {self.page: after})
        self.assert_quiet(report)

    def test_cli_json_markdown_and_fixed_error_channels_do_not_leak_canaries(self):
        before = '<form action="/api/orders"><input name="' + self.canary + '"></form>'
        after = before.replace('</form>', '<input type="hidden" name="email" value="' + self.canary + '"></form>')
        _, base, head = self.pair({self.page: before}, {self.page: after})
        arguments = ["compare-git", "--repo", str(self.repo), "--base", base, "--head", head,
                     "--javascript-engine", "basic"]
        for fmt in ("json", "markdown"):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli_main(arguments + ["--format", fmt])
            self.assertEqual((code, err.getvalue()), (0, ""))
            self.assertNotIn(self.canary, out.getvalue())
            self.assertNotIn(str(self.repo), out.getvalue())
        arguments[arguments.index(base)] = self.canary
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(arguments)
        self.assertEqual((code, out.getvalue()), (2, ""))
        self.assertNotIn(self.canary, err.getvalue())

    def test_literal_local_source_is_never_executed(self):
        marker = self.repo / "executed.txt"
        source = 'require("fs").writeFileSync(' + json.dumps(str(marker)) + ',"' + self.canary + '");'
        report, _, _ = self.pair({self.script: "const enabled=true;"}, {self.script: source})
        self.assertFalse(marker.exists())
        self.assertEqual(report["review_signals"]["coverage"]["tracking_analysis"], "unavailable_or_partial")
        self.assertTrue(report["review_signals"]["coverage"]["warnings"])

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_r1_literal_fetch_xhr_and_imported_axios_destination_changes(self):
        patterns = ["fetch('{}');", "const x=new XMLHttpRequest(); x.open('POST','{}');",
                    "import axios from 'axios'; axios.post('{}', {{email:'" + self.canary + "'}});"]
        for index, pattern in enumerate(patterns):
            with self.subTest(client=index):
                before = pattern.format("https://first-" + self.canary + ".invalid/api/orders")
                after = pattern.format("https://second-" + self.canary + ".invalid/api/orders")
                report, _, _ = self.pair({self.script: before}, {self.script: after}, engine="enhanced")
                group = self.group(report, "endpoint")
                self.assertEqual((len(group["added"]), len(group["removed"])), (1, 1))
                self.assertEqual(group["added"][0]["path_id"], group["removed"][0]["path_id"])
                self.assertNotEqual(group["added"][0]["origin_id"], group["removed"][0]["origin_id"])

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_r1_dynamic_fetch_method_keeps_literal_origin_changes_visible_as_unknown(self):
        before = "fetch('https://first-" + self.canary + ".invalid/api/orders', {method: window.httpMethod});"
        after = before.replace("first-", "second-")
        report, _, _ = self.pair({self.script: before}, {self.script: after}, engine="enhanced")
        endpoints = self.group(report, "endpoint")
        self.assertEqual((len(endpoints["added"]), len(endpoints["removed"])), (1, 1))
        self.assertEqual(endpoints["added"][0]["attributes"][0]["method"], "UNKNOWN")
        self.assertEqual(endpoints["removed"][0]["attributes"][0]["method"], "UNKNOWN")
        self.assertNotEqual(endpoints["added"][0]["origin_id"], endpoints["removed"][0]["origin_id"])

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_cross_file_global_axios_assignment_suppresses_only_ambient_endpoint_hints(self):
        shadow, imported = "shadow_" + self.script, "z_imported_" + self.script
        url = "https://first-" + self.canary + ".invalid/api/orders"
        old = {self.script: "axios.get('" + url + "');", shadow: "window.axios=customClient;",
               imported: "import realAxios from 'axios'; realAxios.get('" + url + "');"}
        new = {name: text.replace("first-", "second-") for name, text in old.items()}
        report, _, _ = self.pair(old, new, engine="enhanced", files=[self.script, shadow, imported])
        endpoints = self.group(report, "endpoint")
        self.assertEqual((len(endpoints["added"]), len(endpoints["removed"])), (1, 1))
        self.assertTrue(report["review_signals"]["coverage"]["warnings"])
        for row in endpoints["added"] + endpoints["removed"]:
            self.assertNotIn("unverified_global_axios", row["assumptions"])

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_r5_supported_tracking_event_and_initialization_calls_disappear(self):
        calls = {
            "gtag_event": "gtag('event','" + self.canary + "');",
            "gtag_config": "gtag('config','" + self.canary + "');",
            "posthog_capture": "posthog.capture('" + self.canary + "');",
            "analytics_track": "analytics.track('" + self.canary + "');",
            "analytics_page": "analytics.page('" + self.canary + "');",
            "mixpanel_track": "mixpanel.track('" + self.canary + "');",
            "fbq_track": "fbq('track','" + self.canary + "');",
            "fbq_track_custom": "fbq('trackCustom','" + self.canary + "');",
            "fbq_init": "fbq('init','" + self.canary + "');",
            "data_layer_event": "dataLayer.push({event:'" + self.canary + "'});",
            "navigator_send_beacon": "navigator.sendBeacon('https://" + self.canary + ".invalid/api/events', '{}');",
        }
        for api, call in calls.items():
            with self.subTest(api=api):
                report, _, _ = self.pair({self.script: call}, {self.script: "const enabled=true;"}, engine="enhanced")
                rows = self.group(report, "tracking")["removed"]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["attributes"][0]["api"], api)
                self.assertEqual(rows[0]["confidence"], "inferred")

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_r5_imported_posthog_alias_is_recognized_as_declared_import(self):
        before = "import metrics from 'posthog-js'; metrics.capture('" + self.canary + "');"
        after = "import metrics from 'posthog-js'; const enabled=true;"
        report, _, _ = self.pair({self.script: before}, {self.script: after}, engine="enhanced")
        row = self.group(report, "tracking")["removed"][0]
        self.assertIn("declared_tracking_import", row["assumptions"])

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_r5_canonical_inline_gtag_queue_shim_and_html_positions(self):
        bootstrap = "window.dataLayer=window.dataLayer||[];\nfunction gtag(){dataLayer.push(arguments);}\n"
        before = "<html>\n<script>\n" + bootstrap + "gtag('event','" + self.canary + "');\n</script></html>"
        after = "<html>\n<script>\n" + bootstrap + "</script></html>"
        report, _, _ = self.pair({self.page: before}, {self.page: after}, engine="enhanced")
        row = self.group(report, "tracking")["removed"][0]
        self.assertEqual(row["evidence"], [{"source": "source_1", "location": "line 5"}])
        self.assertIn("static_queue_shim_only", row["assumptions"])

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_r5_comments_strings_and_local_shadowing_do_not_become_lost_tracking(self):
        cases = [("// posthog.capture('x');", "const text=\"posthog.capture('x')\";"),
                 ("function posthog(){}; posthog.capture('x');", "function posthog(){};"),
                 ("export {}; function gtag(){dataLayer.push(arguments);} gtag('event','x');", "export {}; const enabled=true;")]
        for before, after in cases:
            with self.subTest(case=cases.index((before, after))):
                report, _, _ = self.pair({self.script: before}, {self.script: after}, engine="enhanced")
                self.assert_quiet(report, ("tracking",))

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_r5_reformat_and_tracking_parameter_changes_are_quiet(self):
        before = "posthog.capture('signup',{email:'" + self.canary + "'});"
        after = "// formatting\nposthog.capture(\"signup\", {email:'different'});"
        report, base, head = self.pair({self.script: before}, {self.script: after}, engine="enhanced")
        self.assert_quiet(report)
        again = git_compare.compare_git(self.repo, base, head, javascript_engine="enhanced")
        self.assertEqual(ci.json_output(report), ci.json_output(again))
        self.assertEqual(git_compare.markdown(report), git_compare.markdown(again))

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_standalone_syntax_failure_without_legacy_evidence_is_sanitized(self):
        base = self.commit({self.script: "posthog.capture('" + self.canary + "');"})
        head = self.commit({self.script: "posthog.capture('"})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(git_compare.GitCompareError) as caught:
                git_compare.compare_git(self.repo, base, head, javascript_engine="enhanced")
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))
        self.assertNotIn(self.canary, str(caught.exception))

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_tracking_limit_keeps_legacy_report_and_suppresses_paired_false_removals(self):
        after = ";".join("posthog.capture('event" + str(i) + "')" for i in range(257))
        report, base, head = self.pair({self.script: "posthog.capture('" + self.canary + "');"},
                                     {self.script: after}, engine="enhanced", fail_on=("warnings",))
        self.assert_incomplete(report, categories=("tracking", "endpoint"))
        self.assert_quiet(report)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(["compare-git", "--repo", str(self.repo), "--base", base, "--head", head,
                             "--javascript-engine", "enhanced", "--fail-on", "warnings", "--format", "json"])
        self.assertEqual((code, err.getvalue()), (1, ""))
        self.assertNotIn(self.canary, out.getvalue())
        self.assertIsNotNone(json.loads(out.getvalue())["evidence_comparison"])

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_unrendered_inline_template_keeps_html_evidence_without_false_lost_tracking(self):
        before = ('<a href="/dashboard">Home</a><script>posthog.capture(\'' + self.canary + '\');</script>')
        after = ('<a href="/reports">Reports</a><script>window.bootstrap={{ server_config|tojson }};'
                 'posthog.capture(\'' + self.canary + '\');</script>')
        report, _, _ = self.pair({self.page: before}, {self.page: after}, engine="enhanced", fail_on=("warnings",))
        self.assert_incomplete(report, categories=("tracking", "endpoint"))
        self.assert_quiet(report, ("tracking", "endpoint"))
        self.assertEqual(len(self.group(report, "navigation")["added"]), 1)
        self.assertEqual(len(self.group(report, "navigation")["removed"]), 1)
        legacy = report["evidence_comparison"]["comparison"]
        self.assertTrue(legacy["added"] or legacy["removed"])

    def test_html_record_limit_suppresses_only_its_source_and_retains_healthy_page_changes(self):
        field = '<input type="hidden" name="' + self.canary + '">'
        form = lambda count: '<form action="/api/orders">' + field * count + '</form>'
        healthy = "z_" + self.page
        external = '<script src="https://' + self.canary + '.invalid/assets/app.js"></script>'
        report, _, _ = self.pair({self.page: form(1) + '<a href="/settings">Settings</a>' + external, healthy: '<a href="/dashboard">Home</a>'},
                                  {self.page: form(4097), healthy: '<p>Home removed</p>'},
                                  files=[self.page, healthy])
        self.assert_incomplete(report, categories=("form_field", "navigation", "endpoint"))
        self.assert_quiet(report, ("form_field", "endpoint", "script", "origin"))
        removed = self.group(report, "navigation")["removed"]
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["evidence"][0]["source"], "source_2")

    def test_identical_over_limit_inline_source_is_incomplete_on_both_sides(self):
        html = '<a href="/dashboard">Home</a><script>/*' + 'x' * (512 * 1024) + '*/</script>'
        report, _, _ = self.pair({self.page: html}, {self.page: html}, files=[self.page])
        self.assert_incomplete(report, categories=("tracking",))
        self.assert_quiet(report)
        sides = {item["side"] for item in report["review_signals"]["coverage"]["warnings"]}
        self.assertEqual(sides, {"before", "after"})

    @unittest.skipUnless(READY, "Pinned syntax parser unavailable")
    def test_six_pages_with_thirty_two_inline_scripts_keep_complete_tracking_coverage(self):
        before, after = {}, {}
        for page in range(6):
            scripts = ["posthog.capture('page" + str(page) + "');"]
            scripts.extend("const config" + str(index) + "={enabled:true};" for index in range(1, 32))
            html = '<a href="/dashboard">Home</a>' + ''.join('<script>' + script + '</script>' for script in scripts)
            name = str(page) + "_" + self.page
            before[name] = html
            after[name] = html.replace(scripts[0], "const enabled=true;") if page == 0 else html
        report, _, _ = self.pair(before, after, engine="enhanced", files=list(before), fail_on=("warnings",))
        output = os.environ.get("SURFACE_RIK_ACCEPTANCE_REPORT_DIR")
        if output:
            timing = {"compare_git_and_markdown_seconds": self.last_analysis_seconds,
                      "html_sources": 6, "inline_scripts_per_side": 192,
                      "purpose": "Measurement only; no brittle machine-speed assertion"}
            (Path(output) / "six_page_inline_timing.json").write_text(json.dumps(timing, indent=2), encoding="utf-8")
        coverage = report["review_signals"]["coverage"]
        self.assertEqual(coverage["before"]["html"], 6)
        self.assertEqual(coverage["before"]["inline_javascript"], 192)
        self.assertEqual(coverage["after"]["inline_javascript"], 192)
        self.assertEqual(coverage["warnings"], [])
        self.assertEqual(coverage["incomplete_sources"], [])
        self.assertEqual(len(self.group(report, "tracking")["removed"]), 1)
        self.assertEqual(self.group(report, "tracking")["unchanged_count"], 5)
        self.assertTrue(report["policy"]["passed"])


class LegacyCaptureGoldenTests(unittest.TestCase):
    def test_v1_v2_json_and_markdown_match_trusted_0206244_implementation(self):
        # Byte goldens were generated with the reviewed ci.py from 0206244,
        # independently compared with current JSON and Markdown, then frozen.
        # They require neither a full Git history nor execution of old code in CI.
        goldens = {
            (0, False): ("a8be9dc21163a71251fe014df31e66ddadad97213c565107fa0bd073a889b35d", "d84710bac98365c1274d3ead99c7524c6870b7086c46ea1ae3b50e16440e8ee3"),
            (0, True): ("d998dbe9aec3457bbd8595ada792e9dcd09946d7d83a8ae6563a78adc515afe5", "f4ae3eb1fe915aab876e8cb5ce1e814d4ac3dc7c22812a7e7786409fde424558"),
            (1, False): ("20914be8dc9d8b014fd32e3e0382eabc6cd897b209243ed7d6fd28aed02c051c", "5b32e8a0a5e12fee6e652a7dbbdd2652142b49b25c8208a58c65142fb90bae9b"),
            (1, True): ("c7eb9d64a9da226ab1c1cec7a87d243e38dfa4e65e5ae83f8be859c048d9a7a4", "05a27983f2e65fd1438d73ed69ebf65bf14f5051d62e4c04f71fd9d4b77e74e5"),
        }
        samples = [
            ({"artifacts": [{"kind": "html", "text": '<form action="/api/orders"><input name="email"></form>'}]},
             {"artifacts": [{"kind": "html", "text": '<form action="/api/orders" method="post"><input name="email"></form><script src="/assets/app.js"></script>'}]}),
            ({"artifacts": [{"kind": "html", "text": '<script src="https://one.invalid/assets/app.js"></script>'}]},
             {"artifacts": [{"kind": "html", "text": '<script src="https://two.invalid/assets/app.js"></script>'}]}),
        ]
        for index, (before, after) in enumerate(samples):
            for assets in (False, True):
                with self.subTest(sample=index, include_assets=assets):
                    kwargs = {"javascript_engine": "basic", "fail_on": "added,absent,changed", "include_assets": assets}
                    actual = ci.compare_captures(before, after, **kwargs)
                    outputs = (ci.json_output(actual), ci.markdown(actual))
                    hashes = tuple(hashlib.sha256(value.encode("utf-8")).hexdigest() for value in outputs)
                    self.assertEqual(hashes, goldens[(index, assets)])


if __name__ == "__main__":
    unittest.main()
