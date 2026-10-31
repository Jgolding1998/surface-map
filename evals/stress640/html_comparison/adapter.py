"""Our extraction policy over Beautiful Soup/html5lib, using input HTML only."""
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.builder._html5lib import HTML5TreeBuilder
import html5lib

from surface_map.analyze import safe_field, safe_path

EXCLUDED_ANCESTORS = frozenset("template script style title textarea xmp iframe noembed noframes noscript plaintext".split())


class ScriptingEnabledBuilder(HTML5TreeBuilder):
    """Set html5lib's scripting state; this parses scripts without executing them."""
    def feed(self, markup):
        parser = html5lib.HTMLParser(tree=self.create_treebuilder)
        self.underlying_builder.parser = parser
        try:
            parser.parse(markup, scripting=True)
        finally:
            self.underlying_builder.parser = None


def active(tag):
    return (tag.namespace == "http://www.w3.org/1999/xhtml"
            and not any(parent.name in EXCLUDED_ANCESTORS for parent in tag.parents))


def extract(html, document_url=None):
    soup = BeautifulSoup(html, builder=ScriptingEnabledBuilder())
    base = document_url
    for tag in soup.find_all("base", href=True):
        if active(tag):
            base = urljoin(document_url or "", tag["href"])
            break
    result = {}

    def put(method, raw, fields=(), is_form=False):
        target = safe_path(raw)
        if target is None:
            return
        path, query = target
        data = result.setdefault((method, path), {"query_fields": [], "request_fields": []})
        used = set()
        controls = sorted(safe_field(name, index, used) for index, name in enumerate(sorted(set(fields)), 1))
        query_fields = controls if is_form and method == "GET" else query
        data["query_fields"] = sorted(set(data["query_fields"]) | set(query_fields))
        if is_form and method == "POST":
            data["request_fields"] = sorted(set(data["request_fields"]) | set(controls))

    for tag in soup.find_all("a", href=True):
        if active(tag):
            put("GET", urljoin(base, tag["href"]) if base else tag["href"])
    for form in soup.find_all("form"):
        if not active(form):
            continue
        method = str(form.get("method", "GET")).upper()
        if method == "DIALOG":
            continue
        if method not in {"GET", "POST"}:
            method = "GET"
        action = form.get("action", "")
        target = (urljoin(base, action) if base else action) if action else document_url
        if not target:
            continue
        controls = [tag["name"] for tag in form.find_all(["input", "select", "textarea"], attrs={"name": True})
                    if active(tag) and tag.find_parent("form") is form]
        put(method, target, controls, is_form=True)
    return result
