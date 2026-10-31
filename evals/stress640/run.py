"""Run the frozen 640-case suite offline; inputs are data, never executable tasks."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from evals.diverse.run import EvaluationError, _read, evaluate
from .build import SCHEMA, input_fingerprint

INPUT_FIELDS = ("task", "artifacts", "before", "after", "document_url",
                "before_document_url", "after_document_url", "include_page_signals")
PROJECTION_FIELDS = ("routes", "unresolved", "details", "signals", "added", "removed", "changed",
                     "unresolved_added", "unresolved_removed", "unresolved_changed", "rejected")


def load_corpus(folder=None):
    folder = Path(folder) if folder is not None else Path(__file__).parent
    data = _read(folder / "corpus.json", 2 * 1024 * 1024)
    checksum = _read(folder / "corpus.sha256", 100).decode("ascii").strip()
    if not re.fullmatch(r"[a-f0-9]{64}", checksum) or hashlib.sha256(data).hexdigest() != checksum:
        raise EvaluationError("Corpus checksum mismatch")
    corpus = json.loads(data)
    if not isinstance(corpus, dict) or corpus.get("schema") != SCHEMA or corpus.get("version") != 1:
        raise EvaluationError("Unsupported stress corpus")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or len(cases) != 640:
        raise EvaluationError("The frozen suite requires exactly 640 cases")
    ids, fingerprints, families = set(), set(), Counter()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", case["id"]) or case["id"] in ids
                or not isinstance(case.get("family"), str) or not isinstance(case.get("axes"), dict)
                or case.get("task") not in {"analyze", "compare"} or not isinstance(case.get("expected"), dict)):
            raise EvaluationError("Invalid case metadata")
        ids.add(case["id"])
        families[case["family"]] += 1
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
                raise EvaluationError("Invalid artifacts")
            for item in artifacts:
                if (not isinstance(item, dict) or set(item) != {"kind", "text"}
                        or not isinstance(item["text"], str) or len(item["text"].encode("utf-8")) > 32768
                        or (not expected.get("rejected") and item["kind"] not in {"html", "har", "javascript", "typescript"})):
                    raise EvaluationError("Invalid artifact content")
        fingerprint = input_fingerprint(case)
        if fingerprint in fingerprints:
            raise EvaluationError("Repeated exact input")
        fingerprints.add(fingerprint)
    if len(families) != 16 or set(families.values()) != {40}:
        raise EvaluationError("Expected 16 balanced families")
    return {**corpus, "sha256": checksum}


def unresolved_metrics(corpus, report):
    """Count normalized unknown-method targets separately from known operations."""
    expected_by_id = {case["id"]: case for case in corpus["cases"]}
    counts = Counter(tp=0, fp=0, fn=0)
    for row in report["results"]:
        case = expected_by_id[row["id"]]
        if case["task"] != "analyze":
            continue
        expected = {target[0] for target in case["expected"].get("unresolved", [])}
        observed = {target[0] for target in row["prediction"].get("unresolved", [])}
        counts.update(tp=len(expected & observed), fp=len(observed - expected), fn=len(expected - observed))
    tp, fp, fn = (counts[key] for key in ("tp", "fp", "fn"))
    return {**counts, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "scope": "Normalized unresolved targets in analysis cases only; unexpected rejection loses expected targets. Evidence correctness is scored by the full case rubric."}


def case_composition(corpus):
    """Describe correlated outcomes separately from distinct input fingerprints."""
    families, projections = {}, {}
    for case in corpus["cases"]:
        name, expected = case["family"], case["expected"]
        counts = families.setdefault(name, Counter(accepted_analysis_cases=0,
            accepted_analysis_cases_with_known_operations=0, accepted_analysis_cases_with_unresolved_targets=0,
            accepted_analysis_cases_without_targets=0, expected_rejections=0, comparison_cases=0))
        if expected.get("rejected"):
            counts["expected_rejections"] += 1
        elif case["task"] == "compare":
            counts["comparison_cases"] += 1
        else:
            counts["accepted_analysis_cases"] += 1
            known, unresolved = bool(expected["routes"]), bool(expected["unresolved"])
            counts["accepted_analysis_cases_with_known_operations"] += int(known)
            counts["accepted_analysis_cases_with_unresolved_targets"] += int(unresolved)
            counts["accepted_analysis_cases_without_targets"] += int(not known and not unresolved)
        projection = {key: expected[key] for key in PROJECTION_FIELDS if key in expected}
        for key, value in projection.items():
            if isinstance(value, list):
                projection[key] = sorted(value, key=lambda item: json.dumps(item, sort_keys=True))
        projections.setdefault(name, set()).add(json.dumps(projection, sort_keys=True, separators=(",", ":")))
    totals = Counter()
    for counts in families.values():
        totals.update(counts)
    return {"totals": dict(totals), "families": {
                name: {**counts, "distinct_expected_projections": len(projections[name])}
                for name, counts in sorted(families.items())},
            "scope": "Accepted analysis cases with known and unresolved targets may overlap. Distinct expected projections exclude warnings and privacy canaries; repeated expected outcomes are correlated checks, not independent observations."}


def export_inputs(corpus):
    return {"schema": SCHEMA, "corpus_sha256": corpus["sha256"], "cases": [
        {"id": f"case_{index:04d}", **{key: case[key] for key in INPUT_FIELDS if key in case}}
        for index, case in enumerate(corpus["cases"], 1)]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("basic", "enhanced"), default="basic")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--export-inputs", type=Path, help="Export synthetic inputs without expected labels")
    parser.add_argument("--check", action="store_true", help="Exit nonzero if any declared case fails")
    args = parser.parse_args(argv)
    try:
        corpus = load_corpus()
        if args.export_inputs:
            bundle = export_inputs(corpus)
            args.export_inputs.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 0
        from surface_map.analyze import ArtifactError, analyze
        from surface_map.export import compare
        from surface_map.js_parser import available, close
        if args.engine == "enhanced" and not available()[0]:
            raise EvaluationError("Enhanced parser unavailable; use the documented optional setup first")
        report = evaluate(corpus, args.engine, args.repeats, analyze=analyze, compare=compare,
                          artifact_error=ArtifactError, close=close)
        report["schema"] = "surface-map-stress-results/1"
        report["unique_input_count"] = len({input_fingerprint(case) for case in corpus["cases"]})
        report["unresolved_targets"] = unresolved_metrics(corpus, report)
        composition = case_composition(corpus)
        report["case_composition"] = {key: value for key, value in composition.items() if key != "families"}
        for family, counts in composition["families"].items():
            report["families"][family].update(counts)
        rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return int(args.check and report["passed"] != report["cases"])
    except (EvaluationError, OSError, ValueError, TypeError):
        print("Stress evaluation could not run: invalid corpus, arguments, output path or unavailable parser.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
