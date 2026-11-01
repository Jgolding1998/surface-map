"""Maintained URL-context regressions for the private PR comparison projection."""
import unittest
from unittest.mock import patch

from surface_map import ci, js_parser, pr_html_signals, pr_signals


CANARY = "private_context_canary_72619"
PAGE = "https://site.invalid/account/" + CANARY + ".html?token=" + CANARY
BASE = "https://cdn.invalid/payment/"


def capture(*sources, document_url=None):
    result = {"artifacts": [{"kind": kind, "text": text} for kind, text in sources]}
    if document_url is not None:
        result["document_url"] = document_url
    return result


class ComparisonChecks:
    def compare(self, before, after, *, engine="basic", **options):
        result = pr_signals.compare_signals(before, after, javascript_engine=engine, **options)
        rendered = ci.json_output(result) + pr_signals.markdown(result)
        for value in (CANARY, "site.invalid", "cdn.invalid", "https://", "base_url", "document_url"):
            self.assertNotIn(value, rendered)
        return result

    def quiet(self, result, categories=pr_signals.CATEGORIES):
        for category in categories:
            for kind in ("added", "removed", "changed"):
                self.assertEqual(result["comparison"][category][kind], [], (category, kind))

    def records(self, text, category, **options):
        result = pr_html_signals.collect_html(text, **options)
        return [row for row in result["records"] if row["category"] == category]


class BaseContextTests(ComparisonChecks, unittest.TestCase):
    def test_missing_and_empty_actions_use_document_even_with_cross_origin_base(self):
        # HTML form submission steps 13-14 substitute the document only for an
        # empty action, before parsing every nonempty action against its base.
        for action in ("", ' action=""'):
            with self.subTest(action=action):
                text = '<base href="' + BASE + '"><form' + action + ' method="post"><input name="email"></form>'
                known = self.records(text, "endpoint", document_url=PAGE, site_origin="https://site.invalid")[0]
                self.assertEqual(known["path"], "/account/" + CANARY + ".html")
                self.assertEqual(known["attrs"]["origin_relation"], "same_origin")
                unknown = self.records(text, "endpoint", site_origin="https://site.invalid")[0]
                self.assertEqual(unknown["target"][0], "current_document")
                self.assertEqual(unknown["path"], "")

    def test_nonempty_fragment_and_whitespace_actions_use_absolute_base(self):
        for action in ("#", "#step", "  #step  ", " ", "\t\n\f\r "):
            for base_first in (True, False):
                with self.subTest(action=repr(action), base_first=base_first):
                    form = '<form action="' + action + '" method="post"><input name="email"></form>'
                    base = '<base href="' + BASE + '">'
                    text = base + form if base_first else form + base
                    endpoint = self.records(text, "endpoint", site_origin="https://site.invalid")[0]
                    self.assertEqual(endpoint["path"], "/payment/")
                    self.assertEqual(endpoint["origin"], "https://cdn.invalid")
                    self.assertEqual(endpoint["attrs"]["origin_relation"], "cross_origin")

    def test_relative_base_and_current_document_are_separate_with_context(self):
        text = '<base href="../payment/"><form action="#step"></form><form></form>'
        endpoints = self.records(text, "endpoint", document_url=PAGE)
        self.assertEqual([row["path"] for row in endpoints], ["/payment/", "/account/" + CANARY + ".html"])
        self.assertTrue(all(row["attrs"]["origin_relation"] == "unknown" for row in endpoints))

    def test_document_context_never_infers_first_party_classification(self):
        text = '<a href="next">Next</a><form></form><script src="app.js"></script>'
        collected = pr_html_signals.collect_html(text, document_url=PAGE)
        self.assertTrue(collected["records"])
        self.assertTrue(all(row["attrs"]["origin_relation"] == "unknown" for row in collected["records"]))
        self.assertNotIn("document_path_unknown", collected["scope_notes"])
        result = self.compare(capture(("html", text)), capture(("html", text)),
                              before_document_url=PAGE, after_document_url=PAGE)
        self.quiet(result)
        self.assertFalse(result["coverage"]["site_origin_supplied"])

    def test_site_origin_alone_does_not_invent_document_path(self):
        text = '<a href="next">Next</a><form></form>'
        rows = pr_html_signals.collect_html(text, site_origin="https://site.invalid")["records"]
        link = next(row for row in rows if row["category"] == "navigation")
        form = next(row for row in rows if row["category"] == "endpoint")
        self.assertEqual(link["target"][0], "relative")
        self.assertEqual(link["path"], "next")
        self.assertEqual(form["target"][0], "current_document")

    def test_base_changes_do_not_move_missing_or_empty_form_actions(self):
        form = '<form method="post"><input name="' + CANARY + '"></form><form action=""></form>'
        before = capture(("html", '<base href="https://cdn.invalid/one/">' + form))
        after = capture(("html", '<base href="https://cdn.invalid/two/">' + form))
        result = self.compare(before, after, before_document_url=PAGE, after_document_url=PAGE)
        self.quiet(result, ("endpoint", "form_field"))
        self.assertEqual(len(result["comparison"]["document_base"]["added"]), 1)

    def test_first_base_wins_and_template_base_is_inert(self):
        text = ('<template><base href="https://ignored.invalid/"></template>'
                '<base href="../payment/"><base href="https://ignored.invalid/">'
                '<form action=" "></form>')
        endpoint = self.records(text, "endpoint", document_url=PAGE)[0]
        self.assertEqual(endpoint["path"], "/payment/")
        self.assertEqual(endpoint["origin"], "https://site.invalid")

    def test_unsupported_base_does_not_fall_back_to_document_for_relative_forms(self):
        text = '<base href="data:text/plain,' + CANARY + '"><form action="#step"></form><form></form>'
        collected = pr_html_signals.collect_html(text, document_url=PAGE)
        endpoints = [row for row in collected["records"] if row["category"] == "endpoint"]
        self.assertEqual(endpoints[0]["target"][0], "unsupported_form_target")
        self.assertEqual(endpoints[1]["path"], "/account/" + CANARY + ".html")
        self.assertIn("unsupported_form_target_unresolved", collected["limitations"])

    def test_packet_and_explicit_document_context_have_identical_projection(self):
        before, after = '<form action="first"></form>', '<form action="second"></form>'
        packet = self.compare(capture(("html", before), document_url=PAGE),
                              capture(("html", after), document_url=PAGE))
        explicit = self.compare(capture(("html", before)), capture(("html", after)),
                                before_document_url=PAGE, after_document_url=PAGE)
        self.assertEqual(packet, explicit)

    def test_ambiguous_context_and_unsupported_selections_reject_without_echo(self):
        normal = capture(("html", "<p>page</p>"))
        cases = [
            (normal, normal, {"before_document_url": PAGE}),
            (capture(("html", ""), document_url=PAGE), normal,
             {"before_document_url": "https://other.invalid/" + CANARY, "after_document_url": PAGE}),
            (capture(("javascript", "")), capture(("javascript", "")),
             {"before_document_url": PAGE, "after_document_url": PAGE}),
            (capture(("html", ""), ("html", "")), capture(("html", ""), ("html", "")),
             {"before_document_url": PAGE, "after_document_url": PAGE}),
            (capture(("har", '{"log":{"entries":[]}}')), capture(("har", '{"log":{"entries":[]}}')), {}),
            (normal, capture(("javascript", "")), {}),
            (normal, normal, {"before_document_url": "https://user:" + CANARY + "@site.invalid/",
                              "after_document_url": PAGE}),
        ]
        for before, after, options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError) as caught:
                pr_signals.compare_signals(before, after, javascript_engine="basic", **options)
            self.assertNotIn(CANARY, str(caught.exception))

    def test_area_navigation_is_comparable_and_reordering_is_quiet(self):
        first = '<area href="next?token=' + CANARY + '#one" shape="rect" coords="0,0,10,10">'
        second = '<area href="/checkout" shape="default">'
        before = capture(("html", '<map name="menu">' + first + second + '</map>'))
        reordered = capture(("html", '<map name="menu">' + second + first + '</map>'))
        self.quiet(self.compare(before, reordered, before_document_url=PAGE, after_document_url=PAGE))
        result = self.compare(before, capture(("html", '<map name="menu">' + second + '</map>')),
                              before_document_url=PAGE, after_document_url=PAGE)
        self.assertEqual(len(result["comparison"]["navigation"]["removed"]), 1)
        self.assertEqual(result["coverage"]["warnings"], [])

    def test_basic_never_invokes_parser_and_keeps_independent_html_declarations(self):
        script = 'window.open=custom;posthog.capture("' + CANARY + '");fetch("/api/send");'
        before = capture(("html", '<form action="/old"><input name="email"></form><a href="/old">Old</a>'
                          '<script>' + script + '</script>'), ("javascript", script))
        after = capture(("html", '<form action="/new"><input name="email"></form><a href="/new">New</a>'),
                        ("javascript", ""))
        with patch("surface_map.pr_js_signals.parse", side_effect=AssertionError("AST forbidden in basic")) as parse, \
                patch("surface_map.pr_js_signals.available", side_effect=AssertionError("Node probe forbidden in basic")) as available:
            result = self.compare(before, after)
        parse.assert_not_called()
        available.assert_not_called()
        for category in ("endpoint", "navigation"):
            group = result["comparison"][category]
            self.assertEqual((len(group["added"]), len(group["removed"])), (1, 1))
            self.assertTrue(all(row["confidence"] == "declared" for row in group["added"] + group["removed"]))
        self.assertEqual(len(result["comparison"]["form_field"]["changed"]), 1)
        self.assertEqual(result["comparison"]["tracking"]["removed"], [])
        self.assertTrue(pr_signals.policy_hits(result)["incomplete"])
        for source in result["coverage"]["incomplete_sources"]:
            self.assertEqual(source["endpoint_scope"], "javascript_only")
            self.assertEqual(source["navigation_scope"], "javascript_only")
        self.assertEqual(result["coverage"]["tracking_analysis"], "unavailable_or_partial")


@unittest.skipUnless(js_parser.available()[0], "Pinned syntax parser unavailable")
class ScriptBaseContextTests(ComparisonChecks, unittest.TestCase):
    SCRIPT = 'fetch("child"); location.assign("next"); navigator.sendBeacon("/collect", "' + CANARY + '");'

    def test_inline_base_origin_swap_moves_fetch_navigation_and_beacon(self):
        def page(host):
            return '<script>\n' + self.SCRIPT + '\n</script><base href="https://' + host + '/payment/">'
        result = self.compare(capture(("html", page("old.invalid"))), capture(("html", page("new.invalid"))),
                              engine="enhanced", site_origin="https://site.invalid")
        for category in ("endpoint", "navigation", "tracking"):
            group = result["comparison"][category]
            self.assertEqual((len(group["added"]), len(group["removed"])), (1, 1), category)
            old, new = group["removed"][0], group["added"][0]
            self.assertNotEqual(old["origin_id"], new["origin_id"])
            self.assertEqual(old["path_id"], new["path_id"])
            self.assertEqual(new["attributes"][0]["origin_relation"], "cross_origin")
            self.assertEqual(new["evidence"], [{"source": "source_1", "location": "line 2"}])

    def test_separate_script_before_html_uses_the_single_document_base(self):
        before = capture(("javascript", self.SCRIPT), ("html", '<base href="../one/">'))
        after = capture(("javascript", self.SCRIPT), ("html", '<base href="../two/">'))
        result = self.compare(before, after, engine="enhanced", before_document_url=PAGE, after_document_url=PAGE)
        for category in ("endpoint", "navigation"):
            self.assertEqual(len(result["comparison"][category]["added"]), 1)
            self.assertEqual(result["comparison"][category]["added"][0]["evidence"][0]["source"], "source_1")
        # The beacon is root relative; only a base path changed.
        self.quiet(result, ("tracking",))

    def test_absolute_and_relative_literals_compare_equal_in_known_context(self):
        relative = 'fetch("child"); location.assign("next"); navigator.sendBeacon("/collect", "one");'
        absolute = ('fetch("https://cdn.invalid/payment/child"); location.assign("https://cdn.invalid/payment/next");'
                    'navigator.sendBeacon("https://cdn.invalid/collect", "two");')
        base = '<base href="' + BASE + '">'
        result = self.compare(capture(("html", base + '<script>' + relative + '</script>')),
                              capture(("html", base + '<script>' + absolute + '</script>')), engine="enhanced")
        self.quiet(result)

    def test_per_side_document_paths_move_relative_script_and_actionless_form(self):
        text = '<form></form><script>fetch("child");location.assign("next");navigator.sendBeacon("collect", "x");</script>'
        selected = capture(("html", text))
        result = self.compare(selected, selected, engine="enhanced", before_document_url="https://site.invalid/one/page",
                              after_document_url="https://site.invalid/two/page")
        self.assertEqual(len(result["comparison"]["endpoint"]["added"]), 2)
        for category in ("navigation", "tracking"):
            self.assertEqual(len(result["comparison"][category]["added"]), 1)
        self.quiet(result, ("origin",))

    def test_multiple_html_sources_use_independent_inline_bases(self):
        def page(path):
            return '<base href="https://cdn.invalid/' + path + '/"><script>' + self.SCRIPT + '</script>'
        before = capture(("html", page("one")), ("html", page("other")))
        after = capture(("html", page("two")), ("html", page("other")))
        result = self.compare(before, after, engine="enhanced")
        for category in ("endpoint", "navigation"):
            group = result["comparison"][category]
            self.assertEqual(len(group["added"]), 1)
            self.assertEqual(group["added"][0]["evidence"][0]["source"], "source_1")
            self.assertEqual(group["unchanged_count"], 1)
        self.quiet(result, ("tracking",))

    def test_uninspectable_html_context_withholds_associated_script_on_both_sides(self):
        before = capture(("javascript", self.SCRIPT), ("html", '<base href="' + BASE + '">'))
        after = capture(("javascript", self.SCRIPT), ("html", '<script>unterminated'))
        result = self.compare(before, after, engine="enhanced")
        self.quiet(result)
        self.assertTrue(pr_signals.policy_hits(result)["incomplete"])
        excluded = next(row for row in result["coverage"]["incomplete_sources"] if row["source"] == "source_1")
        self.assertIn("document_context_uninspected", excluded["reasons"])

    def test_unsupported_base_does_not_resolve_relative_scripts_to_document(self):
        page = '<base href="data:text/plain,' + CANARY + '"><script>' + self.SCRIPT + '</script>'
        result = self.compare(capture(("html", page)), capture(("html", page)), engine="enhanced",
                              before_document_url=PAGE, after_document_url=PAGE)
        self.quiet(result)
        for category in ("endpoint", "navigation"):
            self.assertEqual(result["comparison"][category]["unchanged_count"], 0)
        beacon = result["comparison"]["tracking"]
        self.assertEqual(beacon["unchanged_count"], 1)
        self.assertTrue(result["coverage"]["warnings"])

    def test_resolution_does_not_normalize_rejected_control_characters_into_targets(self):
        script = 'fetch("bad\\tpath");location.assign("bad\\tpath");navigator.sendBeacon("bad\\tpath", "x");'
        text = '<base href="' + BASE + '"><script>' + script + '</script>'
        result = self.compare(capture(("html", text)), capture(("html", text)), engine="enhanced")
        self.quiet(result)
        self.assertEqual(result["comparison"]["endpoint"]["unchanged_count"], 0)
        self.assertEqual(result["comparison"]["navigation"]["unchanged_count"], 0)
        self.assertTrue(result["coverage"]["warnings"])

    def test_inline_syntax_failure_preserves_independent_form_target_changes(self):
        before = capture(("html", '<form action="/old"><input name="email"></form><script>fetch("/api");</script>'))
        after = capture(("html", '<form action="/new"><input name="email"></form><script>fetch(</script>'))
        result = self.compare(before, after, engine="enhanced")
        endpoints = result["comparison"]["endpoint"]
        self.assertEqual((len(endpoints["added"]), len(endpoints["removed"])), (1, 1))
        self.assertTrue(all(row["confidence"] == "declared" for row in endpoints["added"] + endpoints["removed"]))
        self.assertTrue(pr_signals.policy_hits(result)["incomplete"])
        self.assertEqual(result["coverage"]["incomplete_sources"][0]["endpoint_scope"], "javascript_only")


if __name__ == "__main__":
    unittest.main()
