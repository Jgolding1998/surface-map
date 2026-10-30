"""Minimized source hints for agent guidance and machine-readable resources.

This is a static HTML inventory, not a browser visibility or prompt-safety
classifier. Page text is inspected as data and never returned as instructions.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import re
import time
from urllib.parse import unquote, urljoin, urlsplit

MAX_FINDINGS = 64
MAX_TEXT_WINDOW = 8192
MAX_DEPTH = 256
SCOPE = (
    "Untrusted source-only declarations, not instructions to follow or verified services. "
    "No resource is fetched. Raw text, origins and query values are omitted. Visibility flags "
    "describe attributes and inline styles only; aria-hidden is accessibility evidence, not "
    "visual hiding. Only hidden, display:none, visibility:hidden and numeric opacity:0 are recorded; "
    "CSS colors, dimensions, classes, clipping and external/computed CSS are not evaluated. "
    "Common optional HTML end tags are handled, but full browser tree repair and runtime behavior are not. "
    "SVG/MathML and raw-text/RCDATA containers are skipped, as are arbitrary attribute text and encoded scripts. "
    "Detection uses a limited English audience-marker set; markers do not prove instructions or intent, "
    "and missing findings do not prove absence."
)
_VOID = set("area base br col embed hr img input link meta param source track wbr".split())
_FOREIGN_BREAKOUT = set("b big blockquote body br center code dd div dl dt em embed h1 h2 h3 h4 h5 h6 head hr i img li listing menu meta nobr ol p pre ruby s small span strike strong sub sup table tt u ul var".split())
_BLOCK = set("address article aside blockquote body caption dd details dialog div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr html li main nav ol p pre section table tbody td tfoot th thead tr ul".split())
_RAW = set("script style title textarea xmp iframe noembed noframes noscript plaintext".split())
_AGENT_MARKER = re.compile(
    r"\b(?:if\s+you\s+are\s+(?:(?:an?|the)\s+)?(?:ai\s+agent|llm|automated\s+system)\b"
    r"|(?:for|attention|dear)\s+(?:all\s+)?(?:ai\s+agents?|llms?|automated\s+systems?)\b"
    r"|(?:ai\s+agents?|llms?|automated\s+systems?)\s*[:,])", re.I
)
_BARE_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_STYLE_RULE = re.compile(r"^\s*([a-z-]+)\s*:\s*([^;]+)\s*$", re.I)
_RESOURCE_NAMES = {
    "llms.txt": "llms_index", "llms-full.txt": "llms_full",
    "auth.md": "auth_document", "skill.md": "agent_skill",
    "openapi.json": "openapi_spec", "openapi.yaml": "openapi_spec", "openapi.yml": "openapi_spec",
    "swagger.json": "swagger_spec", "swagger.yaml": "swagger_spec", "swagger.yml": "swagger_spec",
}


@dataclass
class _Frame:
    tag: str
    visual: tuple[str, ...]
    accessibility: tuple[str, ...]
    inert: tuple[str, ...]


def _context(parent, tag, attrs):
    visual, accessibility, inert = map(set, parent)
    if "hidden" in attrs:
        visual.add("hidden_attribute")
    if (attrs.get("aria-hidden") or "").strip().lower() == "true":
        accessibility.add("aria_hidden_true")
    if tag == "template":
        inert.add("template")
    # Record declarations, not a claim about the winning cascade value. A
    # later rule or !important stylesheet may change what a browser renders.
    for rule in (attrs.get("style") or "").split(";"):
        match = _STYLE_RULE.fullmatch(rule)
        if not match:
            continue
        key, value = match.groups()
        value = re.sub(r"\s*!\s*important\s*$", "", value, flags=re.I).strip().lower()
        key = key.lower()
        if key == "display" and value == "none":
            visual.add("inline_display_none")
        elif key == "visibility" and value == "hidden":
            visual.add("inline_visibility_hidden")
        elif key == "opacity" and re.fullmatch(r"[+]?0*(?:\.0*)?(?:%)?", value) and any(c.isdigit() for c in value):
            visual.add("inline_opacity_zero")
    return tuple(sorted(visual)), tuple(sorted(accessibility)), tuple(sorted(inert))


class _Signals(HTMLParser):
    def __init__(self, source, document_url, base_url, deadline):
        # Keep character references as separate chunks: a decoded &#10; is
        # whitespace for matching, but must not advance a source line number.
        super().__init__(convert_charrefs=False)
        self.source, self.document_url, self.base_url = source, document_url, base_url
        self.deadline = deadline
        self.frames = []
        self.raw = None
        self.foreign = []
        self.omitted_contexts = set()
        self.tags = 0
        self.findings = []
        self.seen = set()
        self.truncated = False
        self.truncation_reasons = set()
        self.text = []
        self.text_offsets = []
        self.text_positions = []
        self.text_size = 0
        self.text_line = 1
        self.text_context = ((), (), ())

    def _check(self):
        from .analyze import ArtifactError
        if time.monotonic() >= self.deadline:
            raise ArtifactError("HTML signals exceeded their work deadline")

    def set_cdata_mode(self, *args, **kwargs):
        # HTMLParser otherwise treats a foreign <style> or <title> as HTML
        # raw/RCDATA, potentially consuming the subsequent </svg> and page.
        if not self.foreign:
            return super().set_cdata_mode(*args, **kwargs)

    def _truncate(self, reason):
        self.truncated = True
        self.truncation_reasons.add(reason)

    def _current(self):
        if not self.frames:
            return (), (), ()
        frame = self.frames[-1]
        return frame.visual, frame.accessibility, frame.inert

    def _emit(self, kind, line, via, context, **extra):
        self._check()
        visual, accessibility, inert = context
        finding = {"kind": kind, "location": f"line {line}", "declared_via": via,
                   "confidence": "source_only", "visual_hiding_hints": list(visual),
                   "accessibility_hints": list(accessibility), "inert_context": list(inert), **extra}
        identity = repr(finding)
        if identity in self.seen:
            return
        if len(self.findings) >= MAX_FINDINGS:
            self._truncate("finding_limit")
            return
        self.seen.add(identity)
        self.findings.append(finding)

    def _resource(self, value, line, via, context, raw_attribute=None):
        from .analyze import ArtifactError, _guard_html_url, safe_path
        if not value or len(value) > 4096 or "\\" in value or value.startswith("#") or any(ord(c) <= 32 or ord(c) == 127 for c in value):
            return
        try:
            parts = urlsplit(value)
            if parts.scheme and parts.scheme.lower() not in {"http", "https"}:
                return
            if "@" in parts.netloc or (parts.netloc and not parts.hostname):
                return
            parts.port
            if parts.scheme and (not parts.hostname or not value[len(parts.scheme) + 1:].startswith("//")):
                return
            path_parts = [unquote(p).lower() for p in parts.path.split("/") if p]
            if not path_parts:
                return
            resource = _RESOURCE_NAMES.get(path_parts[-1])
            if not resource and any(p in {"mcp", "mcp-server", "mcp-oauth", "mcp-search"} for p in path_parts):
                resource = "mcp_reference"
            if not resource:
                return
            if raw_attribute is not None:
                try:
                    _guard_html_url(raw_attribute, "href", value)
                except ArtifactError:
                    self._truncate("ambiguous_resource_url")
                    return
            if parts.scheme:
                resolution = "absolute_reference"
                resolved = value
            elif self.base_url:
                resolution = "resolved_against_document_base"
                resolved = urljoin(self.base_url, value)
            elif value.startswith("//"):
                resolution = "scheme_relative"
                resolved = value
            elif value.startswith("/"):
                resolution = "origin_relative"
                resolved = value
            else:
                resolution = "unresolved_relative"
                resolved = value
            normalized = safe_path(resolved)
            if normalized is None:
                return
        except (ValueError, UnicodeError):
            return
        self._emit("machine_resource", line, via, context, resource_kind=resource,
                   path=normalized[0], resolution=resolution, evidence_basis="path_pattern")

    def _scan_text(self, text, line, via, context, line_at=None):
        if line_at is None:
            line_at = lambda offset: line + text.count("\n", 0, offset)
        for match in _AGENT_MARKER.finditer(text):
            self._emit("agent_audience_marker", line_at(match.start()), via, context)
        for match in _BARE_URL.finditer(text):
            # Punctuation immediately following prose URLs is not a resource
            # part in common Markdown/email-like text. No request is made.
            value = match.group().rstrip(".,;:!?)]}")
            self._resource(value, line_at(match.start()), via, context)

    def _flush(self):
        if self.text:
            text = "".join(self.text)
            def line_at(offset):
                index = bisect_right(self.text_offsets, offset) - 1
                line, literal_newlines = self.text_positions[index]
                return line + (text.count("\n", self.text_offsets[index], offset) if literal_newlines else 0)
            self._scan_text(text, self.text_line, "text", self.text_context, line_at)
        self.text, self.text_size = [], 0
        self.text_offsets, self.text_positions = [], []

    def _close_in_scope(self, targets, boundaries):
        for index in range(len(self.frames) - 1, -1, -1):
            tag = self.frames[index].tag
            if tag in targets:
                self._flush()
                del self.frames[index:]
                return
            if tag in boundaries:
                return

    def _implied_end(self, tag):
        # Limited HTML optional-end-tag rules stop sibling options/list items
        # and table rows accumulating artificial nesting or inherited hints.
        # Scope boundaries keep a nested list/table from closing its parent.
        if tag in _BLOCK:
            self._close_in_scope({"p"}, {"html", "body", "table", "td", "th", "select", "template"})
        if tag == "li":
            self._close_in_scope({"li"}, {"ul", "ol", "menu", "template"})
        elif tag in {"dt", "dd"}:
            self._close_in_scope({"dt", "dd"}, {"dl", "template"})
        elif tag == "option":
            self._close_in_scope({"option"}, {"select", "datalist", "optgroup", "template"})
        elif tag == "optgroup":
            self._close_in_scope({"option"}, {"select", "datalist", "template"})
            self._close_in_scope({"optgroup"}, {"select", "datalist", "template"})
        elif tag == "tr":
            self._close_in_scope({"tr"}, {"table", "tbody", "thead", "tfoot", "template"})
        elif tag in {"td", "th"}:
            self._close_in_scope({"td", "th"}, {"tr", "table", "tbody", "thead", "tfoot", "template"})
        elif tag in {"tbody", "thead", "tfoot"}:
            self._close_in_scope({"tr"}, {"table", "tbody", "thead", "tfoot", "template"})
            self._close_in_scope({"tbody", "thead", "tfoot"}, {"table", "template"})

    def _tag(self):
        from .analyze import ArtifactError, MAX_HTML_TAGS
        self._check()
        self.tags += 1
        if self.tags > MAX_HTML_TAGS:
            raise ArtifactError("HTML signals exceeded the tag limit")

    def _foreign_breakout(self, tag, attributes):
        return tag in _FOREIGN_BREAKOUT or (tag == "font" and any(
            key in {"color", "face", "size"} for key, _ in attributes))

    def handle_starttag(self, tag, attributes):
        from .analyze import ArtifactError
        self._check()
        if self.raw:
            return
        self._tag()
        if self.foreign and self._foreign_breakout(tag, attributes):
            # HTML breakout tags end foreign parsing even without </svg> or
            # </math>; otherwise one unclosed SVG could mask the rest of a page.
            self.foreign.clear()
        if self.foreign:
            if tag in {"svg", "math"}:
                if len(self.foreign) >= MAX_DEPTH:
                    raise ArtifactError("HTML signals exceeded the nesting limit")
                self.foreign.append(tag)
                self.omitted_contexts.add(tag)
            return
        if tag in {"svg", "math"}:
            self._flush()
            self.foreign.append(tag)
            self.omitted_contexts.add(tag)
            return
        self._implied_end(tag)
        attrs = {}
        for key, value in attributes:
            attrs.setdefault(key, value)
        context = _context(self._current(), tag, attrs)
        if tag in _BLOCK or tag in _RAW or tag == "template" or context != self._current():
            self._flush()
        if tag in {"a", "link"} and attrs.get("href"):
            value = attrs["href"].strip("\t\n\f\r ")
            self._resource(value, self.getpos()[0], "anchor_href" if tag == "a" else "link_href", context,
                           raw_attribute=self.get_starttag_text())
        if tag not in _VOID:
            if len(self.frames) >= MAX_DEPTH:
                raise ArtifactError("HTML signals exceeded the nesting limit")
            self.frames.append(_Frame(tag, *context))
        if tag in _RAW:
            self.raw = tag
            self.set_cdata_mode(tag)
        elif tag in {"br", "wbr"}:
            self.handle_data(" ")

    def handle_startendtag(self, tag, attrs):
        if self.foreign and self._foreign_breakout(tag, attrs):
            self.handle_starttag(tag, attrs)
            return
        if self.foreign or tag in {"svg", "math"}:
            self._tag()
            if tag in {"svg", "math"}:
                self.omitted_contexts.add(tag)
            return
        # In HTML syntax '/>' only self-closes void elements. Treat <script/>
        # and <div/> like their ordinary opening tags.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        self._check()
        if self.foreign:
            found = next((i for i in range(len(self.foreign) - 1, -1, -1) if self.foreign[i] == tag), None)
            if found is not None:
                del self.foreign[found:]
            return
        if self.raw:
            if tag != self.raw or tag == "plaintext":
                return
            self.raw = None
        found = next((i for i in range(len(self.frames) - 1, -1, -1) if self.frames[i].tag == tag), None)
        if found is None:
            return
        old_context = self._current()
        new_context = ((), (), ()) if found == 0 else (self.frames[found-1].visual, self.frames[found-1].accessibility, self.frames[found-1].inert)
        if tag in _BLOCK or tag in _RAW or tag == "template" or old_context != new_context:
            self._flush()
        del self.frames[found:]

    def handle_data(self, data):
        self._append_text(data, literal_newlines=True)

    def handle_entityref(self, name):
        self._append_text(unescape("&" + name + ";"), literal_newlines=False)

    def handle_charref(self, name):
        self._append_text(unescape("&#" + name + ";"), literal_newlines=False)

    def _append_text(self, data, *, literal_newlines):
        self._check()
        if self.raw or self.foreign or not data:
            return
        if not self.text:
            self.text_line = self.getpos()[0]
            self.text_context = self._current()
        space = MAX_TEXT_WINDOW - self.text_size
        if len(data) > space:
            self._truncate("text_window")
        if space > 0:
            self.text_offsets.append(self.text_size)
            self.text_positions.append((self.getpos()[0], literal_newlines))
            self.text.append(data[:space])
            self.text_size += min(len(data), space)

    def handle_comment(self, data):
        self._check()
        if self.raw or self.foreign:
            return
        self._flush()
        visual, accessibility, inert = self._current()
        context = visual, accessibility, tuple(sorted(set(inert) | {"comment"}))
        if len(data) > MAX_TEXT_WINDOW:
            self._truncate("comment_window")
        self._scan_text(data[:MAX_TEXT_WINDOW], self.getpos()[0], "comment", context)


def inspect_html_signals(text, source, *, document_url=None, base_url=None, deadline=None):
    """Inspect already supplied HTML; never follow discovered resources.

    ``base_url`` is the caller's already validated document base. Without it,
    relative resources are inventoried with an unresolved/origin-relative label.
    The containing analyzer supplies generic source IDs, not user filenames.
    """
    from .analyze import ArtifactError, HTML_WORK_SECONDS, MAX_HTML_BYTES
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_HTML_BYTES:
        raise ArtifactError("Unsupported HTML signal input")
    limit = time.monotonic() + HTML_WORK_SECONDS
    if deadline is not None:
        limit = min(limit, deadline)
    parser = _Signals(source, document_url, base_url, limit)
    try:
        parser._check()
        parser.feed(text)
        parser.close()
        parser._flush()
        parser._check()
    except AssertionError:
        raise ArtifactError("Malformed HTML signals input") from None
    return {"source": source, "findings": parser.findings, "truncated": parser.truncated,
            "truncation_reasons": sorted(parser.truncation_reasons),
            "omitted_contexts": sorted(parser.omitted_contexts), "scope": SCOPE}
