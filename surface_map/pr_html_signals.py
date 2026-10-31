"""Private, bounded HTML declarations for one before/after review comparison.

The return value contains private comparison identities. It is not an export
format and must never be dumped directly into a report, log or exception.
"""
from __future__ import annotations

import ipaddress
import math
import re
import time
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from .analyze import HTML, HTML_WORK_SECONDS, document_base

MAX_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 4096
MAX_INLINE_SCRIPTS = 64
MAX_INLINE_BYTES = 512 * 1024
_SOURCE = re.compile(r"source_[1-6]\Z")
_ERROR = "HTML review signals rejected: unsupported input or exceeded work limit."
_TYPES = frozenset("hidden text search tel url email password date month week time datetime-local number range color checkbox radio file submit image reset button".split())


def _fail():
    raise ValueError(_ERROR) from None


def _origin(parts, *, keep_default_port=False):
    """Canonicalize a conservative ASCII HTTP(S) origin without credentials."""
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc or "@" in parts.netloc:
        return None
    host = parts.hostname
    if not host or not host.isascii() or "%" in host:
        return None
    host = host.lower()
    try:
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            return None
        if ":" in host:
            host = "[" + str(ipaddress.IPv6Address(host)) + "]"
        else:
            if not re.fullmatch(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*\.?", host):
                return None
            # Browsers interpret several non-canonical numeric hosts as IPv4.
            # Only the ordinary dotted-decimal representation is supported here.
            last = host.rstrip(".").rsplit(".", 1)[-1]
            if last.isdigit() or last.startswith("0x"):
                canonical = str(ipaddress.IPv4Address(host))
                if canonical != host:
                    return None
                host = canonical
        default = 443 if parts.scheme.lower() == "https" else 80
        suffix = "" if port is None or port == default and not keep_default_port else ":" + str(port)
        return parts.scheme.lower() + "://" + host + suffix
    except (ValueError, UnicodeError):
        return None


def validate_site_origin(value):
    """Validate optional classification metadata, never a page URL or fetch."""
    try:
        if (type(value) is not str or not value or len(value) > 4096 or "\\" in value
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
            _fail()
        parts = urlsplit(value)
        origin = _origin(parts)
        if origin is None or parts.path not in {"", "/"} or parts.query or parts.fragment:
            _fail()
        return origin
    except (ValueError, UnicodeError, TypeError):
        _fail()


def canonical_target(raw, site_origin=None):
    """Return private literal target identity, omitting values and fragments.

    Relative paths remain relative: an origin alone supplies no document path.
    The caller must not serialize this return value directly.
    """
    try:
        site = validate_site_origin(site_origin) if site_origin is not None else None
        if (type(raw) is not str or not raw or len(raw) > 4096 or "\\" in raw
                or any(ord(char) <= 32 or ord(char) == 127 for char in raw)):
            return None
        parts = urlsplit(raw)
        if parts.scheme and parts.scheme.lower() not in {"http", "https"}:
            return None
        if raw.startswith("///") or "@" in parts.netloc:
            return None
        if parts.scheme and not raw[len(parts.scheme) + 1:].startswith("//"):
            return None
        if parts.netloc:
            if parts.scheme:
                origin = _origin(parts)
                mode = "absolute"
            elif site:
                origin = _origin(urlsplit(urlsplit(site).scheme + ":" + raw))
                mode = "absolute"
            else:
                # Keep scheme-relative identity distinct while its scheme and
                # first-party classification are not known.
                candidate = _origin(urlsplit("https:" + raw), keep_default_port=True)
                origin = candidate[len("https:"):] if candidate else None
                mode = "network_path"
            if origin is None:
                return None
            relation = "unknown" if site is None or mode == "network_path" else (
                "same_origin" if origin == site else "cross_origin")
        elif parts.scheme:
            return None
        else:
            # A supplied origin is enough for root-relative paths, but not for
            # page-relative paths such as "child" or a query-only reference.
            mode, origin = ("absolute" if site and parts.path.startswith("/") else "relative"), site
            relation = "same_origin" if site else "relative"
        path = parts.path or ("/" if mode in {"absolute", "network_path"} else "")
        if len(path) > 2048 or len(path.split("/")) > 25:
            return None
        # Avoid guessing browser path normalization or accepting escaped control
        # characters. Encoded separators remain literal comparison evidence.
        decoded = unquote(path, errors="strict")
        if (any(ord(char) < 32 or ord(char) == 127 for char in decoded)
                or mode != "relative" and any(piece in {".", ".."} for piece in decoded.split("/"))):
            return None
        keys = tuple(sorted({key for key, _ in parse_qsl(parts.query, keep_blank_values=True,
                                                        max_num_fields=64, errors="strict")}))
        return {"identity": (mode, origin, path, keys), "origin": origin, "path": path,
                "origin_relation": relation}
    except (ValueError, UnicodeError, TypeError, OverflowError):
        return None


class _Builder:
    def __init__(self, source, site):
        self.source, self.site = source, site
        self.document_url = None  # site_origin is not the document URL.
        self.base_url = None
        self.relative_reason = "Document path is unknown."
        self.records, self.limitations, self.scope_notes = [], set(), set()
        self.omitted_count = 0
        self.parser = None
        self.form_targets = set()
        self.base_seen = False
        self.inline_scripts = []
        self.inline_bytes = 0

    def warn(self, source, reason):
        if "Inline scripts" in reason:
            # Executable inline bodies are handed privately to the coordinator
            # for syntax analysis; no JavaScript is scanned as HTML here.
            return
        elif "form without an action" in reason:
            self.scope_notes.add("current_document_target_unresolved")
            return
        elif "Template" in reason:
            self.scope_notes.add("template_content_excluded")
            return
        elif "nested form" in reason:
            code = "nested_form_ownership_unsupported"
        elif "SVG" in reason:
            code = "foreign_content_omitted"
        elif "Embedded" in reason:
            code = "embedded_document_contents_uninspected"
        elif "noscript" in reason:
            code = "conditional_content_uninspected"
        elif "text container" in reason:
            code = "unclosed_text_container"
        else:
            code = "html_coverage_incomplete"
        self.limitations.add(code)

    def record_omission(self, *args):
        _fail()

    def resolve(self, raw, kind):
        if kind == "link" and isinstance(raw, str) and raw.startswith("#"):
            return raw
        if self.base_url is not None and isinstance(raw, str):
            try:
                return urljoin(self.base_url, raw)
            except ValueError:
                return raw
        return raw

    def target(self, raw):
        # These references are outside the HTTP(S) route/resource inventory,
        # rather than failed evidence. They must not make an ordinary page look
        # incomplete merely because it offers an email or telephone link.
        if isinstance(raw, str):
            try:
                if raw.startswith("#") or urlsplit(raw).scheme.lower() in {"mailto", "tel", "javascript", "data"}:
                    self.scope_notes.add("non_http_references_excluded")
                    return None
            except ValueError:
                pass
        target = canonical_target(self.resolve(raw, "reference"), self.site)
        if target is None:
            self.omitted_count += 1
            self.limitations.add("unsupported_url_omitted")
        elif target["identity"][0] == "relative":
            self.scope_notes.add("document_path_unknown")
        elif target["identity"][0] == "network_path":
            self.scope_notes.add("scheme_relative_origin_unclassified")
        elif self.site is None:
            self.scope_notes.add("first_party_origin_unknown")
        return target

    def current_document_target(self):
        self.scope_notes.add("current_document_target_unresolved")
        return {"identity": ("current_document", self.site, self.source, ()),
                "origin": self.site, "path": "",
                "origin_relation": "same_origin" if self.site else "unknown"}

    def form_target(self, raw):
        omitted_before = self.omitted_count
        target = self.target(raw)
        if target is not None:
            return target
        # Keep the declaration's controls visible even when its destination is
        # outside the supported URL inventory. No unsupported action text is
        # retained in this fixed private identity.
        self.limitations.add("unsupported_form_target_unresolved")
        if self.omitted_count == omitted_before:
            self.omitted_count += 1
        return {"identity": ("unsupported_form_target", None, self.source, ()),
                "origin": None, "path": "", "origin_relation": "unknown"}

    def document_base(self, raw, location):
        if self.base_seen:
            return
        self.base_seen = True
        raw = (raw or "").strip("\t\n\f\r ")
        # Compare the first literal base declaration independently from route
        # resolution: a relative base can change every child reference even
        # while the document's absolute location remains unknown.
        target = self.current_document_target() if not raw or raw.startswith("#") else canonical_target(raw, self.site)
        if target is None:
            self.limitations.add("unsupported_document_base_omitted")
            self.omitted_count += 1
            return
        self.emit("document_base", target, location)
        if target["identity"][0] == "relative":
            self.scope_notes.add("document_path_unknown")

    def emit(self, category, target, location, *, method=None, name=None, attrs=None):
        if target is None:
            return
        if len(self.records) >= MAX_RECORDS:
            _fail()
        identity = (target["identity"], method, name) if category == "form_field" else (
            (target["identity"], method) if category == "endpoint" else target["identity"])
        labels = {"origin_relation": target["origin_relation"], **(attrs or {})}
        if method:
            labels["method"] = method
        row = {"category": category, "identity": identity,
               "evidence": {"source": self.source, "location": location},
               "confidence": "declared", "attrs": labels,
               "target": target["identity"], "origin": target["origin"], "path": target["path"]}
        if name is not None:
            row["name"] = name
        self.records.append(row)

    def add(self, raw, method, kind, source, location, **kwargs):
        if kind != "link" or self.parser.template_depth:
            if self.parser.template_depth:
                self.scope_notes.add("template_content_excluded")
            return
        if not isinstance(raw, str) or not raw or raw.startswith("#"):
            return
        self.emit("navigation", self.target(raw), location, method="GET")

    def asset(self, raw, kind, source, location):
        if self.parser.template_depth:
            self.scope_notes.add("template_content_excluded")
            return
        target = self.target(raw)
        self.emit("script", target, location, attrs={"asset_kind": kind})
        if target is not None:
            # Different resource kinds at one path remain distinct declarations.
            self.records[-1]["identity"] = (kind, target["identity"])


class _HTML(HTML):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._inline = None

    def handle_starttag(self, tag, attributes):
        attrs = {}
        for key, value in attributes:
            attrs.setdefault(key, value)
        active = (self.raw is None and (not self.foreign or self._foreign_breakout(tag, attributes))
                  and tag not in {"svg", "math"})
        form_was_open = self.form_open
        controls = tag in {"input", "textarea", "select"}
        external = controls and "form" in attrs
        # The inherited parser's normalized field-name set has a separate
        # 32-name limit. This private collector keeps bounded control occurrences
        # itself, so do not populate that unused normalized set.
        forwarded = [(key, value) for key, value in attributes if key != "name"] if controls else attributes
        super().handle_starttag(tag, forwarded)
        if not active:
            return
        if tag == "form" and not self.template_depth and not form_was_open:
            action = attrs.get("action")
            method = (attrs.get("method") or "").lower()
            action = action.strip("\t\n\f\r ") if action is not None else ""
            if method != "dialog" and (not action or action.startswith("#")):
                self.form = {"url": None, "method": "POST" if method == "post" else "GET", "names": set(),
                             "location": "line " + str(self.getpos()[0]), "_pr_current_document": True}
        if not self.template_depth and any(name.startswith("on") for name in attrs):
            self.builder.limitations.add("event_handler_attributes_uninspected")
        if tag == "script" and not self.template_depth and "src" not in attrs:
            script_type = (attrs.get("type") or "").strip().lower()
            if script_type in {"", "text/javascript", "application/javascript", "module"}:
                self._inline = {"parts": [], "bytes": 0, "line_offset": None,
                                "module_mode": script_type == "module"}
            elif script_type not in {"application/json", "application/ld+json", "importmap", "speculationrules"}:
                self.builder.limitations.add("script_type_uninspected")
        if tag == "base" and not self.template_depth and "href" in attrs:
            self.builder.document_base(attrs["href"], "line " + str(self.getpos()[0]))
            self.builder.scope_notes.add("document_path_unknown")
        if not self.template_depth and tag in {"input", "button"} and (
                "formaction" in attrs or "formmethod" in attrs):
            self.builder.limitations.add("submitter_overrides_uninspected")
        if not self.template_depth and tag == "button" and attrs.get("name"):
            self.builder.limitations.add("button_controls_uninspected")
        if not controls or self.template_depth:
            return
        if external:
            self.builder.limitations.add("external_form_ownership_omitted")
            self.builder.omitted_count += 1
            return
        if self.form is None or not attrs.get("name"):
            return
        name = attrs["name"]
        if len(name) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in name):
            self.builder.omitted_count += 1
            self.builder.limitations.add("unsupported_field_name_omitted")
            return
        declared = (attrs.get("type") or "text").lower() if tag == "input" else tag
        input_type = declared if declared in _TYPES or declared in {"textarea", "select"} else "text"
        fields = self.form.setdefault("_pr_fields", [])
        if len(fields) >= MAX_RECORDS:
            _fail()
        # Repeated names are separate declared controls. Collapsing them would
        # hide an added hidden input that submits another value under the same
        # field name. Values remain deliberately uncollected.
        fields.append({"name": name, "input_type": input_type,
                       "location": "line " + str(self.getpos()[0])})

    def handle_data(self, data):
        super().handle_data(data)
        if self._inline is None:
            return
        size = len(data.encode("utf-8"))
        self._inline["bytes"] += size
        if (self._inline["bytes"] > MAX_INLINE_BYTES
                or self.builder.inline_bytes + self._inline["bytes"] > MAX_INLINE_BYTES):
            _fail()
        if self._inline["line_offset"] is None:
            self._inline["line_offset"] = self.getpos()[0] - 1
        self._inline["parts"].append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.raw == "script" and self._inline is not None:
            inline, self._inline = self._inline, None
            text = "".join(inline["parts"])
            if text.strip():
                if len(self.builder.inline_scripts) >= MAX_INLINE_SCRIPTS:
                    _fail()
                self.builder.inline_bytes += inline["bytes"]
                self.builder.inline_scripts.append({"text": text, "line_offset": inline["line_offset"] or 0,
                                                    "kind": "javascript", "module_mode": inline["module_mode"]})
        super().handle_endtag(tag)

    def _finish_form(self):
        self.form_open = False
        if self.form is None:
            return
        form, self.form = self.form, None
        target = (self.builder.current_document_target() if form.get("_pr_current_document")
                  else self.builder.form_target(form["url"]))
        form_key = target["identity"], form["method"]
        if form_key in self.builder.form_targets:
            self.builder.scope_notes.add("forms_aggregated_by_target")
        self.builder.form_targets.add(form_key)
        self.builder.emit("endpoint", target, form["location"], method=form["method"])
        for field in form.get("_pr_fields", []):
            self.builder.emit("form_field", target, field["location"], method=form["method"], name=field["name"],
                              attrs={"input_type": field["input_type"],
                                     "hidden": field["input_type"] == "hidden"})

    def close(self):
        super().close()
        if self._inline is not None:
            # A parser must not treat a missing closing tag as proof that an
            # earlier tracking call disappeared from the selected source.
            _fail()
        if self.form is not None:
            self.builder.limitations.add("unclosed_form_declaration")
            self._finish_form()


def collect_html(text, source="source_1", *, site_origin=None, deadline=None):
    """Collect private declarations; the caller must alias identities before export."""
    try:
        if (type(text) is not str or type(source) is not str or not _SOURCE.fullmatch(source)
                or len(text.encode("utf-8")) > MAX_BYTES or "\x00" in text):
            _fail()
        if deadline is not None and (type(deadline) not in {float, int} or not math.isfinite(deadline)):
            _fail()
        site = validate_site_origin(site_origin) if site_origin is not None else None
        end = min(time.monotonic() + HTML_WORK_SECONDS, deadline) if deadline is not None else time.monotonic() + HTML_WORK_SECONDS
        builder = _Builder(source, site)
        # An absolute first <base href> can supply a path context. An origin
        # option alone never supplies the unknown page's directory or filename.
        builder.base_url = document_base(text, None, end)
        parser = _HTML(source, builder, end)
        builder.parser = parser
        parser.feed(text)
        parser.close()
        parser._check_time()
        return {"records": builder.records, "limitations": sorted(builder.limitations),
                "scope_notes": sorted(builder.scope_notes),
                "engine": "html-static", "omitted_count": builder.omitted_count,
                "inline_scripts": builder.inline_scripts}
    except Exception:
        _fail()
