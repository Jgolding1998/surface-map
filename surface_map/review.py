"""Small review briefs derived only from an already-minimized capture report."""
from __future__ import annotations

from collections import Counter


CHECKS = {
    "resolve_method": "Inspect the calling code or an authorized network capture to establish the request method.",
    "inspect_error_response": "Inspect the recorded error response and capture conditions; a status alone does not establish the cause.",
    "capture_response": "Obtain an authorized network capture before treating this declared or inferred operation as observed behavior.",
    "inspect_missing_status": "The supplied HAR records a request without a supported response status; inspect the capture before drawing response conclusions.",
}


def review(report, *, route_limit=12):
    """Summarize evidence, never fetch, replay, or judge whether a service is safe."""
    if type(route_limit) is float and route_limit.is_integer():
        route_limit = int(route_limit)
    if type(route_limit) is not int or not 1 <= route_limit <= 25:
        raise ValueError("Review route limit must be an integer from 1 to 25")
    routes = report["routes"]
    unresolved = report.get("unresolved_operations", [])
    counts = Counter(row["confidence"] for row in routes)
    candidates, checks = [], Counter()
    for route in [*unresolved, *routes]:
        statuses = route.get("statuses", [])
        if route["method"] is None:
            reason = "resolve_method"
        elif any(status >= 400 for status in statuses):
            reason = "inspect_error_response"
        elif route["confidence"] != "observed":
            reason = "capture_response"
        elif not statuses:
            reason = "inspect_missing_status"
        else:
            continue
        checks[reason] += 1
        evidence = sorted(route["evidence"], key=lambda row: {"har": 0, "form": 1, "link": 1}.get(row["kind"], 2))
        candidates.append({"id": route["id"], "method": route["method"], "path": route["path"],
                           "confidence": route["confidence"], "reason": reason,
                           "statuses": statuses, "evidence": evidence[:3],
                           "additional_evidence_count": max(0, len(evidence) - 3)})
    order = {code: index for index, code in enumerate(CHECKS)}
    candidates.sort(key=lambda row: (order[row["reason"]], row["path"], row["method"] or ""))
    result = {
        "format": "surface-map-review-v1",
        "counts": {"known_operations": len(routes), "unresolved_targets": len(unresolved),
                   **{name: counts[name] for name in ("observed", "declared", "inferred")},
                   "assets": len(report["assets"]), "review_candidates": len(candidates),
                   "not_prioritized_operations": len(routes) + len(unresolved) - len(candidates)},
        "next_checks": [{"code": code, "count": checks[code], "suggestion": text}
                        for code, text in CHECKS.items() if checks[code]],
        "routes_to_review": candidates[:route_limit],
        "additional_review_candidates": max(0, len(candidates) - route_limit),
        "selection": "Unknown methods first, then recorded error statuses, unobserved operations and missing response statuses. Observed operations without those flags are counted as not prioritized; they are not verified or exempt from review. This is a review order, not a risk score.",
        "scope": "A compact view of supplied static evidence, not the full inventory. Counts describe retained analysis, not complete site coverage. Successful HAR statuses do not verify current availability, authorization or safety. No discovered operation is replayed. Request output report for full retained evidence; a repeated URL capture may change.",
        "privacy": report["privacy"],
    }
    for key in ("sources", "warnings", "javascript_analysis", "evidence_limits", "script_omissions",
                "capture", "capture_warnings"):
        if key in report:
            result[key] = report[key]
    if "page_signals" in report:
        result["page_signals"] = [
            {key: value for key, value in page.items() if key != "findings"}
            | {"finding_count": len(page["findings"]), "findings_omitted_from_review": True}
            for page in report["page_signals"]
        ]
    return result
