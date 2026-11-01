"""Compare saved evidence, with optional private-identity static signals.

HAR observations stay in the normalized comparison. Supplemental static hints
use corresponding non-HAR slots and remap locations to the original sources.
No application source, URL or discovered operation is executed.
"""
from __future__ import annotations

from copy import deepcopy
import time

from .analyze import ArtifactError, Builder, MAX_ARTIFACTS, _read_artifacts, analyze
from .export import compare


CONTEXT_KEYS = ("sources", "warnings", "javascript_analysis", "page_signals",
                "evidence_limits", "script_omissions", "has_document_context", "har_body_analysis")
SIGNAL_SCOPE = (
    "Supplemental differences describe paired supplied HTML and script hints, not service behavior. "
    "Opaque identities are local to this comparison. HAR observations are compared separately in comparison. "
    "Unavailable or partial analysis is not an unchanged or passing result."
)


def _canonical(artifacts, engine, deadline):
    """Use the existing byte/UTF-8 checks for HTTP's encoded artifacts too."""
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= MAX_ARTIFACTS:
        raise ArtifactError("Unsupported capture selection")
    def tick():
        if deadline is not None and time.monotonic() >= deadline:
            raise ArtifactError("Analysis exceeded the shared request deadline; use fewer artifacts")
    tick()
    return [{"kind": kind, "text": text} for _, kind, text in
            _read_artifacts(artifacts, Builder(engine), tick)]


def report_context(report):
    return {key: report[key] for key in CONTEXT_KEYS if key in report}


def _signals(before, after, *, engine, deadline, before_document_url, after_document_url):
    kinds = [item["kind"] for item in before]
    if kinds != [item["kind"] for item in after]:
        return {"status": "not_applicable", "reason": "unpaired_artifact_kinds", "scope": SIGNAL_SCOPE}
    slots = [index for index, kind in enumerate(kinds) if kind != "har"]
    if not slots:
        return {"status": "not_applicable", "reason": "har_only_capture", "scope": SIGNAL_SCOPE}
    if (before_document_url is None) != (after_document_url is None):
        return {"status": "unavailable", "reason": "document_context_mismatch", "scope": SIGNAL_SCOPE}
    from .pr_signals import compare_signals
    try:
        value = compare_signals(
            {"artifacts": [before[index] for index in slots]},
            {"artifacts": [after[index] for index in slots]},
            before_document_url=before_document_url, after_document_url=after_document_url,
            javascript_engine=engine, deadline=deadline)
    except ValueError:
        return {"status": "unavailable", "reason": "unsupported_or_limit", "scope": SIGNAL_SCOPE}
    # The static collector numbers its filtered slots. Keep its evidence in the
    # same original source namespace as the normalized analyzer and body rows.
    names = {f"source_{number}": f"source_{index + 1}" for number, index in enumerate(slots, 1)}
    value = deepcopy(value)
    pending = [value]
    while pending:
        row = pending.pop()
        if isinstance(row, dict):
            if isinstance(row.get("source"), str) and row["source"] in names:
                row["source"] = names[row["source"]]
            pending.extend(item for item in row.values() if isinstance(item, (dict, list)))
        elif isinstance(row, list):
            pending.extend(item for item in row if isinstance(item, (dict, list)))
    coverage = value["coverage"]
    coverage["excluded_sources"] = [{"source": f"source_{index + 1}", "kind": "har",
                                     "reason": "observations_compared_in_normalized_report"}
                                    for index, kind in enumerate(kinds) if kind == "har"]
    coverage["paired_static_sources"] = len(slots)
    partial = bool(coverage.get("warnings") or coverage.get("incomplete_sources") or coverage.get("omissions")
                   or coverage.get("tracking_analysis") == "unavailable_or_partial"
                   or coverage.get("javascript_navigation_analysis") == "unavailable_or_partial")
    value["status"] = "partial" if partial else "available"
    value["capture_scope"] = SIGNAL_SCOPE
    return value


def compare_saved(before, after, *, javascript_engine="auto", deadline=None,
                  before_document_url=None, after_document_url=None, include_review_signals=True):
    if type(include_review_signals) is not bool:
        raise ValueError("Review signal selection must be a boolean")
    before = _canonical(before, javascript_engine, deadline)
    after = _canonical(after, javascript_engine, deadline)
    old = analyze(before, javascript_engine=javascript_engine, deadline=deadline,
                  document_url=before_document_url)
    new = analyze(after, javascript_engine=javascript_engine, deadline=deadline,
                  document_url=after_document_url)
    result = {"comparison": compare(old, new), "before": report_context(old), "after": report_context(new)}
    if include_review_signals:
        result["review_signals"] = _signals(before, after, engine=javascript_engine, deadline=deadline,
                                            before_document_url=before_document_url,
                                            after_document_url=after_document_url)
    return result
