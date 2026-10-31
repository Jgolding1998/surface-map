"""Preserve known real-project gaps while failing on newly regressed cases."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from surface_map.analyze import strict_json
from surface_map.source_io import read_ordinary

ROOT = Path(__file__).resolve().parent
SCHEMA = "surface-map-realworld-regression/1"


def snapshot(report):
    if (type(report) is not dict or report.get("schema") != "surface-map-holdout-results/1"
            or report.get("engine") != "enhanced" or report.get("split") != "all"
            or report.get("analysis_calls", 0) < 1 or type(report.get("results")) is not list):
        raise ValueError
    rows = report["results"]
    ids = [row["id"] for row in rows]
    if not rows or len(ids) != len(set(ids)) or report["composition"]["tasks"] != len(rows):
        raise ValueError
    if (any(type(row.get("passed")) is not bool or type(row.get("stable")) is not bool for row in rows)
            or report["passed"] != sum(row["passed"] for row in rows)
            or report["stable_cases"] != sum(row["stable"] for row in rows)):
        raise ValueError
    return {
        "schema": SCHEMA,
        "corpus_sha256": report["corpus_sha256"],
        "labels_sha256": report["labels_sha256"],
        "engine": "enhanced",
        "passing_case_ids": sorted(row["id"] for row in rows if row["passed"]),
        "known_failure_case_ids": sorted(row["id"] for row in rows if not row["passed"]),
        "scope": "Development regression baseline, not gold labels or unseen accuracy evidence. Known failures remain in the full evaluation and its denominator. Improvements are allowed; a previously passing case must not fail.",
    }


def check(report, baseline):
    current = snapshot(report)
    if (type(baseline) is not dict or set(baseline) != set(current)
            or any(baseline[key] != current[key] for key in ("schema", "corpus_sha256", "labels_sha256", "engine"))):
        raise ValueError
    old_pass, old_fail = baseline["passing_case_ids"], baseline["known_failure_case_ids"]
    if (type(old_pass) is not list or type(old_fail) is not list
            or any(type(value) is not str for value in old_pass + old_fail)
            or len(old_pass + old_fail) != len(set(old_pass + old_fail))
            or set(old_pass + old_fail) != set(current["passing_case_ids"] + current["known_failure_case_ids"])):
        raise ValueError
    # An execution error on a previously failing case is still a new failure mode.
    execution_errors = [row["id"] for row in report["results"]
                        if set(row.get("failures", [])) & {"analysis_error", "evaluation_error", "unexpected_rejection"}
                        or row.get("prediction", {}).get("error")]
    regressed = sorted(set(old_pass) - set(current["passing_case_ids"]))
    unstable = sorted(row["id"] for row in report["results"] if not row["stable"])
    return {"passed": not (regressed or unstable or execution_errors), "regressed": regressed,
            "unstable": unstable, "execution_errors": execution_errors,
            "improved": sorted(set(current["passing_case_ids"]) & set(old_fail)),
            "known_failures": len(current["known_failure_case_ids"]),
            "total_cases": len(report["results"]), "scope": current["scope"]}


def _read(path):
    return strict_json(read_ordinary(path, 16 * 1024 * 1024), depth=40, nodes=500000)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=ROOT / "baseline.json")
    parser.add_argument("--record", action="store_true", help="Write a new baseline without changing labels; refuses existing destinations")
    args = parser.parse_args(argv)
    try:
        report = _read(args.results)
        if args.record:
            baseline = snapshot(report)
            if report["stable_cases"] != len(report["results"]):
                raise ValueError
            with args.baseline.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(baseline, indent=2) + "\n")
            print("Recorded a development regression baseline; gold labels were not changed.")
            return 0
        result = check(report, _read(args.baseline))
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1
    except Exception:
        print("Regression check rejected: invalid or changed dataset, results, baseline, or destination.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
