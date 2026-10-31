"""Promptfoo Python provider; calls the existing analyzer and objective evaluator."""
from __future__ import annotations

import json
from pathlib import Path
import sys

# Promptfoo imports this file from its own Python worker. Anchor the trusted
# checkout rather than depending on the worker's current directory or PYTHONPATH.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.diverse.run import load_corpus, run_case
from surface_map.analyze import ArtifactError, analyze
from surface_map.export import compare


def case_by_id(case_id):
    corpus = load_corpus()
    cases = {case["id"]: case for case in corpus["cases"]}
    if not isinstance(case_id, str) or case_id not in cases:
        raise ValueError("Unknown frozen evaluation case")
    return corpus, cases[case_id]


def call_api(prompt, options, context):
    """Return a scored projection without an AI API, network request or MCP hop."""
    engine = options.get("config", {}).get("engine", "basic")
    if engine not in {"basic", "enhanced"}:
        raise ValueError("Select basic or enhanced analysis explicitly")
    case_id = context.get("vars", {}).get("case_id")
    if prompt != case_id:
        raise ValueError("The direct provider expects a frozen case identity")
    corpus, case = case_by_id(case_id)
    projection = run_case(case, engine, analyze=analyze, compare=compare,
                          artifact_error=ArtifactError)
    return {
        "output": json.dumps(projection, ensure_ascii=False, sort_keys=True),
        "metadata": {"case_id": case_id, "family": case["family"],
                     "corpus_sha256": corpus["sha256"], "engine": engine,
                     "condition": "direct-python", "synthetic": True},
    }
