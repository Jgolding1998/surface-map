"""Trusted Action entry point: read Git objects, never run the target checkout."""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(environ=None):
    env = os.environ if environ is None else environ
    # Import failures must stay inside the fixed generic error boundary too.
    selection_errors = ()
    try:
        from surface_map.ci import json_output, parse_fail_on
        from surface_map.git_compare import compare_git, markdown, GitSelectionLimitError, selection_limit_message
        selection_errors = GitSelectionLimitError
        from surface_map.source_io import read_ordinary
        from scripts.ci_action import _ordinary_components, _workspace_path, _write_report

        raw_workspace = env.get("GITHUB_WORKSPACE", "")
        if not raw_workspace or any(c in raw_workspace for c in "\r\n\x00"):
            raise ValueError
        workspace = Path(raw_workspace).absolute()
        if not workspace.is_dir():
            raise ValueError
        _ordinary_components(workspace)
        repository = _workspace_path(env.get("SURFACE_MAP_REPOSITORY", "."), workspace)
        destination = _workspace_path(env.get("SURFACE_MAP_OUTPUT_DIRECTORY", "surface-map-pr-results"), workspace)
        if destination.exists():
            raise ValueError
        selected = env.get("SURFACE_MAP_FILES", "")
        if not isinstance(selected, str) or len(selected) > 8192:
            raise ValueError
        files = selected.splitlines() if selected else None
        if files is not None and any(not path for path in files):
            raise ValueError
        origin_file = env.get("SURFACE_MAP_SITE_ORIGIN_FILE", "")
        site_origin = None
        if origin_file:
            metadata = _workspace_path(origin_file, workspace)
            site_origin = read_ordinary(metadata, 4096).strip("\t\r\n ")
        result = compare_git(repository, env.get("SURFACE_MAP_BASE", ""), env.get("SURFACE_MAP_HEAD", ""),
                             files=files, javascript_engine=env.get("SURFACE_MAP_ENGINE", "enhanced"),
                             fail_on=parse_fail_on(env.get("SURFACE_MAP_FAIL_ON", "")), site_origin=site_origin)
        rendered = json_output(result), markdown(result)
        destination.mkdir(parents=True, exist_ok=False)
        paths = destination / "comparison.json", destination / "comparison.md"
        for path, contents in zip(paths, rendered):
            _write_report(path, contents)
        policy = result["policy"]
        performed = result["analysis_performed"]
        status = policy["status"]
        if status not in {"advisory", "passed", "failed", "not_applicable"} or type(performed) is not bool:
            raise ValueError
        if env.get("GITHUB_OUTPUT"):
            verdict = str(policy["passed"]).lower() if status in {"passed", "failed"} else ""
            with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"report-json={paths[0]}\nreport-markdown={paths[1]}\n")
                handle.write(f"analysis-performed={str(performed).lower()}\npolicy-status={status}\npolicy-passed={verdict}\n")
        if env.get("GITHUB_STEP_SUMMARY"):
            with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered[1] + "\n")
        print("Surface Map Git comparison complete: " + status + ".")
        return 1 if status == "failed" else 0
    except selection_errors as error:
        print(selection_limit_message(error, action=True), file=sys.stderr)
        return 2
    except Exception:
        print("Surface Map Git comparison rejected: invalid or unavailable Git inputs, unsupported selection, incomplete evidence, or report destination. No input text or paths are returned.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
