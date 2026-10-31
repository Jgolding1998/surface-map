"""Comparison-local review identities. Private collector records never serialize."""
from __future__ import annotations

import json
import re
import time

from . import ci

MAX_RECORDS = 8192
MAX_EVIDENCE = 32
ANALYSIS_SECONDS = 30.0
CATEGORIES = ("endpoint", "form_field", "script", "navigation", "tracking", "document_base", "origin")
APIS = frozenset({"gtag_event", "posthog_capture", "analytics_track", "mixpanel_track",
                  "fbq_track", "fbq_track_custom", "data_layer_event", "navigator_send_beacon",
                  "gtag_config", "fbq_init", "analytics_page"})
ROOTS = frozenset({"gtag", "posthog", "analytics", "mixpanel", "fbq", "dataLayer", "navigator"})
_SOURCE = re.compile(r"source_[1-6]\Z")
_LOCATION = re.compile(r"line [1-9][0-9]{0,8}\Z")
_ID = re.compile(r"(?:signal|origin|target|path|field|event)_[1-9][0-9]{0,5}\Z")
_ATTRS = {
    "method": {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "UNKNOWN"},
    "origin_relation": {"same_origin", "cross_origin", "relative", "unknown"},
    "asset_kind": {"script", "stylesheet", "link", "embedded-document"},
    "input_type": {"hidden", "text", "email", "password", "number", "checkbox", "radio", "submit",
                   "button", "file", "search", "tel", "url", "date", "datetime-local", "time", "month",
                   "week", "color", "range", "reset", "image", "textarea", "select", "unknown", "mixed"},
    "api": APIS,
}
_BASELINE_LIMITS = {"tracking_static_syntax_only", "tracking_wrappers_and_aliases_not_resolved",
                    "document_path_unknown", "first_party_origin_unknown", "scheme_relative_origin_unclassified",
                    "forms_aggregated_by_target", "template_content_excluded", "non_http_references_excluded"}
SCOPE = (
    "Review hints from selected static source, not executed behavior, proven breakage or a security verdict. "
    "Opaque IDs are assigned jointly to the two versions and apply only to this comparison. "
    "Original domains, targets, field names, event names and values are not exported. "
    "Uninspected files, generated code, dynamic loaders, framework navigation, handler attributes and arbitrary "
    "tracking wrappers can remain outside coverage. Absolute origins are not classified as third party "
    "without caller-supplied site-origin context. Inferred SDK calls do not prove library identity or delivery."
)


def _tick(deadline):
    if time.monotonic() >= deadline:
        raise ValueError("Static review signals rejected: unsupported or over limit.")


def _freeze(value, depth=0):
    if depth > 8:
        raise ValueError
    if value is None or type(value) is bool:
        return value
    if type(value) is str and len(value) <= 4096:
        return value
    if type(value) in {list, tuple} and len(value) <= 64:
        return tuple(_freeze(item, depth + 1) for item in value)
    raise ValueError


class _Aliases:
    def __init__(self):
        self.tables = {}

    def get(self, kind, private):
        if private is None:
            return None
        key = _freeze(private)
        table = self.tables.setdefault(kind, {})
        if key not in table:
            if len(table) >= MAX_RECORDS * 2:
                raise ValueError
            table[key] = kind + "_" + str(len(table) + 1)
        return table[key]


def _evidence(value):
    if (type(value) is not dict or type(value.get("source")) is not str
            or not _SOURCE.fullmatch(value["source"]) or type(value.get("location")) is not str
            or not _LOCATION.fullmatch(value["location"])):
        raise ValueError
    return {"source": value["source"], "location": value["location"]}


def _attributes(record):
    raw = record.get("attrs", {})
    if type(raw) is not dict:
        raise ValueError
    result = {}
    for key, value in raw.items():
        if key in {"hidden", "event_literal"}:
            if type(value) is not bool:
                raise ValueError
        elif key not in _ATTRS or type(value) is not str or value not in _ATTRS[key]:
            raise ValueError
        result[key] = value
    if record.get("category") == "tracking":
        api = record.get("api")
        if api not in APIS or type(record.get("event_literal")) is not bool:
            raise ValueError
        result.update(api=api, event_literal=record["event_literal"])
    return result


def _index(records, aliases, *, site_origin, deadline):
    from .pr_html_signals import canonical_target

    groups = {category: {} for category in CATEGORIES}
    for record in records:
        _tick(deadline)
        category = record.get("category")
        if category not in CATEGORIES[:-1]:
            raise ValueError
        evidence = _evidence(record.get("evidence"))
        attributes = _attributes(record)
        field_method = attributes.get("method")
        private_identity = _freeze(record.get("identity"))
        if private_identity is None:
            raise ValueError
        origin, target, path, name = (record.get(key) for key in ("origin", "target", "path", "name"))
        event = None
        if category == "tracking":
            api, event = private_identity
            if api == "navigator_send_beacon" and event is not None:
                reference = canonical_target(event, site_origin=site_origin)
                if reference is None:
                    # Keep the syntactic call as unresolved, without storing a
                    # raw URL or implying that its destination was understood.
                    private_identity, event = (api, None), None
                    attributes["event_literal"] = False
                else:
                    target, origin, path = reference["identity"], reference["origin"], reference["path"]
                    attributes["origin_relation"] = reference["origin_relation"]
                    private_identity, event = (api, _freeze(reference["identity"])), None
        # Per-source multisets retain page-local disappearances even when the
        # same URL/event survives on another page. Fields are matched by private
        # name within a source so action/method changes do not invent lost fields.
        if category == "form_field":
            private_identity = _freeze(name)
            if private_identity is None:
                raise ValueError
            attributes = {key: value for key, value in attributes.items() if key in {"hidden", "input_type"}}
        key = (category, evidence["source"], private_identity)
        identifier = aliases.get("signal", key)
        confidence = record.get("confidence", "declared")
        if confidence not in {"declared", "inferred"}:
            raise ValueError
        assumptions = record.get("assumptions", [])
        if (type(assumptions) is not list or any(value not in {
                "unverified_global_tracking_sdk", "declared_tracking_import", "static_queue_shim_only",
                "unverified_global_axios"} for value in assumptions)):
            raise ValueError
        row = groups[category].setdefault(key, {
            "id": identifier, "count": 0, "attributes": [], "evidence": [],
            "confidence": confidence,
            "origin_id": aliases.get("origin", origin), "target_id": aliases.get("target", target),
            "path_id": aliases.get("path", path), "field_id": aliases.get("field", name),
            "event_id": aliases.get("event", event),
            "assumptions": sorted(set(assumptions)),
        })
        if category == "form_field":
            if field_method not in _ATTRS["method"] or target is None:
                raise ValueError
            association = {"target_id": aliases.get("target", target), "method": field_method,
                           "input_type": attributes.get("input_type", "unknown"),
                           "hidden": attributes.get("hidden", False)}
            associations = row.setdefault("associations", [])
            existing = next((item for item in associations if all(item[key] == value
                             for key, value in association.items())), None)
            if existing is None:
                associations.append({**association, "count": 1})
            else:
                existing["count"] += 1
        row["count"] += 1
        if attributes not in row["attributes"]:
            row["attributes"].append(attributes)
        if evidence not in row["evidence"]:
            if len(row["evidence"]) < MAX_EVIDENCE:
                row["evidence"].append(evidence)
            else:
                row["evidence_truncated"] = True
        if origin is not None:
            origin_key = ("origin", _freeze(origin))
            origin_row = groups["origin"].setdefault(origin_key, {
                "id": aliases.get("origin", origin), "count": 1,
                "attributes": [{"origin_relation": attributes.get("origin_relation", "unknown")}],
                "evidence": [], "confidence": confidence, "assumptions": sorted(set(assumptions)),
            })
            if evidence not in origin_row["evidence"]:
                if len(origin_row["evidence"]) < MAX_EVIDENCE:
                    origin_row["evidence"].append(evidence)
                else:
                    origin_row["evidence_truncated"] = True
    for group in groups.values():
        for row in group.values():
            row["attributes"].sort(key=lambda item: json.dumps(item, sort_keys=True))
            if "associations" in row:
                row["associations"].sort(key=lambda item: (item["target_id"], item["method"], item["input_type"], item["hidden"]))
            row["evidence"].sort(key=lambda item: (item["source"], int(item["location"][5:])))
    return groups


def compare_signals(before, after, *, javascript_engine="enhanced", site_origin=None, deadline=None, module_sources=()):
    """Compare private identities in memory, then return only an allowlisted projection."""
    try:
        from .pr_html_signals import collect_html, validate_site_origin
        from .pr_js_signals import collect_javascript

        import math
        if deadline is not None and (type(deadline) not in {int, float} or not math.isfinite(deadline)):
            raise ValueError
        deadline = deadline if deadline is not None else time.monotonic() + ANALYSIS_SECONDS
        origin = validate_site_origin(site_origin) if site_origin is not None else None
        if any(type(source) is not str or not _SOURCE.fullmatch(source) for source in module_sources):
            raise ValueError
        ci._validate_capture(before)
        ci._validate_capture(after)
        if ([item["kind"] for item in before["artifacts"]]
                != [item["kind"] for item in after["artifacts"]]):
            raise ValueError
        sides, warnings, notes, mutated = [], [], [], set()
        incomplete = {}
        source_counts = []
        tracking_engines = set()
        axios_state = {"global_axios_modified": False, "global_axios_lexical": False}
        omissions = []

        def warn(side, source, reason):
            warning = {"side": side, "source": source, "reason": reason}
            if warning not in warnings:
                warnings.append(warning)

        def mark_incomplete(source, categories, reason):
            entry = incomplete.setdefault(source, {"source": source, "categories": set(), "reasons": set()})
            entry["categories"].update(categories)
            entry["reasons"].add(reason)

        def scope_limits(collected, side, source, reason):
            limitations = collected.get("limitations", [])
            if type(limitations) is not list:
                raise ValueError
            for item in limitations:
                if item in _BASELINE_LIMITS:
                    if item not in notes:
                        notes.append(item)
                else:
                    warn(side, source, reason)
            if collected.get("omitted_count", 0):
                warn(side, source, reason)
            for item in collected.get("scope_notes", []):
                if item in _BASELINE_LIMITS and item not in notes:
                    notes.append(item)
            for key, category in (("tracking_identity_uncertain", "tracking"),
                                  ("axios_identity_uncertain", "endpoint")):
                if collected.get(key):
                    mark_incomplete(source, (category,), "global_client_identity_uncertain")
                    warn(side, source, "global_client_identity_uncertain")
            counts = {key: collected.get(key, 0) for key in ("omitted_count", "endpoint_omitted_count")}
            if any(type(count) is not int or not 0 <= count <= MAX_RECORDS * 2 for count in counts.values()):
                raise ValueError
            if any(counts.values()):
                omissions.append({"side": side, "source": source, **counts})

        def collect_tracking(text, source, kind, module_mode, offset=0):
            collected = collect_javascript(text, source, kind=kind, engine=javascript_engine,
                                           deadline=deadline, module_mode=module_mode)
            for key in axios_state:
                axios_state[key] |= bool(collected.get(key))
            tracking_engines.add(collected.get("engine", "unavailable"))
            for root in collected.get("mutated_roots", []):
                if root not in ROOTS:
                    raise ValueError
                mutated.add(root)
            rows = collected["records"]
            if type(rows) is not list:
                raise ValueError
            rows = [dict(row) for row in rows]
            from .pr_html_signals import canonical_target
            for call in collected.get("endpoint_calls", []):
                method = call.get("method")
                if method not in _ATTRS["method"]:
                    method = "UNKNOWN"
                target = canonical_target(call.get("url"), site_origin=origin)
                if target is None:
                    collected.setdefault("limitations", []).append("unsupported_endpoint_target")
                    continue
                rows.append({"category": "endpoint", "identity": (target["identity"], method),
                             "target": target["identity"], "origin": target["origin"], "path": target["path"],
                             "attrs": {"method": method, "origin_relation": target["origin_relation"]},
                             "evidence": call["evidence"], "confidence": "inferred",
                             "assumptions": call.get("assumptions", []),
                             "assumed_global_axios": "unverified_global_axios" in call.get("assumptions", []),
                             "assumed_bare_axios": bool(call.get("assumed_bare"))})
            if collected.get("endpoint_omitted_count", 0):
                collected.setdefault("limitations", []).append("endpoint_static_coverage_partial")
            if offset:
                for row in rows:
                    evidence = _evidence(row["evidence"])
                    row["evidence"] = {"source": evidence["source"],
                                       "location": "line " + str(int(evidence["location"][5:]) + offset)}
            collected["records"] = rows
            return collected
        for side, capture in enumerate((before, after)):
            records, counts = [], {"html": 0, "javascript": 0, "inline_javascript": 0}
            side_name = "before" if side == 0 else "after"
            for index, artifact in enumerate(capture["artifacts"], 1):
                source, kind = "source_" + str(index), artifact["kind"]
                if kind == "html":
                    counts["html"] += 1
                    try:
                        _tick(deadline)
                        collected = collect_html(artifact["text"], source, site_origin=origin, deadline=deadline)
                    except Exception:
                        mark_incomplete(source, CATEGORIES[:-1], "html_uninspected")
                        warn(side_name, source, "html_uninspected")
                        continue
                    warning_kind = "html_static_coverage_partial"
                    html_rows = list(collected["records"])
                    for script in collected.get("inline_scripts", []):
                        counts["inline_javascript"] += 1
                        try:
                            _tick(deadline)
                            inline = collect_tracking(script["text"], source, "javascript", script["module_mode"],
                                                      script["line_offset"])
                        except Exception:
                            mark_incomplete(source, ("tracking", "endpoint"), "inline_javascript_uninspected")
                            warn(side_name, source, "inline_javascript_uninspected")
                            tracking_engines.add("unavailable")
                            continue
                        html_rows.extend(inline["records"])
                        scope_limits(inline, side_name, source, "tracking_static_coverage_partial")
                    collected["records"] = html_rows
                elif kind in {"javascript", "typescript"}:
                    counts["javascript"] += 1
                    try:
                        _tick(deadline)
                        collected = collect_tracking(artifact["text"], source, kind, source in module_sources)
                    except Exception:
                        mark_incomplete(source, ("tracking", "endpoint"), "javascript_uninspected")
                        warn(side_name, source, "javascript_uninspected")
                        tracking_engines.add("unavailable")
                        continue
                    warning_kind = "tracking_static_coverage_partial"
                else:
                    raise ValueError
                scope_limits(collected, side_name, source, warning_kind)
                rows = collected["records"]
                if type(rows) is not list:
                    raise ValueError
                if len(records) + len(rows) > MAX_RECORDS:
                    mark_incomplete(source, CATEGORIES[:-1], "review_record_limit")
                    warn(side_name, source, "review_record_limit")
                    continue
                records.extend(rows)
            sides.append(records)
            source_counts.append(counts)
        if mutated:
            for index, records in enumerate(sides):
                filtered = []
                for row in records:
                    if (row.get("category") == "tracking" and row.get("ambient", True)
                            and row.get("sdk_root") in mutated):
                        source = _evidence(row["evidence"])["source"]
                        mark_incomplete(source, ("tracking",), "global_tracking_identity_uncertain")
                        warn("before" if index == 0 else "after", source, "global_tracking_identity_uncertain")
                    else:
                        filtered.append(row)
                sides[index] = filtered
        for index, records in enumerate(sides):
            for row in records:
                if row.get("category") == "endpoint" and row.get("assumed_global_axios") and (
                        axios_state["global_axios_modified"] or
                        axios_state["global_axios_lexical"] and row.get("assumed_bare_axios")):
                    source = _evidence(row["evidence"])["source"]
                    mark_incomplete(source, ("endpoint",), "global_client_identity_uncertain")
                    warn("before" if index == 0 else "after", source, "global_client_identity_uncertain")
        # One-sided parse/limit failures must never fabricate disappearances.
        # Suppress the affected category for that source on BOTH sides.
        for index, records in enumerate(sides):
            sides[index] = [row for row in records if row.get("category") not in
                            incomplete.get(_evidence(row["evidence"])["source"], {}).get("categories", set())]
        aliases = _Aliases()
        # Indexing has a separate bounded reserve after a collection deadline.
        old, new = [_index(records, aliases, site_origin=origin, deadline=time.monotonic() + 3.0) for records in sides]
        changes = {}
        for category in CATEGORIES:
            a, b = old[category], new[category]
            if category == "origin" and incomplete:
                # Origins are a union across sources. Any incomplete reference
                # category makes absence/newness in that union uncertain.
                a, b = {}, {}
            order = lambda key: (a.get(key) or b[key])["id"]
            added = [b[key] for key in sorted(b.keys() - a.keys(), key=order)]
            absent = [a[key] for key in sorted(a.keys() - b.keys(), key=order)]
            changed = []
            for key in sorted(a.keys() & b.keys(), key=order):
                fields = [name for name, first, second in (
                    ("occurrences", a[key]["count"], b[key]["count"]),
                    ("attributes", a[key]["attributes"], b[key]["attributes"]),
                    ("associations", a[key].get("associations", []), b[key].get("associations", [])))
                    if first != second]
                if fields:
                    changed.append({**b[key], "before_count": a[key]["count"],
                                    "changed_fields": fields,
                                    "before_attributes": a[key]["attributes"], "before_evidence": a[key]["evidence"],
                                    "before_associations": a[key].get("associations", [])})
            changes[category] = {"added": added, "removed": absent, "changed": changed,
                                 "unchanged_count": len(a.keys() & b.keys()) - len(changed)}
        incomplete_sources = [{"source": source, "categories": sorted(entry["categories"] | {"origin"}),
                               "reasons": sorted(entry["reasons"])} for source, entry in sorted(incomplete.items())]
        tracking_incomplete = any("tracking" in entry["categories"] for entry in incomplete_sources)
        result = {"format": "surface-map-pr-review-signals-v1", "comparison": changes,
                  "coverage": {"before": source_counts[0], "after": source_counts[1],
                               "site_origin_supplied": origin is not None,
                               "tracking_analysis": ("not_applicable" if not tracking_engines and not tracking_incomplete else
                                   "enhanced_static_hints" if tracking_engines == {"enhanced"} and not tracking_incomplete else
                                   "unavailable_or_partial"),
                               "warnings": warnings, "scope_notes": sorted(notes),
                               "omissions": omissions,
                               "incomplete_sources": incomplete_sources,
                               "selected_static_source_only": True},
                  "scope": SCOPE}
        ci.json_output(result)
        return result
    except Exception:
        raise ValueError("Static review signals rejected: unsupported, ambiguous or over limit. No input text or paths are returned.") from None


def policy_hits(result):
    groups = result["comparison"].values()
    return {"added": any(row["added"] for row in groups),
            "absent": any(row["removed"] for row in result["comparison"].values()),
            "changed": any(row["changed"] for row in result["comparison"].values()),
            "warnings": bool(result["coverage"]["warnings"]),
            "incomplete": bool(result["coverage"].get("incomplete_sources"))}


def incomplete_signals(source_count, *, site_origin_supplied=False):
    """Small fixed fallback preserves the compatibility report on review failure."""
    if (type(source_count) is not int or not 1 <= source_count <= 6
            or type(site_origin_supplied) is not bool):
        raise ValueError
    return {"format": "surface-map-pr-review-signals-v1",
            "comparison": {category: {"added": [], "removed": [], "changed": [], "unchanged_count": 0}
                           for category in CATEGORIES},
            "coverage": {"before": {}, "after": {}, "site_origin_supplied": site_origin_supplied,
                         "tracking_analysis": "unavailable_or_partial", "scope_notes": [],
                         "warnings": [{"side": "both", "source": "selection", "reason": "review_unavailable"}],
                         "incomplete_sources": [{"source": "source_" + str(index), "categories": list(CATEGORIES),
                                                 "reasons": ["review_unavailable"]}
                                                for index in range(1, source_count + 1)],
                         "selected_static_source_only": True}, "scope": SCOPE}


def markdown(result):
    if result.get("format") != "surface-map-pr-review-signals-v1":
        raise ValueError("Invalid static review signals.")
    lines = ["## Static changes to review", "", SCOPE, "",
             "Site-origin classification context supplied: " + str(result["coverage"]["site_origin_supplied"]).lower() + ".",
             "Tracking coverage: " + ci._cell(result["coverage"]["tracking_analysis"]) + ".", ""]
    if result["coverage"].get("incomplete_sources"):
        lines.extend(["**Incomplete comparison.** Affected source categories were excluded on both versions. "
                      "Their absence is unknown; the combined check cannot pass. Origin changes are also withheld "
                      "because origins are shared across sources.", ""])
    for category in CATEGORIES:
        group = result["comparison"][category]
        lines.extend(["### " + category.replace("_", " ").title(), "",
                      "Added: " + str(len(group["added"])) + ". Absent: " + str(len(group["removed"]))
                      + ". Changed: " + str(len(group["changed"])) + "."])
        rows = [(label, row) for label, key in (("Added", "added"), ("Absent", "removed"), ("Changed", "changed"))
                for row in group[key]]
        if rows:
            lines.extend(["", "| Change | Opaque reference | Attributes | Evidence |", "| --- | --- | --- | --- |"])
            for label, row in rows[:ci.MAX_MARKDOWN_ROWS]:
                references = " ".join(str(row[key]) for key in ("id", "origin_id", "target_id", "path_id", "field_id", "event_id")
                                      if row.get(key) is not None)
                for value in references.split():
                    if not _ID.fullmatch(value):
                        raise ValueError
                attributes = json.dumps(row["attributes"], sort_keys=True, separators=(",", ":"))
                if "before_attributes" in row:
                    attributes = json.dumps(row["before_attributes"], sort_keys=True, separators=(",", ":")) + " -> " + attributes
                if row.get("associations"):
                    associations = json.dumps(row["associations"], sort_keys=True, separators=(",", ":"))
                    if "before_associations" in row:
                        associations = json.dumps(row["before_associations"], sort_keys=True, separators=(",", ":")) + " -> " + associations
                    attributes += " associations: " + associations
                if row.get("evidence_truncated"):
                    attributes += " evidence locations capped; occurrence counts retained"
                evidence = ("base: " if label == "Absent" else "head: ") + ci._references(row["evidence"])
                if "before_evidence" in row:
                    evidence = "base: " + ci._references(row["before_evidence"]) + "; " + evidence
                lines.append("| " + " | ".join((label, ci._cell(references), ci._cell(attributes), evidence)) + " |")
            if len(rows) > ci.MAX_MARKDOWN_ROWS:
                lines.extend(["", str(len(rows) - ci.MAX_MARKDOWN_ROWS) + " additional rows omitted; retain JSON."])
        lines.append("")
    warnings = result["coverage"]["warnings"]
    if warnings:
        lines.extend(["### Coverage warnings", "", "| Side | Source | Reason |", "| --- | --- | --- |"])
        for warning in warnings:
            lines.append("| " + " | ".join(ci._cell(warning[key]) for key in ("side", "source", "reason")) + " |")
    rendered = "\n".join(lines) + "\n"
    if len(rendered.encode("utf-8")) > ci.MAX_OUTPUT_BYTES:
        raise ValueError("Static review output exceeded its limit.")
    return rendered
