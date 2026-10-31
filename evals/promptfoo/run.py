"""Run the locally pinned Promptfoo CLI without shell interpolation or uploads."""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

FOLDER = Path(__file__).resolve().parent
ROOT = FOLDER.parents[1]
sys.path.insert(0, str(ROOT))

from evals.promptfoo.prepare import generated_files, main as check_cases

REPEATS = 3
CASE_COUNTS = {"basic": 46, "enhanced": 64, "mcp": 4}


def validate_export(document, mode):
    """Fail a partial, duplicated, cached or internally inconsistent successful run."""
    if mode not in CASE_COUNTS or not isinstance(document, dict):
        raise ValueError("Unsupported evaluation export")
    tests = json.loads(generated_files()[f"tests-{'basic' if mode == 'basic' else mode}.json"])
    case_ids = {case["vars"]["case_id"] for case in tests}
    if len(case_ids) != CASE_COUNTS[mode]:
        raise ValueError("Unexpected evaluation suite size")
    expected_total = len(case_ids) * REPEATS
    results = document["results"]
    stats = results["stats"]
    if any(type(stats.get(key)) is not int or stats[key] != expected
           for key, expected in (("successes", expected_total), ("failures", 0), ("errors", 0))):
        raise ValueError("Incomplete or failed evaluation statistics")
    rows = results["results"]
    if not isinstance(rows, list) or len(rows) != expected_total:
        raise ValueError("Incomplete evaluation rows")
    seen = set()
    for row in rows:
        case_id = row["vars"]["case_id"]
        repeat = row["repeatIndex"]
        identity = (case_id, repeat)
        if (case_id not in case_ids or type(repeat) is not int or not 0 <= repeat < REPEATS
                or identity in seen or row.get("success") is not True
                or row.get("error") or row["response"].get("error")
                or row["response"].get("cached", False) is not False
                or row.get("promptIdx") != 0
                or row["gradingResult"].get("pass") is not True
                or row["namedScores"].get("exact_case") != 1):
            raise ValueError("Failed, cached, duplicate or unexpected evaluation row")
        seen.add(identity)
    expected_pairs = {(case_id, repeat) for case_id in case_ids for repeat in range(REPEATS)}
    if seen != expected_pairs:
        raise ValueError("Missing evaluation repetitions")
    stability = results["repeatStability"]
    if any(type(stability.get(key)) is not int or stability[key] != expected
           for key, expected in (("totalGroups", len(case_ids)), ("unstableGroups", 0),
                                  ("groupsWithErrors", 0), ("cachedResults", 0))):
        raise ValueError("Unstable, cached or incomplete repeat summary")
    groups = stability["groups"]
    if not isinstance(groups, list) or len(groups) != len(case_ids):
        raise ValueError("Incomplete repeat groups")
    group_ids = set()
    for group in groups:
        case_id = group["description"]
        if (case_id not in case_ids or case_id in group_ids or group.get("unstable") is not False
                or any(type(group.get(key)) is not int or group[key] != expected
                       for key, expected in (("repetitions", REPEATS), ("passed", REPEATS),
                                             ("failed", 0), ("errors", 0), ("cached", 0)))):
            raise ValueError("Incomplete or unsuccessful repeat group")
        group_ids.add(case_id)
    if document.get("shareableUrl") is not None:
        raise ValueError("Unexpected shared evaluation URL")
    return expected_total


def finish_run(returncode, result_path, mode):
    """Preserve vendor failures; independently check every supposedly passing run."""
    if returncode:
        return returncode
    try:
        if result_path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Evaluation export too large")
        total = validate_export(json.loads(result_path.read_text(encoding="utf-8")), mode)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        print("Evaluation postflight failed: missing, partial, cached or inconsistent results.")
        return 1
    print(f"Verified {total}/{total} successful calls, {CASE_COUNTS[mode]} cases x {REPEATS} fresh repetitions, zero cached results.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("basic", "enhanced", "mcp", "view"), default="basic", nargs="?")
    args = parser.parse_args(argv)
    if args.mode == "view":
        from evals.promptfoo.viewer import main as view_reports
        return view_reports()
    node = shutil.which("node")
    cli = FOLDER / "node_modules" / "promptfoo" / "dist" / "src" / "main.js"
    if not node or not cli.is_file():
        print("Install the pinned optional evaluation UI: cd evals/promptfoo, then npm ci --ignore-scripts")
        return 2
    if check_cases(["--check"]):
        return 2
    if args.mode == "mcp":
        pinned_mcp = next(line.split("==", 1)[1].strip()
                          for line in (ROOT / "requirements-mcp.txt").read_text(encoding="utf-8").splitlines()
                          if line.startswith("mcp=="))
        try:
            installed_mcp = importlib.metadata.version("mcp")
        except importlib.metadata.PackageNotFoundError:
            installed_mcp = None
        if importlib.util.find_spec("mcp") is None or installed_mcp != pinned_mcp:
            print(f"MCP transport requires mcp=={pinned_mcp}; install requirements-mcp.txt in a virtual environment first.")
            return 2
    if args.mode == "enhanced":
        from surface_map.js_parser import available
        if not available()[0]:
            print("Enhanced parser unavailable; run the documented setup-js command first.")
            return 2
    env = dict(os.environ)
    env["PROMPTFOO_PYTHON"] = sys.executable
    env["PROMPTFOO_DISABLE_TELEMETRY"] = "1"
    env["PROMPTFOO_DISABLE_UPDATE"] = "1"
    env["PROMPTFOO_PASS_RATE_THRESHOLD"] = "100"
    env["PROMPTFOO_FAILED_TEST_EXIT_CODE"] = "1"
    env["PROMPTFOO_CONFIG_DIR"] = str(FOLDER / "results" / ".promptfoo")
    # The MCP .py path launcher uses python from PATH. Ensure the same explicit
    # interpreter selected above (including an active virtual environment).
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    config = "promptfooconfig.yaml" if args.mode == "basic" else args.mode + ".yaml"
    (FOLDER / "results").mkdir(exist_ok=True)
    result_path = FOLDER / "results" / f"{args.mode}.json"
    # A vendor success without a new export must not validate an old passing run.
    result_path.unlink(missing_ok=True)
    command = [node, str(cli), "eval", "--config", config, "--no-cache", "--no-share",
               "--no-progress-bar", "--no-table", "--output", f"results/{args.mode}.json",
               "--output", f"results/{args.mode}.html", "--output", f"results/{args.mode}.junit.xml"]
    result = subprocess.run(command, cwd=FOLDER, env=env, check=False)
    return finish_run(result.returncode, result_path, args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
