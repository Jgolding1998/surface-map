from __future__ import annotations

import json
import ntpath
import os
import stat
from pathlib import Path

from .analyze import ArtifactError, strict_json
from .source import MAX_ARTIFACTS, MAX_FILE_BYTES, MAX_TOTAL_BYTES


def inspect_sources(artifacts, *, javascript_engine="auto"):
    from .source import source_inventory

    return source_inventory(artifacts, javascript_engine=javascript_engine)


def source_kind(name):
    filename = Path(name).name.lower()
    manifests = {"requirements.txt": "requirements", "pyproject.toml": "pyproject", "package.json": "package", "cargo.toml": "cargo"}
    if filename in manifests:
        return manifests[filename]
    extension = Path(filename).suffix
    return {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript",
            ".mjs": "javascript", ".cjs": "javascript", ".rs": "rust", ".sh": "shell", ".bash": "shell"}.get(extension)


def _windows_local_path(value):
    drive, tail = ntpath.splitdrive(value)
    return not drive.startswith(("\\", "/")) and not tail.startswith(("\\\\", "//")) and ":" not in tail


def _windows_drive_type(root):
    import ctypes

    query = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    query.argtypes = [ctypes.c_wchar_p]
    query.restype = ctypes.c_uint
    return query(root)


def read_ordinary(path, limit):
    try:
        if os.name == "nt" and not _windows_local_path(os.fspath(path)):
            raise ValueError
        path = Path(path).absolute()
        if os.name == "nt" and (not _windows_local_path(os.fspath(path)) or _windows_drive_type(path.anchor) not in {2, 3, 5, 6}):
            raise ValueError
        for component in (*reversed(path.parents), path):
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise ValueError
        return data.decode("utf-8")
    except (OSError, ValueError, UnicodeError):
        raise ArtifactError("Choose ordinary local UTF-8 files within the supported byte limits") from None


def capture_sources(path):
    document = strict_json(read_ordinary(path, 3 * 1024 * 1024), depth=6, nodes=100)
    if type(document) is not dict or set(document) != {"artifacts"}:
        raise ArtifactError("A source capture must contain only an artifacts array")
    return document["artifacts"]


def selected_sources(paths):
    if not 1 <= len(paths) <= MAX_ARTIFACTS:
        raise ArtifactError("Choose 1 to 6 selected source files")
    result, total = [], 0
    for path in paths:
        kind = source_kind(path)
        if kind is None:
            raise ArtifactError("Selected source format is unsupported")
        content = read_ordinary(path, MAX_FILE_BYTES)
        total += len(content.encode("utf-8"))
        if total > MAX_TOTAL_BYTES:
            raise ArtifactError("Selected sources exceed the combined byte limit")
        result.append({"kind": kind, "content": content})
    return result


def compare_sources(before, after):
    if any(source["status"] != "parsed" for report in (before, after) for source in report["sources"]):
        raise ArtifactError("Source comparison requires fully parsed selections")
    result = {"format": "surface-map-source-diff-v1", "groups": {},
              "note": "Differences describe selected static evidence only. An absent declaration is not proof that a feature or dependency was removed from the application."}
    for group in ("languages", "dependencies", "capabilities", "install_hints"):
        def keyed(report):
            return {(entry.get("ecosystem", ""), entry["name"]): entry for entry in report[group]}

        old, new = keyed(before), keyed(after)
        changed = [{**new[key], "previous_confidence": old[key].get("confidence", "static")}
                   for key in sorted(new.keys() & old.keys()) if old[key].get("confidence") != new[key].get("confidence")]
        result["groups"][group] = {"added": [new[key] for key in sorted(new.keys() - old.keys())],
                                  "absent": [old[key] for key in sorted(old.keys() - new.keys())], "changed": changed}
    return result


def source_examples():
    return [
        {"kind": "pyproject", "content": '[project]\nname="example-app"\ndependencies=["fastapi>=0.100", "uvicorn"]\n[project.scripts]\nexample="example:main"\n'},
        {"kind": "package", "content": json.dumps({"dependencies": {"react": "*"}, "devDependencies": {"vite": "*", "typescript": "*"}, "scripts": {"build": "vite build"}})},
        {"kind": "python", "content": "from fastapi import FastAPI\nimport sounddevice\n"},
    ]
