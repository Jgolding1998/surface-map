import unittest

from surface_map.analyze import analyze


class HTMLTextContainersTests(unittest.TestCase):
    def report(self, text):
        return analyze([{"kind": "html", "text": text}], javascript_engine="basic")

    def test_script_slash_does_not_expose_script_text_as_declared_link(self):
        report = self.report('<script src="/app.js"/><a href="/api/users">fake</a></script><a href="/docs">real</a>')
        self.assertEqual([(r["method"], r["path"]) for r in report["routes"]], [("GET", "/docs")])
        self.assertEqual(report["assets"][0]["path"], "/app.js")

    def test_text_containers_never_fabricate_links_forms_or_scripts(self):
        for tag in ("style", "title", "textarea", "xmp", "iframe", "noembed", "noframes", "noscript"):
            for closing in (">", "/>"):
                with self.subTest(tag=tag, closing=closing):
                    text = (f'<{tag}{closing}<a href="/api/users">fake</a>'
                            '<form action="/api/orders"><input name="password"></form>'
                            f'<script src="/fake.js"></script></{tag}><a href="/docs">real</a>')
                    report = self.report(text)
                    self.assertEqual([r["path"] for r in report["routes"]], ["/docs"])
                    self.assertEqual(report["assets"], [])

    def test_plaintext_has_no_closing_tag(self):
        report = self.report('<plaintext/>ignored</plaintext><a href="/api/items">still text</a>')
        self.assertEqual(report["routes"], [])
        self.assertTrue(any("end of the capture" in w["reason"] for w in report["warnings"]))

    def test_foreign_self_closing_text_containers_do_not_hide_later_html(self):
        for root in ("svg", "math"):
            for tag in ("script", "style", "title"):
                with self.subTest(root=root, tag=tag):
                    report = self.report(f'<{root}><{tag}/></{root}><a href="/docs">real</a>')
                    self.assertEqual([r["path"] for r in report["routes"]], ["/docs"])
                    self.assertTrue(any("foreign-content" in w["reason"] for w in report["warnings"]))

    def test_foreign_roots_and_nonempty_containers_do_not_swallow_html(self):
        for prefix in ('<svg/>', '<math/>', '<svg><style>ignored</style></svg>', '<svg><title>ignored</title></svg>'):
            with self.subTest(prefix=prefix):
                self.assertEqual([r["path"] for r in self.report(prefix + '<a href="/docs">real</a>')["routes"]], ["/docs"])

    def test_unclosed_foreign_markup_allows_html_breakout(self):
        report = self.report('<svg><path/><div><a href="/docs">real</a></div>')
        self.assertEqual([r["path"] for r in report["routes"]], ["/docs"])

    def test_conditional_and_unclosed_text_containers_report_omissions(self):
        report = self.report('<noscript>conditional</noscript><textarea>unfinished')
        warnings = [w["reason"] for w in report["warnings"]]
        self.assertTrue(any("scripting state" in w for w in warnings))
        self.assertTrue(any("end of the capture" in w for w in warnings))

    def test_foreign_text_containers_do_not_hide_or_supply_document_base(self):
        for prefix in ('<svg><script/></svg>', '<math><style/></math>', '<svg><title>text</title></svg>',
                       '<svg><base href="/private/"></base></svg>', '<svg/>', '<math/>'):
            with self.subTest(prefix=prefix):
                report = analyze([{"kind": "html", "text": prefix + '<base href="/api/"><a href="users">real</a>'}],
                                 javascript_engine="basic", document_url="https://example.com/dashboard")
                self.assertEqual([r["path"] for r in report["routes"]], ["/api/users"])

    def test_form_slash_keeps_following_fields_and_textarea_owns_only_its_name(self):
        report = self.report('<form action="/api/orders" method="post"/>'
                             '<textarea name="description"/><input name="password"></textarea>'
                             '<input name="quantity"/></form>')
        route = report["routes"][0]
        self.assertEqual(route["method"], "POST")
        self.assertEqual(set(route["request_shapes"][0]["properties"]), {"description", "quantity"})


if __name__ == "__main__":
    unittest.main()
