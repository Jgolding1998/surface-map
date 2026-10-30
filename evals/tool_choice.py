"""Score a description-only routing rehearsal; this does not invoke MCP tools."""
import argparse
import json
from pathlib import Path

FOLDER = Path(__file__).parent


def score(cases, choices):
    expected = {case["id"]: case["expected"] for case in cases}
    if not isinstance(choices, list) or len(choices) != len(expected):
        raise ValueError("Supply exactly one prediction per case")
    predicted = {}
    allowed = {"analyze_url", "analyze_capture", "compare_captures", "inspect_sources", "limits", "demo", "none"}
    for row in choices:
        if (not isinstance(row, dict) or set(row) != {"id", "tool"} or not isinstance(row["id"], str)
                or row["id"] not in expected or row["id"] in predicted
                or not isinstance(row["tool"], str) or row["tool"] not in allowed):
            raise ValueError("Invalid or duplicate prediction")
        predicted[row["id"]] = row["tool"]
    failed = [key for key in expected if predicted[key] != expected[key]]
    return {"cases": len(expected), "passed": len(expected) - len(failed), "failed_ids": failed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path, help='JSON with a "choices" array of id/tool objects')
    args = parser.parse_args()
    try:
        if args.predictions.stat().st_size > 65536:
            raise ValueError("Oversized predictions")
        cases = json.loads((FOLDER / "tool_choice.json").read_text(encoding="utf-8"))["cases"]
        choices = json.loads(args.predictions.read_text(encoding="utf-8"))["choices"]
        result = score(cases, choices)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(2, "Invalid routing predictions\n")
    print(json.dumps(result, indent=2))
    return int(bool(result["failed_ids"]))


if __name__ == "__main__":
    raise SystemExit(main())
