"""Bounded offline analysis and evidence-change policies for CI callers.

Matching capture metadata is a prerequisite, not proof of equivalent coverage.
The caller remains responsible for selecting corresponding artifacts and pages.
"""
from __future__ import annotations

from collections import Counter
import html
import json
import re
import time

from .analyze import analyze, strict_json
from .export import compare
from .source_io import read_ordinary

MAX_CAPTURE_BYTES = 16 * 1024 * 1024 + 64 * 1024
MAX_OUTPUT_BYTES = 3 * 1024 * 1024
ANALYSIS_SECONDS = 10.0
MAX_MARKDOWN_ROWS = 100
POLICIES = ("added", "absent", "changed", "unresolved", "warnings")
SCOPE = (
    "Changes describe supplied normalized evidence, not verified endpoint behavior. "
    "Absent evidence does not prove service removal. Matching artifact kind counts, "
    "document-context availability and parser modes is necessary but does not establish "
    "equivalent capture coverage. The caller must capture corresponding pages and resources. "
    "Redaction can merge different routes or hide changes to unknown names."
)
_ERRORS = {
    "input": "Offline capture rejected: unsupported, malformed, unavailable, or over limit. No input text or paths are returned.",
    "coverage": "Comparison rejected: capture coverage metadata differs or retained evidence is incomplete. Use corresponding captures with matching artifact kinds, counts, document context and parser modes. No input text or paths are returned.",
    "policy": "Policy rejected: choose comma-separated added, absent, changed, unresolved or warnings.",
    "output": "Output rejected: the serialized report exceeds the supported byte limit. Use smaller captures.",
}


class CIError(ValueError):
    def __init__(self, reason="input"):
        super().__init__(_ERRORS.get(reason, _ERRORS["input"]))


def load_capture(path):
    """Read only one explicitly named ordinary local UTF-8 capture file."""
    try:
        document = strict_json(read_ordinary(path, MAX_CAPTURE_BYTES), depth=6, nodes=100)
        _validate_capture(document)
        return document
    except Exception:
        raise CIError("input") from None


def _validate_capture(document):
    if (type(document) is not dict or "artifacts" not in document
            or set(document) - {"artifacts", "document_url"}):
        raise CIError("input")
    artifacts = document["artifacts"]
    if type(artifacts) is not list or not 1 <= len(artifacts) <= 6:
        raise CIError("input")
    for artifact in artifacts:
        if (type(artifact) is not dict or set(artifact) != {"kind", "text"}
                or type(artifact["kind"]) is not str
                or artifact["kind"] not in {"html", "har", "javascript", "typescript"}
                or type(artifact["text"]) is not str):
            raise CIError("input")
    if "document_url" in document and type(document["document_url"]) is not str:
        raise CIError("input")


def parse_fail_on(value):
    """Normalize explicit policy names; an empty selection is advisory."""
    if isinstance(value, str):
        names = [part.strip() for part in value.split(",")] if value.strip() else []
    elif isinstance(value, (list, tuple, set, frozenset)):
        names = list(value)
    else:
        raise CIError("policy")
    if any(type(name) is not str or name not in POLICIES for name in names):
        raise CIError("policy")
    return [name for name in POLICIES if name in names]


def _analyze(document, engine, deadline):
    _validate_capture(document)
    try:
        return analyze(document["artifacts"], javascript_engine=engine,
                       document_url=document.get("document_url"), deadline=deadline)
    except Exception:
        raise CIError("input") from None


def analyze_capture(document, *, javascript_engine="enhanced"):
    report = _analyze(document, javascript_engine, time.monotonic() + ANALYSIS_SECONDS)
    json_output(report)
    return report


def _context(report):
    result = {key: report[key] for key in (
        "sources", "warnings", "javascript_analysis", "evidence_limits", "unresolved_operations"
    ) if key in report}
    result["has_document_context"] = bool(report.get("has_document_context"))
    return result


def _complete(report):
    return (not report.get("evidence_limits") and not report.get("script_omissions")
            and not any(source.get("status") in {"rejected", "omitted"} for source in report["sources"]))


def _coverage(report):
    return (Counter(source["kind"] for source in report["sources"]),
            bool(report.get("has_document_context")),
            Counter(item["engine"] for item in report.get("javascript_analysis", [])))


def compare_captures(before_document, after_document, *, javascript_engine="enhanced", fail_on=()):
    """Compare matching offline selections; policy hits never assert breakage."""
    policies = parse_fail_on(fail_on)
    deadline = time.monotonic() + ANALYSIS_SECONDS
    before = _analyze(before_document, javascript_engine, deadline)
    after = _analyze(after_document, javascript_engine, deadline)
    if not _complete(before) or not _complete(after) or _coverage(before) != _coverage(after):
        raise CIError("coverage")
    difference = compare(before, after)
    hits = {
        "added": bool(difference["added"] or difference.get("unresolved_added")),
        "absent": bool(difference["removed"] or difference.get("unresolved_removed")),
        "changed": bool(difference["changed"] or difference.get("unresolved_changed")),
        # Existing unknown methods are still uncertainty, even when unchanged.
        "unresolved": bool(before.get("unresolved_operations") or after.get("unresolved_operations")),
        "warnings": bool(before["warnings"] or after["warnings"]),
    }
    matched = [name for name in policies if hits[name]]
    result = {"format": "surface-map-ci-comparison-v1", "comparison": difference,
              "before": _context(before), "after": _context(after),
              "policy": {"fail_on": policies, "matched": matched, "passed": not matched,
                         "status": "advisory" if not policies else "failed" if matched else "passed"},
              "scope": SCOPE}
    json_output(result)
    return result


def json_output(value):
    """Serialize completely before a caller writes any stdout, including its LF."""
    chunks, size = [], 1
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > MAX_OUTPUT_BYTES:
            raise CIError("output")
        chunks.append(chunk)
    return "".join(chunks)


def _cell(value):
    text = "".join(char if ord(char) >= 32 and ord(char) != 127 else " " for char in str(value))
    if len(text) > 240:
        text = text[:237] + "..."
    return re.sub(r"([\\`*_{\[\]}()|!])", r"\\\1", html.escape(text, quote=True))


def _references(evidence):
    values = [str(row.get("source", "")) + ": " + str(row.get("location", "")) for row in evidence[:4]]
    return _cell("; ".join(values) or "No retained location")


def markdown(result):
    """Render only normalized evidence, escaping every dynamic Markdown cell."""
    if result.get("format") != "surface-map-ci-comparison-v1":
        raise CIError("input")
    difference, policy = result["comparison"], result["policy"]
    lines = ["## Surface Map evidence comparison", "", SCOPE, "",
             "Policy result: " + ("advisory (no policies selected)" if not policy["fail_on"]
                                   else "passed" if policy["passed"] else "selected policy matched") + ".", "",
             "| Evidence category | Count |", "| --- | ---: |"]
    groups = (("Added evidence", "added"), ("Absent evidence", "removed"), ("Changed evidence", "changed"),
              ("Added unknown-method targets", "unresolved_added"),
              ("Absent unknown-method targets", "unresolved_removed"),
              ("Changed unknown-method targets", "unresolved_changed"))
    lines.extend("| " + title + " | " + str(len(difference.get(key, []))) + " |" for title, key in groups)
    lines.extend(["", "Selected policies: " + _cell(", ".join(policy["fail_on"]) or "none (advisory)") + ".",
                  "Matched policies: " + _cell(", ".join(policy["matched"]) or "none") + "."])
    for title, key in groups:
        rows = difference.get(key, [])
        if not rows:
            continue
        lines.extend(["", "### " + title, "", "| Method | Normalized path | Changed fields | Before evidence | After evidence |",
                      "| --- | --- | --- | --- | --- |"])
        for row in rows[:MAX_MARKDOWN_ROWS]:
            prior = row.get("before_evidence", row.get("evidence", []) if "removed" in key else [])
            current = row.get("evidence", []) if "removed" not in key else []
            lines.append("| " + " | ".join((_cell(row.get("method") or "unknown"), _cell(row["path"]),
                                             _cell(", ".join(row.get("changed_fields", [])) or "none"),
                                             _references(prior), _references(current))) + " |")
        if len(rows) > MAX_MARKDOWN_ROWS:
            lines.extend(["", str(len(rows) - MAX_MARKDOWN_ROWS) + " additional rows are omitted from this summary; retain the JSON report."])
    for side, title in (("before", "Before capture"), ("after", "After capture")):
        context = result[side]
        lines.extend(["", "### " + title, "",
                      "Selected artifacts: " + str(len(context["sources"])) + ". Unknown-method targets: "
                      + str(len(context.get("unresolved_operations", []))) + "."])
        if context["warnings"]:
            lines.extend(["", "| Warning source | Reason |", "| --- | --- |"])
            for warning in context["warnings"]:
                lines.append("| " + _cell(warning.get("source", "")) + " | " + _cell(warning.get("reason", "")) + " |")
        targets = context.get("unresolved_operations", [])
        if targets:
            lines.extend(["", "| Unknown-method target | Retained evidence |", "| --- | --- |"])
            for target in targets[:MAX_MARKDOWN_ROWS]:
                lines.append("| " + _cell(target["path"]) + " | " + _references(target.get("evidence", [])) + " |")
            if len(targets) > MAX_MARKDOWN_ROWS:
                lines.extend(["", str(len(targets) - MAX_MARKDOWN_ROWS) + " additional unknown-method targets are omitted from this summary; retain the JSON report."])
    rendered = "\n".join(lines)
    if len(rendered.encode("utf-8")) + 1 > MAX_OUTPUT_BYTES:
        raise CIError("output")
    return rendered
