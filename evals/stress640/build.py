"""Build authored scenario combinations; never import or invoke the analyzer."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

SCHEMA = "surface-map-stress-tasks/1"
SCOPE = ("640 deterministic synthetic scenario combinations in 16 families, authored by the developers. "
         "Not 640 independent websites, a hidden holdout, a model comparison, or a universal accuracy claim.")


def artifact(kind, text):
    return {"kind": kind, "text": text}


def evidence(kind="link", source=1, location="line 1"):
    return [f"source_{source}", location, kind]


def route(method, path, kind="link", source=1, location="line 1", *, facts=None):
    facts = facts or [evidence(kind, source, location)]
    kinds = {fact[2] for fact in facts}
    confidence = "observed" if "har" in kinds else "declared" if kinds & {"link", "form"} else "inferred"
    return [method, path, confidence, facts]


def expectation(routes=(), unresolved=(), **extra):
    return {"routes": list(routes), "unresolved": list(unresolved), **extra}


def detail(method, path, **fields):
    return {"method": method, "path": path, **fields}


def entry(method="GET", url="https://capture.example/api/items", status=200, request=None, response=None):
    request_data = {"method": method, "url": url}
    response_data = {"status": status}
    if request is not None:
        request_data["postData"] = {"mimeType": "application/json", "text": json.dumps(request)}
    if response is not None:
        response_data["content"] = {"mimeType": "application/json", "text": json.dumps(response)}
    return {"request": request_data, "response": response_data}


def har(*entries):
    return artifact("har", json.dumps({"log": {"entries": entries}}, separators=(",", ":")))


def input_fingerprint(case):
    fields = {key: case[key] for key in ("task", "artifacts", "before", "after", "document_url",
              "before_document_url", "after_document_url", "include_page_signals") if key in case}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build():
    cases = []

    def add(family, axes, artifacts, expected, **options):
        number = sum(case["family"] == family for case in cases) + 1
        cases.append({"id": f"{family}-{number:02}", "family": family, "axes": axes,
                      "task": "analyze", "artifacts": artifacts, "expected": expected, **options})

    def compare(axes, before, after, **expected):
        family = "capture-comparison"
        number = sum(case["family"] == family for case in cases) + 1
        labels = {name: [] for name in ("added", "removed", "changed", "unresolved_added",
                                       "unresolved_removed", "unresolved_changed")}
        labels.update(expected)
        cases.append({"id": f"{family}-{number:02}", "family": family, "axes": axes,
                      "task": "compare", "before": before, "after": after, "expected": labels})

    # Destination normalization crossed with contexts that alter the evidence set.
    destinations = [
        ("known-path", "/docs", "/docs", []),
        ("numeric-id", "/users/42", "/users/{integer_2}", []),
        ("unknown-segment", "/users/visitor-name", "/users/{segment_2}", []),
        ("query-names", "/search?q=PRIVATE&page=2", "/search", ["page", "q"]),
        ("absolute-origin", "https://untrusted.example/api/items", "/api/items", []),
        ("network-path", "//untrusted.example/api/orders", "/api/orders", []),
        ("encoded-segment", "/api/%69tems", "/api/items", []),
        ("fragment", "/settings#private-fragment", "/settings", []),
    ]
    for label, href, path, fields in destinations:
        anchor = f'<a href="{href}">Open</a>'
        contexts = [
            ("ordinary", anchor, [evidence()]),
            ("duplicate-lines", anchor + "\n" + anchor, [evidence(), evidence(location="line 2")]),
            ("comment-decoy", '<!-- <a href="/admin">Decoy</a> -->\n' + anchor, [evidence(location="line 2")]),
            ("raw-decoy", '<textarea><a href="/admin">Decoy</a></textarea>\n' + anchor, [evidence(location="line 2")]),
            ("post-form", anchor + f'<form action="{href}" method="post"><input name="id"></form>', [evidence()]),
        ]
        for context, html, facts in contexts:
            routes = [route("GET", path, facts=facts)]
            details = [detail("GET", path, query_fields=fields)]
            if context == "post-form":
                routes.append(route("POST", path, "form"))
                details.append(detail("POST", path, request_fields=["id"], query_fields=fields))
            add("html-navigation", {"destination": label, "context": context}, [artifact("html", html)],
                expectation(routes, details=details, forbidden=["untrusted.example", "PRIVATE", "private-fragment", "visitor-name"]))

    # First-base selection and RFC URL resolution, including empty actions.
    bases = [("document", "", "/docs/"), ("relative-base", '<base href="../api/">', "/api/"),
             ("absolute-base", '<base href="https://cdn.example/v2/">', "/v2/"),
             ("first-base", '<base href="/v1/"><base href="/v3/">', "/v1/"),
             ("inert-base", '<template><base href="/private/"></template>', "/docs/")]
    for base_name, markup, prefix in bases:
        for name, target, suffix, absolute, query in [
            ("child", "items", "items", None, []), ("dot-child", "./orders", "orders", None, []),
            ("parent", "../users", "", "/users", []), ("query", "?page=7", "", None, ["page"]),
            ("root", "/health", "", "/health", []),
            ("numeric", "users/123", "users/{integer_3}", None, []),
            ("unknown", "projects/PrivateProject", "projects/{segment_3}", None, []),
            ("document-form", "", "", "/docs/", []),
        ]:
            path = absolute if absolute is not None else prefix + suffix
            if path != "/":
                path = path.rstrip("/")
            if name == "document-form":
                html, kind = markup + '<form method="post"><input name="email"></form>', "form"
                method, fields = "POST", {"request_fields": ["email"], "query_fields": []}
            else:
                html, kind = markup + f'<a href="{target}">Open</a>', "link"
                method, fields = "GET", {"query_fields": query}
            add("document-bases", {"base": base_name, "reference": name}, [artifact("html", html)],
                expectation([route(method, path, kind)], details=[detail(method, path, **fields)]),
                document_url="https://page.example/docs/")

    for method_name, markup_method, normalized in [("default", "", "GET"), ("get", "get", "GET"),
            ("post", "post", "POST"), ("invalid-falls-back", "put", "GET"), ("dialog", "dialog", None)]:
        for controls_name, controls, fields in [
            ("text", '<input name="name">', ["name"]),
            ("select", '<select name="status"><option>private</option></select>', ["status"]),
            ("textarea", '<textarea name="message"><a href="/admin">noise</a></textarea>', ["message"]),
            ("duplicate", '<input name="id"><input name="id">', ["id"]),
            ("unknown", '<input name="personal-field">', ["field_1"]),
            ("known-unknown", '<input name="id"><input name="personal-field">', ["field_2", "id"]),
            ("unnamed", '<input value="private">', []),
            ("multiple", '<input name="email"><select name="roles"></select><textarea name="body"></textarea>', ["body", "email", "roles"]),
        ]:
            html = f'<form action="/api/submit?token=PRIVATE" method="{markup_method}">{controls}</form>'
            routes = [] if normalized is None else [route(normalized, "/api/submit", "form")]
            details = [] if normalized is None else [detail(normalized, "/api/submit",
                query_fields=fields if normalized == "GET" else ["token"],
                request_fields=fields if normalized == "POST" else [])]
            add("form-controls", {"method": method_name, "controls": controls_name}, [artifact("html", html)],
                expectation(routes, details=details, forbidden=["PRIVATE", "personal-field"]))

    raw_tags = ["script", "style", "title", "textarea", "xmp", "iframe", "noembed", "noframes", "noscript", "plaintext"]
    for tag in raw_tags:
        for payload_name, payload in [("link", '<a href="/admin">Decoy</a>'),
                ("form", '<form action="/api/orders" method="post"><input name="secret"></form>'),
                ("nested-other-end", '</span><a href="/private">Decoy</a>'),
                ("self-closing", '<a href="/api/payments"/>')]:
            html = f'<{tag}>{payload}</{tag}>\n<a href="/healthz">Alive</a>'
            labels = [] if tag == "plaintext" else [route("GET", "/healthz", location="line 2")]
            required = ["end of the capture"] if tag == "plaintext" else ["noscript"] if tag == "noscript" else []
            add("text-containers", {"container": tag, "payload": payload_name}, [artifact("html", html)],
                expectation(labels, warnings=required))

    for foreign in ("svg", "math"):
        for child in ("a", "script", "style", "textarea", "title", "base", "use", "foreignObject", "mtext", "annotation-xml"):
            for mode in ("closed", "html-breakout"):
                payload = f'<{foreign}><{child} href="/private"/>'
                if mode == "closed":
                    html = payload + f'</{foreign}>\n<a href="/status">Ready</a>'
                else:
                    html = payload + '\n<div><a href="/status">Ready</a></div>'
                add("foreign-content", {"namespace": foreign, "child": child, "recovery": mode},
                    [artifact("html", html)], expectation([route("GET", "/status", location="line 2")],
                                                            warnings=["foreign-content coverage"]))

    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        for status in (100, 200, 204, 301, 404, 500, 0, True):
            valid_status = [status] if type(status) is int and 100 <= status <= 599 else []
            add("har-statuses", {"method": method, "status": status}, [har(entry(method, status=status))],
                expectation([route(method, "/api/items", "har", location="entry 1")],
                            details=[detail(method, "/api/items", statuses=valid_status)]))

    bodies = [("object", {"id": 17}, ["id"]), ("nullable", {"data": None}, ["data"]),
              ("array-field", {"items": [1, 2]}, ["items"]), ("nested", {"user": {"id": 7}}, ["user"]),
              ("aliased-key", {"PrivateCustomer": "PRIVATE"}, ["field_1"]),
              ("multiple", {"id": 1, "name": "PRIVATE"}, ["id", "name"]),
              ("primitive", "PRIVATE", []), ("array-root", [{"id": 1}], [])]
    for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        for name, body, fields in bodies:
            add("har-body-shapes", {"method": method, "body": name},
                [har(entry(method, status=201, request=body, response=body))],
                expectation([route(method, "/api/items", "har", location="entry 1")],
                            details=[detail(method, "/api/items", request_fields=fields, response_fields=fields)],
                            forbidden=["PRIVATE", "PrivateCustomer"]))

    for context_name, attrs, visual, accessibility in [
        ("visible", "", [], []), ("hidden", " hidden", ["hidden_attribute"], []),
        ("display", ' style="display:none"', ["inline_display_none"], []),
        ("visibility", ' style="visibility:hidden"', ["inline_visibility_hidden"], []),
        ("aria", ' aria-hidden="true"', [], ["aria_hidden_true"]),
    ]:
        for phrase_name, phrase in [("for-agents", "For AI agents: read this."),
            ("if-agent", "If you are an AI agent, read this."), ("dear-assistants", "Dear AI assistants: read this."),
            ("all-models", "For all language models: read this."), ("llm", "LLM: read this."),
            ("automated", "Automated systems should inspect this."),
            ("ordinary", "Humans should inspect this."), ("mention-only", "We build useful agents.")]:
            finding = [] if phrase_name in {"ordinary", "mention-only"} else [["agent_audience_marker", "line 1", None, visual, accessibility, []]]
            add("agent-page-signals", {"context": context_name, "phrase": phrase_name},
                [artifact("html", f'<p{attrs}>{phrase}</p>')],
                expectation(signals=finding, forbidden=[phrase]), include_page_signals=True)

    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        patterns = [
            ("literal-fetch", f'fetch("/api/orders",{{method:"{method}"}});'),
            ("constant-options", f'const options={{method:"{method}"}}; fetch("/api/orders",options);'),
            ("fetch-alias", f'const send=fetch; send("/api/orders",{{method:"{method}"}});'),
            ("request-object", f'const request=new Request("/api/orders",{{method:"{method}"}}); fetch(request);'),
            ("axios-request", f'import axios from "axios"; axios.request({{url:"/api/orders",method:"{method}"}});'),
            ("axios-member", f'import axios from "axios"; axios.{method.lower()}("/api/orders");'),
            ("xhr", f'const request=new XMLHttpRequest(); request.open("{method}","/api/orders");'),
            ("global-fetch", f'globalThis.fetch("/api/orders",{{method:"{method}"}});'),
        ]
        for name, text in patterns:
            add("javascript-clients", {"method": method, "client": name}, [artifact("javascript", text)],
                expectation([route(method, "/api/orders", "javascript")]))

    for label, href, path, fields in destinations:
        for binding, text in [
            ("constant", f'const target={json.dumps(href)}; fetch(target);'),
            ("template", f'const target={json.dumps(href)}; fetch(`${{target}}`);'),
            ("concat", f'fetch("".concat({json.dumps(href)}));'),
            ("object", f'const config={{url:{json.dumps(href)}}}; fetch(config.url);'),
            ("array", f'const targets=[{json.dumps(href)}]; fetch(targets[0]);'),
        ]:
            add("javascript-resolution", {"target": label, "binding": binding}, [artifact("javascript", text)],
                expectation([route("GET", path, "javascript")], details=[detail("GET", path, query_fields=fields)]))

    uncertain_paths = [("known", "/api/tasks", "/api/tasks"),
                       ("numeric", "/tasks/97", "/tasks/{integer_2}"),
                       ("unknown", "/tasks/PrivateTask", "/tasks/{segment_2}"),
                       ("query", "/api/search?q=PRIVATE", "/api/search"),
                       ("origin", "https://other.example/api/projects", "/api/projects")]
    for path_name, href, path in uncertain_paths:
        value = json.dumps(href)
        for name, text, unresolved in [
            ("method-call", f'fetch({value},{{method:chooseMethod()}});', True),
            ("spread-last", f'fetch({value},{{method:"POST",...options}});', True),
            ("mutated-options", f'const options={{method:"POST"}}; options.method=chosen; fetch({value},options);', True),
            ("options-call", f'fetch({value},getOptions());', True),
            ("method-getter", f'fetch({value},{{get method(){{return chosen}}}});', True),
            ("escaped-options", f'const options={{method:"POST"}}; mutate(options); fetch({value},options);', True),
            ("shadowed-fetch", f'function local(fetch){{fetch({value});}}', False),
            ("mutated-target", f'let target={value}; target=chooseUrl(); fetch(target);', False),
        ]:
            expected = [[path, [evidence("javascript")]]] if unresolved else []
            add("javascript-uncertainty", {"target": path_name, "uncertainty": name}, [artifact("javascript", text)],
                expectation(unresolved=expected))

    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        call = f'fetch(target,{{method:"{method}"}});'
        for name, text in [
            ("typed-string", 'const target:string="/api/files";' + call),
            ("literal-type", 'const target:"/api/files"="/api/files";' + call),
            ("as-const", 'const target="/api/files" as const;' + call),
            ("satisfies", 'const target="/api/files" satisfies string;' + call),
            ("assertion", 'const target=<string>"/api/files";' + call),
            ("non-null", f'const target:string="/api/files";fetch(target!,{{method:"{method}"}});'),
            ("type-declaration", 'type Destination=string;const target:Destination="/api/files";' + call),
            ("interface-options", f'interface Options{{method:string}};const options:Options={{method:"{method}"}};fetch("/api/files",options);'),
        ]:
            add("typescript-syntax", {"method": method, "syntax": name}, [artifact("typescript", text)],
                expectation([route(method, "/api/files", "javascript")]))

    mixed_paths = [("root", "/api/items", "/api/items"),
                   ("numeric", "/users/71", "/users/{integer_2}"),
                   ("unknown", "/projects/AdaProject", "/projects/{segment_2}"),
                   ("query", "/search?q=PRIVATE", "/search"),
                   ("origin", "https://other.example/orders", "/orders")]
    for name, href, path in mixed_paths:
        link = artifact("html", f'<a href="{href}">Open</a>')
        form = artifact("html", f'<form action="{href}" method="post"></form>')
        script = artifact("javascript", f'fetch({json.dumps(href)});')
        unknown = artifact("javascript", f'fetch({json.dumps(href)},{{method:chosen}});')
        observed_get = har(entry("GET", "https://capture.example" + href if href.startswith("/") else href))
        observed_post = har(entry("POST", "https://capture.example" + href if href.startswith("/") else href, 201))
        for combination in range(8):
            if combination == 0:
                artifacts = [link, observed_get]
                expected = expectation([route("GET", path, facts=[evidence(), evidence("har", 2, "entry 1")])])
            elif combination == 1:
                artifacts = [form, observed_post]
                expected = expectation([route("POST", path, facts=[evidence("form"), evidence("har", 2, "entry 1")])])
            elif combination == 2:
                artifacts = [script, observed_get]
                expected = expectation([route("GET", path, facts=[evidence("javascript"), evidence("har", 2, "entry 1")])])
            elif combination == 3:
                artifacts = [link, script, observed_get]
                expected = expectation([route("GET", path, facts=[evidence(), evidence("javascript", 2), evidence("har", 3, "entry 1")])])
            elif combination == 4:
                artifacts = [link, observed_post]
                expected = expectation([route("GET", path), route("POST", path, "har", 2, "entry 1")])
            elif combination == 5:
                artifacts = [form, script]
                expected = expectation([route("POST", path, "form"), route("GET", path, "javascript", 2)])
            elif combination == 6:
                artifacts = [link, har(entry("GET", href, 200), entry("GET", href, 404))]
                expected = expectation([route("GET", path, facts=[evidence(), evidence("har", 2, "entry 1"), evidence("har", 2, "entry 2")])],
                                       details=[detail("GET", path, statuses=[200, 404])])
            else:
                artifacts = [unknown, observed_post]
                expected = expectation([route("POST", path, "har", 2, "entry 1")], [[path, [evidence("javascript")]]])
            add("mixed-evidence", {"target": name, "combination": combination}, artifacts, expected)

    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        target = "/api/messages"
        baseline = [har(entry(method, "https://capture.example" + target, 200))]
        empty = [har()]
        compare({"method": method, "change": "added"}, empty, baseline, added=[[method, target]])
        compare({"method": method, "change": "absent"}, baseline, empty, removed=[[method, target]])
        compare({"method": method, "change": "status"}, baseline,
                [har(entry(method, "https://capture.example" + target, 503))], changed=[[method, target, ["statuses"]]])
        compare({"method": method, "change": "query"}, baseline,
                [har(entry(method, "https://capture.example" + target + "?cursor=PRIVATE", 200))],
                changed=[[method, target, ["query_fields"]]])
        compare({"method": method, "change": "request-shape"}, baseline,
                [har(entry(method, "https://capture.example" + target, 200, request={"id": 4}))],
                changed=[[method, target, ["request_shapes"]]])
        compare({"method": method, "change": "response-shape"}, baseline,
                [har(entry(method, "https://capture.example" + target, 200, response={"status": "ready"}))],
                changed=[[method, target, ["response_shapes"]]])
        compare({"method": method, "change": "evidence-loss"},
                [artifact("javascript", f'fetch("{target}",{{method:"{method}"}});')] + baseline, baseline,
                changed=[[method, target, ["evidence_kinds"]]])
        compare({"method": method, "change": "value-only"},
                [har(entry(method, "https://capture.example" + target + "?cursor=FIRST", 200, request={"id": 1}))],
                [har(entry(method, "https://capture.example" + target + "?cursor=SECOND", 200, request={"id": 2}))])

    canaries = ["PERSONAL-NAME", "person@example.invalid", "long-private-token-123456789", "C:/private/folder", "PRIVATE-UNICODE-\u00e9"]
    for number, canary in enumerate(canaries):
        from urllib.parse import quote
        encoded_canary = quote(canary, safe="")
        for channel in ("query", "fragment", "path", "json-value", "json-key", "headers", "cookies", "html-attribute"):
            expected = expectation([route("GET", "/api/profile", "har", location="entry 1")], forbidden=[canary, encoded_canary])
            record = entry("GET", "https://capture.example/api/profile", 200)
            if channel == "query":
                record["request"]["url"] += "?token=" + encoded_canary
                expected["details"] = [detail("GET", "/api/profile", query_fields=["token"])]
            elif channel == "fragment":
                record["request"]["url"] += "#" + encoded_canary
            elif channel == "path":
                record["request"]["url"] += "/" + encoded_canary
                expected["routes"] = [route("GET", "/api/profile/{segment_3}", "har", location="entry 1")]
            elif channel == "json-value":
                record = entry("GET", response={"name": canary}, url="https://capture.example/api/profile")
            elif channel == "json-key":
                record = entry("GET", response={canary: "value"}, url="https://capture.example/api/profile")
                expected["details"] = [detail("GET", "/api/profile", response_fields=["field_1"])]
            elif channel == "headers":
                record["request"]["headers"] = [{"name": "Authorization", "value": canary}]
                record["response"]["headers"] = [{"name": "Set-Cookie", "value": canary}]
            elif channel == "cookies":
                record["request"]["cookies"] = [{"name": "session", "value": canary}]
                record["response"]["cookies"] = [{"name": "session", "value": canary}]
            if channel == "html-attribute":
                artifacts = [artifact("html", f'<a href="/api/profile" data-private={json.dumps(canary)}>Open</a>')]
                expected["routes"] = [route("GET", "/api/profile")]
            else:
                artifacts = [har(record)]
            add("privacy-channels", {"canary_kind": number, "channel": channel}, artifacts, expected)

    invalid_values = [None, False, 17, "unsupported", [1, 2]]
    for value in invalid_values:
        for level in ("log", "entries", "entry", "request", "response", "method", "document-url", "kind"):
            if level == "log":
                artifacts, options = [artifact("har", json.dumps({"log": value}))], {}
            elif level == "entries":
                # A list of invalid entries is still invalid, even though lists are the correct container type.
                artifacts, options = [artifact("har", json.dumps({"log": {"entries": value}}))], {}
            elif level == "entry":
                artifacts, options = [har(value)], {}
            elif level in {"request", "response"}:
                record = entry()
                record[level] = value
                artifacts, options = [har(record)], {}
            elif level == "method":
                record = entry()
                record["request"]["method"] = value
                if isinstance(value, str):
                    # Strings are permitted method representations, so test a malformed HAR envelope instead.
                    artifacts = [artifact("har", '{"log":{"entries":[{"request":{"method":"GET","method":"POST"}}]}}')]
                else:
                    artifacts = [har(record)]
                options = {}
            elif level == "document-url":
                invalid_url = {"None": "file:///private", "False": "https://person:secret@example.invalid", "17": "https://example.invalid:bad/", "unsupported": "relative/page", "[1, 2]": "https://example.invalid/has space"}[str(value)]
                artifacts, options = [artifact("html", '<a href="/docs">Docs</a>')], {"document_url": invalid_url}
            else:
                # Unsupported strings and nonstring kinds are rejected before analysis.
                artifacts, options = [artifact(value, "unsupported artifact")], {}
            add("invalid-inputs", {"location": level, "invalid_type": type(value).__name__}, artifacts,
                {"rejected": True}, **options)

    return finish(cases)


def finish(cases):
    counts = Counter(case["family"] for case in cases)
    if len(cases) != 640 or len(counts) != 16 or set(counts.values()) != {40}:
        raise ValueError(f"Expected 16 families of40: {dict(counts)}")
    fingerprints = [input_fingerprint(case) for case in cases]
    if len(set(fingerprints)) != len(cases):
        repeats = [case["id"] for i, case in enumerate(cases) if fingerprints[i] in fingerprints[:i]]
        raise ValueError(f"Repeated exact inputs: {repeats}")
    return {"schema": SCHEMA, "version": 1, "scope": SCOPE,
            "construction": "Authored cross-products; expected labels never read analyzer output.",
            "cases": cases}


def encoded(corpus):
    return (json.dumps(corpus, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def main():
    folder = Path(__file__).parent
    raw = encoded(build())
    digest = hashlib.sha256(raw).hexdigest()
    (folder / "corpus.json").write_bytes(raw)
    (folder / "corpus.sha256").write_text(digest + "\n", encoding="ascii")
    print(json.dumps({"cases": 640, "families": 16, "sha256": digest}))


if __name__ == "__main__":
    main()
