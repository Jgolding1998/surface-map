"""Read bounded static web evidence from immutable Git objects, never check it out.

This is a narrow review aid. It does not execute the proposed application, inspect
every dependency, or establish runtime compatibility. Local same-account races
and replacement of the installed Git executable are outside this boundary.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import threading
import time

from . import ci
from .source_io import _windows_drive_type, _windows_local_path

MAX_FILES = 6
MAX_HTML_BYTES = 4 * 1024 * 1024
MAX_SCRIPT_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_LIST_BYTES = 256 * 1024
MAX_CONFIG_BYTES = 128 * 1024
GIT_SECONDS = 15.0
_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
_KINDS = {".html": "html", ".htm": "html", ".js": "javascript",
          ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
          ".ts": "typescript", ".tsx": "typescript"}
_SUFFIXES = frozenset(value.encode("ascii") for value in _KINDS)
_ERROR = ("Git comparison rejected: use an ordinary local repository, full commit IDs, "
          "and at most six supported UTF-8 files within the byte limits. Git must "
          "support --no-lazy-fetch. No input text or paths are returned.")
SCOPE = ("Static evidence from selected Git files only. The effective base is the "
         "unique merge base of the requested base and head commits. Unchanged source "
         "files and generated assets are not inspected unless explicitly selected. "
         "Imports are not followed automatically. Running pages and server behavior "
         "are never checked. A missing Git file is compared with an empty "
         "artifact; absent evidence does not prove removal from the application. "
         "Source text, repository paths and original filenames are not returned. "
         "Privacy normalization can hide route or field renames. Findings are changes "
         "for review, not proven bugs, a security audit or a complete app inventory.")


class GitCompareError(ValueError):
    def __init__(self):
        super().__init__(_ERROR)


def _ordinary(path, *, directory=False):
    path = Path(path).absolute()
    if os.name == "nt" and (not _windows_local_path(os.fspath(path))
                            or _windows_drive_type(path.anchor) not in {2, 3, 5, 6}):
        raise GitCompareError
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise GitCompareError
    mode = path.lstat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise GitCompareError
    return path


def _metadata(repo, deadline):
    """Deliberately refuse worktree gitfiles, redirects, includes and lazy stores."""
    repo = _ordinary(repo, directory=True)
    dotgit = repo / ".git"
    gitdir = _ordinary(dotgit if dotgit.exists() or dotgit.is_symlink() else repo,
                       directory=True)
    for name in ("HEAD", "config"):
        _ordinary(gitdir / name)
    _ordinary(gitdir / "objects", directory=True)
    for name in ("commondir", "config.worktree", "info/grafts", "shallow",
                 "objects/info/alternates", "objects/info/http-alternates"):
        value = gitdir / name
        if value.exists() or value.is_symlink():
            raise GitCompareError
    config = gitdir / "config"
    if config.stat().st_size > MAX_CONFIG_BYTES:
        raise GitCompareError
    with config.open("rb") as handle:
        contents = handle.read(MAX_CONFIG_BYTES + 1).decode("utf-8-sig")
    if (len(contents.encode("utf-8")) > MAX_CONFIG_BYTES
            or re.search(r"^\s*\[\s*include(?:if)?(?:\s|\])", contents, re.I | re.M)
            or re.search(r"^\s*(?:partialclone|promisor|worktreeconfig)\s*=", contents, re.I | re.M)):
        raise GitCompareError
    # Git reads its own metadata. Reject redirects inside it before starting Git.
    count = 0
    pending = [(gitdir, 0)]
    while pending:
        directory, depth = pending.pop()
        if depth > 20:
            raise GitCompareError
        for entry in directory.iterdir():
            count += 1
            if count > 100000 or time.monotonic() >= deadline:
                raise GitCompareError
            info = entry.lstat()
            if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
                    or entry.name.endswith(".promisor")):
                raise GitCompareError
            if stat.S_ISDIR(info.st_mode):
                pending.append((entry, depth + 1))
            elif not stat.S_ISREG(info.st_mode):
                raise GitCompareError
    return repo, gitdir


def _safe_path(value):
    if (type(value) is not str or not value or len(value.encode("utf-8")) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or "\\" in value or ":" in value):
        raise GitCompareError
    path = PurePosixPath(value)
    if (path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(part.lower() == ".git" for part in path.parts)):
        raise GitCompareError
    return value


def _supported_path_bytes(value):
    name = value.rsplit(b"/", 1)[-1]
    position = name.rfind(b".")
    return position > 0 and name[position:].lower() in _SUFFIXES


def _git_binary(repo):
    search = []
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        candidate = Path(entry).resolve()
        if candidate == Path.cwd().resolve() or candidate.is_relative_to(repo.resolve()):
            continue
        search.append(str(candidate))
    # Do not use shutil.which: older Windows Python can search cwd before the
    # supplied PATH. Every candidate here is constructed as an absolute path.
    for directory in search:
        binary = (Path(directory) / ("git.exe" if os.name == "nt" else "git")).resolve()
        if binary.is_relative_to(repo.resolve()):
            continue
        try:
            _ordinary(binary)
        except (OSError, ValueError):
            continue
        if os.name != "nt" and not os.access(binary, os.X_OK):
            continue
        return str(binary), os.pathsep.join(search)
    raise GitCompareError


def _environment(path):
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP")
           if key in os.environ}
    env.update({"PATH": path, "LC_ALL": "C", "LANG": "C", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
                "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
                "GIT_ATTR_NOSYSTEM": "1", "GIT_LITERAL_PATHSPECS": "1"})
    return env


class _Git:
    def __init__(self, repo):
        self.deadline = time.monotonic() + GIT_SECONDS
        self.repo, self.gitdir = _metadata(repo, self.deadline)
        self.binary, path = _git_binary(self.repo)
        self.env = _environment(path)
        self.prefix = [self.binary, "--no-pager", "--no-optional-locks", "--no-lazy-fetch",
                       "--literal-pathspecs", "-c", "core.fsmonitor=false", "-c",
                       "core.hooksPath=" + os.devnull, "-c", "core.attributesFile=" + os.devnull]
        # Parse the explicit file without selecting the untrusted Git directory.
        # Git's own parser handles valid same-line section/key syntax that a
        # lexical precheck cannot reliably recognize. Includes are never followed.
        config = self.run("config", "--file", str(self.gitdir / "config"),
                          "--no-includes", "--null", "--list", limit=MAX_CONFIG_BYTES)
        for record in config.split(b"\x00"):
            if not record:
                continue
            key = record.split(b"\n", 1)[0].decode("utf-8").lower()
            if (key == "include.path" or key.startswith("includeif.")
                    or key in {"extensions.partialclone", "extensions.worktreeconfig", "core.worktree"}
                    or key.startswith("remote.") and key.endswith(".promisor")):
                raise GitCompareError
        self.prefix.append("--git-dir=" + str(self.gitdir))

    def run(self, *arguments, limit=MAX_LIST_BYTES):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise GitCompareError
        # Never use the data repository as cwd: Windows executable lookup must
        # not find a PR-supplied git.exe. The executable is already absolute.
        process = subprocess.Popen(self.prefix + list(arguments), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   cwd=Path(__file__).resolve().parents[1], env=self.env,
                                   shell=False, close_fds=True,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        chunks, state = [], {"size": 0, "over": False, "error": False}

        def read():
            try:
                while True:
                    data = process.stdout.read(min(65536, limit + 1 - state["size"]))
                    if not data:
                        break
                    state["size"] += len(data)
                    if state["size"] > limit:
                        state["over"] = True
                        break
                    chunks.append(data)
            except (OSError, ValueError):
                state["error"] = True

        reader = threading.Thread(target=read, daemon=True)
        started = False
        try:
            reader.start()
            started = True
            reader.join(remaining)
            if reader.is_alive() or state["over"] or state["error"]:
                raise GitCompareError
            code = process.wait(timeout=max(0.01, self.deadline - time.monotonic()))
            if code:
                raise GitCompareError
            return b"".join(chunks)
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            if started:
                reader.join(1)
            process.stdout.close()

    def commit(self, value, length):
        if type(value) is not str or not _SHA.fullmatch(value) or len(value) != length:
            raise GitCompareError
        value = value.lower()
        if self.run("cat-file", "-t", value, limit=32).strip() != b"commit":
            raise GitCompareError
        return value

    def blob(self, commit, path, maximum):
        entry = self.run("ls-tree", "-z", commit, "--", path, limit=8192)
        if not entry:
            return None
        if not entry.endswith(b"\x00") or entry.count(b"\x00") != 1:
            raise GitCompareError
        metadata, actual = entry[:-1].split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split(" ")
        if actual.decode("utf-8") != path or mode not in {"100644", "100755"} or kind != "blob":
            raise GitCompareError
        oid = oid.decode("ascii") if isinstance(oid, bytes) else oid
        if not _SHA.fullmatch(oid):
            raise GitCompareError
        size = self.run("cat-file", "-s", oid, limit=32).strip()
        if not size.isdigit() or int(size) > maximum:
            raise GitCompareError
        data = self.run("cat-file", "blob", oid, limit=maximum)
        if len(data) != int(size):
            raise GitCompareError
        if b"\x00" in data:
            raise GitCompareError
        text = data.decode("utf-8-sig")
        if text.lstrip("\ufeff").splitlines()[:1] == ["version https://git-lfs.github.com/spec/v1"]:
            raise GitCompareError
        return text


def compare_git(repo, base, head, *, files=None, javascript_engine="enhanced", fail_on=()):
    """Compare selected static files against the unique PR merge base.

    Added/deleted paths receive an empty artifact on their missing side. Filenames
    stay local; source_1 corresponds to the first selected path on both sides.
    """
    try:
        if any(type(value) is not str or not _SHA.fullmatch(value) for value in (base, head)):
            raise GitCompareError
        policies = ci.parse_fail_on(fail_on)
        if javascript_engine not in {"enhanced", "basic", "auto"}:
            raise GitCompareError
        git = _Git(repo)
        object_format = git.run("rev-parse", "--show-object-format", limit=16).strip()
        if object_format not in {b"sha1", b"sha256"}:
            raise GitCompareError
        length = 40 if object_format == b"sha1" else 64
        requested_base, head = git.commit(base, length), git.commit(head, length)
        bases = git.run("merge-base", "--all", requested_base, head, limit=132).decode("ascii").splitlines()
        if len(bases) != 1 or not _SHA.fullmatch(bases[0]) or len(bases[0]) != length:
            raise GitCompareError
        base = git.commit(bases[0], length)
        listing = git.run("diff-tree", "-r", "--no-commit-id", "--name-only", "-z",
                          "--no-renames", "--no-ext-diff", "--no-textconv", base, head, "--")
        if listing and not listing.endswith(b"\x00"):
            raise GitCompareError
        changed = [value for value in listing.split(b"\x00") if value]
        # Unsupported file names stay opaque bytes. A strange documentation or
        # image name should not prevent the selected web evidence being checked.
        supported = [value for value in changed if _supported_path_bytes(value)]
        excluded = len(changed) - len(supported)
        explicit = files is not None
        if explicit:
            if type(files) not in {list, tuple} or not 1 <= len(files) <= MAX_FILES:
                raise GitCompareError
            paths = [_safe_path(path) for path in files]
            if len(set(paths)) != len(paths) or any(PurePosixPath(path).suffix.lower() not in _KINDS for path in paths):
                raise GitCompareError
        else:
            paths = [_safe_path(value.decode("utf-8")) for value in supported]
            if len(paths) > MAX_FILES:
                raise GitCompareError
        paths = sorted(paths)
        selection = {"mode": "explicit_files" if explicit else "changed_web_files",
                     "selected_files": len(paths), "unsupported_changed_files": excluded,
                     "supported_changed_files_not_selected": len(set(supported) - {path.encode("utf-8") for path in paths}),
                     "missing_base_files": 0, "missing_head_files": 0}
        result = {"format": "surface-map-git-comparison-v1", "requested_base_commit": requested_base,
                  "base_commit": base, "head_commit": head, "selection": selection,
                  "analysis_performed": bool(paths), "evidence_comparison": None,
                  "policy": {"fail_on": policies, "matched": [], "passed": None, "status": "not_applicable"},
                  "scope": SCOPE}
        if paths:
            captures = [{"artifacts": []}, {"artifacts": []}]
            totals = [0, 0]
            for path in paths:
                kind = _KINDS[PurePosixPath(path).suffix.lower()]
                values = [git.blob(commit, path, MAX_HTML_BYTES if kind == "html" else MAX_SCRIPT_BYTES)
                          for commit in (base, head)]
                if values == [None, None]:
                    raise GitCompareError
                for index, value in enumerate(values):
                    if value is None:
                        selection["missing_base_files" if index == 0 else "missing_head_files"] += 1
                        value = ""
                    totals[index] += len(value.encode("utf-8"))
                    if totals[index] > MAX_TOTAL_BYTES:
                        raise GitCompareError
                    captures[index]["artifacts"].append({"kind": kind, "text": value})
            evidence = ci.compare_captures(*captures, javascript_engine=javascript_engine,
                                           fail_on=policies, include_assets=True)
            result["evidence_comparison"], result["policy"] = evidence, evidence["policy"]
        ci.json_output(result)
        return result
    except Exception:
        raise GitCompareError from None


def markdown(result):
    """Render only validated fixed metadata and the existing minimized report."""
    if type(result) is not dict or result.get("format") != "surface-map-git-comparison-v1":
        raise GitCompareError
    try:
        for key in ("requested_base_commit", "base_commit", "head_commit"):
            if type(result[key]) is not str or not _SHA.fullmatch(result[key]):
                raise GitCompareError
        selection = result["selection"]
        for key in ("selected_files", "unsupported_changed_files", "supported_changed_files_not_selected",
                    "missing_base_files", "missing_head_files"):
            if type(selection[key]) is not int or not 0 <= selection[key] <= MAX_LIST_BYTES:
                raise GitCompareError
        lines = ["## Surface Map static PR evidence", "", SCOPE, "",
                 "Requested base: `" + result["requested_base_commit"] + "`.",
                 "Effective merge base: `" + result["base_commit"] + "`.",
                 "Head: `" + result["head_commit"] + "`.", "",
                 "Selected files: " + str(selection["selected_files"]) + ". Unsupported changed files excluded: "
                 + str(selection["unsupported_changed_files"]) + ".",
                 "Supported changed files outside this selection: "
                 + str(selection["supported_changed_files_not_selected"]) + ".",
                 "Files missing at base/head: " + str(selection["missing_base_files"]) + "/"
                 + str(selection["missing_head_files"]) + ".", ""]
        if not result["analysis_performed"]:
            lines.append("Not applicable: no supported changed web files were selected. No application evidence was analyzed; this is not an all-clear result.")
        else:
            lines.append(ci.markdown(result["evidence_comparison"]))
        output = "\n".join(lines) + "\n"
        if len(output.encode("utf-8")) > ci.MAX_OUTPUT_BYTES:
            raise GitCompareError
        return output
    except Exception:
        raise GitCompareError from None
