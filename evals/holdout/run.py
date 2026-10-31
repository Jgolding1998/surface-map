"""Validate and score saved captures without downloading data or calling a model."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import socket
import stat
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

CORPUS_SCHEMA = "surface-map-holdout-corpus/1"
LABEL_SCHEMA = "surface-map-holdout-labels/1"
INPUT_SCHEMA = "surface-map-holdout-inputs/1"
PREDICTION_SCHEMA = "surface-map-holdout-predictions/1"
RESULT_SCHEMA = "surface-map-holdout-results/1"
KINDS = {"html", "har", "javascript", "typescript"}
EVIDENCE_KINDS = {"link", "form", "har", "javascript"}
CONFIDENCE = {"declared", "inferred", "observed"}
MAX_JSON = 16 * 1024 * 1024
MAX_CAPTURE = 4 * 1024 * 1024
MAX_CASES = 10000
ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")


class EvaluationError(ValueError):
    """A fixed, source-free validation error suitable for CLI output."""


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or set(value) - set(required) - set(optional):
        raise EvaluationError("Invalid or unknown object fields")


def _string(value, maximum=2048, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise EvaluationError("Invalid text field")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise EvaluationError("Invalid UTF-8 text") from None
    return value


def _identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise EvaluationError("Invalid identifier")
    return value


def _date(value):
    _string(value, 40)
    try:
        if len(value) == 10:
            date.fromisoformat(value)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
    except ValueError:
        raise EvaluationError("Invalid date or timezone-aware timestamp") from None


def _url(value):
    _string(value)
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        parsed.port
    except ValueError:
        raise EvaluationError("Invalid provenance or document URL") from None
    return value


def _provenance(value):
    _keys(value, {"url", "revision", "license", "retrieved_at"})
    _url(value["url"])
    _string(value["revision"], 256)
    _string(value["license"], 256)
    _date(value["retrieved_at"])


def provenance_identity(url):
    """Identify known forge repositories independently of revision/file URLs.

    GitHub and Bitbucket use the first owner/repository path pair. Modern
    GitLab URLs retain nested namespaces before the reserved ``/-/`` marker.
    Unknown forges and ordinary sites retain the previous URL identity; their
    project independence still relies on honest group declarations. This does
    not discover forks, mirrors or repositories hosted under unrelated domains.
    """
    _url(url)
    parsed = urlsplit(url)
    host = parsed.hostname.casefold()
    pieces = [part.casefold() for part in unquote(parsed.path, errors="strict").split("/") if part]
    if host in {"github.com", "www.github.com", "raw.githubusercontent.com"} and len(pieces) >= 2:
        owner, repository = pieces[:2]
        return ("github", owner, repository.removesuffix(".git"))
    if host in {"bitbucket.org", "www.bitbucket.org"} and len(pieces) >= 2:
        owner, repository = pieces[:2]
        return ("bitbucket", owner, repository.removesuffix(".git"))
    if host in {"gitlab.com", "www.gitlab.com"}:
        project = pieces[:pieces.index("-")] if "-" in pieces else pieces
        if len(project) >= 2:
            project[-1] = project[-1].removesuffix(".git")
            return ("gitlab", *project)
    return ("url", parsed.netloc.casefold(), parsed.path.rstrip("/").removesuffix(".git").casefold())


def _register_provenance(value, group, registry):
    _provenance(value)
    identity = provenance_identity(value["url"])
    if identity in registry and registry[identity] != group:
        raise EvaluationError("The same provenance project has multiple group IDs")
    registry[identity] = group


def _review(value):
    _keys(value, {"status", "reviewer", "reviewed_at", "notes"})
    if value["status"] not in {"unreviewed", "agent-reviewed", "human-reviewed"}:
        raise EvaluationError("Invalid label review status")
    _string(value["reviewer"], 256, empty=value["status"] == "unreviewed")
    _string(value["notes"], 4000, empty=True)
    if value["reviewed_at"] is None:
        if value["status"] != "unreviewed":
            raise EvaluationError("Reviewed labels need a review date")
    else:
        _date(value["reviewed_at"])


def _read(path, limit):
    """Read ordinary local files; reject links/reparse points in every ancestor."""
    path = Path(os.path.abspath(path))
    if str(path).startswith(("\\\\", "//")):
        raise EvaluationError("Network file paths are unsupported")
    try:
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise EvaluationError("Linked corpus paths are unsupported")
        if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > limit:
            raise EvaluationError("Corpus file type or size limit exceeded")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise EvaluationError("Corpus file size limit exceeded")
        return data
    except OSError:
        raise EvaluationError("Corpus file cannot be read") from None


def _json_bytes(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EvaluationError("Duplicate JSON object key")
            result[key] = value
        return result
    def constant(_):
        raise EvaluationError("Non-finite JSON number")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, EvaluationError):
            raise
        raise EvaluationError("Invalid JSON document") from None


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sorted(values):
    return sorted(values, key=_canonical)


def _list(value, maximum=10000):
    if not isinstance(value, list) or len(value) > maximum:
        raise EvaluationError("Invalid or oversized list")
    return value


def _unique(values):
    serialized = [_canonical(value) for value in values]
    if len(serialized) != len(set(serialized)):
        raise EvaluationError("Duplicate list member")


def _evidence(value):
    result = []
    for item in _list(value, 256):
        _keys(item, {"source", "location", "kind"})
        if not isinstance(item["source"], str) or not re.fullmatch(r"source_[1-6]", item["source"]):
            raise EvaluationError("Invalid source evidence ID")
        if item["kind"] not in EVIDENCE_KINDS:
            raise EvaluationError("Invalid source evidence kind")
        _string(item["location"], 128)
        result.append(item)
    _unique(result)
    return _sorted(result)


def _route(value, *, unresolved=False):
    required = {"path", "confidence", "evidence_kinds"} | (set() if unresolved else {"method"})
    _keys(value, required, {"evidence"})
    _string(value["path"], 2048)
    if not value["path"].startswith("/") or any(c.isspace() for c in value["path"]):
        raise EvaluationError("Invalid normalized route path")
    if not unresolved and (not isinstance(value["method"], str) or not re.fullmatch(r"[A-Z]{1,20}", value["method"])):
        raise EvaluationError("Invalid route method")
    if value["confidence"] not in ({"inferred"} if unresolved else CONFIDENCE):
        raise EvaluationError("Invalid evidence confidence")
    kinds = _list(value["evidence_kinds"], 4)
    if not kinds or any(not isinstance(kind, str) or kind not in EVIDENCE_KINDS for kind in kinds):
        raise EvaluationError("Invalid evidence kinds")
    _unique(kinds)
    result = {**value, "evidence_kinds": sorted(kinds)}
    if unresolved and kinds != ["javascript"]:
        raise EvaluationError("Unresolved targets require JavaScript evidence")
    if "evidence" in value:
        result["evidence"] = _evidence(value["evidence"])
        if not result["evidence"] or not {item["kind"] for item in result["evidence"]} <= set(kinds):
            raise EvaluationError("Evidence locations and kinds disagree")
    return result


def _canonical_prediction(value):
    """Validate every field; retain unexpected operations for scoring."""
    if isinstance(value, dict) and set(value) == {"rejected"} and value["rejected"] is True:
        return {"rejected": True}
    _keys(value, {"routes", "unresolved"})
    routes = [_route(row) for row in _list(value["routes"], 1000)]
    unresolved = [_route(row, unresolved=True) for row in _list(value["unresolved"], 1000)]
    for rows, keys in ((routes, ("method", "path")), (unresolved, ("path",))):
        identities = [tuple(row[key] for key in keys) for row in rows]
        if len(identities) != len(set(identities)):
            raise EvaluationError("Duplicate route or unresolved target")
    return {"routes": _sorted(routes), "unresolved": _sorted(unresolved)}


def canonical_prediction(value):
    try:
        return _canonical_prediction(value)
    except (TypeError, KeyError, RecursionError, OverflowError):
        raise EvaluationError("Invalid prediction structure") from None


def _capture_path(root, name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise EvaluationError("Invalid relative capture path")
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(part in {".", ".."} for part in name.split("/")) or "" in name.split("/"):
        raise EvaluationError("Invalid relative capture path")
    return root.joinpath(*relative.parts)


def _load_corpus(path=None, labels_path=None):
    """Validate provenance, exact capture hashes, labels and project separation."""
    path = Path(path) if path is not None else Path(__file__).with_name("corpus.json")
    labels_path = Path(labels_path) if labels_path is not None else path.with_name("labels.json")
    raw = _read(path, MAX_JSON).replace(b"\r\n", b"\n")
    corpus = _json_bytes(raw)
    _keys(corpus, {"schema", "name", "projects", "cases"})
    if corpus["schema"] != CORPUS_SCHEMA:
        raise EvaluationError("Unsupported corpus schema")
    _string(corpus["name"], 256)
    projects, groups, provenance_groups = {}, {}, {}
    for project in _list(corpus["projects"], MAX_CASES):
        _keys(project, {"id", "group", "split", "kind", "provenance"}, {"exposure"})
        identity = _identifier(project["id"])
        group = _string(project["group"], 256)
        if group != group.casefold().rstrip("/") or not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]{0,255}", group):
            raise EvaluationError("Project groups must be canonical lowercase identifiers")
        if project["split"] not in {"development", "holdout"} or project["kind"] not in {"real", "synthetic"}:
            raise EvaluationError("Invalid project split or kind")
        if project.get("exposure", "unknown") not in {"unseen", "exposed", "unknown"}:
            raise EvaluationError("Invalid project exposure declaration")
        if project["kind"] == "synthetic" and project["split"] == "holdout":
            raise EvaluationError("Synthetic projects cannot be independent holdout evidence")
        if identity in projects:
            raise EvaluationError("Duplicate project ID")
        declaration = (project["split"], project["kind"], project.get("exposure", "unknown"))
        if group in groups and groups[group] != declaration:
            raise EvaluationError("Project group leaks across splits, kinds or exposure declarations")
        _register_provenance(project["provenance"], group, provenance_groups)
        groups[group] = declaration
        projects[identity] = project
    cases, ids, capture_splits, used_projects = [], set(), {}, set()
    for case in _list(corpus["cases"], MAX_CASES):
        _keys(case, {"id", "project", "family", "artifacts"}, {"document_url"})
        identity = _identifier(case["id"])
        _identifier(case["family"])
        if identity in ids or case["project"] not in projects:
            raise EvaluationError("Duplicate case ID or unknown project")
        ids.add(identity)
        used_projects.add(case["project"])
        project = projects[case["project"]]
        artifacts, captures, total = [], [], 0
        if not 1 <= len(_list(case["artifacts"], 6)) <= 6:
            raise EvaluationError("A case requires one to six artifacts")
        for item in case["artifacts"]:
            _keys(item, {"kind", "path", "sha256"}, {"provenance"})
            if item["kind"] not in KINDS or not isinstance(item["sha256"], str) or not SHA.fullmatch(item["sha256"]):
                raise EvaluationError("Invalid capture kind or SHA-256")
            if "provenance" in item:
                _register_provenance(item["provenance"], project["group"], provenance_groups)
            data = _read(_capture_path(path.parent, item["path"]), MAX_CAPTURE if item["kind"] == "html" else 512 * 1024)
            total += len(data)
            if _digest(data) != item["sha256"]:
                raise EvaluationError("Capture SHA-256 mismatch")
            if total > MAX_CAPTURE:
                raise EvaluationError("Combined case capture size limit exceeded")
            prior = capture_splits.setdefault(item["sha256"], project["split"])
            if prior != project["split"]:
                raise EvaluationError("Identical capture bytes leak across splits")
            try:
                content = data.decode("utf-8")
            except UnicodeError:
                raise EvaluationError("Capture is not strict UTF-8") from None
            artifacts.append({"kind": item["kind"], "text": content})
            captures.append({**item, "provenance": item.get("provenance", project["provenance"])})
        if "document_url" in case:
            _url(case["document_url"])
        cases.append({**case, "artifacts": artifacts, "captures": captures})
    if not cases or not projects or used_projects != set(projects):
        raise EvaluationError("Corpus must contain cases and no unused projects")
    label_raw = _read(labels_path, MAX_JSON).replace(b"\r\n", b"\n")
    labels = _json_bytes(label_raw)
    _keys(labels, {"schema", "corpus_sha256", "labels"})
    if labels["schema"] != LABEL_SCHEMA or labels["corpus_sha256"] != _digest(raw):
        raise EvaluationError("Label schema or corpus digest mismatch")
    indexed = {}
    for label in _list(labels["labels"], MAX_CASES):
        _keys(label, {"id", "review", "expected"})
        if label["id"] not in ids or label["id"] in indexed:
            raise EvaluationError("Duplicate or unknown label ID")
        _review(label["review"])
        indexed[label["id"]] = {**label, "expected": canonical_prediction(label["expected"])}
    if set(indexed) != ids:
        raise EvaluationError("Every case requires exactly one label")
    return {"name": corpus["name"], "sha256": _digest(raw), "labels_sha256": _digest(label_raw),
            "projects": projects, "cases": [{**case, **indexed[case["id"]]} for case in cases]}


def load_corpus(path=None, labels_path=None):
    try:
        return _load_corpus(path, labels_path)
    except EvaluationError:
        raise
    except (TypeError, ValueError, KeyError, RecursionError, OverflowError):
        raise EvaluationError("Invalid corpus or label structure") from None


def select_cases(corpus, split="all"):
    if split not in {"all", "development", "holdout"}:
        raise EvaluationError("Invalid split selection")
    cases = [case for case in corpus["cases"] if split == "all" or corpus["projects"][case["project"]]["split"] == split]
    if not cases:
        raise EvaluationError("The selected split has no cases")
    return cases


def _opaque_id(corpus, case):
    return "case_" + _digest((corpus["sha256"] + ":" + case["id"]).encode("utf-8"))[:20]


def export_inputs(corpus, split="all"):
    """Export only opaque IDs, capture contents and required document context."""
    cases = []
    for case in select_cases(corpus, split):
        value = {"id": _opaque_id(corpus, case), "artifacts": case["artifacts"]}
        if "document_url" in case:
            value["document_url"] = case["document_url"]
        cases.append(value)
    bundle = {"schema": INPUT_SCHEMA, "cases": sorted(cases, key=lambda item: item["id"])}
    return {**bundle, "input_sha256": _digest(_canonical(bundle).encode("utf-8"))}


def project_report(report):
    """Project only scored route facts, retaining complete route sets."""
    def route(row, unresolved=False):
        evidence = [{key: item[key] for key in ("source", "location", "kind")} for item in row["evidence"]]
        evidence = [json.loads(item) for item in sorted({_canonical(item) for item in evidence})]
        value = {"path": row["path"], "confidence": row["confidence"],
                 "evidence_kinds": sorted(set(row.get("kinds", [item["kind"] for item in evidence]))),
                 "evidence": evidence}
        if not unresolved:
            value["method"] = row["method"]
        return value
    return canonical_prediction({"routes": [route(row) for row in report["routes"]],
                                 "unresolved": [route(row, True) for row in report.get("unresolved_operations", [])]})


def score(expected, observed):
    if isinstance(observed, dict) and observed.get("error"):
        return ["evaluation_error"]
    try:
        expected, observed = canonical_prediction(expected), canonical_prediction(observed)
    except EvaluationError:
        return ["invalid_prediction"]
    if expected.get("rejected"):
        return [] if observed == {"rejected": True} else ["expected_rejection"]
    if observed.get("rejected"):
        return ["unexpected_rejection"]
    failures = []
    for field, identity in (("routes", lambda row: (row["method"], row["path"])),
                            ("unresolved", lambda row: (row["path"],))):
        wanted = {identity(row): row for row in expected[field]}
        actual = {identity(row): row for row in observed[field]}
        if set(wanted) != set(actual):
            failures.append(field)
        for key in wanted.keys() & actual.keys():
            keys = set(wanted[key])
            if any(actual[key].get(name) != wanted[key][name] for name in keys):
                failures.append(field + "_evidence")
    return sorted(set(failures))


def _metrics(expected, observed, field):
    def identities(value):
        return {(row["method"], row["path"]) if field == "routes" else (row["path"],)
                for row in value.get(field, [])}
    wanted, actual = identities(expected), identities(observed)
    return Counter(tp=len(wanted & actual), fp=len(actual - wanted), fn=len(wanted - actual))


def _rate(counts):
    tp, fp, fn = (counts.get(name, 0) for name in ("tp", "fp", "fn"))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}


def describe(corpus, cases):
    selected = {case["project"]: corpus["projects"][case["project"]] for case in cases}
    real = {project["group"] for project in selected.values() if project["kind"] == "real"}
    synthetic = {project["group"] for project in selected.values() if project["kind"] == "synthetic"}
    unseen = {project["group"] for project in selected.values() if project["kind"] == "real"
              and project["split"] == "holdout" and project.get("exposure", "unknown") == "unseen"}
    eligible = {group for group in unseen if all(case["review"]["status"] == "human-reviewed"
                for case in cases if selected[case["project"]]["group"] == group)}
    return {"tasks": len(cases), "project_records": len(selected), "independent_project_groups": len(real),
            "synthetic_project_groups": len(synthetic),
            "declared_unseen_holdout_project_groups": len(unseen),
            "declared_unseen_human_reviewed_holdout_groups": len(eligible),
            "unique_capture_hashes": len({item["sha256"] for case in cases for item in case["captures"]}),
            "families": dict(sorted(Counter(case["family"] for case in cases).items())),
            "splits": dict(sorted(Counter(selected[case["project"]]["split"] for case in cases).items())),
            "label_review": dict(sorted(Counter(case["review"]["status"] for case in cases).items())),
            "project_exposure_declarations": dict(sorted(Counter(project.get("exposure", "unknown") for project in selected.values()).items())),
            "scope": "Real project groups and exposure/review status are declared metadata, not external attestations. Tasks, captures and repeats are not independent websites. Public or previously inspected cases are not unseen holdout evidence."}


def analyzer_identity():
    """Record public source/runtime identities without local paths or source text."""
    from surface_map import __version__
    root = Path(__file__).resolve().parents[2]
    files = ("surface_map/__init__.py", "surface_map/analyze.py", "surface_map/export.py",
             "surface_map/js_analysis.py", "surface_map/js_parser.py", "evals/holdout/run.py",
             "analyzers/javascript/parse.mjs", "analyzers/javascript/package-lock.json",
             "analyzers/javascript/integrity.json")
    return {"surface_map_version": __version__, "python": platform.python_version(),
            "source_sha256": {name: _digest(_read(root / name, MAX_JSON)) for name in files},
            "scope": "Source and parser-lock identities, not proof that a declared corpus was unseen. Enhanced analysis uses the explicitly configured parser; this record does not claim OS sandboxing or a model comparison."}


def _aggregate(rows):
    known, unresolved = Counter(), Counter()
    for row in rows:
        known.update(row["known_operations"])
        unresolved.update(row["unresolved_targets"])
    return {"cases": len(rows), "passed": sum(row["passed"] for row in rows),
            "stable_cases": sum(row["stable"] for row in rows),
            "known_operations": _rate(known), "unresolved_targets": _rate(unresolved)}


def summarize(corpus, cases, predictions, *, engine, split, repeats):
    rows = []
    for case in cases:
        observations = predictions[case["id"]]
        observed = observations[0]
        failures = score(case["expected"], observed)
        stable = all(value == observed for value in observations[1:])
        if not stable:
            failures.append("repeat_inconsistency")
        rows.append({"id": case["id"], "input_id": _opaque_id(corpus, case), "project": case["project"],
                     "family": case["family"], "review_status": case["review"]["status"],
                     "passed": not failures, "stable": stable, "failures": failures,
                     "known_operations": dict(_metrics(case["expected"], observed, "routes")),
                     "unresolved_targets": dict(_metrics(case["expected"], observed, "unresolved")),
                     "prediction": observed})
    projects, families = defaultdict(list), defaultdict(list)
    for row in rows:
        projects[row["project"]].append(row)
        families[row["family"]].append(row)
    return {"schema": RESULT_SCHEMA, "corpus_sha256": corpus["sha256"], "labels_sha256": corpus["labels_sha256"],
            "input_sha256": export_inputs(corpus, split)["input_sha256"], "engine": engine,
            "split": split, "repeats": repeats,
            "analysis_calls": 0 if engine == "recorded-predictions" else len(cases) * repeats,
            "scored_observations": len(cases) * repeats,
            "composition": describe(corpus, cases), **_aggregate(rows),
            "analyzer": {**analyzer_identity(), "executed": engine != "recorded-predictions"},
            "projects": {key: _aggregate(value) for key, value in sorted(projects.items())},
            "families": {key: _aggregate(value) for key, value in sorted(families.items())}, "results": rows,
            "metric_scope": "Precision/recall count method/path identities from the first repetition. Strict case scores also require confidence, evidence kinds and any labeled locations. Empty precision, recall and F1 denominators are null. All repetitions must have identical scored projections."}


def evaluate(corpus, engine="basic", repeats=3, *, split="all", analyze=None, artifact_error=None, close=None):
    if engine not in {"basic", "enhanced"} or type(repeats) is not int or not 1 <= repeats <= 10:
        raise EvaluationError("Choose basic/enhanced and one to ten repetitions")
    if analyze is None:
        from surface_map.analyze import ArtifactError, analyze
        from surface_map.js_parser import available, close
        artifact_error = ArtifactError
        if engine == "enhanced" and not available()[0]:
            raise EvaluationError("Enhanced parser unavailable; explicit parser setup is required")
    if artifact_error is None:
        raise EvaluationError("An analyzer rejection exception is required")
    cases = select_cases(corpus, split)
    predictions = {}
    def blocked(*args, **kwargs):
        raise EvaluationError("Network operations are forbidden during saved-capture evaluation")
    try:
        if close:
            close()
        # This catches ordinary Python network regressions. It is not an OS sandbox
        # and does not claim to constrain the explicitly configured Node parser.
        with patch.object(socket.socket, "connect", blocked), patch.object(socket.socket, "connect_ex", blocked), patch.object(socket, "create_connection", blocked), patch.object(socket, "getaddrinfo", blocked):
            for case in cases:
                observations = []
                for _ in range(repeats):
                    try:
                        result = analyze(case["artifacts"], javascript_engine=engine, document_url=case.get("document_url"))
                        observations.append(project_report(result))
                    except EvaluationError:
                        observations.append({"error": "analysis_failed"})
                    except artifact_error:
                        observations.append({"rejected": True})
                    except Exception:
                        observations.append({"error": "analysis_failed"})
                predictions[case["id"]] = observations
    finally:
        if close:
            close()
    return summarize(corpus, cases, predictions, engine=engine, split=split, repeats=repeats)


def score_prediction_files(corpus, paths, *, split="all"):
    """Score frozen model/adapter outputs without making a model/API call."""
    if not 1 <= len(paths) <= 10:
        raise EvaluationError("Select one to ten prediction files")
    bundle = export_inputs(corpus, split)
    cases = select_cases(corpus, split)
    id_map = {_opaque_id(corpus, case): case["id"] for case in cases}
    predictions = {case["id"]: [] for case in cases}
    for path in paths:
        data = _json_bytes(_read(path, MAX_JSON))
        _keys(data, {"schema", "input_sha256", "results"})
        if data["schema"] != PREDICTION_SCHEMA or data["input_sha256"] != bundle["input_sha256"]:
            raise EvaluationError("Prediction schema or input digest mismatch")
        indexed = {}
        for row in _list(data["results"], MAX_CASES):
            if not isinstance(row, dict) or "id" not in row or not isinstance(row["id"], str) or row["id"] not in id_map or row["id"] in indexed:
                raise EvaluationError("Missing, duplicate or unknown prediction ID")
            indexed[row["id"]] = canonical_prediction({key: value for key, value in row.items() if key != "id"})
        if set(indexed) != set(id_map):
            raise EvaluationError("Every selected input requires one prediction")
        for opaque, identity in id_map.items():
            predictions[identity].append(indexed[opaque])
    return summarize(corpus, cases, predictions, engine="recorded-predictions", split=split, repeats=len(paths))


def aggregate_report(report):
    """Omit case predictions/failures; review remaining aggregates before sharing.

    This is not automatic anonymization. Project/family names, counts, source
    identities and corpus digests can still disclose private information. Keep
    full reports and private labels in a private location and review any proposed
    aggregate publication. Exported source inputs cannot use this mode.
    """
    if report.get("schema") not in {RESULT_SCHEMA, CORPUS_SCHEMA}:
        raise EvaluationError("Aggregate publication requires a result or validation report")
    return {**{key: value for key, value in report.items() if key != "results"},
            "aggregate_only": True,
            "publication_scope": "Case-level predictions, failures and results were omitted. Aggregate project/family identifiers, counts and digests still require privacy review before publication. Keep full reports and labels private."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all")
    parser.add_argument("--engine", choices=("basic", "enhanced"), default="basic")
    parser.add_argument("--repeats", type=int, default=3)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--validate", action="store_true", help="Validate an imported corpus, hashes, labels and split separation")
    action.add_argument("--export-inputs", type=Path, help="Export opaque IDs and inputs without labels or provenance")
    action.add_argument("--predictions", type=Path, action="append", help="Score a saved model/adapter batch; repeat flag for repeated trials")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--aggregate-only", action="store_true",
                        help="Omit case results, predictions and failures; remaining aggregates still need privacy review")
    args = parser.parse_args(argv)
    try:
        if args.aggregate_only and args.export_inputs:
            raise EvaluationError("Aggregate-only mode cannot export source inputs")
        corpus = load_corpus(args.corpus, args.labels)
        cases = select_cases(corpus, args.split)
        if args.validate:
            report = {"schema": CORPUS_SCHEMA, "corpus_sha256": corpus["sha256"],
                      "labels_sha256": corpus["labels_sha256"], "composition": describe(corpus, cases)}
        elif args.export_inputs:
            report = export_inputs(corpus, args.split)
        elif args.predictions:
            report = score_prediction_files(corpus, args.predictions, split=args.split)
        else:
            report = evaluate(corpus, args.engine, args.repeats, split=args.split)
        if args.aggregate_only:
            report = aggregate_report(report)
        rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        output = args.export_inputs if args.export_inputs else args.output
        if output is not None:
            output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return int(args.check and "passed" in report and report["passed"] != report["cases"])
    except (EvaluationError, OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        print("Evaluation could not run: invalid corpus, labels, capture hashes, predictions, output path or parser setup.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
