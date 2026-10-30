import json
import unittest
from unittest.mock import patch

from surface_map.analyze import ArtifactError, analyze
from surface_map.js_parser import available


READY = available()[0]
HOST = "docs-canary.example"
PAGE = f"https://{HOST}/api/v1/index.html?view=1#top"


def html(text):
    return {"kind": "html", "text": text}


def js(text):
    return {"kind": "javascript", "text": text}


def run(artifacts, document_url=PAGE, engine="basic"):
    return analyze(artifacts, javascript_engine=engine, document_url=document_url)


def routes(report):
    return {(route["method"], route["path"]) for route in report["routes"]}


def assets(report):
    return {(item["kind"], item["path"]) for item in report["assets"]}


def reasons(report):
    return [item["reason"] for item in report["warnings"]]


class DocumentUrlTests(unittest.TestCase):
    def assertNoUrlLeak(self, report):
        text = json.dumps(report)
        for secret in (HOST, "https://", "http://", "cdn.example", "view=1"):
            self.assertNotIn(secret, text)

    def test_relative_links_assets_and_forms_resolve_against_document(self):
        page = """<a href="orders/7">o</a>
<a href="../settings?tab=1">s</a>
<a href="?page=2">p</a>
<a href="/api/health">h</a>
<script src="static/app.js"></script>
<link rel="stylesheet" href="css/main.css">
<iframe src="embed/item"></iframe>
<form method="post" action="checkout"><input name="email"></form>"""
        report = run([html(page)])
        self.assertTrue({("GET", "/api/v1/orders/{integer_4}"), ("GET", "/api/settings"), ("GET", "/api/v1/index.html"),
                         ("GET", "/api/health"), ("POST", "/api/v1/checkout")} <= routes(report))
        self.assertTrue({("script", "/api/v1/static/app.js"), ("stylesheet", "/api/v1/css/main.css"),
                         ("embedded-document", "/api/v1/{segment_3}/item")} <= assets(report))
        checkout = next(route for route in report["routes"] if route["path"] == "/api/v1/checkout")
        self.assertEqual(checkout["evidence"], [{"source": "source_1", "location": "line 8", "kind": "form"}])
        self.assertEqual(checkout["confidence"], "declared")
        self.assertNotIn("A relative URL was omitted because the page URL is unknown", reasons(report))
        self.assertNoUrlLeak(report)

    def test_default_offline_behavior_is_unchanged(self):
        page = '<a href="orders/7">o</a><a href="#x">f</a><form action=""><input name="q"></form><form><input name="q"></form><a href="/api/users">u</a>'
        artifacts = [html(page), js('fetch("api/items")')]
        default = analyze(artifacts, javascript_engine="basic")
        self.assertEqual(default, analyze(artifacts, javascript_engine="basic", document_url=None))
        self.assertEqual(routes(default), {("GET", "/api/users")})
        self.assertIn("A relative URL was omitted because the page URL is unknown", reasons(default))
        self.assertIn("A form without an action was omitted because its page URL is unknown", reasons(default))

    def test_base_href_applies_before_and_after_its_position(self):
        page = """<html><head><title>t</title></head><body>
<a href="users">e</a>
<base href="https://cdn.example/docs/">
<base href="/ignored/">
<a href="orders">l</a>
</body></html>"""
        report = run([html(page)])
        self.assertEqual(routes(report), {("GET", "/docs/users"), ("GET", "/docs/orders")})
        self.assertEqual({route["evidence"][0]["location"] for route in report["routes"]}, {"line 2", "line 5"})
        self.assertNoUrlLeak(report)

    def test_relative_base_resolves_against_document_and_template_base_is_ignored(self):
        page = '<template><base href="/wrong/"></template><base target="_top"><base href="../v2/"><a href="users">u</a>'
        self.assertEqual(routes(run([html(page)])), {("GET", "/api/v2/users")})

    def test_empty_or_missing_form_action_targets_document_not_base(self):
        page = """<base href="/public/">
<form method="post"><input name="email"></form>
<form action="" method="post"><input name="name"></form>
<form action="   " method="get"><input name="q"></form>"""
        report = run([html(page)])
        self.assertEqual(routes(report), {("POST", "/api/v1/index.html"), ("GET", "/public")})
        post = next(route for route in report["routes"] if route["method"] == "POST")
        self.assertEqual(post["request_shapes"], [{"type": "object", "properties": {"email": {"type": "string"}}},
                                                  {"type": "object", "properties": {"name": {"type": "string"}}}])
        get = next(route for route in report["routes"] if route["method"] == "GET")
        self.assertEqual(get["query_fields"], ["q"])
        self.assertNoUrlLeak(report)

    def test_base_markup_displayed_as_text_does_not_change_resolution(self):
        page = '<title>Example <base href="/wrong/"></title><textarea><base href="/wrong/"></textarea><base href="/docs/"><a href="users">u</a>'
        self.assertEqual(routes(run([html(page)])), {("GET", "/docs/users")})

    def test_fragment_links_are_ignored(self):
        report = run([html('<a href="#section">s</a><a href="#">t</a>')])
        self.assertEqual(report["routes"], [])
        self.assertEqual(report["warnings"], [])

    def test_inactive_and_raw_text_bases_are_ignored(self):
        for tag in ("noscript", "xmp", "iframe", "noembed", "noframes", "title", "textarea", "script", "style"):
            for closing in (">", "/>"):
                with self.subTest(tag=tag, closing=closing):
                    page = f'<{tag}{closing}<base href="/wrong/"></{tag}><base href="/docs/"><a href="users">u</a>'
                    self.assertEqual(routes(run([html(page)])), {("GET", "/docs/users")})

    def test_unsupported_base_does_not_label_relative_urls(self):
        for base in ("javascript:void(0)", "data:text/html,x", "ftp://files.example/", "https://user:pw@cdn.example/",
                     "https://cdn.example\\x/", "https://cdn.example:99999/", "http://"):
            with self.subTest(base=base):
                page = f'<base href="{base}"><a href="users/1">u</a><script src="app.js"></script><form method="post"><input name="q"></form>'
                report = run([html(page), js('fetch("api/items")')])
                self.assertEqual(routes(report), {("POST", "/api/v1/index.html")})
                self.assertEqual(report["assets"], [])
                self.assertIn("The document base URL is unsupported; relative URLs were omitted", reasons(report))
                self.assertIn("A relative URL was omitted because the document base URL is unsupported", reasons(report))
                self.assertNoUrlLeak(report)

    def test_unsupported_relative_references_are_not_mislabeled(self):
        page = r'<a href="a\b">x</a><a href="%2e%2e/admin">y</a><a href="mailto:x@example.invalid">m</a>'
        report = run([html(page)])
        self.assertEqual(report["routes"], [])
        self.assertIn("An unsupported URL or request method was omitted", reasons(report))

    def test_invalid_document_urls_are_rejected_without_echo(self):
        for value in ("/app/index.html", "app/index.html", "ftp://files.example/a", "https://user@files.example/",
                      "https://files.example:pw@x/", "https:files.example/a", "https://files.example/a b", "https://files.example\\a",
                      "https://files.example:99999/", "https://", "", 7, b"https://files.example/", "javascript:alert(1)",
                      "https://files.example/" + "a" * 5000):
            with self.subTest(value=value), self.assertRaises(ArtifactError) as caught:
                run([html("<a href='x'>x</a>")], document_url=value)
            self.assertNotIn("files.example", str(caught.exception))

    def test_document_url_requires_one_html_document_without_har(self):
        har = {"kind": "har", "text": json.dumps({"log": {"entries": []}})}
        for artifacts in ([js("fetch('/a')")], [html("<p>"), html("<p>")], [html("<p>"), har]):
            with self.subTest(kinds=[item["kind"] for item in artifacts]), self.assertRaises(ArtifactError):
                run(artifacts)

    def test_relative_fetch_uses_document_base_even_when_script_precedes_html(self):
        report = run([js('fetch("api/items"); fetch("../v1/orders", {method: "POST"}); fetch("#x")'),
                      html('<a href="/x">x</a><base href="/api/v3/">')])
        self.assertTrue({("GET", "/api/v3/api/items"), ("POST", "/api/v1/orders"), ("GET", "/api/v3")} <= routes(report))
        items = next(route for route in report["routes"] if route["path"] == "/api/v3/api/items")
        self.assertEqual(items["evidence"], [{"source": "source_1", "location": "line 1", "kind": "javascript"}])
        self.assertNoUrlLeak(report)

    def test_base_prepass_shares_html_caps_and_deadline(self):
        with self.assertRaises(ArtifactError) as caught:
            run([html("<p>" * 10001 + '<base href="/x/">')])
        self.assertIn("source_1:", str(caught.exception))
        self.assertIn("tag limit", str(caught.exception))
        with patch("surface_map.analyze.time.monotonic", side_effect=[0, 0, 0] + [10] * 100), self.assertRaises(ArtifactError) as caught:
            run([html("<p>a</p>")])
        self.assertIn("deadline", str(caught.exception))

    @unittest.skipUnless(READY, "Explicit optional parser installation and supported Node required")
    def test_enhanced_relative_fetch_and_unresolved_targets_use_document_base(self):
        report = run([html('<base href="/public/">'),
                      js('fetch("api/items"); fetch("api/send", {method: "POST"}); fetch("api/maybe", options);')], engine="enhanced")
        self.assertTrue({("GET", "/public/api/items"), ("POST", "/public/api/{segment_3}")} <= routes(report))
        self.assertEqual(report["javascript_analysis"][0]["engine"], "enhanced")
        self.assertEqual([item["path"] for item in report["unresolved_operations"]], ["/public/api/{segment_3}"])
        self.assertNoUrlLeak(report)


if __name__ == "__main__":
    unittest.main()
