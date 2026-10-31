import json
import time
import unittest
from unittest.mock import patch

from surface_map import pr_html_signals as signals


CANARY = "PRIVATE_HTML_SIGNAL_CANARY_27361"


class HTMLReviewSignalTests(unittest.TestCase):
    def records(self, text, category=None, **options):
        result = signals.collect_html(text, **options)
        return [record for record in result["records"] if category is None or record["category"] == category]

    def test_unknown_field_names_and_hidden_type_are_kept_only_in_private_records(self):
        records = self.records('<form action="/submit" method="post"><input name="private_first" '
                               'type="hidden" value="' + CANARY + '"></form>', "form_field")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"], "private_first")
        self.assertTrue(records[0]["attrs"]["hidden"])
        self.assertEqual(records[0]["attrs"]["input_type"], "hidden")
        self.assertNotIn(CANARY, repr(records))
        renamed = self.records('<form action="/submit" method="post"><input name="private_second" '
                               'type="hidden"></form>', "form_field")
        self.assertNotEqual(records[0]["identity"], renamed[0]["identity"])

    def test_input_type_changes_are_attributes_not_field_identity(self):
        template = '<form action="/submit"><input name="same" type="{}"></form>'
        old = self.records(template.format("text"), "form_field")[0]
        new = self.records(template.format("hidden"), "form_field")[0]
        self.assertEqual(old["identity"], new["identity"])
        self.assertNotEqual(old["attrs"], new["attrs"])

    def test_duplicate_hidden_controls_remain_distinct_without_values(self):
        prefix = '<form action="/submit" method="post"><input name="same" type="hidden" value="first">'
        old = self.records(prefix + '</form>', "form_field")
        new = self.records(prefix + '<input name="same" type="hidden" value="' + CANARY + '"></form>', "form_field")
        self.assertEqual(len(old), 1)
        self.assertEqual(len(new), 2)
        self.assertEqual(new[0]["identity"], new[1]["identity"])
        self.assertEqual(new[0]["attrs"], new[1]["attrs"])
        self.assertNotIn(CANARY, repr(new))

    def test_same_name_different_types_preserve_each_control_and_location(self):
        fields = self.records('<form action="/submit">\n<input name="same" type="text">\n'
                              '<input name="same" type="hidden"></form>', "form_field")
        self.assertEqual(len(fields), 2)
        self.assertEqual(fields[0]["identity"], fields[1]["identity"])
        self.assertEqual([row["attrs"]["input_type"] for row in fields], ["text", "hidden"])
        self.assertEqual([row["evidence"]["location"] for row in fields], ["line 2", "line 3"])

    def test_reordered_controls_preserve_private_multiset(self):
        from collections import Counter

        first = '<input name="same" type="text">'
        second = '<input name="same" type="hidden">'
        before = self.records('<form action="/submit">' + first + second + '</form>', "form_field")
        after = self.records('<form action="/submit">' + second + first + '</form>', "form_field")
        def keyed(rows):
            return Counter((row["identity"], row["attrs"]["input_type"], row["attrs"]["hidden"]) for row in rows)
        self.assertEqual(keyed(before), keyed(after))

    def test_same_name_controls_across_targets_keep_target_association(self):
        text = ('<form action="/one"><input name="same" type="hidden"><input name="same" type="hidden"></form>'
                '<form action="/two"><input name="same" type="hidden"></form>')
        fields = self.records(text, "form_field")
        self.assertEqual([row["path"] for row in fields], ["/one", "/one", "/two"])

    def test_duplicate_controls_still_obey_record_bound(self):
        with patch.object(signals, "MAX_RECORDS", 3):
            with self.assertRaises(ValueError):
                signals.collect_html('<form action="/submit">' + '<input name="same">' * 3 + '</form>')

    def test_form_method_and_origin_changes_have_distinct_private_identities(self):
        form = '<form action="https://{}/orders" method="{}"><input name="email"></form>'
        first = self.records(form.format("first.invalid", "get"), "endpoint")[0]
        method = self.records(form.format("first.invalid", "post"), "endpoint")[0]
        origin = self.records(form.format("second.invalid", "get"), "endpoint")[0]
        self.assertNotEqual(first["identity"], method["identity"])
        self.assertNotEqual(first["identity"], origin["identity"])
        self.assertEqual(first["path"], origin["path"])

    def test_same_script_path_different_domain_is_visible_privately(self):
        first = self.records('<script src="https://first.invalid/app.js"></script>', "script")[0]
        second = self.records('<script src="https://second.invalid/app.js"></script>', "script")[0]
        self.assertNotEqual(first["identity"], second["identity"])
        self.assertEqual(first["path"], second["path"])
        self.assertEqual(first["attrs"]["origin_relation"], "unknown")

    def test_site_origin_classifies_but_does_not_invent_document_path(self):
        result = signals.collect_html('<form><input name="email"></form><a href="child">Child</a>',
                                      site_origin="https://example.invalid")
        endpoint = next(row for row in result["records"] if row["category"] == "endpoint")
        self.assertEqual(endpoint["target"][0], "current_document")
        self.assertEqual(endpoint["path"], "")
        self.assertIn("current_document_target_unresolved", result["scope_notes"])
        navigation = next(row for row in result["records"] if row["category"] == "navigation")
        self.assertEqual(navigation["path"], "child")
        self.assertEqual(navigation["attrs"]["origin_relation"], "same_origin")
        self.assertIn("document_path_unknown", result["scope_notes"])
        self.assertEqual(result["limitations"], [])

    def test_external_origin_classification_and_default_port_host_case(self):
        record = self.records('<script src="HTTPS://EXAMPLE.INVALID:443/app.js"></script>', "script",
                              site_origin="https://example.invalid")[0]
        self.assertEqual(record["origin"], "https://example.invalid")
        self.assertEqual(record["attrs"]["origin_relation"], "same_origin")
        external = self.records('<script src="https://cdn.invalid/app.js"></script>', "script",
                                site_origin="https://example.invalid")[0]
        self.assertEqual(external["attrs"]["origin_relation"], "cross_origin")

    def test_trailing_dot_origin_remains_distinct(self):
        first = signals.canonical_target("https://example.invalid/app.js", "https://example.invalid")
        dotted = signals.canonical_target("https://example.invalid./app.js", "https://example.invalid")
        self.assertNotEqual(first["identity"], dotted["identity"])
        self.assertEqual(dotted["origin_relation"], "cross_origin")

    def test_root_relative_and_same_origin_absolute_targets_are_equivalent_with_context(self):
        relative = signals.canonical_target("/api/orders?email=first", "https://example.invalid")
        absolute = signals.canonical_target("https://EXAMPLE.INVALID:443/api/orders?email=second")
        self.assertEqual(relative["identity"], absolute["identity"])
        child = signals.canonical_target("child", "https://example.invalid")
        self.assertEqual(child["identity"][0], "relative")

    def test_scheme_relative_origin_is_classified_only_with_scheme_context(self):
        unknown = signals.canonical_target("//cdn.invalid/app.js")
        known = signals.canonical_target("//cdn.invalid/app.js", "https://example.invalid")
        self.assertEqual(unknown["origin_relation"], "unknown")
        self.assertEqual(known["origin_relation"], "cross_origin")
        self.assertEqual(known["origin"], "https://cdn.invalid")

    def test_query_values_fragments_and_query_order_do_not_change_identity(self):
        first = signals.canonical_target("https://host.invalid/path?b=first&a=" + CANARY + "#one")
        second = signals.canonical_target("https://host.invalid/path?a=other&b=last#two")
        self.assertEqual(first["identity"], second["identity"])
        self.assertNotIn(CANARY, repr(first))

    def test_values_and_prose_are_not_collected(self):
        text = '<p>' + CANARY + '</p><form action="/submit"><input name="email" value="' + CANARY + '"></form>'
        self.assertNotIn(CANARY, repr(signals.collect_html(text)))

    def test_duplicate_attributes_use_first_value(self):
        records = self.records('<form action="/first" action="/second" method="get" method="post">'
                               '<input name="one" name="two" type="hidden" type="text"></form>')
        endpoint = next(row for row in records if row["category"] == "endpoint")
        field = next(row for row in records if row["category"] == "form_field")
        self.assertEqual(endpoint["path"], "/first")
        self.assertEqual(endpoint["attrs"]["method"], "GET")
        self.assertEqual(field["name"], "one")
        self.assertTrue(field["attrs"]["hidden"])

    def test_template_links_scripts_forms_and_controls_are_omitted(self):
        text = ('<template><a href="/ignored">Ignored</a><script src="https://ignored.invalid/a.js"></script>'
                '<form action="/ignored"><input name="ignored"></form></template><a href="/kept">Kept</a>')
        result = signals.collect_html(text)
        self.assertEqual([(r["category"], r["path"]) for r in result["records"]], [("navigation", "/kept")])
        self.assertIn("template_content_excluded", result["scope_notes"])

    def test_raw_text_comments_inline_scripts_and_foreign_content_are_not_scanned(self):
        text = ('<!-- <form action="/comment"></form> --><script>const text="<a href=\'/script\'>";</script>'
                '<textarea><a href="/textarea">No</a></textarea><svg><a href="/foreign">No</a></svg>'
                '<a href="/kept">Yes</a>')
        result = signals.collect_html(text)
        self.assertEqual([(r["category"], r["path"]) for r in result["records"]], [("navigation", "/kept")])
        self.assertEqual(len(result["inline_scripts"]), 1)
        self.assertIn("foreign_content_omitted", result["limitations"])

    def test_foreign_breakout_uses_existing_parser_rules(self):
        records = self.records('<svg><p>Breakout</p><a href="/kept">Yes</a>')
        self.assertEqual(records[0]["path"], "/kept")

    def test_unclosed_form_is_retained_with_explicit_limitation(self):
        result = signals.collect_html('<form action="/submit"><input name="email" type="hidden">')
        self.assertEqual({r["category"] for r in result["records"]}, {"endpoint", "form_field"})
        self.assertIn("unclosed_form_declaration", result["limitations"])

    def test_nested_ignored_form_closes_ancestor_using_existing_semantics(self):
        result = signals.collect_html('<form action="/outer"><input name="one"><form action="/inner">'
                                      '<input name="two"></form><input name="three"></form>')
        endpoints = [r for r in result["records"] if r["category"] == "endpoint"]
        fields = [r["name"] for r in result["records"] if r["category"] == "form_field"]
        self.assertEqual([r["path"] for r in endpoints], ["/outer"])
        self.assertEqual(fields, ["one", "two"])
        self.assertIn("nested_form_ownership_unsupported", result["limitations"])

    def test_external_form_ownership_is_not_attached_to_ancestor(self):
        result = signals.collect_html('<form action="/submit"><input name="outside" form="other">'
                                      '<input name="inside"></form><input name="external" form="other">')
        self.assertEqual([r["name"] for r in result["records"] if r["category"] == "form_field"], ["inside"])
        self.assertIn("external_form_ownership_omitted", result["limitations"])
        self.assertEqual(result["omitted_count"], 2)

    def test_submitter_override_is_explicitly_uninspected(self):
        result = signals.collect_html('<form action="/submit"><input name="send" type="submit" '
                                      'formaction="/different" formmethod="post"></form>')
        self.assertIn("submitter_overrides_uninspected", result["limitations"])

    def test_identical_targets_across_forms_are_explicitly_aggregated(self):
        result = signals.collect_html('<form action="/submit"><input name="one"></form>'
                                      '<form action="/submit"><input name="two"></form>')
        self.assertIn("forms_aggregated_by_target", result["scope_notes"])

    def test_absolute_base_applies_before_its_textual_location(self):
        result = signals.collect_html('<a href="child">Child</a><base href="https://cdn.invalid/dir/">',
                                      site_origin="https://example.invalid")
        nav = next(row for row in result["records"] if row["category"] == "navigation")
        self.assertEqual(nav["path"], "/dir/child")
        self.assertEqual(nav["attrs"]["origin_relation"], "cross_origin")

    def test_invalid_first_base_is_not_replaced_with_later_valid_base(self):
        result = signals.collect_html('<base href="javascript:bad"><base href="https://cdn.invalid/dir/">'
                                      '<a href="child">Child</a>')
        self.assertEqual(result["records"][0]["path"], "child")
        self.assertIn("document_path_unknown", result["scope_notes"])

    def test_invalid_urls_are_counted_without_leaking_values(self):
        values = ("https://user:" + CANARY + "@host.invalid/x", "https://host.invalid:99999/x",
                  "https://host.invalid/" + "\\" + CANARY,
                  "https://[bad/" + CANARY, "https://host.invalid/%0a" + CANARY,
                  "https://unicodé.invalid/" + CANARY, "https://127.1/" + CANARY)
        for raw in values:
            with self.subTest(case=values.index(raw)):
                result = signals.collect_html('<script src="' + raw + '"></script>')
                self.assertEqual(result["records"], [])
                self.assertIn("unsupported_url_omitted", result["limitations"])
                self.assertEqual(result["omitted_count"], 1)
                self.assertNotIn(CANARY, json.dumps(result))

    def test_origin_validator_is_fixed_and_does_not_accept_page_paths(self):
        self.assertEqual(signals.validate_site_origin("HTTPS://EXAMPLE.INVALID:443/"), "https://example.invalid")
        for raw in ("https://host.invalid/path", "https://host.invalid?token=" + CANARY,
                    "https://user:" + CANARY + "@host.invalid", CANARY, "https://host.invalid#" + CANARY):
            with self.assertRaises(ValueError) as caught:
                signals.validate_site_origin(raw)
            self.assertNotIn(CANARY, str(caught.exception))

    def test_blank_and_fragment_only_navigation_are_not_page_changes(self):
        self.assertEqual(self.records('<a href="">Blank</a><a href="#part">Part</a>'), [])

    def test_stylesheet_and_embedded_resource_origins_are_retained_privately(self):
        rows = self.records('<link rel="stylesheet" href="https://cdn.invalid/style.css">'
                            '<iframe src="https://frame.invalid/app"></iframe>', "script")
        self.assertEqual({r["attrs"]["asset_kind"] for r in rows}, {"stylesheet", "embedded-document"})

    def test_size_record_tag_and_deadline_limits_fail_with_fixed_error(self):
        for text, options in (("x", {"source": CANARY}), ("\x00", {}), ("x", {"deadline": time.monotonic() - 1}),
                              ("x", {"deadline": float("inf")})):
            with self.assertRaises(ValueError) as caught:
                signals.collect_html(text, **options)
            self.assertNotIn(CANARY, str(caught.exception))
        with patch.object(signals, "MAX_BYTES", 2):
            with self.assertRaises(ValueError):
                signals.collect_html("more")
        with patch.object(signals, "MAX_RECORDS", 1):
            with self.assertRaises(ValueError):
                signals.collect_html('<a href="/one">One</a><a href="/two">Two</a>')
        with self.assertRaises(ValueError):
            signals.collect_html("<p>" * 10001)


    def test_scheme_relative_explicit_ports_do_not_collapse_without_scheme(self):
        implicit = signals.canonical_target("//cdn.invalid/app.js")
        for port in (80, 443):
            explicit = signals.canonical_target("//cdn.invalid:" + str(port) + "/app.js")
            self.assertNotEqual(implicit["identity"], explicit["identity"])
        self.assertEqual(signals.canonical_target("//cdn.invalid:443/app.js", "https://example.invalid")["identity"],
                         signals.canonical_target("https://cdn.invalid/app.js")["identity"])

    def test_button_submitter_override_is_explicitly_uninspected(self):
        button = signals.collect_html('<form action="/submit"><button formaction="/different" '
                                      'formmethod="post">Send</button></form>')
        self.assertIn("submitter_overrides_uninspected", button["limitations"])

    def test_fragment_only_navigation_stays_ignored_with_base_and_origin(self):
        for options in ({}, {"site_origin": "https://example.invalid"}):
            result = signals.collect_html('<base href="https://example.invalid/dir/">'
                                          '<a href="#section">Here</a>', **options)
            self.assertEqual([r for r in result["records"] if r["category"] == "navigation"], [])
            self.assertEqual(len([r for r in result["records"] if r["category"] == "document_base"]), 1)

    def test_executable_inline_scripts_are_collected_privately_and_never_executed(self):
        body = 'throw new Error("' + CANARY + '"); gtag("event", "purchase");'
        with patch("subprocess.Popen", side_effect=AssertionError("No processes allowed")), \
                patch("socket.create_connection", side_effect=AssertionError("No network allowed")):
            result = signals.collect_html('<script>' + body + '</script>')
        self.assertEqual(result["records"], [])
        self.assertEqual(result["inline_scripts"], [{"text": body, "line_offset": 0,
                                                     "kind": "javascript", "module_mode": False}])

    def test_inline_javascript_type_and_module_mode(self):
        for script_type in ("", ' type="text/javascript"', ' type="application/javascript"', ' type="module"'):
            result = signals.collect_html('<script' + script_type + '>gtag("event", "purchase");</script>')
            self.assertEqual(len(result["inline_scripts"]), 1)
            self.assertEqual(result["inline_scripts"][0]["module_mode"], "module" in script_type)

    def test_non_executable_template_foreign_comment_and_external_bodies_are_skipped(self):
        body = 'gtag("event", "private");'
        examples = ('<script type="application/json">' + body + '</script>',
                    '<script type="application/ld+json">' + body + '</script>',
                    '<script type="importmap">' + body + '</script>',
                    '<script type="speculationrules">' + body + '</script>',
                    '<template><script>' + body + '</script></template>',
                    '<svg><script>' + body + '</script></svg>',
                    '<!-- <script>' + body + '</script> -->',
                    '<script src="/app.js">' + body + '</script>',
                    '<script src="">' + body + '</script>')
        for text in examples:
            self.assertEqual(signals.collect_html(text)["inline_scripts"], [])

    def test_inline_line_offsets_account_for_multiline_opening_tags(self):
        text = '<p>Page</p>\n<script\n type="module">\nconst x = 1;\ngtag("event", "purchase");\n</script>'
        inline = signals.collect_html(text)["inline_scripts"][0]
        js_line = inline["text"][:inline["text"].index("gtag(")].count("\n") + 1
        actual = text[:text.index("gtag(")].count("\n") + 1
        self.assertEqual(inline["line_offset"] + js_line, actual)
        self.assertTrue(inline["module_mode"])

    def test_multiple_inline_line_offsets_are_independent(self):
        text = '<script>first();</script>\n<p>Body</p>\n<script>\nsecond();</script>'
        scripts = signals.collect_html(text)["inline_scripts"]
        self.assertEqual([script["line_offset"] for script in scripts], [0, 2])

    def test_empty_inline_bodies_are_omitted(self):
        self.assertEqual(signals.collect_html('<script>  \n</script>' * 9)["inline_scripts"], [])

    def test_inline_bounds_and_unclosed_bodies_fail_closed_with_fixed_errors(self):
        cases = ('<script>' + CANARY, '<script/>' + CANARY)
        for text in cases:
            with self.assertRaises(ValueError) as caught:
                signals.collect_html(text)
            self.assertNotIn(CANARY, str(caught.exception))
        with patch.object(signals, "MAX_INLINE_BYTES", 8):
            for text in ('<script>123456789</script>', '<script>12345</script><script>12345</script>'):
                with self.assertRaises(ValueError):
                    signals.collect_html(text)
        with patch.object(signals, "MAX_INLINE_SCRIPTS", 2):
            with self.assertRaises(ValueError):
                signals.collect_html('<script>x();</script>' * 3)

    def test_event_handler_attributes_are_explicitly_uninspected(self):
        result = signals.collect_html('<button onclick="' + CANARY + '">Click</button>')
        self.assertIn("event_handler_attributes_uninspected", result["limitations"])
        self.assertNotIn(CANARY, repr(result))

    def test_actionless_form_retains_hidden_control_without_inventing_root_url(self):
        for suffix in ("", ' action=""', ' action="   "'):
            result = signals.collect_html('<form' + suffix + '><input type="hidden" name="private_field" '
                                          'value="' + CANARY + '"></form>')
            endpoints = [row for row in result["records"] if row["category"] == "endpoint"]
            fields = [row for row in result["records"] if row["category"] == "form_field"]
            self.assertEqual(len(endpoints), 1)
            self.assertEqual(endpoints[0]["path"], "")
            self.assertEqual(endpoints[0]["target"][0], "current_document")
            self.assertEqual(len(fields), 1)
            self.assertTrue(fields[0]["attrs"]["hidden"])
            self.assertEqual(result["limitations"], [])
            self.assertEqual(result["omitted_count"], 0)
            self.assertNotIn(CANARY, repr(result))

    def test_actionless_target_is_stable_per_source_and_ignores_absolute_base(self):
        text = '<base href="https://cdn.invalid/elsewhere"><form><input name="email"></form>'
        first = self.records(text, "endpoint", source="source_1")[0]
        second = self.records(text, "endpoint", source="source_2")[0]
        self.assertNotEqual(first["target"], second["target"])
        self.assertEqual(first["path"], "")
        self.assertIsNone(first["origin"])

    def test_fragment_form_actions_keep_current_document_fields(self):
        bare = self.records('<form><input name="private_field" type="hidden"></form>', "form_field")[0]
        for action in ("#", "#section", "  #section  "):
            result = signals.collect_html('<base href="https://cdn.invalid/elsewhere/">'
                                          '<form action="' + action + '"><input name="private_field" '
                                          'type="hidden" value="' + CANARY + '"></form>')
            fields = [r for r in result["records"] if r["category"] == "form_field"]
            self.assertEqual(len(fields), 1)
            self.assertEqual(fields[0]["identity"], bare["identity"])
            self.assertTrue(fields[0]["attrs"]["hidden"])
            self.assertEqual(result["limitations"], [])
            self.assertEqual(result["omitted_count"], 0)
            self.assertNotIn(CANARY, repr(result))

    def test_non_http_form_actions_preserve_controls_with_actual_limitation(self):
        targets = []
        for scheme in ("javascript:", "data:text/plain,", "mailto:person@", "tel:"):
            result = signals.collect_html('<form action="' + scheme + CANARY + '" method="post">'
                                          '<input name="private_field" type="hidden" value="' + CANARY + '"></form>')
            fields = [r for r in result["records"] if r["category"] == "form_field"]
            self.assertEqual(len(fields), 1)
            self.assertTrue(fields[0]["attrs"]["hidden"])
            self.assertEqual(fields[0]["target"][0], "unsupported_form_target")
            targets.append(fields[0]["target"])
            self.assertIn("unsupported_form_target_unresolved", result["limitations"])
            self.assertEqual(result["omitted_count"], 1)
            self.assertNotIn(CANARY, repr(result))
        self.assertEqual(len(set(targets)), 1)

    def test_invalid_http_form_action_preserves_controls_without_raw_destination(self):
        result = signals.collect_html('<form action="https://user:' + CANARY + '@host.invalid/submit">'
                                      '<input name="private_field"></form>')
        fields = [r for r in result["records"] if r["category"] == "form_field"]
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0]["target"][0], "unsupported_form_target")
        self.assertIn("unsupported_url_omitted", result["limitations"])
        self.assertIn("unsupported_form_target_unresolved", result["limitations"])
        self.assertEqual(result["omitted_count"], 1)
        self.assertNotIn(CANARY, repr(result))

    def test_relative_base_changes_are_private_declarations_with_literal_children(self):
        results = [signals.collect_html('<base href="' + base + '"><form action="submit">'
                                        '<input name="email"></form><a href="pricing">Pricing</a>')
                   for base in ("/app/", "/other/")]
        bases = [next(r for r in result["records"] if r["category"] == "document_base") for result in results]
        self.assertNotEqual(bases[0]["identity"], bases[1]["identity"])
        self.assertEqual([r["path"] for r in bases], ["/app/", "/other/"])
        for result in results:
            self.assertEqual([r["path"] for r in result["records"] if r["category"] == "endpoint"], ["submit"])
            self.assertEqual([r["path"] for r in result["records"] if r["category"] == "navigation"], ["pricing"])
            self.assertEqual(result["limitations"], [])

    def test_first_active_base_only_uses_first_duplicate_href(self):
        text = ('<base target="_blank"><!-- <base href="/comment/"> -->'
                '<template><base href="/template/"></template><script>"<base href=\'/script/\'>";</script>'
                '<base href="/first/" href="/duplicate/"><base href="/later/">')
        bases = self.records(text, "document_base")
        self.assertEqual(len(bases), 1)
        self.assertEqual(bases[0]["path"], "/first/")

    def test_invalid_first_base_reports_omission_without_using_later_base(self):
        result = signals.collect_html('<base href="javascript:' + CANARY + '">'
                                      '<base href="https://cdn.invalid/later/"><a href="child">Child</a>')
        self.assertEqual([r for r in result["records"] if r["category"] == "document_base"], [])
        self.assertEqual([r["path"] for r in result["records"] if r["category"] == "navigation"], ["child"])
        self.assertIn("unsupported_document_base_omitted", result["limitations"])
        self.assertEqual(result["omitted_count"], 1)
        self.assertNotIn(CANARY, repr(result))

    def test_parent_and_dot_relative_links_are_retained_without_guessing_directory(self):
        for path in ("../next", "./next", "dir/../next"):
            result = signals.collect_html('<a href="' + path + '">Next</a>')
            self.assertEqual(result["records"][0]["path"], path)
            self.assertEqual(result["records"][0]["target"][0], "relative")
            self.assertEqual(result["limitations"], [])
            self.assertEqual(result["omitted_count"], 0)
            self.assertIn("document_path_unknown", result["scope_notes"])

    def test_non_http_and_fragment_links_are_quietly_excluded(self):
        text = ('<a href="mailto:person@example.invalid">Email</a><a href="tel:123">Call</a>'
                '<a href="javascript:' + CANARY + '">Run</a><a href="data:text/plain,' + CANARY + '">Data</a>'
                '<a href="#section">Here</a>')
        result = signals.collect_html(text)
        self.assertEqual(result["records"], [])
        self.assertEqual(result["limitations"], [])
        self.assertEqual(result["omitted_count"], 0)
        self.assertIn("non_http_references_excluded", result["scope_notes"])
        self.assertNotIn(CANARY, repr(result))

    def test_large_ordinary_link_and_inline_counts_fit_reasonable_bounds(self):
        result = signals.collect_html(''.join('<a href="/page/' + str(i) + '">Page</a>' for i in range(600)))
        self.assertEqual(len(result["records"]), 600)
        self.assertEqual(result["limitations"], [])
        scripts = signals.collect_html('<script>x();</script>' * 9)
        self.assertEqual(len(scripts["inline_scripts"]), 9)
        self.assertEqual(scripts["limitations"], [])

    def test_private_collector_handles_many_fields_without_changing_legacy_limit(self):
        from surface_map.analyze import analyze

        text = '<form action="/submit">' + ''.join('<input name="field_' + str(i) + '">' for i in range(40)) + '</form>'
        result = signals.collect_html(text)
        self.assertEqual(len([r for r in result["records"] if r["category"] == "form_field"]), 40)
        self.assertEqual(result["limitations"], [])
        legacy = analyze([{"kind": "html", "text": text}], javascript_engine="basic")
        self.assertTrue(legacy.get("evidence_limits"))


if __name__ == "__main__":
    unittest.main()
