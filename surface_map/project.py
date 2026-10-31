"""Bounded inventory and literal import links for explicitly supplied project files."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import posixpath
import re
import time

from .analyze import ArtifactError
from .source import (SOURCE_KINDS, MAX_ARTIFACTS as GROUP_FILES, MAX_FILE_BYTES,
                     MAX_TOTAL_BYTES as GROUP_BYTES, MAX_EVIDENCE, source_inventory)
from .source_io import read_ordinary, source_kind

MAX_FILES = 24
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_EDGES = 200
MAX_WARNINGS = 100
WORK_SECONDS = 10.0
_PRIVATE_PARTS = frozenset({"credentials", "secrets", "private", "keys", "certs", "node_modules",
                          "venv", "env", "__pycache__", "dist", "build", "coverage"})
_MATERIAL = re.compile(r"(?:credentials?|secrets?|private[-_]keys?|api[-_]?keys?|tokens?|passwords?|"
                       r"service[-_]accounts?|id_rsa|id_ed25519)(?:[._-]|$)", re.I)
_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.I)
_SOURCE_ID = re.compile(r"\bsource_([1-9][0-9]*)\b")
PRIVACY = ("Only curated inventory labels, generic source IDs, line numbers and fixed omission reasons are returned. "
           "Logical paths, filenames, source text, private package names and module specifiers remain internal. "
           "Supplying contents to an AI client is separate from output minimization.")
GRAPH_SCOPE = ("Candidate literal links between supplied JavaScript/TypeScript files only. "
               "No files are discovered, fetched, imported or executed. Exact relative specifiers are matched; "
               "extension guessing, package resolution, import maps, require calls, escaped/template/computed "
               "specifiers and source maps are not resolved. Links do not prove runtime loading. "
               "Generic IDs are local to this selection and are not stable project identities.")


def _logical_path(value):
    if (not isinstance(value, str) or not value or len(value) > 256 or value.startswith(("/", "~"))
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(char in value for char in "\\:%?#")):
        raise ArtifactError("Project paths must be portable relative file identifiers")
    parts = value.split("/")
    if any(not part or part in {".", ".."} or part.startswith(" ") or part.endswith((".", " "))
           or _DEVICE.match(part) for part in parts):
        raise ArtifactError("Project paths must be normalized within the selected root")
    lowered = [part.lower() for part in parts]
    if (any(part.startswith(".") or part in _PRIVATE_PARTS for part in lowered)
            or _MATERIAL.search(lowered[-1])
            or lowered[-1].endswith((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))):
        raise ArtifactError("Private, credential, generated or dependency paths are excluded from project selections")
    return value


def _end(deadline):
    end = time.monotonic() + WORK_SECONDS
    if deadline is not None:
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not (-float("inf") < deadline < float("inf")):
            raise ArtifactError("Project deadline must be a finite monotonic time")
        end = min(end, deadline)
    if time.monotonic() >= end:
        raise ArtifactError("Project inspection exceeded its work deadline")
    return end


def _prepare(files, end):
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ArtifactError("Choose 1 to 24 explicitly selected project files")
    prepared, seen, total = [], set(), 0
    for item in files:
        if time.monotonic() >= end:
            raise ArtifactError("Project inspection exceeded its work deadline")
        if not isinstance(item, dict) or set(item) != {"kind", "content", "logical_path"}:
            raise ArtifactError("Each project file needs exactly kind, content and logical_path")
        path = _logical_path(item["logical_path"])
        kind, content = item["kind"], item["content"]
        if kind not in SOURCE_KINDS or source_kind(path) != kind:
            raise ArtifactError("Project file kind and supported extension must agree")
        if path.casefold() in seen:
            raise ArtifactError("Project file identifiers must be unique, including case aliases")
        seen.add(path.casefold())
        if not isinstance(content, str) or len(content) > MAX_FILE_BYTES:
            raise ArtifactError("Project source exceeds the text or per-file byte limit")
        try:
            size = len(content.encode("utf-8"))
        except UnicodeError:
            raise ArtifactError("Project source contains unsupported Unicode") from None
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ArtifactError("Project selection exceeds its byte limits")
        prepared.append({**item, "id": f"source_{len(prepared) + 1}", "bytes": size})
    return prepared


def _groups(files):
    batch, size = [], 0
    for item in files:
        if batch and (len(batch) == GROUP_FILES or size + item["bytes"] > GROUP_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(item)
        size += item["bytes"]
    if batch:
        yield batch


def _merge(stores, report, ids, omissions):
    for group, store in stores.items():
        for row in report[group]:
            key = (row.get("ecosystem", ""), row["name"])
            incoming = {**row, "evidence": [{**item, "source": ids[item["source"]]} for item in row["evidence"]]}
            current = store.get(key)
            if current is None:
                store[key] = incoming
                continue
            if incoming.get("confidence") == "declared" and current.get("confidence") == "inferred":
                omissions["superseded_evidence"] += len(current["evidence"])
                store[key] = incoming
                continue
            if incoming.get("confidence") == "inferred" and current.get("confidence") == "declared":
                omissions["superseded_evidence"] += len(incoming["evidence"])
                continue
            for item in incoming["evidence"]:
                if item not in current["evidence"]:
                    if len(current["evidence"]) < MAX_EVIDENCE:
                        current["evidence"].append(item)
                    else:
                        omissions["inventory_evidence_limit"] += 1


def _graph(files, sources, end):
    from .references import _iter_module_specifiers

    nodes = [{"id": item["id"], "kind": item["kind"]} for item in files]
    lookup = {item["logical_path"]: item for item in files}
    status = {item["id"]: item["status"] for item in sources}
    edges, seen = [], set()
    omitted = Counter({name: 0 for name in ("outside_root", "missing_selected_target", "external_or_package",
                                           "unsupported_reference", "edge_limit", "unparsed_sources", "work_limit")})
    script_files = [item for item in files if item["kind"] in {"javascript", "typescript"}]
    for index, item in enumerate(script_files):
        if time.monotonic() >= end:
            omitted["work_limit"] += len(script_files) - index
            break
        if status[item["id"]] != "parsed":
            omitted["unparsed_sources"] += 1
            continue
        try:
            references = _iter_module_specifiers(item["content"], deadline=end)
            for raw, line, relation in references:
                if not raw.startswith(("./", "../")):
                    omitted["external_or_package"] += 1
                    continue
                if (len(raw) > 256 or any(char in raw for char in "\\:%?#")
                        or any(ord(char) < 32 or ord(char) == 127 for char in raw)):
                    omitted["unsupported_reference"] += 1
                    continue
                target_path = posixpath.normpath(posixpath.join(posixpath.dirname(item["logical_path"]), raw))
                if target_path == ".." or target_path.startswith("../") or target_path.startswith("/"):
                    omitted["outside_root"] += 1
                    continue
                target = lookup.get(target_path)
                if target is None:
                    omitted["missing_selected_target"] += 1
                    continue
                if target["kind"] not in {"javascript", "typescript"}:
                    omitted["unsupported_reference"] += 1
                    continue
                key = (item["id"], target["id"], line, relation)
                if key in seen:
                    continue
                if len(edges) == MAX_EDGES:
                    omitted["edge_limit"] += 1
                    continue
                seen.add(key)
                edges.append({"source": item["id"], "target": target["id"], "line": line, "relation": relation})
        except ArtifactError:
            omitted["work_limit" if time.monotonic() >= end else "unparsed_sources"] += 1
    return {"nodes": nodes, "edges": edges, "omissions": dict(omitted),
            "truncated": any(omitted[name] for name in ("edge_limit", "unparsed_sources", "work_limit")),
            "omission_scope": "Reference counts cover recognized literal specifiers only; unsupported syntax may be absent without a count. Work-limit and unparsed counts describe sources.",
            "scope": GRAPH_SCOPE}


def inspect_project(files, *, javascript_engine="auto", deadline=None):
    if not isinstance(javascript_engine, str) or javascript_engine not in {"auto", "basic", "enhanced"}:
        raise ArtifactError("Unsupported JavaScript analysis engine")
    end = _end(deadline)
    prepared = _prepare(files, end)
    stores = {name: {} for name in ("languages", "dependencies", "capabilities", "install_hints")}
    sources, warnings, groups = [], [], 0
    omissions = Counter(inventory_evidence_limit=0, superseded_evidence=0, warning_limit=0)
    for batch in _groups(prepared):
        if time.monotonic() >= end:
            break
        report = source_inventory([{"kind": item["kind"], "content": item["content"]} for item in batch],
                                  javascript_engine=javascript_engine)
        groups += 1
        ids = {f"source_{index}": item["id"] for index, item in enumerate(batch, 1)}
        sources.extend({**row, "id": ids[row["id"]]} for row in report["sources"])
        _merge(stores, report, ids, omissions)
        for warning in report["warnings"]:
            warning = _SOURCE_ID.sub(lambda match: ids.get(match.group(), "source_unknown"), warning)
            if len(warnings) < MAX_WARNINGS:
                warnings.append(warning)
            else:
                omissions["warning_limit"] += 1
    inspected = len(sources)
    sources.extend({"id": item["id"], "kind": item["kind"], "bytes": item["bytes"], "status": "omitted"}
                   for item in prepared[inspected:])
    if inspected != len(prepared):
        if len(warnings) < MAX_WARNINGS:
            warnings.append("Some selected sources were omitted after the shared work deadline.")
        else:
            omissions["warning_limit"] += 1
    graph = _graph(prepared, sources, end)
    partial = any(row["status"] != "parsed" for row in sources) or graph["truncated"]
    return {"format": "surface-map-project-v1", "sources": sources,
            **{name: [store[key] for key in sorted(store)] for name, store in stores.items()},
            "warnings": warnings, "graph": graph, "omissions": dict(omissions),
            "coverage": {"selected_files": len(prepared), "inspected_files": inspected,
                         "rejected_files": sum(row["status"] == "rejected" for row in sources),
                         "omitted_files": len(prepared) - inspected, "groups": groups, "partial": partial},
            "limits": {"max_files": MAX_FILES, "file_bytes": MAX_FILE_BYTES, "total_bytes": MAX_TOTAL_BYTES,
                       "max_edges": MAX_EDGES, "max_evidence_per_row": MAX_EVIDENCE, "work_seconds": WORK_SECONDS,
                       "deadline_scope": "Checked between groups and graph scans. An in-flight source group retains its existing 5-second parser budget.",
                       "omission_scope": "Inventory omission counts cover merging retained group evidence, not occurrences already capped inside a group."},
            "privacy": PRIVACY}


def selected_project(paths, *, root=None):
    """Read explicit ordinary files beneath an explicit local root, without walking it."""
    if root is None or not isinstance(paths, (list, tuple)) or not 1 <= len(paths) <= MAX_FILES:
        raise ArtifactError("Provide an explicit root and 1 to 24 selected files")
    try:
        root = Path(root).absolute()
        if ".." in root.parts:
            raise ValueError
        selected, seen = [], set()
        for value in paths:
            path = Path(value)
            path = path.absolute() if path.is_absolute() else (root / path).absolute()
            relative = path.relative_to(root).as_posix()
            logical = _logical_path(relative)
            if logical.casefold() in seen:
                raise ValueError
            seen.add(logical.casefold())
            kind = source_kind(logical)
            if kind is None:
                raise ValueError
            selected.append((path, kind, logical))
        result, total = [], 0
        for path, kind, logical in selected:
            content = read_ordinary(path, MAX_FILE_BYTES)
            total += len(content.encode("utf-8"))
            if total > MAX_TOTAL_BYTES:
                raise ValueError
            result.append({"kind": kind, "content": content, "logical_path": logical})
        return result
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ArtifactError("Choose supported ordinary local files strictly within the explicit project root") from None
