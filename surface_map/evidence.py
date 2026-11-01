"""Bounded views over fresh minimized capture evidence, never saved handles."""
from __future__ import annotations

from copy import deepcopy
import re

from .analyze import MAX_ARTIFACTS, METHODS, analyze
from .capture_compare import report_context

MAX_PAGE = 100
CATEGORIES = ("routes", "assets", "unresolved", "har_bodies")
KINDS = ("har", "form", "link", "javascript", "script", "stylesheet", "embedded-document")
SCOPE = (
    "A filtered page of retained supplied evidence, not full website coverage. Counts and warnings "
    "refer to this fresh analysis; omitted evidence is not recovered by paging. No state or cursor is "
    "stored. Reuse identical artifacts and options when continuing at next_offset. Summary rows omit "
    "schemas and cap evidence examples; detail true returns the retained row subject to result limits."
)


def _integer(value, minimum, maximum):
    if type(value) is float and value.is_integer():
        value = int(value)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Evidence page offset or limit is invalid")
    return value


def _summary(row, category):
    if category == "har_bodies":
        return deepcopy(row)
    result = {key: deepcopy(row[key]) for key in
              ("id", "method", "path", "kind", "confidence", "kinds", "statuses", "origin_relations", "assumptions")
              if key in row}
    for key in ("query_fields", "request_shapes", "response_shapes", "request_fields"):
        if key in row:
            result["request_field_observations_count" if key == "request_fields" else key + "_count"] = len(row[key])
    evidence = row.get("evidence", [])
    result["evidence"] = deepcopy(evidence[:3])
    result["additional_evidence_count"] = max(0, len(evidence) - 3)
    return result


def page_report(report, *, category="routes", offset=0, limit=25, detail=False,
                source_id=None, method=None, confidence=None, evidence_kind=None, body_state=None):
    """Internal projection: report must come directly from our analyzer."""
    offset, limit = _integer(offset, 0, 100000), _integer(limit, 1, MAX_PAGE)
    if category not in CATEGORIES or type(detail) is not bool:
        raise ValueError("Unsupported evidence page selection")
    if source_id is not None and (type(source_id) is not str or
                                  not re.fullmatch(r"source_[1-" + str(MAX_ARTIFACTS) + r"]", source_id)):
        raise ValueError("Unsupported evidence source filter")
    if method is not None and (type(method) is not str or method not in METHODS | {"UNKNOWN"}):
        raise ValueError("Unsupported evidence method filter")
    if confidence is not None and (type(confidence) is not str or confidence not in {"observed", "declared", "inferred"}):
        raise ValueError("Unsupported evidence confidence filter")
    if evidence_kind is not None and (type(evidence_kind) is not str or evidence_kind not in KINDS):
        raise ValueError("Unsupported evidence kind filter")
    if body_state is not None and (type(body_state) is not str or body_state not in {"inspected", "omitted"}):
        raise ValueError("Unsupported evidence body filter")
    if category not in {"routes", "unresolved"} and any(value is not None for value in (method, confidence)):
        raise ValueError("Operation filters require an operation category")
    if category != "har_bodies" and body_state is not None:
        raise ValueError("Body filters require the body category")
    if category == "har_bodies":
        rows = [{"source": entry["source"], **body} for entry in report.get("har_body_analysis", [])
                for body in entry["bodies"]]
    else:
        rows = report[{"unresolved": "unresolved_operations"}.get(category, category)] if category != "unresolved" else report.get("unresolved_operations", [])
    matching = []
    for row in rows:
        evidence = [{"source": row.get("source"), "kind": "har"}] if category == "har_bodies" else row.get("evidence", [])
        if category == "assets":
            evidence = [{**item, "kind": row["kind"]} for item in evidence]
        if (source_id is not None or evidence_kind is not None) and not any(
                (source_id is None or item.get("source") == source_id)
                and (evidence_kind is None or item.get("kind") == evidence_kind) for item in evidence):
            continue
        if method is not None and (row.get("method") or "UNKNOWN") != method:
            continue
        if confidence is not None and row.get("confidence") != confidence:
            continue
        if body_state is not None and row.get("state") != body_state:
            continue
        matching.append(row)
    selected = matching[offset:offset + limit]
    context = deepcopy(report_context(report))
    for entry in context.get("har_body_analysis", []):
        entry["retained_body_rows_not_in_context"] = len(entry.pop("bodies", []))
    return {
        "format": "surface-map-evidence-page-v1", "category": category,
        "filters": {key: value for key, value in {"source_id": source_id, "method": method,
                    "confidence": confidence, "evidence_kind": evidence_kind, "body_state": body_state}.items()
                    if value is not None},
        "counts": {"retained": len(rows), "matching": len(matching), "returned": len(selected),
                   "remaining": max(0, len(matching) - offset - len(selected))},
        "page": {"offset": offset, "limit": limit, "detail": detail,
                 "next_offset": offset + len(selected) if offset + len(selected) < len(matching) else None},
        "items": deepcopy(selected) if detail else [_summary(row, category) for row in selected],
        "privacy": report["privacy"], "scope": SCOPE, **context,
    }


def inspect_evidence(artifacts, *, javascript_engine="auto", document_url=None, deadline=None, **selection):
    report = analyze(artifacts, javascript_engine=javascript_engine, document_url=document_url, deadline=deadline)
    return page_report(report, **selection)
