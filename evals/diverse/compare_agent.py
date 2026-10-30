"""Score saved agent predictions against the frozen route/evidence rubric."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .run import EvaluationError, _read, load_corpus


METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
KINDS = {"link", "form", "javascript", "har"}
CONFIDENCE = {"declared", "inferred", "observed"}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise EvaluationError("Duplicate JSON object key")
        value[key] = item
    return value


def parse_response(text):
    """Accept a JSON response, optionally inside one complete Markdown code fence."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > 256 * 1024:
        raise EvaluationError("Prediction size limit exceeded")
    text = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", text, re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        result = json.loads(text, object_pairs_hook=_unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(EvaluationError("Nonfinite JSON value")))
    except (ValueError, RecursionError):
        raise EvaluationError("Malformed prediction JSON") from None
    if not isinstance(result, dict) or set(result) != {"results"} or not isinstance(result["results"], list):
        raise EvaluationError("Prediction must contain a results array")
    return result["results"]


def _path(value):
    if (not isinstance(value, str) or not value.startswith("/") or len(value) > 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise EvaluationError("Malformed route path")
    return value


def _evidence(value, *, unresolved=False):
    if not isinstance(value, list) or not value or len(value) > 64:
        raise EvaluationError("Malformed evidence list")
    rows = []
    for item in value:
        if (not isinstance(item, list) or len(item) != 3 or any(not isinstance(v, str) for v in item)
                or not re.fullmatch(r"source_[1-9][0-9]*", item[0])
                or not re.fullmatch(r"(?:line|entry) [1-9][0-9]*", item[1])
                or item[2] not in KINDS or unresolved and item[2] != "javascript"):
            raise EvaluationError("Malformed source evidence")
        rows.append(tuple(item))
    return tuple(sorted(set(rows)))


def canonical_prediction(value):
    """Use exact set semantics for routes and nested evidence, without path filtering."""
    if not isinstance(value, dict) or set(value) != {"id", "routes", "unresolved"}:
        raise EvaluationError("Malformed case prediction")
    if not isinstance(value["id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value["id"]):
        raise EvaluationError("Malformed case identity")
    routes, unresolved = value["routes"], value["unresolved"]
    if not isinstance(routes, list) or not isinstance(unresolved, list) or len(routes) + len(unresolved) > 400:
        raise EvaluationError("Malformed operation lists")
    known, unknown = [], []
    for row in routes:
        if (not isinstance(row, list) or len(row) != 4 or not isinstance(row[0], str) or row[0] not in METHODS
                or not isinstance(row[2], str) or row[2] not in CONFIDENCE):
            raise EvaluationError("Malformed known operation")
        known.append((row[0], _path(row[1]), row[2], _evidence(row[3])))
    for row in unresolved:
        if not isinstance(row, list) or len(row) != 2:
            raise EvaluationError("Malformed unresolved operation")
        unknown.append((_path(row[0]), _evidence(row[1], unresolved=True)))
    return {"routes": tuple(sorted(set(known))), "unresolved": tuple(sorted(set(unknown)))}


def index_predictions(rows, expected_ids):
    if not isinstance(rows, list) or len(rows) > 100:
        raise EvaluationError("Malformed prediction batch")
    allowed = set(expected_ids)
    result = {}
    for row in rows:
        prediction = canonical_prediction(row)
        case_id = row["id"]
        if case_id not in allowed or case_id in result:
            raise EvaluationError("Unknown or duplicate case identity")
        result[case_id] = prediction
    if set(result) != allowed:
        raise EvaluationError("Missing case predictions")
    return result


def score_predictions(truth, predictions):
    if set(truth) != set(predictions):
        raise EvaluationError("Case identity mismatch")
    rows = {}
    counts = Counter(tp=0, fp=0, fn=0)
    for case_id, expected in truth.items():
        actual = predictions[case_id]
        failures = [key for key in ("routes", "unresolved") if expected[key] != actual[key]]
        wanted_ops = {r[:2] for r in expected["routes"]}
        actual_ops = {r[:2] for r in actual["routes"]}
        counts.update(tp=len(wanted_ops & actual_ops), fp=len(actual_ops - wanted_ops), fn=len(wanted_ops - actual_ops))
        rows[case_id] = {"passed": not failures, "failures": failures}
    tp, fp, fn = (counts[key] for key in ("tp", "fp", "fn"))
    return {"cases": len(rows), "passed": sum(row["passed"] for row in rows.values()),
            "failed_ids": [case_id for case_id, row in rows.items() if not row["passed"]],
            "known_operations": {**counts, "precision": tp / (tp + fp) if tp + fp else None,
                                  "recall": tp / (tp + fn) if tp + fn else None}, "results": rows}


def _manifest_file(folder, filename, hashes):
    if (not isinstance(filename, str) or PurePosixPath(filename).parts != (filename,)
            or not re.fullmatch(r"[a-z][a-z0-9-]*\.(?:json|txt)", filename)
            or filename not in hashes):
        raise EvaluationError("Unsupported baseline artifact")
    raw = _read(folder / filename, 256 * 1024, normalize_newlines=not filename.startswith("claude-trial-"))
    if hashlib.sha256(raw).hexdigest() != hashes[filename]:
        raise EvaluationError("Baseline artifact checksum mismatch")
    return raw.decode("utf-8")


def load_baseline(folder=None):
    folder = Path(folder) if folder is not None else Path(__file__).parent / "agent_baseline"
    manifest = json.loads(_read(folder / "manifest.json", 65536), object_pairs_hook=_unique_object)
    if manifest.get("schema") != "surface-map-agent-baseline/1":
        raise EvaluationError("Unsupported baseline manifest")
    corpus = load_corpus()
    if manifest.get("corpus_sha256") != corpus["sha256"]:
        raise EvaluationError("Baseline corpus identity mismatch")
    batches = manifest.get("batches")
    if not isinstance(batches, list) or len(batches) != 4:
        raise EvaluationError("Unsupported baseline batches")
    ids = []
    for batch in batches:
        if not isinstance(batch, list) or not batch or any(not isinstance(v, str) for v in batch):
            raise EvaluationError("Invalid baseline batch identities")
        ids.extend(batch)
    if len(ids) != 45 or len(set(ids)) != 45:
        raise EvaluationError("Invalid baseline case identities")
    cases = {case["id"]: case for case in corpus["cases"]}
    if any(case_id not in cases or "routes" not in cases[case_id]["expected"] for case_id in ids):
        raise EvaluationError("Unknown baseline case identity")
    truth = index_predictions([{"id": case_id, **{key: cases[case_id]["expected"][key]
                                               for key in ("routes", "unresolved")}} for case_id in ids], ids)
    hashes = manifest.get("files")
    if (not isinstance(hashes, dict) or len(hashes) != 14
            or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v) for v in hashes.values())):
        raise EvaluationError("Invalid baseline artifact hashes")
    receipts = manifest.get("batch_receipts")
    if not isinstance(receipts, list) or len(receipts) != 12:
        raise EvaluationError("Missing baseline batch receipts")
    receipt_keys = set()
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != {
                "trial", "batch", "started_at", "finished_at", "raw_response_file", "raw_response_sha256"}:
            raise EvaluationError("Malformed baseline batch receipt")
        trial, batch = receipt["trial"], receipt["batch"]
        if type(trial) is not int or type(batch) is not int or not 1 <= trial <= 3 or not 1 <= batch <= 4:
            raise EvaluationError("Malformed baseline receipt identity")
        identity = trial, batch
        if identity in receipt_keys:
            raise EvaluationError("Duplicate baseline receipt")
        receipt_keys.add(identity)
        filename = receipt["raw_response_file"]
        if (filename not in {f"claude-trial-{trial}-batch-{batch}.txt", f"claude-trial-{trial}-batch-{batch}.json"}
                or receipt["raw_response_sha256"] != hashes.get(filename)):
            raise EvaluationError("Baseline receipt hash mismatch")
        try:
            started = datetime.fromisoformat(receipt["started_at"])
            finished = datetime.fromisoformat(receipt["finished_at"])
        except (ValueError, TypeError):
            raise EvaluationError("Malformed baseline receipt timestamps") from None
        if started.tzinfo is None or finished.tzinfo is None or started > finished:
            raise EvaluationError("Malformed baseline receipt interval")
    _manifest_file(folder, "prompt-policy.txt", hashes)
    tool = index_predictions(parse_response(_manifest_file(folder, "surface-map-routes.json", hashes)), ids)
    trials = []
    for trial in range(1, 4):
        prediction = {}
        for batch, batch_ids in enumerate(batches, 1):
            stem = f"claude-trial-{trial}-batch-{batch}"
            names = [stem + suffix for suffix in (".txt", ".json") if stem + suffix in hashes]
            if len(names) != 1:
                raise EvaluationError("Missing or ambiguous baseline response")
            name = names[0]
            prediction.update(index_predictions(parse_response(_manifest_file(folder, name, hashes)), batch_ids))
        trials.append(prediction)
    return manifest, truth, tool, trials


def compare_baseline(manifest, truth, tool, trials):
    tool_score = score_predictions(truth, tool)
    trial_results = []
    for number, predictions in enumerate(trials, 1):
        agent_score = score_predictions(truth, predictions)
        paired = Counter(tool_only=0, agent_only=0, both_correct=0, both_incorrect=0)
        for case_id in truth:
            tool_ok = tool_score["results"][case_id]["passed"]
            agent_ok = agent_score["results"][case_id]["passed"]
            paired["both_correct" if tool_ok and agent_ok else "tool_only" if tool_ok else "agent_only" if agent_ok else "both_incorrect"] += 1
        trial_results.append({"trial": number, "agent": agent_score, "paired": paired})
    stable = sum(all(predictions[case_id] == trials[0][case_id] for predictions in trials[1:]) for case_id in truth)
    return {"schema": "surface-map-agent-comparison/1", "corpus_sha256": manifest["corpus_sha256"],
            "model": manifest["model"], "run_date": manifest["run_date"],
            "tool_engine": manifest["conditions"]["tool_engine"],
            "scope": "45 synthetic route/evidence tasks; three text-only agent trials in four fresh batches each. Not an MCP-selection, latency, or full 64-task comparison.",
            "unique_cases": len(truth), "agent_trials": len(trials), "stable_agent_cases": stable,
            "tool": tool_score, "trials": trial_results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tool-results", type=Path, help="Use predictions from a new evals.diverse JSON report")
    args = parser.parse_args(argv)
    try:
        manifest, truth, tool, trials = load_baseline()
        if args.tool_results:
            report = json.loads(_read(args.tool_results, 1024 * 1024), object_pairs_hook=_unique_object)
            if report.get("schema") != "surface-map-diverse-results/1" or report.get("corpus_sha256") != manifest["corpus_sha256"]:
                raise EvaluationError("New tool results use a different corpus")
            result_ids = [row["id"] for row in report["results"]]
            if len(result_ids) != len(set(result_ids)) or set(result_ids) != {case["id"] for case in load_corpus()["cases"]}:
                raise EvaluationError("Invalid tool result case identities")
            rows = [{"id": row["id"], **{key: row["prediction"][key] for key in ("routes", "unresolved")}}
                    for row in report["results"] if row["id"] in truth]
            tool = index_predictions(rows, truth)
            if report.get("engine") not in {"basic", "enhanced"}:
                raise EvaluationError("Unknown tool engine")
            manifest = {**manifest, "conditions": {**manifest["conditions"], "tool_engine": report["engine"]}}
        result = compare_baseline(manifest, truth, tool, trials)
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    except (EvaluationError, OSError, ValueError, KeyError, TypeError, RecursionError):
        print("Comparison rejected: invalid, incomplete or changed baseline inputs. No prediction details are returned.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
