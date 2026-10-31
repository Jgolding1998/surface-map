"""Compare saved synthetic HTML offline. Never fetch URLs or run fixture scripts."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
import hashlib
import http.client
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import time
from unittest.mock import patch
import urllib.request
import webbrowser

from evals.stress640.run import load_corpus

HERE = Path(__file__).parent
REPO = HERE.parents[2]
SPEC_SHA256 = "1c112cef78a4dbec6c13bbe2aacc030f331cbff4116954eea94d626668699032"
VERSIONS = {"beautifulsoup4": "4.15.0", "html5lib": "1.1", "soupsieve": "3.0.4",
            "typing-extensions": "4.16.0", "six": "1.17.0", "webencodings": "0.6.1"}


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def project(report):
    return {(row["method"], row["path"]): {
        "query_fields": sorted(row["query_fields"]),
        "request_fields": sorted({field for shape in row["request_shapes"] for field in shape.get("properties", {})})}
        for row in report["routes"] if any(e["kind"] in {"link", "form"} for e in row["evidence"])}


def expected(case):
    result = {(row[0], row[1]): {"query_fields": [], "request_fields": []} for row in case["expected"]["routes"]}
    for row in case["expected"].get("details", []):
        result[(row["method"], row["path"])] = {
            kind: sorted(row.get(kind, [])) for kind in ("query_fields", "request_fields")}
    return result


def fields(result):
    return {(method, path, kind, field) for (method, path), details in result.items()
            for kind, names in details.items() for field in names}


def counts(truth, observed):
    return {"tp": len(truth & observed), "fp": len(observed - truth), "fn": len(truth - observed)}


def ratios(values):
    tp, fp, fn = (values[key] for key in ("tp", "fp", "fn"))
    return {**values, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None}


def evaluate():
    if digest(HERE / "spec.json") != SPEC_SHA256:
        raise ValueError("The pre-scoring specification changed")
    spec = json.loads((HERE / "spec.json").read_text(encoding="utf-8"))
    installed = {name: version(name) for name in VERSIONS}
    if installed != VERSIONS:
        raise ValueError("Use the pinned optional requirements for this comparison")
    corpus = load_corpus()
    cases = [case for case in corpus["cases"] if case["family"] in spec["families"]]
    if corpus["sha256"] != spec["corpus_sha256"] or len(cases) != spec["case_count"]:
        raise ValueError("Unexpected comparison corpus")
    if not all(case["task"] == "analyze" and len(case["artifacts"]) == 1
               and case["artifacts"][0]["kind"] == "html" and not case["expected"].get("rejected") for case in cases):
        raise ValueError("Comparison requires accepted synthetic HTML cases")
    sources = sorted((REPO / "surface_map").glob("*.py"))
    source_hashes = {path.relative_to(REPO).as_posix(): digest(path) for path in sources}
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("External operations are disabled during this offline comparison")

    with ExitStack() as guards:
        for owner, name in [(socket.socket, "connect"), (socket.socket, "connect_ex"),
                            (socket, "create_connection"), (urllib.request, "urlopen"),
                            (http.client.HTTPConnection, "connect"), (subprocess, "Popen"),
                            (os, "system"), (webbrowser, "open")]:
            guards.enter_context(patch.object(owner, name, deny))
        from .adapter import extract
        from surface_map.analyze import analyze
        tools = {"beautifulsoup_html5lib": extract,
                 "surface_map_basic_html": lambda text, url: project(analyze(
                     [{"kind": "html", "text": text}], javascript_engine="basic", document_url=url))}
        totals = {name: {"cases": 0, "operations_exact": 0, "full_exact": 0, "stable": 0,
                        "operations": Counter(tp=0, fp=0, fn=0), "fields": Counter(tp=0, fp=0, fn=0),
                        "families": defaultdict(Counter), "durations": []} for name in tools}
        failures = []
        for number, case in enumerate(cases):
            truth = expected(case)
            observations, durations = defaultdict(list), defaultdict(list)
            for repeat in range(spec["repeats"]):
                names = list(tools)
                if (number + repeat) % 2:
                    names.reverse()
                for name in names:
                    started = time.perf_counter()
                    value = tools[name](case["artifacts"][0]["text"], case.get("document_url"))
                    durations[name].append((time.perf_counter() - started) * 1000)
                    observations[name].append(value)
            for name, total in totals.items():
                observed = observations[name][0]
                exact = observed == truth
                stable = all(value == observed for value in observations[name])
                total["cases"] += 1
                total["operations_exact"] += set(observed) == set(truth)
                total["full_exact"] += exact
                total["stable"] += stable
                total["operations"].update(counts(set(truth), set(observed)))
                total["fields"].update(counts(fields(truth), fields(observed)))
                total["durations"].extend(durations[name])
                total["families"][case["family"]].update(cases=1, full_exact=exact)
                if not exact or not stable:
                    failures.append({"id": case["id"], "tool": name, "exact": exact, "stable": stable,
                                     "missing_operations": sorted(set(truth) - set(observed)),
                                     "extra_operations": sorted(set(observed) - set(truth)),
                                     "missing_fields": sorted(fields(truth) - fields(observed)),
                                     "extra_fields": sorted(fields(observed) - fields(truth))})
    if source_hashes != {path.relative_to(REPO).as_posix(): digest(path) for path in sources}:
        raise ValueError("Source changed during the comparison; rerun after edits finish")
    for total in totals.values():
        durations = sorted(total.pop("durations"))
        total["timing"] = {"median_ms": round(statistics.median(durations), 6),
                           "p95_ms": round(durations[(95 * len(durations) + 99) // 100 - 1], 6)}
        for key in ("operations", "fields"):
            total[key] = ratios(total[key])
    return {"schema": "surface-map-html-library-comparison/1", "spec_sha256": SPEC_SHA256,
            "corpus_sha256": corpus["sha256"], "adapter_sha256": digest(HERE / "adapter.py"),
            "runner_sha256": digest(HERE / "run.py"), "surface_map_source_hashes": source_hashes,
            "package_versions": installed, "python": platform.python_version(), "repeats": spec["repeats"],
            "external_operations_attempted": len(attempts), "summary": totals, "failures": failures,
            "empty_prediction_exact_cases": sum(not expected(case) for case in cases)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Save aggregate results instead of printing them")
    parser.add_argument("--check", action="store_true", help="Exit nonzero for any mismatch or unstable case")
    args = parser.parse_args(argv)
    try:
        result = evaluate()
    except (ImportError, PackageNotFoundError):
        parser.exit(2, "Missing optional dependencies. Install the hash-pinned html_comparison/requirements.txt explicitly.\n")
    except (ValueError, RuntimeError) as error:
        parser.exit(2, str(error) + "\n")
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return int(args.check and bool(result["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
