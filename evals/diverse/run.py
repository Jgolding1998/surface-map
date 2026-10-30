"""Evaluate fixed synthetic captures without requesting URLs or running their code."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import platform
import re
import stat
import statistics
import time


SCHEMA = "surface-map-diverse-tasks/1"
RESULT_SCHEMA = "surface-map-diverse-results/1"


class EvaluationError(ValueError):
    pass


def _read(path, limit, *, normalize_newlines=True):
    for part in (path, path.parent):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise EvaluationError("Linked corpus paths are unsupported")
    if not path.is_file() or path.stat().st_size > limit:
        raise EvaluationError("Unsupported corpus file")
    data = path.read_bytes()
    if normalize_newlines:
        data = data.replace(b"\r\n", b"\n")
    if len(data) > limit:
        raise EvaluationError("Corpus file limit exceeded")
    return data


def load_corpus(folder=None):
    folder = Path(folder) if folder is not None else Path(__file__).parent
    data = _read(folder / "corpus.json", 256 * 1024)
    checksum = _read(folder / "corpus.sha256", 100).decode("ascii").strip()
    if not re.fullmatch(r"[a-f0-9]{64}", checksum) or hashlib.sha256(data).hexdigest() != checksum:
        raise EvaluationError("Corpus checksum mismatch")
    corpus = json.loads(data)
    if corpus.get("schema") != SCHEMA or corpus.get("version") != 1:
        raise EvaluationError("Unsupported corpus version")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not 40 <= len(cases) <= 100:
        raise EvaluationError("Unsupported corpus size")
    seen = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", case["id"])
                or case["id"] in seen or not isinstance(case.get("family"), str)
                or case.get("task") not in {"analyze", "compare"}
                or not isinstance(case.get("expected"), dict)):
            raise EvaluationError("Invalid case metadata")
        seen.add(case["id"])
        expected = case["expected"]
        if "rejected" in expected:
            if expected != {"rejected": True}:
                raise EvaluationError("Invalid rejection expectation")
        else:
            required = ("routes", "unresolved") if case["task"] == "analyze" else (
                "added", "removed", "changed", "unresolved_added", "unresolved_removed", "unresolved_changed")
            if any(not isinstance(expected.get(key), list) for key in required):
                raise EvaluationError("Incomplete case expectation")
        if "forbidden" in expected and (not isinstance(expected["forbidden"], list)
                or any(not isinstance(value, str) or not value for value in expected["forbidden"])):
            raise EvaluationError("Invalid privacy canaries")
        fields = ("before", "after") if case["task"] == "compare" else ("artifacts",)
        for field in fields:
            artifacts = case.get(field)
            if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 6:
                raise EvaluationError("Invalid case artifacts")
            if any(not isinstance(item, dict) or set(item) != {"kind", "text"}
                   or not isinstance(item["kind"], str) or not isinstance(item["text"], str)
                   or len(item["text"].encode("utf-8")) > 32768 for item in artifacts):
                raise EvaluationError("Invalid case artifact")
    return {**corpus, "sha256": checksum}


def _sort(values):
    return sorted(values, key=lambda value: json.dumps(value, sort_keys=True))


def _evidence(items):
    return _sort([[item["source"], item["location"], item["kind"]] for item in items])


def project_report(report):
    routes = _sort([[row["method"], row["path"], row["confidence"], _evidence(row["evidence"])]
                    for row in report["routes"]])
    unresolved = _sort([[row["path"], _evidence(row["evidence"])]
                        for row in report.get("unresolved_operations", [])])
    details = []
    for row in report["routes"]:
        detail = {key: row[key] for key in ("method", "path", "statuses", "query_fields")}
        for prefix in ("request", "response"):
            detail[prefix + "_fields"] = sorted({field for shape in row[prefix + "_shapes"]
                                                   for field in shape.get("properties", {})})
        details.append(detail)
    signals = None
    if "page_signals" in report:
        signals = _sort([[f["kind"], f["location"], f.get("resource_kind"),
                          sorted(f["visual_hiding_hints"]), sorted(f["accessibility_hints"]),
                          sorted(f["inert_context"])]
                         for page in report["page_signals"] for f in page["findings"]])
    return {"routes": routes, "unresolved": unresolved, "details": _sort(details),
            "signals": signals, "warnings": [w["reason"] for w in report["warnings"]]}


def project_comparison(value):
    return {
        "added": _sort([[r["method"], r["path"]] for r in value["added"]]),
        "removed": _sort([[r["method"], r["path"]] for r in value["removed"]]),
        "changed": _sort([[r["method"], r["path"], sorted(r["changed_fields"])] for r in value["changed"]]),
        "unresolved_added": sorted(r["path"] for r in value.get("unresolved_added", [])),
        "unresolved_removed": sorted(r["path"] for r in value.get("unresolved_removed", [])),
        "unresolved_changed": _sort([[r["path"], sorted(r["changed_fields"])]
                                      for r in value.get("unresolved_changed", [])]),
    }


def score(case, observed):
    """Score the declared rubric. Unexpected routes and unresolved targets remain errors."""
    expected = case["expected"]
    if expected.get("rejected"):
        return [] if observed == {"rejected": True} else ["expected_rejection"]
    if observed.get("rejected") or observed.get("error"):
        return ["unexpected_rejection" if observed.get("rejected") else "evaluation_error"]
    failures = []
    for key in ("routes", "unresolved", "added", "removed", "changed", "unresolved_added", "unresolved_removed", "unresolved_changed"):
        if key in expected and (key not in observed or _sort(observed[key]) != _sort(expected[key])):
            failures.append(key)
    if "signals" in expected:
        wanted = expected["signals"]
        if observed.get("signals") != (None if wanted is None else _sort(wanted)):
            failures.append("signals")
    for warning in expected.get("warnings", []):
        if not any(warning in actual for actual in observed.get("warnings", [])):
            failures.append("required_warning")
    for detail in expected.get("details", []):
        rows = [row for row in observed.get("details", [])
                if (row["method"], row["path"]) == (detail["method"], detail["path"])]
        if len(rows) != 1 or any(rows[0].get(key) != value for key, value in detail.items()):
            failures.append("route_details")
    if observed.get("privacy_failure"):
        failures.append("privacy_canary")
    return sorted(set(failures))


def run_case(case, engine, *, analyze, compare, artifact_error):
    try:
        if case["task"] == "compare":
            reports = [analyze(case[field], javascript_engine=engine,
                               document_url=case.get(field + "_document_url")) for field in ("before", "after")]
            comparison = compare(*reports)
            projection = project_comparison(comparison)
            serialized = json.dumps({"before": reports[0], "after": reports[1], "comparison": comparison}, ensure_ascii=False)
        else:
            report = analyze(case["artifacts"], javascript_engine=engine,
                             document_url=case.get("document_url"),
                             include_page_signals=case.get("include_page_signals", False))
            projection = project_report(report)
            serialized = json.dumps(report, ensure_ascii=False)
        if any(canary in serialized for canary in case["expected"].get("forbidden", [])):
            projection["privacy_failure"] = True
        return projection
    except artifact_error:
        return {"rejected": True}
    except Exception:
        return {"error": "evaluation_error"}


def metrics(expected, observed):
    expected, observed = set(expected), set(observed)
    tp, fp, fn = len(expected & observed), len(observed - expected), len(expected - observed)
    return {"tp": tp, "fp": fp, "fn": fn}


def evaluate(corpus, engine, repeats, *, analyze, compare, artifact_error, close=lambda: None):
    if engine not in {"basic", "enhanced"} or type(repeats) is not int or not 1 <= repeats <= 10:
        raise EvaluationError("Select basic/enhanced and 1..10 repeats")
    rows, family = [], defaultdict(lambda: Counter(total=0, passed=0))
    operations = Counter(tp=0, fp=0, fn=0)
    latency = []
    try:
        close()
        for case in corpus["cases"]:
            observations, durations = [], []
            for _ in range(repeats):
                started = time.perf_counter()
                observations.append(run_case(case, engine, analyze=analyze, compare=compare, artifact_error=artifact_error))
                durations.append((time.perf_counter() - started) * 1000)
            observed = observations[0]
            failures = score(case, observed)
            stable = all(value == observed for value in observations[1:])
            if not stable:
                failures.append("repeat_inconsistency")
            row = {"id": case["id"], "family": case["family"], "passed": not failures,
                   "failures": failures, "stable": stable, "prediction": observed,
                   "milliseconds": [round(value, 3) for value in durations]}
            rows.append(row)
            family[case["family"]].update(total=1, passed=int(not failures))
            latency.extend(durations)
            if "routes" in case["expected"]:
                operations.update(metrics((tuple(r[:2]) for r in case["expected"]["routes"]),
                                          (tuple(r[:2]) for r in observed.get("routes", []))))
    finally:
        close()
    tp, fp, fn = (operations[key] for key in ("tp", "fp", "fn"))
    ordered = sorted(latency)
    return {"schema": RESULT_SCHEMA, "corpus_sha256": corpus["sha256"], "engine": engine,
            "python": platform.python_version(), "repeats": repeats,
            "scope": corpus["scope"], "cases": len(rows), "passed": sum(r["passed"] for r in rows),
            "stable_cases": sum(r["stable"] for r in rows), "families": dict(sorted(family.items())),
            "known_operations": {**operations, "precision": tp / (tp + fp) if tp + fp else None,
                                  "recall": tp / (tp + fn) if tp + fn else None},
            "timing": {"median_ms": round(statistics.median(latency), 3),
                       "p95_ms": round(ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)], 3),
                       "scope": "Sequential local calls, one initial worker close; first call may include startup. Not an isolated performance benchmark."},
            "results": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("basic", "enhanced"), default="basic")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--export-inputs", type=Path, help="Write only synthetic task inputs, excluding expected answers")
    parser.add_argument("--check", action="store_true", help="Exit nonzero if any case fails its declared rubric")
    args = parser.parse_args(argv)
    try:
        corpus = load_corpus()
        if args.export_inputs:
            bundle = {"schema": SCHEMA, "corpus_sha256": corpus["sha256"],
                      "cases": [{key: value for key, value in case.items() if key != "expected"} for case in corpus["cases"]]}
            args.export_inputs.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 0
        from surface_map.analyze import ArtifactError, analyze
        from surface_map.export import compare
        from surface_map.js_parser import available, close
        if args.engine == "enhanced" and not available()[0]:
            raise EvaluationError("Enhanced parser unavailable; run the documented optional parser setup first")
        report = evaluate(corpus, args.engine, args.repeats, analyze=analyze, compare=compare, artifact_error=ArtifactError, close=close)
        rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return int(args.check and report["passed"] != report["cases"])
    except (EvaluationError, OSError, ValueError):
        print("Evaluation could not run: invalid corpus, arguments, output path or unavailable parser.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
