"""Trusted composite-action entry point. PR captures are data, never commands."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

# Do not import Python modules from the caller's checkout or captured artifacts.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _ordinary_components(path):
    for part in (*reversed(path.parents), path):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError


def _workspace_path(value, workspace):
    if not isinstance(value, str) or not value or any(c in value for c in "\r\n\x00\\"):
        raise ValueError
    relative = Path(value)
    if relative.is_absolute() or relative.drive or ".." in relative.parts or ":" in value:
        raise ValueError
    result = workspace / relative
    _ordinary_components(result)
    if not result.resolve().is_relative_to(workspace.resolve()):
        raise ValueError
    return result


def _write_report(path, text):
    _ordinary_components(path)
    if path.exists():
        raise ValueError
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main(environ=None):
    env = os.environ if environ is None else environ
    try:
        from surface_map.ci import compare_captures, json_output, load_capture, markdown, parse_fail_on

        workspace_value = env.get("GITHUB_WORKSPACE", "")
        if not workspace_value or any(c in workspace_value for c in "\r\n\x00"):
            raise ValueError
        workspace = Path(workspace_value).absolute()
        if not workspace.is_dir():
            raise ValueError
        _ordinary_components(workspace)
        before = _workspace_path(env.get("SURFACE_MAP_BEFORE"), workspace)
        after = _workspace_path(env.get("SURFACE_MAP_AFTER"), workspace)
        output = _workspace_path(env.get("SURFACE_MAP_OUTPUT_DIRECTORY", "surface-map-results"), workspace)
        paths = (output / "comparison.json", output / "comparison.md")
        if any(target.absolute() in (before.absolute(), after.absolute()) for target in paths):
            raise ValueError
        engine = env.get("SURFACE_MAP_ENGINE", "enhanced")
        fail_on = parse_fail_on(env.get("SURFACE_MAP_FAIL_ON", ""))
        result = compare_captures(load_capture(before), load_capture(after),
                                  javascript_engine=engine, fail_on=fail_on)
        rendered = (json_output(result), markdown(result))
        # Reserve a fresh directory so a rerun cannot overwrite caller data.
        output.mkdir(parents=True, exist_ok=False)
        for path, contents in zip(paths, rendered):
            _write_report(path, contents)
        passed = bool(result["policy"]["passed"])
        status = result["policy"]["status"]
        if env.get("GITHUB_OUTPUT"):
            with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8", newline="\n") as handle:
                policy_output = "" if status == "advisory" else str(passed).lower()
                handle.write(f"report-json={paths[0]}\nreport-markdown={paths[1]}\npolicy-passed={policy_output}\npolicy-status={status}\n")
        if env.get("GITHUB_STEP_SUMMARY"):
            with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered[1] + "\n")
        print("Surface Map comparison complete; " + ("advisory report (no policies selected)." if status == "advisory"
              else "selected evidence policies " + ("passed." if passed else "failed.")))
        return 0 if passed else 1
    except Exception:
        # No filenames, capture data, arbitrary exception text or success output.
        print("Surface Map comparison rejected: invalid inputs, incomplete coverage, unavailable parser, or report destination.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
