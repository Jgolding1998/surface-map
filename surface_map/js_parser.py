"""Bounded transport to a pinned parser. Supplied JavaScript remains data."""
from __future__ import annotations

import hashlib
import atexit
import json
import math
import os
from pathlib import Path
import re
import queue
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

ANALYZER = Path(__file__).resolve().parents[1] / "analyzers" / "javascript"
MAX_SOURCE = 512 * 1024
MAX_OUTPUT = 8 * 1024 * 1024
MAX_NODES = 100000
MAX_EDGES = 200000
TIMEOUT = 3.0
MAX_WORKERS = 2
MAX_WAITERS = 2
IDLE_SECONDS = 5.0
MAX_AGE = 30.0
MAX_REQUESTS = 128
REAP_INTERVAL = 0.25
_PARSER = "node_modules/@babel/parser/lib/index.js"
_PACKAGE = "node_modules/@babel/parser/package.json"
_FILES = {"parse.mjs", "package.json", "package-lock.json", _PARSER, _PACKAGE}
_CODES = {"invalid_input", "input_limit", "syntax", "limit", "busy", "timeout", "output_limit", "failed", "node_missing", "node_unsupported", "parser_missing", "parser_integrity", "npm_missing", "setup_failed"}
_NAME = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*\Z")
_VERSION_CACHE = {}
_CACHE_LOCK = threading.Lock()
_SETUP_LOCK = threading.Lock()
_GATE = (
    "import os,subprocess,sys\n"
    "token=bytearray()\n"
    "while len(token)<32:\n"
    " b=os.read(0,1)\n"
    " if not b: raise SystemExit(125)\n"
    " token.extend(b)\n"
    " if b==b'\\n': break\n"
    "if token!=(str(os.getpid())+'\\n').encode('ascii'): raise SystemExit(125)\n"
    "code=subprocess.call(sys.argv[1:],stdin=sys.stdin.buffer,shell=False,close_fds=True,creationflags=subprocess.CREATE_NO_WINDOW)\n"
    "os._exit(code if code<2**31 else code-2**32)\n"
)


class JsParserError(ValueError):
    def __init__(self, code="failed"):
        self.code = code if code in _CODES else "failed"
        super().__init__(self.code)


def _ordinary(path: Path) -> bool:
    """Reject links before opening. This does not defeat same-user replacement races."""
    try:
        if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
            return False
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return False
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return False
        return True
    except (OSError, ValueError):
        return False


def _search_path() -> str:
    cwd = os.path.normcase(os.path.abspath(os.getcwd()))
    result = []
    for value in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if not value or not os.path.isabs(value):
            continue
        value = os.path.abspath(value)
        if os.path.normcase(value) != cwd:
            result.append(value)
    return os.pathsep.join(result)


def _node() -> Path:
    explicit = os.environ.get("SURFACE_MAP_NODE")
    if explicit:
        path = Path(explicit)
    else:
        found = shutil.which("node.exe" if os.name == "nt" else "node", path=_search_path())
        if not found:
            raise JsParserError("node_missing")
        path = Path(found)
    if (os.name == "nt" and path.suffix.lower() != ".exe") or not _ordinary(path):
        raise JsParserError("node_missing")
    return path


def _environment(node: Path, home: Path) -> dict:
    env = {"PATH": str(node.parent), "HOME": str(home), "USERPROFILE": str(home), "TMP": str(home), "TEMP": str(home)}
    for name in ("SystemRoot", "WINDIR"):
        if os.name == "nt":
            env[name] = os.environ.get(name, "C:/Windows")
    return env


class _WindowsJob:
    def __init__(self):
        self._handle_lock = threading.Lock()
        import ctypes
        from ctypes import wintypes
        self.api = api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID]
        api.OpenProcess.restype = wintypes.HANDLE
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.CloseHandle.argtypes = [wintypes.HANDLE]

        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", wintypes.DWORD),
                        ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t), ("processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        self.handle = api.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError()
        info = Extended()
        info.basic.flags = 0x2000 | 0x400 | 0x8 | 0x200
        info.basic.processes = 2  # PID-bound Python gate and one Node process.
        info.job_memory = 768 * 1024 * 1024
        if not api.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise OSError()

    def assign(self, pid):
        handle = self.api.OpenProcess(0x0001 | 0x0100, False, pid)
        if not handle:
            raise OSError()
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise OSError()
        finally:
            self.api.CloseHandle(handle)

    def terminate(self):
        with self._handle_lock:
            if self.handle:
                self.api.TerminateJobObject(self.handle, 1)

    def close(self):
        with self._handle_lock:
            if self.handle:
                self.api.CloseHandle(self.handle)
                self.handle = None

    def wait_empty(self, timeout=2):
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = [("times", ctypes.c_int64 * 4), ("faults", wintypes.DWORD),
                        ("total", wintypes.DWORD), ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]

        deadline = time.monotonic() + timeout
        while self.handle:
            info = Accounting()
            if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                return False
            if info.active == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(.005)
        return True


def _supported_processes():
    return os.name != "posix" or all(hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOWAIT", "WNOHANG"))


def _exited(proc):
    if os.name != "posix":
        return proc.poll() is not None
    # Serialize with CPython Popen's reaper without consuming the waitable PID.
    with proc._waitpid_lock:
        if proc.returncode is not None:
            return True
        try:
            status = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            return status is not None and status.si_pid == proc.pid
        except ChildProcessError:
            # Another reaper took ownership. Never signal a potentially reused PID.
            proc.returncode = 255
            return True


def _signal_group(proc):
    if os.name != "posix":
        return
    with proc._waitpid_lock:
        if proc.returncode is not None:
            return
        try:
            os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            proc.returncode = 255
            return
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _wait(proc, timeout):
    if os.name != "posix":
        return proc.wait(timeout=timeout)
    deadline = time.monotonic() + timeout
    while not _exited(proc):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        time.sleep(min(.01, remaining))
    # The leader remains waitable until its group is stopped, so its PID cannot be reused.
    _signal_group(proc)
    return proc.wait(timeout=2)


def _run(argv, *, cwd, env, data=b"", timeout=TIMEOUT, cap=MAX_OUTPUT):
    """No shell or inherited source handles. Never expose child diagnostics."""
    job = None
    proc = None
    deadline = time.monotonic() + timeout
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    threads = []
    try:
        if not _supported_processes():
            raise JsParserError("node_unsupported")
        options = {"start_new_session": True} if os.name == "posix" else {"creationflags": subprocess.CREATE_NO_WINDOW}
        command = argv
        if os.name == "nt":
            job = _WindowsJob()
            base = getattr(sys, "_base_executable", None) or sys.executable
            command = [base, "-I", "-S", "-c", _GATE, *argv]
        proc = subprocess.Popen(command, cwd=cwd, env=env, shell=False, close_fds=True,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
        if job:
            job.assign(proc.pid)

        def stop():
            if job:
                job.terminate()
            elif os.name == "posix":
                try:
                    _signal_group(proc)
                except OSError:
                    pass
            try:
                proc.kill()
            except OSError:
                pass

        def drain(pipe, index):
            try:
                while block := pipe.read1(65536):
                    limit = cap if index == 0 else 65536
                    if len(buffers[index]) + len(block) > limit:
                        overflow.set()
                        stop()
                        break
                    buffers[index].extend(block)
            except (OSError, ValueError):
                pass
            finally:
                pipe.close()

        def feed():
            try:
                if job:
                    proc.stdin.write((str(proc.pid) + "\n").encode("ascii"))
                    proc.stdin.flush()
                proc.stdin.write(data)
                proc.stdin.close()
            except (OSError, ValueError):
                pass

        threads = [threading.Thread(target=drain, args=(proc.stdout, 0), daemon=True),
                   threading.Thread(target=drain, args=(proc.stderr, 1), daemon=True), threading.Thread(target=feed, daemon=True)]
        for thread in threads:
            thread.start()
        try:
            _wait(proc, max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            stop()
            proc.wait(timeout=2)
            raise JsParserError("timeout") from None
        for thread in threads:
            thread.join(timeout=1)
        if overflow.is_set():
            raise JsParserError("output_limit")
        if proc.returncode or buffers[1] or any(thread.is_alive() for thread in threads):
            raise JsParserError("failed")
        return bytes(buffers[0])
    except (OSError, subprocess.SubprocessError):
        raise JsParserError("failed") from None
    finally:
        if job:
            job.terminate()
            job.wait_empty()
        elif proc and os.name == "posix":
            try:
                _signal_group(proc)
            except OSError:
                pass
        if proc and proc.returncode is None:
            try:
                if os.name == "posix":
                    _signal_group(proc)
                proc.kill()
                proc.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass
        if proc and proc.stdin:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass
        cleanup_deadline = time.monotonic() + 2
        for thread in threads:
            thread.join(timeout=max(0, cleanup_deadline - time.monotonic()))
        if proc and not any(thread.is_alive() for thread in threads):
            for pipe in (proc.stdout, proc.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except (OSError, ValueError):
                        pass
        if proc and os.name == "nt" and proc.returncode is not None:
            # Hygiene for tracebacks retaining Popen; joins ensure completed I/O cleanup.
            try:
                proc._handle.Close()
            except OSError:
                pass
        if job:
            job.close()


def _check_node(node):
    info = node.stat()
    key = (str(node), info.st_size, info.st_mtime_ns)
    with _CACHE_LOCK:
        if key in _VERSION_CACHE:
            return
    try:
        raw = _run([str(node), "--version"], cwd=ANALYZER, env=_environment(node, ANALYZER), cap=256)
        match = re.fullmatch(rb"v(\d+)\.(\d+)\.(\d+)\s*", raw)
        if not match or tuple(map(int, match.groups())) < (24, 22, 1):
            raise JsParserError("node_unsupported")
    except JsParserError:
        raise JsParserError("node_unsupported") from None
    with _CACHE_LOCK:
        _VERSION_CACHE.clear()
        _VERSION_CACHE[key] = True


def _integrity(*, installed=True):
    manifest = ANALYZER / "integrity.json"
    try:
        if not _ordinary(manifest) or manifest.stat().st_size > 65536:
            raise JsParserError("parser_integrity")
        record = json.loads(manifest.read_text("utf-8"))
        if record.get("format") != 1 or record.get("parser") != "8.0.4" or set(record["files"]) != _FILES:
            raise JsParserError("parser_integrity")
        for name, digest in record["files"].items():
            if not installed and name.startswith("node_modules/"):
                continue
            file = ANALYZER / name
            if not file.exists():
                raise JsParserError("parser_missing")
            if not _ordinary(file) or file.stat().st_size > 4 * 1024 * 1024 or not re.fullmatch(r"[0-9a-f]{64}", digest) or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                raise JsParserError("parser_integrity")
        return tuple(sorted(record["files"].items()))
    except JsParserError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise JsParserError("parser_integrity") from None


def available() -> tuple[bool, str]:
    try:
        if not _supported_processes():
            raise JsParserError("node_unsupported")
        _integrity()
        _check_node(_node())
        return True, "ready"
    except JsParserError as error:
        close()
        return False, error.code
    except OSError:
        close()
        return False, "node_missing"


def _validate(document, source):
    if not isinstance(document, dict):
        raise JsParserError("failed")
    if set(document) == {"error"} and document["error"] in {"invalid_input", "input_limit", "syntax", "limit", "output_limit", "failed"}:
        raise JsParserError(document["error"])
    if set(document) != {"root", "nodes", "parser", "source_type"} or type(document["root"]) is not int or document["root"] != 0 or document["parser"] != "8.0.4" or document["source_type"] not in ("module", "script"):
        raise JsParserError("failed")
    nodes = document["nodes"]
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES:
        raise JsParserError("failed")
    offset = len(source.encode("utf-16-le")) // 2
    edges = 0
    graph = []
    for node in nodes:
        if not isinstance(node, dict) or set(node) != {"type", "line", "start", "end", "fields", "children"}:
            raise JsParserError("failed")
        if not isinstance(node["type"], str) or len(node["type"]) > 100 or not _NAME.fullmatch(node["type"]):
            raise JsParserError("failed")
        if any(type(node[k]) is not int for k in ("line", "start", "end")) or not 1 <= node["line"] <= offset + 1 or not 0 <= node["start"] <= node["end"] <= offset:
            raise JsParserError("failed")
        fields, children = node["fields"], node["children"]
        if not isinstance(fields, dict) or not isinstance(children, dict) or len(fields) > 64 or len(children) > 64:
            raise JsParserError("failed")
        links = []
        for key, value in fields.items():
            if not isinstance(key, str) or len(key) > 100 or not _NAME.fullmatch(key):
                raise JsParserError("failed")
            if key.startswith("__omitted_") and (value is not True or key[10:] in fields):
                raise JsParserError("failed")
            if value is not None and type(value) not in (str, int, float, bool):
                raise JsParserError("failed")
            if isinstance(value, str) and len(value) > 4096 or type(value) in (int, float) and not math.isfinite(value):
                raise JsParserError("failed")
        for key, value in children.items():
            if not isinstance(key, str) or len(key) > 100 or not _NAME.fullmatch(key):
                raise JsParserError("failed")
            values = value if isinstance(value, list) else [value]
            edges += len(values)
            if edges > MAX_EDGES:
                raise JsParserError("failed")
            for reference in values:
                if reference is not None:
                    if type(reference) is not int or not 0 <= reference < len(nodes):
                        raise JsParserError("failed")
                    links.append(reference)
        graph.append(links)
    if nodes[0]["type"] != "Program":
        raise JsParserError("failed")
    # Iterative graph validation accepts shared nodes but rejects cycles and unreachable records.
    colors = bytearray(len(nodes))
    stack = [(0, False)]
    while stack:
        index, leaving = stack.pop()
        if leaving:
            colors[index] = 2
            continue
        if colors[index] == 1:
            raise JsParserError("failed")
        if colors[index] == 2:
            continue
        colors[index] = 1
        stack.append((index, True))
        stack.extend((child, False) for child in reversed(graph[index]))
    if 0 in colors:
        raise JsParserError("failed")
    return document


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))


class _Request:
    def __init__(self, wire, nonce, sequence):
        self.wire, self.nonce, self.sequence = wire, nonce, sequence
        self.done = threading.Event()
        self.document = None
        self.error = None


class _Worker:
    """One trusted parser process, with one serialized request and two pipe readers."""
    def __init__(self, node):
        self.born = self.last_use = time.monotonic()
        self.requests = 0
        self.busy = False
        self.closed = False
        self.node_pid = None
        self.proc = self.job = None
        self._current = None
        self._lock = threading.RLock()
        self._close_lock = threading.RLock()
        self._ended = threading.Event()
        self._queue = queue.Queue(maxsize=1)
        self._threads = []
        worker = ANALYZER / "parse.mjs"
        command = [str(node), "--permission", "--no-addons", "--disable-proto=throw", "--disable-sigusr1", "--disallow-code-generation-from-strings",
                   "--no-extra-info-on-fatal-exception", "--max-old-space-size=256", "--stack-size=512",
                   "--allow-fs-read=" + str(worker), "--allow-fs-read=" + str(ANALYZER / _PARSER), str(worker)]
        try:
            options = {"start_new_session": True} if os.name == "posix" else {"creationflags": subprocess.CREATE_NO_WINDOW}
            if os.name == "nt":
                self.job = _WindowsJob()
                base = getattr(sys, "_base_executable", None) or sys.executable
                command = [base, "-I", "-S", "-c", _GATE, *command]
            self.proc = subprocess.Popen(command, cwd=ANALYZER, env=_environment(node, ANALYZER), shell=False, close_fds=True,
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
            if self.job:
                self.job.assign(self.proc.pid)
                self.proc.stdin.write((str(self.proc.pid) + "\n").encode("ascii"))
                self.proc.stdin.flush()
            self._threads = [threading.Thread(target=self._io, daemon=True), threading.Thread(target=self._errors, daemon=True)]
            for thread in self._threads:
                thread.start()
        except (OSError, subprocess.SubprocessError):
            self.close()
            raise JsParserError("failed") from None

    def expired(self, now):
        return self.closed or self.requests >= MAX_REQUESTS or now - self.born >= MAX_AGE - TIMEOUT or _exited(self.proc)

    def _io(self):
        while True:
            request = self._queue.get()
            if request is None:
                return
            frame = decoded = None
            with self._lock:
                if self.closed:
                    request.wire = b""
                    request.error = "failed"
                    request.done.set()
                    continue
                self._current = request
            error = None
            try:
                self.proc.stdin.write(request.wire)
                self.proc.stdin.flush()
                request.wire = b""
                frame = self.proc.stdout.readline(MAX_OUTPUT + 1)
                if len(frame) > MAX_OUTPUT:
                    raise JsParserError("output_limit")
                if not frame.endswith(b"\n"):
                    raise JsParserError("failed")
                decoded = _json(frame)
                if not isinstance(decoded, dict) or set(decoded) != {"v", "nonce", "sequence", "pid", "document"} or type(decoded["v"]) is not int or decoded["v"] != 1:
                    raise JsParserError("failed")
                if decoded["nonce"] != request.nonce or type(decoded["sequence"]) is not int or decoded["sequence"] != request.sequence:
                    raise JsParserError("failed")
                pid = decoded["pid"]
                if type(pid) is not int or not 0 < pid < 2**32 or self.node_pid not in (None, pid):
                    raise JsParserError("failed")
                if os.name == "posix" and pid != self.proc.pid:
                    raise JsParserError("failed")
                self.node_pid = pid
                with self._lock:
                    if self.closed:
                        raise JsParserError("failed")
                    request.document = decoded["document"]
                    self._current = None
            except JsParserError as failure:
                error = failure.code
            except (OSError, ValueError, TypeError, OverflowError, RecursionError):
                error = "failed"
            finally:
                frame = decoded = None
                request.wire = b""
            if error:
                self.close(error=error)
            else:
                request.done.set()
            request = None

    def _errors(self):
        try:
            if self.proc.stderr.read1(4096):
                self.close(error="failed")
        except (OSError, ValueError):
            pass

    def exchange(self, source, kind, deadline):
        self.requests += 1
        nonce = secrets.token_hex(16)
        wire = json.dumps({"v": 1, "nonce": nonce, "sequence": self.requests, "source": source, "kind": kind}, ensure_ascii=False).encode("utf-8") + b"\n"
        request = _Request(wire, nonce, self.requests)
        try:
            self._queue.put_nowait(request)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not request.done.wait(remaining):
                self.close(error="timeout")
                raise JsParserError("timeout")
            if request.error or self.closed:
                raise JsParserError(request.error or "failed")
            return request.document
        except queue.Full:
            self.close()
            raise JsParserError("failed") from None
        except BaseException:
            self.close()
            raise
        finally:
            wire = b""
            request.wire = b""
            request.document = None

    def close(self, *, error="failed", graceful=False):
        with self._close_lock:
            if self._ended.is_set():
                return
            self.closed = True
            if self.proc:
                if graceful and not self.busy:
                    try:
                        self.proc.stdin.close()
                        _wait(self.proc, .15)
                    except (OSError, ValueError, subprocess.SubprocessError):
                        pass
                if self.job:
                    self.job.terminate()
                elif os.name == "posix":
                    try:
                        _signal_group(self.proc)
                    except OSError:
                        pass
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=1)
                except (OSError, subprocess.SubprocessError):
                    pass
            if self.job:
                self.job.wait_empty()
                self.job.close()
            with self._lock:
                pending = [self._current]
                self._current = None
                try:
                    pending.append(self._queue.get_nowait())
                except queue.Empty:
                    pass
                for request in pending:
                    if request:
                        request.wire = b""
                        request.document = None
                        request.error = error
                        request.done.set()
                try:
                    self._queue.put_nowait(None)
                except queue.Full:
                    pass
            for thread in self._threads:
                if thread is not threading.current_thread():
                    thread.join(timeout=.2)
            if self.proc:
                for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                    try:
                        pipe.close()
                    except (OSError, ValueError):
                        pass
            self._ended.set()


class _Pool:
    def __init__(self):
        self._condition = threading.Condition(threading.RLock())
        self._workers = []
        self._generation = None
        self._epoch = 0
        self._waiting = 0
        self._slots = threading.BoundedSemaphore(MAX_WORKERS + MAX_WAITERS)
        self._reaper = None
        self._shutdown = threading.Event()

    def acquire(self, key, node, deadline):
        if not self._slots.acquire(blocking=False):
            raise JsParserError("busy")
        acquired = self._condition.acquire(timeout=max(0, deadline - time.monotonic()))
        if not acquired:
            self._slots.release()
            raise JsParserError("timeout")
        try:
            if self._shutdown.is_set():
                raise JsParserError("failed")
            if key != self._generation:
                self._clear()
                self._generation = key
            epoch = self._epoch
            if self._waiting >= MAX_WAITERS:
                raise JsParserError("busy")
            self._waiting += 1
            try:
                if self._reaper is None:
                    self._reaper = threading.Thread(target=self._reap, daemon=True)
                    self._reaper.start()
                while True:
                    now = time.monotonic()
                    if epoch != self._epoch:
                        raise JsParserError("failed")
                    if now >= deadline:
                        raise JsParserError("timeout")
                    for worker in list(self._workers):
                        if not worker.busy and worker.expired(now):
                            worker.close(graceful=True)
                            self._workers.remove(worker)
                    worker = next((w for w in self._workers if not w.busy), None)
                    if worker is None and len(self._workers) < MAX_WORKERS:
                        worker = _Worker(node)
                        self._workers.append(worker)
                    if worker is not None:
                        worker.busy = True
                        return worker
                    self._condition.wait(timeout=min(.1, max(0, deadline - now)))
            finally:
                self._waiting -= 1
        except BaseException:
            self._slots.release()
            raise
        finally:
            self._condition.release()

    def release(self, worker):
        try:
            with self._condition:
                worker.busy = False
                worker.last_use = time.monotonic()
                if worker in self._workers and worker.expired(worker.last_use):
                    worker.close(graceful=True)
                    self._workers.remove(worker)
                self._condition.notify_all()
        finally:
            self._slots.release()

    def _clear(self):
        for worker in self._workers:
            worker.close(graceful=not worker.busy)
        self._workers.clear()
        self._generation = None
        self._epoch += 1
        self._condition.notify_all()

    def close(self):
        with self._condition:
            self._clear()

    def _reap(self):
        while not self._shutdown.wait(REAP_INTERVAL):
            with self._condition:
                now = time.monotonic()
                for worker in list(self._workers):
                    if not worker.busy and (worker.expired(now) or now - worker.last_use >= IDLE_SECONDS):
                        worker.close(graceful=True)
                        self._workers.remove(worker)
                self._condition.notify_all()

    def shutdown(self):
        self._shutdown.set()
        self.close()


_POOL = _Pool()
atexit.register(_POOL.shutdown)


def close():
    """Retire owned workers, including in-flight requests. A later parse starts fresh."""
    _POOL.close()


def parse(source: str, kind="javascript") -> dict:
    deadline = time.monotonic() + TIMEOUT
    if not isinstance(source, str) or kind not in ("javascript", "typescript"):
        raise JsParserError("invalid_input")
    try:
        if len(source.encode("utf-8")) > MAX_SOURCE:
            raise JsParserError("input_limit")
    except UnicodeError:
        raise JsParserError("invalid_input") from None
    try:
        if not _supported_processes():
            raise JsParserError("node_unsupported")
        generation = _integrity()
        node = _node()
        _check_node(node)
        info = node.stat()
        key = (str(ANALYZER), str(node), info.st_size, info.st_mtime_ns, generation)
    except JsParserError:
        close()
        raise
    except OSError:
        close()
        raise JsParserError("failed") from None
    worker = _POOL.acquire(key, node, deadline)
    try:
        document = worker.exchange(source, kind, deadline)
        try:
            result = _validate(document, source)
        except JsParserError as error:
            if error.code not in {"syntax", "limit", "output_limit", "invalid_input", "input_limit"}:
                worker.close()
            raise
        if worker.closed:
            raise JsParserError("failed")
        if time.monotonic() >= deadline:
            worker.close(error="timeout")
            raise JsParserError("timeout")
        return result
    except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError) as error:
        if isinstance(error, JsParserError):
            raise
        worker.close()
        raise JsParserError("failed") from None
    finally:
        document = None
        _POOL.release(worker)


def setup() -> int:
    """Explicit local install only. Analysis never invokes npm or downloads packages."""
    with _SETUP_LOCK:
        try:
            _integrity(installed=False)
            node = _node()
            _check_node(node)
            candidates = [node.parent / "node_modules/npm/bin/npm-cli.js", node.parent.parent / "lib/node_modules/npm/bin/npm-cli.js"]
            npm = next((file for file in candidates if _ordinary(file)), None)
            if npm is None:
                raise JsParserError("npm_missing")
            if (ANALYZER / ".npmrc").exists():
                raise JsParserError("setup_failed")
            modules = ANALYZER / "node_modules"
            if modules.exists():
                pending = [modules]
                count = 0
                while pending:
                    folder = pending.pop()
                    info = folder.lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                        raise JsParserError("setup_failed")
                    for child in folder.iterdir():
                        count += 1
                        if count > 10000:
                            raise JsParserError("setup_failed")
                        info = child.lstat()
                        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                            raise JsParserError("setup_failed")
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(child)
                        elif not stat.S_ISREG(info.st_mode):
                            raise JsParserError("setup_failed")
            work = ANALYZER / "work"
            work.mkdir(exist_ok=True)
            info = work.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise JsParserError("setup_failed")
            with tempfile.TemporaryDirectory(prefix="setup-", dir=work, ignore_cleanup_errors=True) as temporary:
                home = Path(temporary)
                user, global_config = home / "user.npmrc", home / "global.npmrc"
                user.write_text("", encoding="utf-8")
                global_config.write_text("", encoding="utf-8")
                env = _environment(node, home)
                env.update({"NPM_CONFIG_USERCONFIG": str(user), "NPM_CONFIG_GLOBALCONFIG": str(global_config), "NPM_CONFIG_CACHE": str(home / "cache"),
                            "NPM_CONFIG_UPDATE_NOTIFIER": "false", "NPM_CONFIG_AUDIT": "false", "NPM_CONFIG_FUND": "false"})
                _run([str(node), str(npm), "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--bin-links=false", "--registry=https://registry.npmjs.org",
                      "--userconfig=" + str(user), "--globalconfig=" + str(global_config), "--cache=" + str(home / "cache")],
                     cwd=ANALYZER, env=env, timeout=90, cap=65536)
            _integrity()
            return 0
        except (JsParserError, OSError):
            return 1
