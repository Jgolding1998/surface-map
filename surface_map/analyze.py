from __future__ import annotations

import json
import math
import time
from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urlsplit

MAX_ARTIFACTS = 6
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_ROUTES = 400
MAX_JS_TOKENS = 300000
JS_WORK_SECONDS = 3.0
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
SEGMENTS = set("api v1 v2 v3 public private users user accounts account auth login logout register session sessions profile settings dashboard health healthz status orders order customers customer invoices invoice products product inventory items item cart checkout payments payment reports report search files file upload download events event notifications messages message contact submit forms form assets static js css images index.html app.js main.js bundle.js app.css main.css index.js favicon.ico favicon.svg robots.txt admin list details create update delete feed tags categories comments posts post projects project tasks task export import docs openapi.json graphql".split())
FIELDS = set("id name email password token authorization api_key secret access_token refresh_token session cookie customer_id invoice_id order_id items total amount currency status created_at updated_at page limit offset search q query sort filter count username enabled success error message description sku quantity price url path method version phone address first_name last_name data result results type key value timestamp code active title content next previous roles role permissions fields format include exclude size file files subject body user customer order invoice product metadata cursor locale language expires csrf csrf_token".split())


class ArtifactError(ValueError):
    pass


def strict_json(text: str, *, depth=20, nodes=20000):
    level, quoted, escaped = 0, False, False
    for character in text:
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
    if not isinstance(raw, str) or not raw or len(raw) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        return None
    try:
        parts = urlsplit(raw)
        if parts.scheme and parts.scheme.lower() not in {"http", "https"}:
            return None
        decoded = unquote(parts.path, errors="replace")
        if len(decoded) > 2048:
            return None
        chunks = [part for part in decoded.split("/") if part]
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
    def __init__(self):
        self.routes, self.assets, self.sources, self.warnings = {}, {}, [], []

    def add(self, raw, method, kind, source, location, *, status=None, request=None, response=None, fields=None):
        sanitized = safe_path(raw)
        if sanitized is None or not isinstance(method, str) or method not in METHODS:
            self.warn(source, "An unsupported URL or request method was omitted")
            return
        path, query_fields = sanitized
        key = (method, path)
        if key not in self.routes:
            if len(self.routes) >= MAX_ROUTES:
                raise ArtifactError("Too many normalized routes")
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

    def asset(self, raw, kind, source, location):
        sanitized = safe_path(raw)
        if sanitized is None:
            return
        path, _ = sanitized
        key = (kind, path)
        if len(self.assets) >= 200 and key not in self.assets:
            raise ArtifactError("Too many normalized assets")
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
        return {"version": 1, "sources": self.sources, "routes": routes, "assets": sorted(self.assets.values(), key=lambda item: (item["kind"], item["path"])), "warnings": self.warnings, "privacy": {"mode": "conservative", "origins": "discarded", "filenames": "discarded", "headers_cookies_values": "discarded", "unknown_names": "aliased", "scope": "Normalized paths may merge unrelated origins or identifiers. Evidence is incomplete; no route was requested or verified by this tool."}}


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


def js_tokens(text):
    result, index, line = [], 0, 1
    deadline = time.monotonic() + JS_WORK_SECONDS

    def emit(kind, value, position):
        result.append((kind, value, position))
        if len(result) > MAX_JS_TOKENS:
            raise ArtifactError("JavaScript token limit exceeded")
        if len(result) % 1024 == 0 and time.monotonic() > deadline:
            raise ArtifactError("JavaScript analysis exceeded its work deadline")
    while index < len(text):
        char = text[index]
        if char == "\n":
            line += 1
        if text.startswith("//", index):
            end = text.find("\n", index + 2)
            index = len(text) if end < 0 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = len(text) if end < 0 else end + 2
            line += text[index:end].count("\n")
            index = end
            continue
        if char in "\"'`":
            quote, value, start_line, escaped = char, [], line, False
            index += 1
            while index < len(text):
                current = text[index]
                if current == "\n":
                    line += 1
                if current == quote:
                    index += 1
                    break
                if current == "\\" and index + 1 < len(text):
                    escaped = True
                    index += 1
                    current = text[index]
                    if current == "\n":
                        line += 1
                if len(value) < 4097:
                    value.append(current)
                index += 1
            emit("template" if quote == "`" else "escaped_string" if escaped else "string", "".join(value), start_line)
            continue
        if char.isascii() and (char.isalpha() or char in "_$"):
            end = index + 1
            while end < len(text) and text[end].isascii() and (text[end].isalnum() or text[end] in "_$"):
                end += 1
            emit("identifier", text[index:end], line)
            index = end
        else:
            if not char.isspace():
                emit("punctuation", char, line)
            index += 1
    return result


def parse_js(text, source, builder):
    tokens = js_tokens(text)
    for index, token in enumerate(tokens):
        if token[:2] != ("identifier", "fetch") or (index and tokens[index - 1][1] == "."):
            continue
        upcoming = tokens[index + 1:index + 5]
        if len(upcoming) < 3 or upcoming[0][1] != "(" or upcoming[1][0] != "string" or upcoming[2][1] not in {",", ")"}:
            builder.warn(source, "A dynamic fetch expression was omitted")
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
                elif level == 1 and value == ".":
                    dynamic = True
                elif level == 1 and value == "method" and offset + 2 < len(tail) and tail[offset + 1][1] == ":":
                    literal = tail[offset + 2]
                    if literal[0] != "string" or offset + 3 >= len(tail) or tail[offset + 3][1] not in {",", "}"}:
                        dynamic = True
                    else:
                        methods.append(literal[1].upper())
            if closed is None or closed + 1 >= len(tail) or tail[closed + 1][1] != ")" or dynamic or len(methods) > 1:
                builder.warn(source, "Fetch options were dynamic or incomplete; method was not inferred")
                continue
            if methods:
                method = methods[0]
        builder.add(upcoming[1][1], method, "javascript", source, f"line {token[2]}")


class HTML(HTMLParser):
    def __init__(self, source, builder):
        super().__init__(convert_charrefs=True)
        self.source, self.builder, self.form = source, builder, None
        self.tags = 0

    def handle_starttag(self, tag, attributes):
        self.tags += 1
        if self.tags > 5000:
            raise ArtifactError("HTML tag limit exceeded")
        attrs = {}
        for key, value in attributes:
            attrs.setdefault(key, value)
        location = f"line {self.getpos()[0]}"
        if tag == "a" and attrs.get("href"):
            self.builder.add(attrs["href"], "GET", "link", self.source, location)
        elif tag == "script" and attrs.get("src"):
            self.builder.asset(attrs["src"], "script", self.source, location)
        elif tag == "link" and attrs.get("href"):
            self.builder.asset(attrs["href"], "stylesheet" if attrs.get("rel") == "stylesheet" else "link", self.source, location)
        elif tag == "form":
            self._finish_form()
            if not (attrs.get("action") or "").strip():
                self.builder.warn(self.source, "A form without an action was omitted because its page URL is unknown")
            else:
                self.form = {"url": attrs["action"], "method": (attrs.get("method") or "GET").upper(), "fields": {}, "location": location}
        elif tag in {"input", "textarea", "select"} and self.form is not None and attrs.get("name"):
            fields = self.form["fields"]
            if len(fields) >= 32:
                return
            name = safe_field(attrs["name"], len(fields) + 1, set(fields))
            fields[name] = {"type": "string"}

    def _finish_form(self):
        if self.form:
            form, self.form = self.form, None
            self.builder.add(form["url"], form["method"], "form", self.source, form["location"], fields=form["fields"])

    def handle_endtag(self, tag):
        if tag == "form":
            self._finish_form()


def analyze(artifacts):
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= MAX_ARTIFACTS:
        raise ArtifactError("Choose 1 to 6 HTML, HAR or JavaScript artifacts")
    builder, total = Builder(), 0
    for index, artifact in enumerate(artifacts, 1):
        source = f"source_{index}"
        if not isinstance(artifact, dict) or set(artifact) - {"kind", "text"} or not isinstance(artifact.get("kind"), str) or artifact.get("kind") not in {"har", "html", "javascript"} or not isinstance(artifact.get("text"), str):
            raise ArtifactError(f"{source}: unsupported artifact format")
        text, kind = artifact["text"], artifact["kind"]
        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            raise ArtifactError(f"{source}: invalid Unicode text") from None
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ArtifactError("Artifact byte limit exceeded")
        builder.sources.append({"id": source, "kind": kind, "bytes": size})
        try:
            if kind == "har":
                parse_har(text, source, builder)
            elif kind == "javascript":
                parse_js(text, source, builder)
            else:
                parser = HTML(source, builder)
                parser.feed(text)
                parser.close()
                parser._finish_form()
        except ArtifactError as error:
            raise ArtifactError(f"{source}: {error}") from None
        except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
            raise ArtifactError(f"{source}: malformed or unsupported artifact") from None
    return builder.finish()
