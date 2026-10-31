"""An implementation starting point from minimized evidence, with explicit gaps."""
from __future__ import annotations

import json
import math
import time

from .analyze import ArtifactError, analyze
from .references import inventory_references

MAX_OPERATIONS = 50
MAX_PAGES = 50
MAX_EVIDENCE = 8
MAX_PLAN_BYTES = 1024 * 1024
WORK_SECONDS = 10.0
UNKNOWNS = (
    "Backend logic, persistence and side effects are not reconstructed.",
    "Authentication, authorization, validation and rate limits need an implementation and tests.",
    "Observed responses describe captured examples, not every possible result or current behavior.",
    "Layout, styling, accessibility and interactions need visual references and browser tests.",
    "Normalized paths and field names are lossy; unrelated originals can share a placeholder.",
)
SCOPE = (
    "A partial mock plan from supplied evidence. It does not reproduce an application. "
    "Candidate strings and navigation links do not become API operations. Every generated "
    "handler returns 501 until implemented, including operations observed in a HAR. "
    "No captured code is executed and no discovered address is contacted."
)
CAPTURE_GUIDE = (
    "Use a test account and a fresh browser profile for a flow you are authorized to inspect.",
    "Clear the Network log, perform one flow, filter unrelated traffic, and export a sanitized HAR.",
    "Review URLs and bodies yourself: sanitized HAR headers do not make the whole capture private.",
    "Supply selected HTML, scripts and a narrow HAR within the displayed limits. Keep screenshots and traces local as separate visual references.",
    "Run the generated mock checks, then implement one operation and write behavior tests. Compare a new authorized capture with the original evidence.",
)


def operation_unknowns(origin_relations, assumptions=()):
    result = []
    if not origin_relations or "unknown" in origin_relations:
        result.append("Origin scope is unclassified; remove unrelated traffic before implementing this operation.")
    if origin_relations and "cross_origin" in origin_relations:
        result.append("Confirm origin scope before including this operation.")
    if "unverified_global_axios" in assumptions:
        result.append("Browser-global Axios identity and prior configuration are assumed; verify them before implementing this operation.")
    return result + [
        "Required inputs and allowed callers are unknown.",
        "Response shapes are combined observations, not guaranteed for each status.",
        "Implement behavior and validate it independently; mock checks only verify the scaffolding.",
    ]


def plan_rebuild(artifacts, *, javascript_engine="auto", document_url=None, deadline=None):
    end = time.monotonic() + WORK_SECONDS
    if deadline is not None:
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ArtifactError("Rebuild deadline must be a finite monotonic time")
        end = min(end, deadline)

    def tick():
        if time.monotonic() >= end:
            raise ArtifactError("Rebuild planning exceeded its work deadline")

    tick()
    report = analyze(artifacts, javascript_engine=javascript_engine,
                     document_url=document_url, deadline=end)
    references = inventory_references(artifacts, document_url=document_url, deadline=end)
    operations, pages = [], []
    excluded_cross_origin = 0
    for route in report["routes"]:
        tick()
        kinds = set(route.get("kinds", (row["kind"] for row in route["evidence"])))
        common = {key: route[key] for key in ("id", "path", "confidence")}
        common["evidence"] = route["evidence"][:MAX_EVIDENCE]
        common["additional_evidence_count"] = max(0, len(route["evidence"]) - MAX_EVIDENCE)
        if "origin_relations" in route:
            common["origin_relations"] = route["origin_relations"]
        if route.get("assumptions"):
            common["assumptions"] = list(route["assumptions"])
        if kinds == {"link"}:
            pages.append(common)
            continue
        cross_origin = route.get("origin_relations") == ["cross_origin"]
        excluded_cross_origin += int(cross_origin)
        operations.append({**common,
            **{key: route[key] for key in ("method", "statuses", "query_fields", "request_shapes", "response_shapes")},
            "stub": "none" if cross_origin else "not_implemented",
            "unknowns": operation_unknowns(route.get("origin_relations"), route.get("assumptions", []))})
    unresolved = report.get("unresolved_operations", [])
    coverage = {key: report[key] for key in (
        "sources", "warnings", "javascript_analysis", "evidence_limits", "script_omissions", "has_document_context"
    ) if key in report}
    coverage.update({"retained_operation_groups": len(operations), "planned_operations": min(len(operations), MAX_OPERATIONS),
                     "omitted_operations": max(0, len(operations) - MAX_OPERATIONS),
                     "omitted_pages": max(0, len(pages) - MAX_PAGES),
                     "omitted_unresolved_targets": max(0, len(unresolved) - MAX_OPERATIONS),
                     "cross_origin_groups_without_stubs": excluded_cross_origin,
                     "origin_classification_available": bool(report.get("has_document_context")),
                     "origin_scope": "Without document context, including HAR captures, first-party and third-party operations cannot be separated. Remove unrelated traffic before implementing a stub.",
                     "selection": "Sorted by method and normalized path; counts are not whole-site coverage."})
    plan = {"format": "surface-map-rebuild-v1", "operations": operations[:MAX_OPERATIONS],
            "pages": pages[:MAX_PAGES], "unresolved": unresolved[:MAX_OPERATIONS],
            "candidate_references": references, "coverage": coverage, "unknowns": list(UNKNOWNS),
            "capture_guide": list(CAPTURE_GUIDE), "privacy": report["privacy"], "scope": SCOPE,
            "limits": {"operations": MAX_OPERATIONS, "pages": MAX_PAGES, "evidence_per_row": MAX_EVIDENCE}}
    if len(json.dumps(plan, ensure_ascii=True, allow_nan=False).encode("utf-8")) > MAX_PLAN_BYTES:
        raise ArtifactError("Rebuild plan exceeded its output limit; choose a narrower capture")
    tick()
    return plan


def scaffold_files(plan):
    from .scaffold import scaffold_files as build
    return build(plan)


def scaffold_zip(plan):
    from .scaffold import scaffold_zip as build
    return build(plan)


def write_scaffold(plan, destination):
    from .scaffold import write_scaffold as write
    return write(plan, destination)
