from __future__ import annotations

import json
import re


def union(schemas):
    return schemas[0] if len(schemas) == 1 else {"anyOf": schemas} if schemas else {}


def openapi_routes(routes):
    groups = {}
    for route in routes:
        template = re.sub(r"\{(?:integer|segment)_[0-9]+\}", "{}", route["path"])
        groups.setdefault(template, []).append(route)
    result = []
    for group in groups.values():
        segments = [route["path"].split("/") for route in group]
        canonical = segments[0][:]
        for index, segment in enumerate(canonical):
            if segment.startswith("{") and any(parts[index].startswith("{segment_") for parts in segments):
                canonical[index] = f"{{segment_{index}}}"
        path, operations = "/".join(canonical), {}
        for route in group:
            method = route["method"]
            if method not in operations:
                operations[method] = {**route, "path": path, **{key: [] for key in ("statuses", "query_fields", "request_shapes", "response_shapes", "evidence")}}
            merged = operations[method]
            for key in ("statuses", "query_fields", "request_shapes", "response_shapes", "evidence"):
                for value in route[key]:
                    if value not in merged[key]:
                        merged[key].append(value)
            if ("inferred", "declared", "observed").index(route["confidence"]) > ("inferred", "declared", "observed").index(merged["confidence"]):
                merged["confidence"] = route["confidence"]
        for route in operations.values():
            route["statuses"].sort()
            route["query_fields"].sort()
            result.append(route)
    return result


def openapi(report):
    paths = {}
    for route in openapi_routes(report["routes"]):
        parameters = [{"name": field, "in": "query", "required": False, "schema": {"type": "string"}, "description": "Name observed; values discarded and type unverified."} for field in route["query_fields"]]
        parameters.extend({"name": name, "in": "path", "required": True, "schema": {"type": "integer" if name.startswith("integer_") else "string"}, "description": "Inferred redaction placeholder, not a verified API parameter."} for name in re.findall(r"\{([a-z0-9_]+)\}", route["path"]))
        operation = {"operationId": route["id"], "summary": f"{route['method']} {route['path']}", "description": "Evidence-derived skeleton only. Origins, raw values and unknown names were removed. Confirm behavior and supply your own server before use.", "parameters": parameters, "responses": {}, "x-surface-map-confidence": route["confidence"], "x-surface-map-evidence": route["evidence"]}
        for status in route["statuses"]:
            operation["responses"][str(status)] = {"description": "Status observed in a HAR entry. Body shapes are combined observations, not guaranteed for this status."}
            if route["response_shapes"]:
                operation["responses"][str(status)]["content"] = {"application/json": {"schema": union(route["response_shapes"])}}
        if not operation["responses"]:
            operation["responses"]["default"] = {"description": "No response observed."}
        if route["request_shapes"]:
            operation["requestBody"] = {"required": False, "description": "Observed JSON or declared form field shapes. Required fields and encoding are unverified.", "content": {"application/json": {"schema": union(route["request_shapes"])}}}
        paths.setdefault(route["path"], {})[route["method"].lower()] = operation
    result = {"openapi": "3.1.0", "info": {"title": "Surface Map evidence skeleton", "version": "0.1.0", "description": "Lossy reconstruction of supplied artifacts. Not a complete or tested service contract."}, "paths": paths, "x-surface-map-privacy": report["privacy"]}
    if report.get("unresolved_operations"):
        result["x-surface-map-unresolved-operations"] = report["unresolved_operations"]
    if report.get("evidence_limits"):
        result["x-surface-map-evidence-limits"] = report["evidence_limits"]
    return result


def contract(report):
    result = {"format": "surface-map-contract-v1", "routes": [{key: route[key] for key in ("method", "path", "confidence", "statuses", "query_fields", "request_shapes", "response_shapes", "evidence")} for route in report["routes"]], "privacy": report["privacy"]}
    if report.get("unresolved_operations"):
        result["unresolved_operations"] = report["unresolved_operations"]
    if report.get("evidence_limits"):
        result["evidence_limits"] = report["evidence_limits"]
    return result


def compare(before, after):
    def evidence_kinds(route):
        return {entry["kind"] for entry in route["evidence"]}

    def changed_fields(a, b, keys):
        fields = []
        for key in keys:
            left, right = a[key], b[key]
            if isinstance(left, list):
                left = sorted(json.dumps(item, sort_keys=True) for item in left)
                right = sorted(json.dumps(item, sort_keys=True) for item in right)
            if left != right:
                fields.append(key)
        if evidence_kinds(a) != evidence_kinds(b):
            fields.append("evidence_kinds")
        return fields

    def keyed(report):
        return {(route["method"], route["path"]): route for route in report["routes"]}

    supplied = {source["kind"] for source in after["sources"]}
    prior_supplied = {source["kind"] for source in before["sources"]}
    unresolved_paths = {target["path"] for target in after.get("unresolved_operations", [])}
    prior_unresolved_paths = {target["path"] for target in before.get("unresolved_operations", [])}
    old_engines = {item["engine"] for item in before.get("javascript_analysis", [])}
    new_engines = {item["engine"] for item in after.get("javascript_analysis", [])}

    def missing_kind(route, available):
        kinds = set(route.get("kinds", (entry["kind"] for entry in route["evidence"])))
        return (("har" in kinds and "har" not in available)
                or (bool(kinds & {"link", "form"}) and "html" not in available)
                or ("javascript" in kinds and not available & {"javascript", "typescript"}))

    def absence_context(route):
        context = []
        kinds = set(route.get("kinds", (entry["kind"] for entry in route["evidence"])))
        if missing_kind(route, supplied):
            context.append("prior_source_kind_not_supplied")
        if route["path"] in unresolved_paths:
            context.append("same_path_unresolved_after")
        if "javascript" in kinds and after.get("script_omissions"):
            context.append("scripts_omitted_from_analysis")
        if "javascript" in kinds and old_engines and new_engines and old_engines != new_engines:
            context.append("javascript_engine_changed")
        return context or ["not_seen_in_supplied_capture"]

    def presence_context(route):
        context = []
        kinds = set(route.get("kinds", (entry["kind"] for entry in route["evidence"])))
        if missing_kind(route, prior_supplied):
            context.append("source_kind_not_supplied_before")
        if route["path"] in prior_unresolved_paths:
            context.append("same_path_unresolved_before")
        if "javascript" in kinds:
            if old_engines and new_engines and old_engines != new_engines:
                context.append("javascript_engine_changed")
            if before.get("script_omissions"):
                context.append("scripts_omitted_before")
        return context or ["not_seen_in_prior_capture"]

    def coverage(report):
        return {"source_kinds": sorted({row["kind"] for row in report["sources"]}),
                "retained_warning_count": len(report["warnings"]),
                "omitted_script_count": len(report.get("script_omissions", [])),
                "evidence_retention_limited": bool(report.get("evidence_limits"))}

    old, new = keyed(before), keyed(after)
    added = [{"method": method, "path": path, "evidence": new[(method, path)]["evidence"],
              "presence_context": presence_context(new[(method, path)])} for method, path in sorted(new.keys() - old.keys())]
    removed = [{"method": method, "path": path, "evidence": old[(method, path)]["evidence"],
                "absence_context": absence_context(old[(method, path)])} for method, path in sorted(old.keys() - new.keys())]
    changed = []
    for method, path in sorted(old.keys() & new.keys()):
        a, b = old[(method, path)], new[(method, path)]
        fields = changed_fields(a, b, ("statuses", "query_fields", "request_shapes", "response_shapes", "confidence"))
        if fields:
            context = []
            if missing_kind(a, supplied):
                context.append("prior_source_kind_not_supplied")
            if missing_kind(b, prior_supplied):
                context.append("source_kind_not_supplied_before")
            kinds = set(a.get("kinds", (entry["kind"] for entry in a["evidence"]))) | set(b.get("kinds", (entry["kind"] for entry in b["evidence"])))
            if "javascript" in kinds and old_engines and new_engines and old_engines != new_engines:
                context.append("javascript_engine_changed")
            changed.append({"method": method, "path": path, "changed_fields": fields,
                            "before_evidence": a["evidence"], "evidence": b["evidence"], "context": context})
    result = {"added": added, "removed": removed, "changed": changed,
              "before_coverage": coverage(before), "after_coverage": coverage(after),
              "note": "Differences are between supplied, normalized evidence. Missing from a capture does not prove a route was removed from a service. Row context is not exhaustive and does not establish causes; inspect before/after warnings. Coverage warnings and retention limits apply to the capture overall."}
    if before.get("evidence_limits") or after.get("evidence_limits"):
        result["evidence_limits"] = {"before": before.get("evidence_limits"), "after": after.get("evidence_limits")}
    old_targets = {row["path"]: row for row in before.get("unresolved_operations", [])}
    new_targets = {row["path"]: row for row in after.get("unresolved_operations", [])}
    if old_targets or new_targets:
        result["unresolved_added"] = [new_targets[path] for path in sorted(new_targets.keys() - old_targets.keys())]
        result["unresolved_removed"] = [old_targets[path] for path in sorted(old_targets.keys() - new_targets.keys())]
        result["unresolved_changed"] = []
        for path in sorted(old_targets.keys() & new_targets.keys()):
            a, b = old_targets[path], new_targets[path]
            fields = changed_fields(a, b, ("query_fields",))
            if fields:
                result["unresolved_changed"].append({"method": None, "path": path, "changed_fields": fields,
                                                     "before_evidence": a["evidence"], "evidence": b["evidence"]})
    return result
