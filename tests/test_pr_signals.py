import contextlib
import io
import time
import unittest
import uuid
from unittest.mock import patch

from surface_map import ci, js_parser, pr_signals


def capture(*sources):
    return {"artifacts": [{"kind": kind, "text": text} for kind, text in sources]}


class ReviewIdentityTests(unittest.TestCase):
    def setUp(self):
        self.canary = "private_" + uuid.uuid4().hex

    def compare(self, before, after, **options):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = pr_signals.compare_signals(before, after, **options)
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))
        rendered = ci.json_output(result) + pr_signals.markdown(result)
        self.assertNotIn(self.canary, rendered)
        return result

    def html(self, before, after, **options):
        return self.compare(capture(("html", before)), capture(("html", after)),
                            javascript_engine="basic", **options)

    def quiet(self, result, categories=pr_signals.CATEGORIES):
        for category in categories:
            for change in ("added", "removed", "changed"):
                self.assertEqual(result["comparison"][category][change], [], (category, change))

    def test_same_path_domain_swap_is_detected_without_exporting_domains(self):
        a = '<script src="https://first-' + self.canary + '.invalid/static/app.js?token=' + self.canary + '"></script>'
        b = '<script src="https://second-' + self.canary + '.invalid/static/app.js?token=changed"></script>'
        result = self.html(a, b)
        scripts = result["comparison"]["script"]
        self.assertEqual((len(scripts["added"]), len(scripts["removed"])), (1, 1))
        old, new = scripts["removed"][0], scripts["added"][0]
        self.assertNotEqual(old["origin_id"], new["origin_id"])
        self.assertEqual(old["path_id"], new["path_id"])
        self.assertEqual(len(result["comparison"]["origin"]["added"]), 1)
        self.assertEqual(old["attributes"][0]["origin_relation"], "unknown")

    def test_unknown_field_rename_is_detected_with_joint_field_ids(self):
        template = '<form action="/api/orders" method="post"><input name="{}" type="hidden"></form>'
        result = self.html(template.format("old_" + self.canary), template.format("new_" + self.canary))
        fields = result["comparison"]["form_field"]
        self.assertEqual((len(fields["added"]), len(fields["removed"])), (1, 1))
        self.assertNotEqual(fields["added"][0]["field_id"], fields["removed"][0]["field_id"])

    def test_type_change_and_hidden_field_presence_are_explicit(self):
        template = '<form action="/api/orders"><input name="' + self.canary + '" type="{}"></form>'
        result = self.html(template.format("text"), template.format("hidden"))
        group = result["comparison"]["form_field"]
        self.assertEqual((len(group["added"]), len(group["removed"]), len(group["changed"])), (0, 0, 1))
        changed = group["changed"][0]
        self.assertFalse(changed["before_attributes"][0]["hidden"])
        self.assertTrue(changed["attributes"][0]["hidden"])

    def test_only_value_fragment_and_query_value_changes_stay_quiet(self):
        before = '<form action="/api/orders?token=first#one"><input name="email" value="' + self.canary + '"></form>'
        after = '<form action="/api/orders?token=second#two"><input name="email" value="other"></form>'
        self.quiet(self.html(before, after))

    def test_form_target_and_method_change_does_not_invent_lost_fields(self):
        template = '<form action="{}" method="{}"><input name="email" type="hidden"></form>'
        result = self.html(template.format("/api/orders", "get"), template.format("/api/reports", "post"))
        endpoints = result["comparison"]["endpoint"]
        self.assertEqual((len(endpoints["added"]), len(endpoints["removed"])), (1, 1))
        fields = result["comparison"]["form_field"]
        self.assertEqual((len(fields["added"]), len(fields["removed"])), (0, 0))
        self.assertEqual(fields["changed"][0]["changed_fields"], ["associations"])

    def test_missing_method_and_explicit_get_are_equivalent(self):
        before = '<form action="/api/orders"><input name="email"></form>'
        after = '<form method="get" action="/api/orders"><input name="email"></form>'
        self.quiet(self.html(before, after))

    def test_page_local_link_disappearance_is_not_hidden_by_another_page(self):
        link = '<a href="/dashboard">Home</a>'
        before = capture(("html", link), ("html", link))
        after = capture(("html", "<p>Body</p>"), ("html", link))
        result = self.compare(before, after, javascript_engine="basic")
        removed = result["comparison"]["navigation"]["removed"]
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["evidence"], [{"source": "source_1", "location": "line 1"}])

    def test_known_origin_classifies_cross_origin_and_same_origin(self):
        page = '<script src="/static/app.js"></script><script src="https://cdn.invalid/static/app.js"></script>'
        result = self.html("", page, site_origin="https://site.invalid:443")
        rows = result["comparison"]["script"]["added"]
        self.assertEqual({r["attributes"][0]["origin_relation"] for r in rows}, {"same_origin", "cross_origin"})
        self.assertTrue(result["coverage"]["site_origin_supplied"])
        self.assertNotIn("site.invalid", ci.json_output(result))

    def test_formatting_and_record_reordering_are_quiet_and_deterministic(self):
        before = '<form action="/api/orders"><input name="email"><input name="quantity"></form><a href="/reports">R</a>'
        after = '<a href="/reports">R</a>\n<form action="/api/orders">\n<input name="quantity">\n<input name="email"></form>'
        first, second = self.html(before, after), self.html(before, after)
        self.quiet(first)
        self.assertEqual(ci.json_output(first), ci.json_output(second))
        self.assertEqual(pr_signals.markdown(first), pr_signals.markdown(second))

    def test_invalid_origin_and_deadline_are_private_errors(self):
        normal = capture(("html", '<a href="/dashboard">D</a>'))
        for options in ({"site_origin": "https://user:" + self.canary + "@private.invalid"},
                        {"deadline": float("nan")}):
            with self.subTest(options=list(options)):
                with self.assertRaises(ValueError) as caught:
                    pr_signals.compare_signals(normal, normal, javascript_engine="basic", **options)
                self.assertNotIn(self.canary, str(caught.exception))

    def test_deadline_and_one_sided_limit_are_incomplete_without_false_changes(self):
        normal = capture(("html", '<a href="/dashboard">D</a>'))
        expired = self.compare(normal, normal, javascript_engine="basic", deadline=time.monotonic() - 1)
        self.assertTrue(pr_signals.policy_hits(expired)["incomplete"])
        self.quiet(expired)
        with patch("surface_map.pr_html_signals.MAX_RECORDS", 1):
            result = self.compare(normal, capture(("html", '<a href="/reports">R</a><a href="/orders">O</a>')),
                                  javascript_engine="basic")
        self.assertTrue(result["coverage"]["incomplete_sources"])
        self.quiet(result)

    def test_same_name_type_redistribution_between_forms_is_visible(self):
        def form(target, kind):
            return '<form action="' + target + '"><input name="same" type="' + kind + '"></form>'
        before = form("/first", "hidden") + form("/second", "text")
        after = form("/first", "text") + form("/second", "hidden")
        result = self.html(before, after)
        self.assertEqual(len(result["comparison"]["form_field"]["changed"]), 1)
        self.assertIn("associations", result["comparison"]["form_field"]["changed"][0]["changed_fields"])

    def test_internal_record_extra_fields_never_become_export_fields(self):
        private = {"category": "navigation", "identity": (self.canary,), "target": (self.canary,),
                   "origin": None, "path": self.canary, "attrs": {"method": "GET"},
                   "evidence": {"source": "source_1", "location": "line 1"}, "raw_secret": self.canary}
        with patch("surface_map.pr_html_signals.collect_html",
                   return_value={"records": [private], "limitations": [], "inline_scripts": []}):
            result = self.html("", "")
        self.assertNotIn("raw_secret", ci.json_output(result))


@unittest.skipUnless(js_parser.available()[0], "Pinned syntax parser unavailable")
class TrackingReviewIntegrationTests(unittest.TestCase):
    setUp = ReviewIdentityTests.setUp
    compare = ReviewIdentityTests.compare
    quiet = ReviewIdentityTests.quiet
    def js(self, before, after):
        return self.compare(capture(("javascript", before)), capture(("javascript", after)), javascript_engine="enhanced")

    def test_tracking_event_rename_and_disappearance_keep_values_private(self):
        before = "posthog.capture('old_" + self.canary + "', {email:'" + self.canary + "'});"
        after = "posthog.capture('new_" + self.canary + "', {email:'other'});"
        result = self.js(before, after)
        group = result["comparison"]["tracking"]
        self.assertEqual((len(group["added"]), len(group["removed"])), (1, 1))
        self.assertNotEqual(group["added"][0]["event_id"], group["removed"][0]["event_id"])
        lost = self.js(before, "const enabled=true;")
        self.assertEqual(len(lost["comparison"]["tracking"]["removed"]), 1)

    def test_tracking_parameter_values_and_comments_are_quiet(self):
        self.quiet(self.js("gtag('event','purchase',{value:'" + self.canary + "'});",
                           "// format\ngtag('event', 'purchase', {value:'other'});"))
        self.quiet(self.js("const text=\"posthog.capture('fake')\";", "/* posthog.capture('fake') */"))

    def test_basic_mode_tracking_is_unavailable_with_warning(self):
        result = self.compare(capture(("javascript", "posthog.capture('x')")),
                              capture(("javascript", "")), javascript_engine="basic")
        self.assertEqual(result["comparison"]["tracking"]["removed"], [])
        self.assertEqual(result["coverage"]["tracking_analysis"], "unavailable_or_partial")
        self.assertTrue(result["coverage"]["warnings"])

    def test_inline_tracking_preserves_html_locations(self):
        before = '<html>\n<script>\nposthog.capture("' + self.canary + '");\n</script></html>'
        result = self.compare(capture(("html", before)), capture(("html", "<html></html>")),
                              javascript_engine="enhanced")
        row = result["comparison"]["tracking"]["removed"][0]
        self.assertEqual(row["evidence"], [{"source": "source_1", "location": "line 3"}])

    def test_mutation_in_another_classic_script_suppresses_unverified_calls(self):
        before = capture(("javascript", "posthog.capture('x');"), ("javascript", "window.posthog=custom;"))
        after = capture(("javascript", ""), ("javascript", "window.posthog=custom;"))
        result = self.compare(before, after, javascript_engine="enhanced")
        self.assertEqual(result["comparison"]["tracking"]["removed"], [])
        self.assertTrue(result["coverage"]["warnings"])

    def test_opaque_loader_uncertainty_cannot_be_a_passing_tracking_result(self):
        loader = "(function(w){w.posthog=w.posthog||[];})(window);"
        result = self.js(loader + "posthog.capture('" + self.canary + "');", loader)
        self.assertEqual(result["comparison"]["tracking"]["removed"], [])
        self.assertTrue(pr_signals.policy_hits(result)["incomplete"])
        self.assertEqual(result["coverage"]["tracking_analysis"], "unavailable_or_partial")
        self.assertIn("Incomplete comparison", pr_signals.markdown(result))


if __name__ == "__main__":
    unittest.main()
