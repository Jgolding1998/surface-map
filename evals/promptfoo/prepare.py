"""Generate versioned Promptfoo cases from the checksum-verified synthetic corpus."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.diverse.run import load_corpus

FOLDER = Path(__file__).resolve().parent
MCP_CASE_IDS = {"post-form-fields", "har-json-shapes", "comparison-status", "agent-hidden-text"}


def basic_case(case):
    return not any(artifact["kind"] in {"javascript", "typescript"}
                   for key in ("artifacts", "before", "after") for artifact in case.get(key, []))


def test_case(case, checksum, *, mcp=False):
    variables = {"case_id": case["id"]}
    if mcp:
        variables["transport"] = "mcp"
        args = {"javascript_engine": "basic"}
        if case["task"] == "compare":
            tool = "compare_captures"
            for key in ("before", "after", "before_document_url", "after_document_url"):
                if key in case:
                    args[key] = case[key]
        else:
            tool = "analyze_capture"
            for key in ("artifacts", "document_url", "include_page_signals"):
                if key in case:
                    args[key] = case[key]
        variables["tool_call"] = json.dumps({"tool": tool, "args": args}, ensure_ascii=False)
    return {"description": case["id"], "vars": variables,
            "metadata": {"family": case["family"], "corpus_sha256": checksum,
                         "synthetic": True, "task": case["task"]}}


def generated_files():
    corpus = load_corpus()
    selections = {
        "tests-basic.json": [(case, False) for case in corpus["cases"] if basic_case(case)],
        "tests-enhanced.json": [(case, False) for case in corpus["cases"]],
        "tests-mcp.json": [(case, True) for case in corpus["cases"] if case["id"] in MCP_CASE_IDS],
    }
    return {name: json.dumps([test_case(case, corpus["sha256"], mcp=mcp)
                              for case, mcp in cases], indent=2, ensure_ascii=False) + "\n"
            for name, cases in selections.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Reject stale generated case files without writing")
    args = parser.parse_args(argv)
    files = generated_files()
    if args.check:
        if any(not (FOLDER / name).is_file() or (FOLDER / name).read_text(encoding="utf-8") != text
               for name, text in files.items()):
            print("Promptfoo cases are stale; run python evals/promptfoo/prepare.py")
            return 1
    else:
        for name, text in files.items():
            (FOLDER / name).write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
