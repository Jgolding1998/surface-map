"""Reuse the published diverse-task rubric for direct Python and MCP outputs."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.diverse.run import project_comparison, project_report, score
from evals.promptfoo.provider import case_by_id


def get_assert(output, context):
    """Fail malformed/error outputs instead of silently dropping them."""
    try:
        _, case = case_by_id(context.get("vars", {}).get("case_id"))
        observed = json.loads(output) if isinstance(output, str) else output
        if not isinstance(observed, dict):
            raise ValueError("Expected an object")
        if context.get("vars", {}).get("transport") == "mcp":
            serialized = json.dumps(observed, ensure_ascii=False)
            observed = (project_comparison(observed["comparison"])
                        if case["task"] == "compare" else project_report(observed))
            if any(value in serialized for value in case["expected"].get("forbidden", [])):
                observed["privacy_failure"] = True
        failures = score(case, observed)
    except (KeyError, TypeError, ValueError, RecursionError):
        return {"pass": False, "score": 0, "reason": "Malformed or unavailable evaluation output"}
    return {"pass": not failures, "score": int(not failures),
            "reason": ", ".join(failures) if failures else "Published objective case rubric passed"}
