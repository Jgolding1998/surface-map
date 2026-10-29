"""Run the fixed, trusted static-route regression corpus without executing its sources."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import stat
import statistics
import sys
import time
from pathlib import Path, PurePosixPath

CORPUS_SCHEMA = "surface-map-static-route-corpus/1"
OUTPUT_SCHEMA = "surface-map-static-route-evaluation/1"
PATHS = {"/api/items", "/api/users", "/api/search"}
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
ERROR_CODES = {"invalid_input", "input_limit", "syntax", "limit", "busy", "timeout", "output_limit", "failed",
               "node_missing", "node_unsupported", "parser_missing", "parser_integrity", "npm_missing", "setup_failed"}


class EvaluationError(ValueError):
    pass


def _bytes(path, limit):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_size > limit:
        raise EvaluationError("Unsupported fixture file")
    data = path.read_bytes()
    if len(data) > limit:
        raise EvaluationError("Fixture byte limit exceeded")
    return data


def load_corpus(folder=None):
    """Load only the fixed corpus and its hash-checked ordinary relative fixture files."""
    folder = Path(folder) if folder is not None else Path(__file__).resolve().parent
    raw = _bytes(folder / "corpus.json", 65536).replace(b"\r\n", b"\n")
    corpus = json.loads(raw)
    manifest = json.loads(_bytes(folder / "corpus.sha256.json", 65536))
    if (corpus.get("schema") != CORPUS_SCHEMA or corpus.get("version") != 1
            or set(corpus.get("allowed_paths", [])) != PATHS
            or not isinstance(corpus.get("cases"), list) or not 12 <= len(corpus["cases"]) <= 16):
        raise EvaluationError("Unsupported corpus")
    expected_hashes = manifest.get("files")
    if (manifest.get("schema") != "surface-map-static-route-hashes/1" or manifest.get("corpus_version") != corpus["version"]
            or manifest.get("normalization") != "CRLF-to-LF"
            or not isinstance(expected_hashes, dict)):
        raise EvaluationError("Unsupported fixture hashes")
    digest = lambda data: hashlib.sha256(data).hexdigest()
    actual = {"corpus.json": digest(raw)}
    seen, cases = set(), []
    for item in corpus["cases"]:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", item["id"]) or item["id"] in seen
                or item.get("kind") not in {"javascript", "typescript"}):
            raise EvaluationError("Invalid fixture identity")
        seen.add(item["id"])
        name = item.get("file")
        if not isinstance(name, str) or "\\" in name:
            raise EvaluationError("Invalid fixture path")
        parts = PurePosixPath(name).parts
        suffixes = {".ts", ".tsx"} if item["kind"] == "typescript" else {".js", ".jsx"}
        if len(parts) != 2 or parts[0] != "cases" or Path(parts[1]).stem != item["id"] or Path(parts[1]).suffix not in suffixes:
            raise EvaluationError("Invalid fixture path")
        if (folder / "cases").is_symlink() or getattr((folder / "cases").lstat(), "st_file_attributes", 0) & 0x400:
            raise EvaluationError("Linked fixture directory")
        data = _bytes(folder.joinpath(*parts), 8192).replace(b"\r\n", b"\n")
        actual[name] = digest(data)
        known, unresolved = item.get("known"), item.get("unresolved")
        if (not isinstance(known, list) or not isinstance(unresolved, list)
                or any(not isinstance(pair, list) or len(pair) != 2 or pair[0] not in METHODS or pair[1] not in PATHS for pair in known)
                or any(path not in PATHS for path in unresolved)
                or len(known) != len({tuple(pair) for pair in known}) or len(unresolved) != len(set(unresolved))):
            raise EvaluationError("Invalid fixture truth")
        cases.append({**item, "text": data.decode("utf-8")})
    aggregate = digest(json.dumps(actual, sort_keys=True, separators=(",", ":")).encode())
    if actual != expected_hashes or aggregate != manifest.get("aggregate_sha256"):
        raise EvaluationError("Fixture checksum mismatch")
    return {"version": corpus["version"], "sha256": actual["corpus.json"], "aggregate_sha256": aggregate, "cases": cases}


def metrics(expected, predicted):
    expected, predicted = set(expected), set(predicted)
    tp, fp, fn = len(expected & predicted), len(predicted - expected), len(expected - predicted)
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 1.0}


def projection(report, mode):
    if not isinstance(report, dict):
        raise EvaluationError("Invalid analysis report")
    engines = report.get("javascript_analysis")
    if (not isinstance(engines, list) or len(engines) != 1 or not isinstance(engines[0], dict)
            or engines[0].get("engine") != mode):
        raise EvaluationError("Requested parser mode was not used")
    routes = report.get("routes")
    unresolved_entries = report.get("unresolved_operations", [])
    if not isinstance(routes, list) or not isinstance(unresolved_entries, list):
        raise EvaluationError("Invalid operation lists")
    known, unresolved = set(), set()
    for entry in routes:
        if not isinstance(entry, dict) or entry.get("method") not in METHODS or not isinstance(entry.get("path"), str):
            raise EvaluationError("Invalid known operation")
        known.add((entry["method"], entry["path"]))
    for entry in unresolved_entries:
        if not isinstance(entry, dict) or "method" not in entry or entry["method"] is not None or not isinstance(entry.get("path"), str):
            raise EvaluationError("Invalid unresolved operation")
        unresolved.add(entry["path"])
    # Do not filter unexpected paths away: they must remain false positives.
    parser = engines[0].get("parser")
    if mode == "enhanced" and (not isinstance(parser, str) or not parser):
        raise EvaluationError("Enhanced parser identity missing")
    return known, unresolved, parser


def evaluate(corpus, mode, trials, *, analyze, close, clock=time.perf_counter):
    """Measure full corpus passes. Dependencies are injected so unit tests never spawn a parser."""
    try:
        if mode not in {"basic", "enhanced"} or type(trials) is not int or not 1 <= trials <= 10:
            raise EvaluationError("Choose basic/enhanced and 1..10 trials")
        reference, latencies, stable, errors, parsers = None, {"cold_pass": [], "warm_pass": []}, True, 0, set()
        for _ in range(trials):
            close()
            for phase in latencies:
                observed = []
                started = clock()
                for case in corpus["cases"]:
                    error_code = None
                    try:
                        report = analyze([{"kind": case["kind"], "text": case["text"]}], javascript_engine=mode)
                        known, unresolved, parser = projection(report, mode)
                        if parser:
                            parsers.add(parser)
                    except Exception as error:
                        code = getattr(error, "code", None)
                        error_code = code if isinstance(code, str) and code in ERROR_CODES else "analysis_failed"
                        known, unresolved = set(), set()
                        errors += 1
                    observed.append((known, unresolved, error_code))
                latencies[phase].append((clock() - started) * 1000)
                if reference is None:
                    reference = observed
                else:
                    stable = stable and observed == reference
        known_truth, known_predicted, unresolved_truth, unresolved_predicted = set(), set(), set(), set()
        target_truth, target_predicted, rows = set(), set(), []
        negative_passed = negative_total = passed = 0
        for case, (known, unresolved, error_code) in zip(corpus["cases"], reference):
            expected_known = {tuple(pair) for pair in case["known"]}
            expected_unresolved = set(case["unresolved"])
            exact = error_code is None and known == expected_known and unresolved == expected_unresolved
            passed += exact
            if not expected_known and not expected_unresolved:
                negative_total += 1
                negative_passed += exact
            prefix = lambda values: {(case["id"], *value) for value in values}
            known_truth.update(prefix(expected_known)); known_predicted.update(prefix(known))
            unresolved_truth.update((case["id"], path) for path in expected_unresolved)
            unresolved_predicted.update((case["id"], path) for path in unresolved)
            target_truth.update((case["id"], path) for path in {path for _, path in expected_known} | expected_unresolved)
            target_predicted.update((case["id"], path) for path in {path for _, path in known} | unresolved)
            rows.append({"id": case["id"], "passed": exact, "error": error_code is not None, "error_code": error_code,
                         "expected_known": sorted(expected_known), "predicted_known": sorted(known),
                         "expected_unresolved": sorted(expected_unresolved), "predicted_unresolved": sorted(unresolved)})
        typed_truth = {(case, method, path) for case, method, path in known_truth} | {(case, None, path) for case, path in unresolved_truth}
        typed_predicted = {(case, method, path) for case, method, path in known_predicted} | {(case, None, path) for case, path in unresolved_predicted}
        summarize = lambda values: {"unit": "milliseconds per complete corpus pass", "samples": len(values),
                                    "case_calls_per_sample": len(corpus["cases"]), "values": values,
                                    "median": statistics.median(values), "min": min(values), "max": max(values)}
        return {"schema": OUTPUT_SCHEMA, "task": "Curated static client-intent regressions, not unseen accuracy",
                "corpus_version": corpus["version"], "corpus_sha256": corpus["sha256"],
                "fixture_aggregate_sha256": corpus["aggregate_sha256"], "mode": mode, "trials": trials,
                "runtime": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                            "system": platform.system(), "machine": platform.machine(), "observed_parsers": sorted(parsers)},
                "accuracy_sample": "First cold pass only; subsequent passes check repeatability without inflating accuracy sample count.",
                "metrics": {"typed_operations": metrics(typed_truth, typed_predicted), "known_operations": metrics(known_truth, known_predicted),
                            "unresolved_targets": metrics(unresolved_truth, unresolved_predicted), "targets_ignoring_method": metrics(target_truth, target_predicted)},
                "exact_cases": {"passed": passed, "total": len(rows)}, "negative_cases": {"passed": negative_passed, "total": negative_total},
                "stable_predictions": stable, "analysis_errors_all_passes": errors,
                "latency": {**{phase: summarize(values) for phase, values in latencies.items()},
                            "scope": "Cold resets parser workers before the pass; warm immediately repeats the corpus with no close between passes. In a fresh enhanced CLI process the first cold pass also pays the Node version probe; close preserves the version cache for later trials. Basic has no worker startup, so its labels mean initial/repeated pass only. Python imports, corpus loading and worker closing are excluded. Every case is analyzed again; no evaluator result cache. This is not a fresh-Python-process cold measurement."},
                "passed": passed == len(rows) and stable and errors == 0, "cases": rows}
    finally:
        close()


def table(result):
    rows = ["| Metric | Result |", "| --- | --- |", f"| Mode | {result['mode']} |",
            f"| Exact cases | {result['exact_cases']['passed']}/{result['exact_cases']['total']} |",
            f"| Negative controls | {result['negative_cases']['passed']}/{result['negative_cases']['total']} |"]
    for name, score in result["metrics"].items():
        value = lambda key: "n/a" if score[key] is None else f"{score[key]:.3f}"
        rows.append(f"| {name} TP/FP/FN; P/R/F1 | {score['tp']}/{score['fp']}/{score['fn']}; {value('precision')}/{value('recall')}/{value('f1')} |")
    for phase in ("cold_pass", "warm_pass"):
        item = result["latency"][phase]
        rows.append(f"| {phase} median, complete corpus | {item['median']:.3f}ms ({item['samples']} samples) |")
    rows.append(f"| Stable; analysis errors | {result['stable_predictions']}; {result['analysis_errors_all_passes']} |")
    return "\n".join(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("basic", "enhanced"), required=True)
    parser.add_argument("--trials", type=int, choices=range(1, 11), default=1)
    parser.add_argument("--check", action="store_true", help="Exit 1 on an accuracy or stability failure; analysis errors always exit 2")
    args = parser.parse_args(argv)
    close, failed = None, False
    try:
        from surface_map import js_parser
        close = js_parser.close
        from surface_map.analyze import analyze
        result = evaluate(load_corpus(), args.mode, args.trials, analyze=analyze, close=close)
        payload, summary = json.dumps(result, indent=2, allow_nan=False), table(result)
    except (Exception, KeyboardInterrupt):
        failed = True
    finally:
        if close is not None:
            try:
                close()
            except (Exception, KeyboardInterrupt):
                failed = True
    if failed:
        print(json.dumps({"schema": OUTPUT_SCHEMA, "mode": args.mode, "error": "Evaluation could not complete"}))
        return 2
    print(payload)
    print(summary, file=sys.stderr)
    if result["analysis_errors_all_passes"]:
        return 2
    return 1 if args.check and not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
