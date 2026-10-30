from __future__ import annotations

import base64
import binascii
import json
import math
import re
import time
from html.entities import html5
from html.parser import HTMLParser
import html.parser as html_parser
from urllib.parse import parse_qsl, unquote, urlsplit

MAX_ARTIFACTS = 6
MAX_FILE_BYTES = 512 * 1024
MAX_HTML_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_HTML_TAGS = 10000
HTML_WORK_SECONDS = 3.0
MAX_ROUTES = 400
MAX_JS_TOKENS = 300000
JS_WORK_SECONDS = 3.0
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
SEGMENTS = set("api v1 v2 v3 public private users user accounts account auth login logout register session sessions profile settings dashboard health healthz status orders order customers customer invoices invoice products product inventory items item cart checkout payments payment reports report search files file upload download events event notifications messages message contact submit forms form assets static js css images index.html app.js main.js bundle.js app.css main.css index.js favicon.ico favicon.svg robots.txt admin list details create update delete feed tags categories comments posts post projects project tasks task export import docs openapi.json graphql".split())
FIELDS = set("id name email password token authorization api_key secret access_token refresh_token session cookie customer_id invoice_id order_id items total amount currency status created_at updated_at page limit offset search q query sort filter count username enabled success error message description sku quantity price url path method version phone address first_name last_name data result results type key value timestamp code active title content next previous roles role permissions fields format include exclude size file files subject body user customer order invoice product metadata cursor locale language expires csrf csrf_token".split())


class ArtifactError(ValueError):
    pass


def _needs_html_attribute_guard():
    class Probe(HTMLParser):
        value = None
        def handle_starttag(self, tag, attrs):
            self.value = dict(attrs).get("href")
    parser = Probe()
    parser.feed('<a href="?x=1&notx=1&reg=2">')
    parser.close()
    return parser.value != "?x=1&notx=1&reg=2"


_HTML_ATTRIBUTE_GUARD = _needs_html_attribute_guard()
_AMBIGUOUS_ATTRIBUTE_REF = re.compile(r"&(?:" + "|".join(re.escape(name) for name in
    sorted((name for name in html5 if not name.endswith(";")), key=len, reverse=True)) + r")(?=[=A-Za-z0-9])")
_NAMED_REF = re.compile(r"&[A-Za-z][A-Za-z0-9]*;?")
_ATTRIBUTE_GUIDANCE = "This Python HTML parser may change a URL attribute. Update Python or encode attribute ampersands as &amp;."


def _ambiguous_attribute_reference(value):
    return any(not (ref.group().endswith(";") and ref.group()[1:] in html5)
               and _AMBIGUOUS_ATTRIBUTE_REF.match(value, ref.start()) for ref in _NAMED_REF.finditer(value))


def _guard_html_url(raw, attribute, decoded=None):
    if not _HTML_ATTRIBUTE_GUARD or not raw or "&" not in raw:
        return
    if decoded is not None and (page_relative(decoded) or safe_path(decoded) is None):
        return
    # Match the same raw attribute boundaries as this interpreter. An unknown
    # tokenizer cannot safely distinguish a risky URL from an unrelated value.
    tag_pattern = getattr(html_parser, "tagfind_tolerant", None)
    attr_pattern = getattr(html_parser, "attrfind_tolerant", None)
    start = tag_pattern.match(raw, 1) if callable(getattr(tag_pattern, "match", None)) else None
    if not start or not callable(getattr(attr_pattern, "match", None)) or getattr(attr_pattern, "groups", 0) < 3:
        if _ambiguous_attribute_reference(raw):
            raise ArtifactError(_ATTRIBUTE_GUIDANCE)
        return
    position = start.end()
    while match := attr_pattern.match(raw, position):
        if match.end() <= position:
            raise ArtifactError(_ATTRIBUTE_GUIDANCE)
        name = match.group(1)
        if not isinstance(name, str):
            break
        if name.lower() == attribute:
            # HTML uses the first duplicate attribute, just as handle_starttag.
            if _ambiguous_attribute_reference(match.group(3) or ""):
                raise ArtifactError(_ATTRIBUTE_GUIDANCE)
            return
        position = match.end()
    if _ambiguous_attribute_reference(raw):
        raise ArtifactError(_ATTRIBUTE_GUIDANCE)


def page_relative(raw):
    return isinstance(raw, str) and not raw.startswith("/") and re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", raw) is None


_ENVELOPE_TOKEN = re.compile(r'["{}\[\],:]|[^\s"{}\[\],:]+')


def _envelope_guard(text, depth, nodes):
    position, level, tokens = 0, 0, 0
    while match := _ENVELOPE_TOKEN.search(text, position):
        tokens += 1
        if tokens > 4 * nodes + 16:
            raise ArtifactError("JSON structure exceeds the supported limit")
        token, position = text[match.start()], match.end()
        if token == '"':
            try:
                decoded, position = json.decoder.scanstring(text, position, True)
                del decoded
            except (ValueError, UnicodeError):
                raise ArtifactError("Invalid or unsupported JSON document") from None
        elif token in ("[", "{"):
            level += 1
            if level > depth:
                raise ArtifactError("JSON nesting exceeds the supported limit")
        elif token in ("]", "}"):
            level -= 1


def strict_json(text: str, *, depth=20, nodes=20000, prescan=True):
    # Large HTTP strings are skipped by the C scanner; wide containers stop
    # before json.loads can allocate millions of objects from a small body.
    if not prescan:
        _envelope_guard(text, depth, nodes)
    level, quoted, escaped = 0, False, False
    for character in text if prescan else ():
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            level += 1
            if level > depth:
                raise ArtifactError("JSON nesting exceeds the supported limit")
        elif character in "]}":
            level -= 1

    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ArtifactError("Duplicate JSON keys are unsupported")
            result[key] = value
        return result

    def constant(_):
        raise ArtifactError("Nonfinite JSON values are unsupported")

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError, OverflowError):
        raise ArtifactError("Invalid or unsupported JSON document") from None
    pending, seen = [(value, 0)], 0
    while pending:
        entry, current = pending.pop()
        seen += 1
        if seen > nodes or current > depth:
            raise ArtifactError("JSON structure exceeds the supported limit")
        if isinstance(entry, dict):
            if len(entry) > 256:
                raise ArtifactError("JSON object has too many fields")
            pending.extend((item, current + 1) for item in entry.values())
        elif isinstance(entry, list):
            if len(entry) > 1000:
                raise ArtifactError("JSON array has too many entries")
            pending.extend((item, current + 1) for item in entry)
        elif isinstance(entry, float) and not math.isfinite(entry):
            raise ArtifactError("Nonfinite JSON values are unsupported")
    return value


def safe_field(raw, index, used=None):
    candidate = raw.lower() if isinstance(raw, str) else ""
    name = candidate if candidate in FIELDS else f"field_{index}"
    if used is not None:
        while name in used:
            name += "_2"
        used.add(name)
    return name


def safe_path(raw):
    if not isinstance(raw, str) or not raw or len(raw) > 4096 or "\\" in raw or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        return None
    try:
        parts = urlsplit(raw)
        if parts.scheme and parts.scheme.lower() not in {"http", "https"}:
            return None
        chunks = [unquote(part, errors="replace") for part in parts.path.split("/")]
        if sum(map(len, chunks)) + len(chunks) - 1 > 2048:
            return None
        chunks = [part for part in chunks if part]
        if len(chunks) > 24 or any(part in {".", ".."} for part in chunks):
            return None
        result = []
        for index, chunk in enumerate(chunks, 1):
            if chunk in SEGMENTS:
                result.append(chunk)
            elif chunk.isascii() and chunk.isdecimal():
                result.append(f"{{integer_{index}}}")
            else:
                result.append(f"{{segment_{index}}}")
        query = parse_qsl(parts.query, keep_blank_values=True, max_num_fields=64)
        used = set()
        names = sorted({safe_field(key, i, used) for i, (key, _) in enumerate(query, 1)})
        return "/" + "/".join(result), names
    except (ValueError, UnicodeError):
        return None


def shape(value, depth=0):
    if depth >= 5:
        return {"x-surface-map-truncated": True}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, dict):
        used = set()
        properties = {safe_field(key, i, used): shape(item, depth + 1) for i, (key, item) in enumerate(list(value.items())[:32], 1)}
        result = {"type": "object", "properties": properties}
        if len(value) > 32:
            result["x-surface-map-truncated"] = True
        return result
    if isinstance(value, list):
        variants = {}
        for item in value[:8]:
            model = shape(item, depth + 1)
            variants[json.dumps(model, sort_keys=True)] = model
        schemas = list(variants.values())
        result = {"type": "array", "items": schemas[0] if len(schemas) == 1 else {"anyOf": schemas} if schemas else {}}
        if len(value) > 8:
            result["x-surface-map-sampled"] = True
        return result
    return {}


def json_body(container):
    if not isinstance(container, dict):
        return None
    mime, text = container.get("mimeType", ""), container.get("text")
    if not isinstance(mime, str) or not isinstance(text, str) or container.get("encoding"):
        return None
    try:
        if len(text.encode("utf-8")) > 65536:
            return None
    except UnicodeError:
        return None
    if mime.split(";", 1)[0].strip().lower() not in {"application/json", "application/problem+json"}:
        return None
    try:
        return shape(strict_json(text, depth=12, nodes=4000))
    except ArtifactError:
        return None


class Builder:
    def __init__(self, javascript_engine="auto"):
        self.routes, self.assets, self.sources, self.warnings = {}, {}, [], []
        self.unresolved, self.javascript = {}, []
        self.javascript_engine = javascript_engine

    def add(self, raw, method, kind, source, location, *, status=None, request=None, response=None, fields=None):
        if kind != "har" and page_relative(raw):
            if not raw.startswith("#"):
                self.warn(source, "A relative URL was omitted because the page URL is unknown")
            return
        sanitized = safe_path(raw)
        if sanitized is None or not isinstance(method, str) or method not in METHODS:
            self.warn(source, "An unsupported URL or request method was omitted")
            return
        path, query_fields = sanitized
        if fields is not None and method == "GET":
            query_fields, fields = sorted(fields), None
        key = (method, path)
        if key not in self.routes:
            if len(self.routes) + len(self.unresolved) >= MAX_ROUTES:
                raise ArtifactError(f"Capture exceeds the {MAX_ROUTES}-route limit; narrow the capture scope")
            self.routes[key] = {"method": method, "path": path, "kinds": [], "statuses": [], "query_fields": [], "request_shapes": [], "response_shapes": [], "evidence": []}
        route = self.routes[key]
        for field, values in [("kinds", [kind]), ("query_fields", query_fields), ("statuses", [status] if status is not None else []), ("request_shapes", [request] if request else []), ("response_shapes", [response] if response else [])]:
            for value in values:
                if value not in route[field]:
                    if len(route[field]) < 64:
                        route[field].append(value)
        if fields:
            model = {"type": "object", "properties": fields}
            if model not in route["request_shapes"] and len(route["request_shapes"]) < 64:
                route["request_shapes"].append(model)
        evidence = {"source": source, "location": location, "kind": kind}
        if evidence not in route["evidence"] and len(route["evidence"]) < 32:
            route["evidence"].append(evidence)

    def unresolved_operation(self, raw, source, location):
        if page_relative(raw):
            self.warn(source, "A relative URL was omitted because the page URL is unknown")
            return
        sanitized = safe_path(raw)
        if sanitized is None:
            return
        path, fields = sanitized
        if path not in self.unresolved:
            if len(self.routes) + len(self.unresolved) >= MAX_ROUTES:
                raise ArtifactError(f"Capture exceeds the {MAX_ROUTES}-route limit; narrow the capture scope")
            self.unresolved[path] = {"method": None, "path": path, "confidence": "inferred", "query_fields": [], "evidence": []}
        target = self.unresolved[path]
        for name in fields:
            if name not in target["query_fields"] and len(target["query_fields"]) < 64:
                target["query_fields"].append(name)
        evidence = {"source": source, "location": location, "kind": "javascript"}
        if evidence not in target["evidence"] and len(target["evidence"]) < 32:
            target["evidence"].append(evidence)

    def asset(self, raw, kind, source, location):
        if page_relative(raw):
            self.warn(source, "A relative URL was omitted because the page URL is unknown")
            return
        sanitized = safe_path(raw)
        if sanitized is None:
            return
        path, _ = sanitized
        key = (kind, path)
        if len(self.assets) >= 200 and key not in self.assets:
            raise ArtifactError("Capture exceeds the 200-asset limit; narrow the capture scope")
        item = self.assets.setdefault(key, {"path": path, "kind": kind, "evidence": []})
        if len(item["evidence"]) < 16:
            item["evidence"].append({"source": source, "location": location})

    def warn(self, source, reason):
        item = {"source": source, "reason": reason}
        if item not in self.warnings and len(self.warnings) < 32:
            self.warnings.append(item)

    def finish(self):
        routes = []
        for index, (_, route) in enumerate(sorted(self.routes.items()), 1):
            for key in ("kinds", "statuses", "query_fields"):
                route[key].sort()
            route["id"] = f"route_{index}"
            route["confidence"] = "observed" if "har" in route["kinds"] else "declared" if "form" in route["kinds"] or "link" in route["kinds"] else "inferred"
            routes.append(route)
        report = {"version": 1, "sources": self.sources, "routes": routes, "assets": sorted(self.assets.values(), key=lambda item: (item["kind"], item["path"])), "warnings": self.warnings, "privacy": {"mode": "conservative", "origins": "discarded", "filenames": "discarded", "headers_cookies_values": "discarded", "unknown_names": "aliased", "scope": "Normalized paths may merge unrelated origins or identifiers. Evidence is incomplete; no route was requested or verified by this tool."}}
        if self.javascript:
            report["javascript_analysis"] = self.javascript
        if self.unresolved:
            report["unresolved_operations"] = []
            for index, (_, target) in enumerate(sorted(self.unresolved.items()), 1):
                target["id"] = f"target_{index}"
                target["query_fields"].sort()
                report["unresolved_operations"].append(target)
        return report


def parse_har(text, source, builder):
    document = strict_json(text)
    entries = document.get("log", {}).get("entries") if isinstance(document, dict) and isinstance(document.get("log"), dict) else None
    if not isinstance(entries, list) or len(entries) > 300:
        raise ArtifactError("HAR must contain at most 300 log.entries")
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or not isinstance(entry.get("request"), dict):
            raise ArtifactError("HAR entry has no supported request object")
        request = entry["request"]
        response = entry.get("response", {})
        if not isinstance(response, dict):
            raise ArtifactError("HAR response must be an object")
        method, url = request.get("method"), request.get("url")
        if not isinstance(method, str):
            raise ArtifactError("HAR request method must be a string")
        status = response.get("status")
        if type(status) is not int or not 100 <= status <= 599:
            status = None
        builder.add(url, method, "har", source, f"entry {index}", status=status, request=json_body(request.get("postData")), response=json_body(response.get("content")))


def _js_tokens(text):
    # Both views share one conservative lexer so regex/JSX handling cannot drift.
    from .source import _Rejected, _ts_tokens

    deadline = time.monotonic() + JS_WORK_SECONDS

    class Budget:
        def tick(self):
            if time.monotonic() > deadline:
                raise ArtifactError("JavaScript analysis exceeded its work deadline")

    budget = Budget()
    try:
        tokens = _ts_tokens(text, budget, max_tokens=MAX_JS_TOKENS)
    except _Rejected:
        raise ArtifactError("JavaScript token limit exceeded") from None
    budget.tick()
    kinds = {"ident": "identifier", "punct": "punctuation", "escaped": "escaped_string"}
    return [(kinds.get(kind, kind), value, line, claimable) for kind, value, line, claimable in tokens]


def js_tokens(text):
    return [token[:3] for token in _js_tokens(text)]


def literal_constants(tokens):
    """Module-level `const NAME = "literal"` declarations whose name has no other binding or assignment site.

    Only top-level declarations of a single unescaped string are kept. Any `with` statement or direct `eval`
    disables resolution, because either can redirect a name lexically, and unbalanced brackets do too.
    """
    from .source import _Rejected, _binding_counts

    if any(not token[3] or token[:2] in {("punctuation", "@"), ("punctuation", "\\")} or
           token[0] == "identifier" and token[1] in {"private", "protected", "public", "readonly", "override"} for token in tokens):
        return {}
    if any(left[:2] == ("punctuation", "=") and right[:2] == ("punctuation", ">") for left, right in zip(tokens, tokens[1:])):
        return {}
    if any(token[0] == "identifier" and token[1] in ("with", "eval") and not (index and tokens[index - 1][1] == ".") for index, token in enumerate(tokens)):
        return {}
    candidates, depth, count = {}, 0, len(tokens)
    for index, token in enumerate(tokens):
        if token[0] == "punctuation" and token[1] in "([{":
            depth += 1
        elif token[0] == "punctuation" and token[1] in ")]}":
            depth -= 1
            if depth < 0:
                return {}
        elif depth == 0 and token[:2] == ("identifier", "const") and not (index and tokens[index - 1][1] == ".") and index + 3 < count:
            name, equals, literal = tokens[index + 1:index + 4]
            after = tokens[index + 4] if index + 4 < count else None
            ends = after is None or after[:2] in {("punctuation", ";"), ("punctuation", ",")} or after[2] > literal[2] and after[0] == "identifier" and after[1] not in ("in", "instanceof", "as", "satisfies")
            if name[0] == "identifier" and equals[:2] == ("punctuation", "=") and literal[0] == "string" and ends and all(item[3] for item in tokens[index:index + 4]):
                candidates[name[1]] = None if name[1] in candidates else literal[1]
    if depth or not candidates:
        return {}
    try:
        sites = _binding_counts(tokens, tuple(candidates))
    except _Rejected:
        raise ArtifactError("JavaScript binding analysis exceeded its work limit") from None
    return {name: literal for name, literal in candidates.items() if literal is not None and sites[name] == 1}


def parse_js_basic(text, source, builder):
    from .source import _Rejected, _binding_counts

    tokens = _js_tokens(text)
    try:
        fetch_bound = _binding_counts(tokens, ("fetch",))["fetch"]
    except _Rejected:
        raise ArtifactError("JavaScript binding analysis exceeded its work limit") from None
    if fetch_bound:
        builder.warn(source, "A local fetch binding was found; fetch hints were omitted")
        return
    constants = literal_constants(tokens)
    direct, depth = set(), 0
    for index, token in enumerate(tokens):
        if token[0] == "punctuation" and token[1] in "([{":
            depth += 1
        elif token[0] == "punctuation" and token[1] in ")]}":
            depth -= 1
        elif depth == 0 and token[:2] == ("identifier", "fetch"):
            direct.add(index)
    for index, token in enumerate(tokens):
        if token[:2] != ("identifier", "fetch") or (index and tokens[index - 1][1] == "."):
            continue
        if not token[3]:
            builder.warn(source, "Ambiguous JavaScript syntax was omitted")
            continue
        upcoming = tokens[index + 1:index + 5]
        target = upcoming[1] if len(upcoming) > 1 else ("", "", 0, False)
        url = target[1] if target[0] == "string" else constants.get(target[1]) if target[0] == "identifier" and index in direct else None
        if len(upcoming) < 3 or upcoming[0][1] != "(" or url is None or upcoming[2][1] not in {",", ")"}:
            builder.warn(source, "A dynamic fetch expression was omitted")
            continue
        if not all(item[3] for item in upcoming[:3]):
            builder.warn(source, "Ambiguous JavaScript syntax was omitted")
            continue
        method = "GET"
        if upcoming[2][1] == ",":
            tail = tokens[index + 4:index + 84]
            if not tail or tail[0][1] != "{":
                builder.warn(source, "Fetch options were dynamic; method was not inferred")
                continue
            level, closed, methods, dynamic = 0, None, [], False
            for offset in range(len(tail)):
                value = tail[offset][1]
                previous = tail[offset - 1][1] if offset else None
                if level == 1:
                    if (value == "[" and previous in {"{", ",", "get", "set", "async"}) or tail[offset][0] == "escaped_string" or value == "\\":
                        dynamic = True
                    if value == "__proto__" and previous in {"{", ","}:
                        dynamic = True
                    if value == "method" and previous in {"{", ",", "get", "set", "async"} and (offset + 1 >= len(tail) or tail[offset + 1][1] != ":"):
                        dynamic = True
                if tail[offset][0] == "punctuation" and value in "{[":
                    level += 1
                elif tail[offset][0] == "punctuation" and value in "}]":
                    level -= 1
                    if level == 0:
                        closed = offset
                        break
                elif level == 1 and value == "." and previous == ".":
                    dynamic = True
                elif level == 1 and value == "method" and offset + 2 < len(tail) and tail[offset + 1][1] == ":":
                    literal = tail[offset + 2]
                    if literal[0] != "string" or offset + 3 >= len(tail) or tail[offset + 3][1] not in {",", "}"}:
                        dynamic = True
                    else:
                        methods.append(literal[1].upper())
            if closed is None or closed + 1 >= len(tail) or tail[closed + 1][1] != ")" or dynamic or len(methods) > 1 or not all(item[3] for item in tail[:closed + 2]):
                builder.warn(source, "Fetch options were dynamic or incomplete; method was not inferred")
                continue
            if methods:
                method = methods[0]
        builder.add(url, method, "javascript", source, f"line {token[2]}")


def parse_js(text, source, builder, *, kind="javascript"):
    from .js_analysis import AnalysisLimit, inspect_ast
    from .js_parser import JsParserError, available, parse

    mode = builder.javascript_engine
    if mode == "basic":
        builder.javascript.append({"source": source, "engine": "basic"})
        return parse_js_basic(text, source, builder)
    ready, reason = available()
    if not ready:
        if mode == "enhanced":
            raise ArtifactError("Enhanced JavaScript analysis is unavailable. Run setup-js with a supported Node installation.")
        builder.javascript.append({"source": source, "engine": "basic", "reason": reason})
        builder.warn(source, "Basic JavaScript hints only. Run setup-js to enable syntax and scope analysis.")
        return parse_js_basic(text, source, builder)
    try:
        result = inspect_ast(parse(text, kind))
    except JsParserError as error:
        raise ArtifactError("Enhanced JavaScript analysis rejected the input: " + error.code) from None
    except AnalysisLimit:
        raise ArtifactError("JavaScript interpretation exceeded its work limit") from None
    builder.javascript.append({"source": source, "engine": "enhanced", "parser": "babel-8.0.4"})
    for call in result["calls"]:
        if call["method"] is None:
            builder.unresolved_operation(call["url"], source, f"line {call['line']}")
        else:
            body = shape(call["body"]) if "body" in call else None
            builder.add(call["url"], call["method"], "javascript", source, f"line {call['line']}", request=body)
    if result["omitted"]:
        builder.warn(source, "Some dynamic JavaScript targets could not be resolved safely.")


class HTML(HTMLParser):
    def __init__(self, source, builder, deadline=None):
        super().__init__(convert_charrefs=True)
        self.source, self.builder, self.form = source, builder, None
        self.tags = 0
        self.deadline = time.monotonic() + HTML_WORK_SECONDS
        if deadline is not None:
            self.deadline = min(self.deadline, deadline)
        self.inline_warning = False

    def _check_time(self):
        if time.monotonic() > self.deadline:
            raise ArtifactError("HTML analysis exceeded its work deadline")

    def handle_starttag(self, tag, attributes):
        self._check_time()
        self.tags += 1
        if self.tags > MAX_HTML_TAGS:
            raise ArtifactError(f"HTML exceeds the {MAX_HTML_TAGS:,}-tag limit; use a smaller page capture")
        attrs = {}
        for key, value in attributes:
            attrs.setdefault(key, value.strip("\t\n\f\r ") if key in {"href", "src", "action"} and value else value)
        location = f"line {self.getpos()[0]}"
        if tag == "script" and not attrs.get("src") and not self.inline_warning:
            self.inline_warning = True
            self.builder.warn(self.source, "HTML analysis reads links, forms and asset references. Inline scripts and embedded JSON are not analyzed; add JavaScript or HAR evidence for those.")
        if tag == "a" and attrs.get("href"):
            _guard_html_url(self.get_starttag_text(), "href", attrs["href"])
            self.builder.add(attrs["href"], "GET", "link", self.source, location)
        elif tag == "script" and attrs.get("src"):
            self.builder.asset(attrs["src"], "script", self.source, location)
        elif tag == "link" and attrs.get("href"):
            self.builder.asset(attrs["href"], "stylesheet" if "stylesheet" in (attrs.get("rel") or "").lower().split() else "link", self.source, location)
        elif tag in {"iframe", "embed"}:
            if attrs.get("src"):
                _guard_html_url(self.get_starttag_text(), "src", attrs["src"])
                self.builder.asset(attrs["src"], "embedded-document", self.source, location)
            self.builder.warn(self.source, "Embedded document content is not inspected; add a separate authorized capture to analyze it.")
        elif tag == "form":
            self._finish_form()
            method = (attrs.get("method") or "").lower()
            if not attrs.get("action"):
                self.builder.warn(self.source, "A form without an action was omitted because its page URL is unknown")
            elif method != "dialog":
                _guard_html_url(self.get_starttag_text(), "action", attrs["action"])
                self.form = {"url": attrs["action"], "method": "POST" if method == "post" else "GET", "fields": {}, "names": set(), "location": location}
        elif tag in {"input", "textarea", "select"} and self.form is not None and attrs.get("name"):
            fields, names = self.form["fields"], self.form["names"]
            if len(fields) >= 32 or attrs["name"] in names:
                return
            names.add(attrs["name"])
            fields[safe_field(attrs["name"], len(fields) + 1, set(fields))] = {"type": "string"}

    def _finish_form(self):
        if self.form:
            form, self.form = self.form, None
            self.builder.add(form["url"], form["method"], "form", self.source, form["location"], fields=form["fields"])

    def handle_endtag(self, tag):
        self._check_time()
        if tag == "form":
            self._finish_form()

    def handle_data(self, data):
        self._check_time()

    def handle_comment(self, data):
        self._check_time()


def analyze(artifacts, *, javascript_engine="auto", deadline=None):
    def check_deadline():
        if deadline is not None and time.monotonic() >= deadline:
            raise ArtifactError("Analysis exceeded the shared request deadline; use fewer artifacts")

    check_deadline()
    if type(javascript_engine) is not str or javascript_engine not in {"auto", "basic", "enhanced"}:
        raise ArtifactError("Choose auto, basic or enhanced JavaScript analysis")
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= MAX_ARTIFACTS:
        raise ArtifactError("Choose 1 to 6 HTML, HAR or JavaScript artifacts")
    builder, total = Builder(javascript_engine), 0
    for index, artifact in enumerate(artifacts, 1):
        check_deadline()
        source = f"source_{index}"
        if (not isinstance(artifact, dict) or set(artifact) - {"kind", "text", "text_b64"}
                or not isinstance(artifact.get("kind"), str)
                or artifact.get("kind") not in {"har", "html", "javascript", "typescript"}
                or ("text" in artifact) == ("text_b64" in artifact)):
            raise ArtifactError(f"{source}: unsupported artifact format")
        kind = artifact["kind"]
        limit = MAX_HTML_BYTES if kind == "html" else MAX_FILE_BYTES
        if "text_b64" in artifact:
            encoded = artifact["text_b64"]
            if not isinstance(encoded, str):
                raise ArtifactError(f"{source}: expected base64-encoded UTF-8 text")
            if len(encoded) > 4 * ((limit + 2) // 3):
                raise ArtifactError(f"{source}: encoded {kind.upper()} exceeds its {limit:,}-byte decoded limit")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError, UnicodeError):
                raise ArtifactError(f"{source}: expected strict base64 containing valid UTF-8 text") from None
            if len(raw) > limit:
                raise ArtifactError(f"{source}: {kind.upper()} exceeds its {limit:,}-byte decoded limit")
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                raise ArtifactError(f"{source}: expected strict base64 containing valid UTF-8 text") from None
        else:
            text = artifact["text"]
            if not isinstance(text, str):
                raise ArtifactError(f"{source}: expected UTF-8 text")
        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            raise ArtifactError(f"{source}: invalid Unicode text") from None
        total += size
        if size > limit:
            raise ArtifactError(f"{source}: {kind.upper()} input is {size:,} UTF-8 bytes; the limit is {limit:,} bytes. Use a smaller artifact.")
        if total > MAX_TOTAL_BYTES:
            raise ArtifactError(f"Capture contains {total:,} UTF-8 bytes; the combined limit is {MAX_TOTAL_BYTES:,} bytes. Analyze fewer artifacts at a time.")
        builder.sources.append({"id": source, "kind": kind, "bytes": size})
        try:
            if kind == "har":
                parse_har(text, source, builder)
            elif kind in {"javascript", "typescript"}:
                parse_js(text, source, builder, kind=kind)
            else:
                parser = HTML(source, builder, deadline=deadline)
                try:
                    parser.feed(text)
                    parser.close()
                except AssertionError:
                    raise ArtifactError("Malformed or unsupported HTML markup") from None
                parser._check_time()
                parser._finish_form()
            check_deadline()
        except ArtifactError as error:
            raise ArtifactError(f"{source}: {error}") from None
        except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
            raise ArtifactError(f"{source}: malformed or unsupported artifact") from None
    return builder.finish()
