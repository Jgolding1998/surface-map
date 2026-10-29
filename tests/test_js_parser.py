import copy
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from surface_map import js_parser as bridge

CANARY = "OWNED_PRIVATE_PARSER_52874"


def document():
    return {"root": 0, "parser": "8.0.4", "source_type": "script", "nodes": [
        {"type": "Program", "line": 1, "start": 0, "end": 2, "fields": {}, "children": {"body": [1]}},
        {"type": "Identifier", "line": 1, "start": 0, "end": 1, "fields": {"name": "x"}, "children": {}},
    ]}


class SchemaTests(unittest.TestCase):
    def test_closed_worker_rejects_without_queueing_or_waiting(self):
        worker = object.__new__(bridge._Worker)
        worker.requests, worker.closed = 0, True
        worker._lock = threading.RLock()
        worker._queue = mock.Mock()
        worker.close = mock.Mock()
        request = mock.Mock()
        request.done.wait.return_value = False
        with mock.patch.object(bridge, "_Request", return_value=request):
            with self.assertRaises(bridge.JsParserError) as caught:
                worker.exchange(CANARY, "javascript", time.monotonic() + 1)
        self.assertEqual(caught.exception.code, "failed")
        worker._queue.put_nowait.assert_not_called()
        request.done.wait.assert_not_called()
        self.assertNotIn(CANARY, str(caught.exception))

    def test_node_package_metadata_matches_supported_series(self):
        package = json.loads((bridge.ANALYZER / "package.json").read_text("utf-8"))
        lock = json.loads((bridge.ANALYZER / "package-lock.json").read_text("utf-8"))
        self.assertEqual(package["engines"]["node"], ">=24.22.1 <25")
        self.assertEqual(lock["packages"][""]["engines"], package["engines"])

    def test_node_version_policy_stays_in_supported_24_series(self):
        node = Path(sys.executable)
        self.addCleanup(bridge._VERSION_CACHE.clear)
        for version, accepted in [(b"v24.22.1\n", True), (b"v24.22.2\n", True),
                                  (b"v24.99.0\n", True), (b"v24.22.0\n", False),
                                  (b"v23.99.99\n", False), (b"v25.0.0\n", False),
                                  (b"v26.13.0\n", False), (b"v999.0.0\n", False),
                                  (b"v24.22.1-rc.1\n", False), (b"not a version", False)]:
            with self.subTest(version=version), mock.patch.object(bridge, "_run", return_value=version):
                bridge._VERSION_CACHE.clear()
                if accepted:
                    bridge._check_node(node)
                    self.assertEqual(len(bridge._VERSION_CACHE), 1)
                else:
                    with self.assertRaises(bridge.JsParserError) as caught:
                        bridge._check_node(node)
                    self.assertEqual(caught.exception.code, "node_unsupported")
                    self.assertEqual(bridge._VERSION_CACHE, {})

    def test_valid_graph_and_utf16_offsets(self):
        self.assertEqual(bridge._validate(document(), "x;"), document())
        doc = document()
        doc["nodes"][0]["end"] = 3
        self.assertEqual(bridge._validate(doc, "😀;"), doc)

    def test_invalid_shapes_refs_cycles_and_scalars_are_rejected(self):
        cases = []
        doc = document(); doc["nodes"][1]["children"]["cycle"] = 0; cases.append(doc)
        doc = document(); doc["nodes"][0]["children"]["body"] = [9]; cases.append(doc)
        doc = document(); doc["nodes"][0]["children"]["body"] = [True]; cases.append(doc)
        doc = document(); doc["nodes"][0]["children"] = {}; cases.append(doc)
        doc = document(); doc["nodes"][1]["end"] = 999; cases.append(doc)
        doc = document(); doc["nodes"][1]["fields"]["value"] = {"private": CANARY}; cases.append(doc)
        doc = document(); doc["nodes"][1]["fields"]["value"] = "x" * 4097; cases.append(doc)
        doc = document(); doc["nodes"][1]["fields"]["value"] = float("inf"); cases.append(doc)
        doc = document(); doc["nodes"][1]["fields"]["__omitted_name"] = False; cases.append(doc)
        for doc in cases:
            with self.subTest(doc=doc):
                with self.assertRaises(bridge.JsParserError) as error:
                    bridge._validate(doc, "x;")
                self.assertEqual(str(error.exception), "failed")

    def test_worker_errors_stay_fixed_and_duplicate_json_is_rejected(self):
        for payload, expected in [(b'{"error":"syntax"}', "syntax"),
                                  (b'{"error":"' + CANARY.encode() + b'"}', "failed"),
                                  (b'{"root":0,"root":1}', "failed"), (b"not JSON", "failed")]:
            with self.assertRaises((bridge.JsParserError, ValueError)) as error:
                bridge._validate(bridge._json(payload), "x;")
            if isinstance(error.exception, bridge.JsParserError):
                self.assertEqual(str(error.exception), expected)
            self.assertNotIn(CANARY, str(error.exception))

    def test_input_limits_and_bad_runtime_paths_do_not_launch(self):
        for value in [None, 3, "\ud800", "x" * (bridge.MAX_SOURCE + 1)]:
            with self.assertRaises(bridge.JsParserError):
                bridge.parse(value)
        for value in ["node.exe", "\\\\owned.invalid\\share\\node.exe", "/owned/node.cmd"]:
            with mock.patch.dict(os.environ, {"SURFACE_MAP_NODE": value}), self.assertRaises(bridge.JsParserError):
                bridge._node()
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {"PATH": temporary, "SURFACE_MAP_NODE": ""}), \
                mock.patch.object(os, "getcwd", return_value=temporary):
            Path(temporary, "node.exe" if os.name == "nt" else "node").write_text(CANARY)
            with self.assertRaises(bridge.JsParserError):
                bridge._node()


class ProcessTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_real_posix_group_cleanup_on_timeout_and_worker_close(self):
        def running(pid):
            try:
                state = Path("/proc", str(pid), "stat")
                if state.exists() and state.read_text().split(") ", 1)[1].startswith("Z"):
                    return False
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False

        python = getattr(sys, "_base_executable", sys.executable)
        for mode in ("timeout", "close"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                pidfile = Path(temporary) / "owned.pid"
                code = ("import subprocess,sys,time;from pathlib import Path;"
                        "p=subprocess.Popen([sys.executable,'-I','-S','-c','import time;time.sleep(30)']);"
                        "Path(" + repr(str(pidfile)) + ").write_text(str(p.pid));time.sleep(30)")
                command = [python, "-I", "-S", "-c", code]
                if mode == "timeout":
                    with self.assertRaises(bridge.JsParserError) as error:
                        bridge._run(command, cwd=temporary, env=bridge._environment(Path(python), Path(temporary)), timeout=1)
                    self.assertEqual(error.exception.code, "timeout")
                else:
                    original = subprocess.Popen
                    def owned_process(*args, **kwargs):
                        return original(command, **kwargs)
                    with mock.patch.object(bridge.subprocess, "Popen", side_effect=owned_process):
                        worker = bridge._Worker(Path(python))
                    try:
                        deadline = time.monotonic() + 3
                        while not pidfile.exists() and time.monotonic() < deadline:
                            time.sleep(.01)
                        self.assertTrue(pidfile.exists())
                    finally:
                        worker.close()
                self.assertTrue(pidfile.exists())
                pid = int(pidfile.read_text())
                deadline = time.monotonic() + 2
                while running(pid) and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(running(pid))

    def test_posix_group_is_signaled_before_its_leader_is_reaped(self):
        bridge.close()
        events = []
        proc = SimpleNamespace(pid=123, returncode=None, args=['owned'], _waitpid_lock=threading.Lock())
        def wait(timeout):
            events.append('reap'); proc.returncode = 0; return 0
        proc.wait = wait
        def observe(*args):
            events.append('observe'); return SimpleNamespace(si_pid=123)
        def kill(*args):
            events.append('signal')
        with mock.patch.object(bridge.os, 'name', 'posix'), mock.patch.object(bridge.signal, 'SIGKILL', 9, create=True), mock.patch.multiple(bridge.os, waitid=observe, killpg=kill,
                P_PID=1, WEXITED=4, WNOWAIT=8, WNOHANG=1, create=True):
            self.assertEqual(bridge._wait(proc, 1), 0)
            self.assertLess(events.index('signal'), events.index('reap'))
            count = len(events); bridge._signal_group(proc)
            self.assertEqual(len(events), count, 'a reaped PID must never receive another group signal')

    def test_lost_posix_child_ownership_never_signals_a_reused_pid(self):
        bridge.close()
        proc = SimpleNamespace(pid=123, returncode=None, _waitpid_lock=threading.Lock())
        kill = mock.Mock()
        with mock.patch.object(bridge.os, 'name', 'posix'), mock.patch.multiple(bridge.os,
                waitid=mock.Mock(side_effect=ChildProcessError), killpg=kill, P_PID=1, WEXITED=4, WNOWAIT=8, WNOHANG=1, create=True):
            bridge._signal_group(proc)
            kill.assert_not_called()
            self.assertEqual(proc.returncode, 255)

    def run_child(self, code, **kwargs):
        python = getattr(sys, "_base_executable", sys.executable)
        with tempfile.TemporaryDirectory() as temporary:
            return bridge._run([python, "-I", "-S", "-c", code], cwd=temporary,
                               env=bridge._environment(Path(python), Path(temporary)), **kwargs)

    def test_output_stderr_and_deadline_are_bounded_and_sanitized(self):
        for code, kwargs, expected in [
            ("print('x'*10000)", {"cap": 256}, "output_limit"),
            ("import sys;sys.stderr.write('" + CANARY + "')", {}, "failed"),
            ("while True: pass", {"timeout": .05}, "timeout"),
        ]:
            with self.subTest(expected=expected), self.assertRaises(bridge.JsParserError) as error:
                self.run_child(code, **kwargs)
            self.assertEqual(str(error.exception), expected)
            self.assertNotIn(CANARY, str(error.exception))

    @unittest.skipUnless(os.name == "nt", "Windows process and pipe teardown")
    def test_timeout_joins_io_and_releases_cwd_with_traceback_retained(self):
        python = getattr(sys, "_base_executable", sys.executable)
        def run(_):
            with tempfile.TemporaryDirectory() as temporary:
                try:
                    bridge._run([python, "-I", "-S", "-c", "while True: pass"], cwd=temporary,
                                env=bridge._environment(Path(python), Path(temporary)), timeout=.05)
                except bridge.JsParserError as error:
                    self.assertEqual(error.code, "timeout")
                    trace = error.__traceback__
                    while trace and trace.tb_frame.f_code.co_name != "_run":
                        trace = trace.tb_next
                    self.assertIsNotNone(trace)
                    state = trace.tb_frame.f_locals
                    self.assertTrue(all(not thread.is_alive() for thread in state["threads"]))
                    proc = state["proc"]
                    self.assertIsNotNone(proc.returncode)
                    self.assertTrue(all(pipe.closed for pipe in (proc.stdin, proc.stdout, proc.stderr)))
                    self.assertTrue(proc._handle.closed)
                    # Keep this exception and its Popen frame alive during removal.
                    Path(temporary).rmdir()
                else:
                    self.fail("owned busy process must time out")
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(run, range(12)))

    @unittest.skipUnless(os.name == "nt", "Windows PID gate")
    def test_gate_rejects_a_token_for_another_pid_before_launch(self):
        python = getattr(sys, "_base_executable", sys.executable)
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "marker"
            command = [python, "-I", "-S", "-c", bridge._GATE, python, "-I", "-S", "-c", "from pathlib import Path;Path(" + repr(str(marker)) + ").touch()"]
            result = subprocess.run(command, input=b"0\n", capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 125)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "nt", "Windows kill-on-parent-exit Job Object")
    def test_parent_exit_kills_its_owned_job(self):
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.restype = wintypes.HANDLE
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        def alive(pid):
            handle = api.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
            finally:
                api.CloseHandle(handle)
        python = getattr(sys, "_base_executable", sys.executable)
        with tempfile.TemporaryDirectory() as temporary:
            pidfile = Path(temporary) / "owned.pid"
            child = "import os,time;from pathlib import Path;Path(" + repr(str(pidfile)) + ").write_text(str(os.getpid()));time.sleep(20)"
            code = "import sys;from pathlib import Path;sys.path.insert(0," + repr(str(bridge.ANALYZER.parents[1])) + ");from surface_map import js_parser as p;p._run(" + repr([python, "-I", "-S", "-c", child]) + ",cwd=" + repr(temporary) + ",env=p._environment(Path(" + repr(python) + "),Path(" + repr(temporary) + ")),timeout=15)"
            parent = subprocess.Popen([python, "-I", "-S", "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline = time.monotonic() + 5
                while not pidfile.exists() and parent.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(pidfile.exists())
                pid = int(pidfile.read_text())
                self.assertTrue(alive(pid))
                parent.kill(); parent.communicate(timeout=5)
                deadline = time.monotonic() + 3
                while alive(pid) and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertFalse(alive(pid))
            finally:
                if parent.poll() is None:
                    parent.kill()
                parent.communicate(timeout=5)


class IntegrityTests(unittest.TestCase):
    def copy_analyzer(self, target):
        record = json.loads((bridge.ANALYZER / "integrity.json").read_text())
        for name in ["integrity.json", *record["files"]]:
            source, destination = bridge.ANALYZER / name, target / name
            if not source.exists():
                self.skipTest("Run setup-js for installed dependency integrity tests")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    def test_worker_parser_and_package_byte_tamper_are_rejected(self):
        for name in ["parse.mjs", bridge._PARSER, bridge._PACKAGE, "package-lock.json"]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary); self.copy_analyzer(target)
                with (target / name).open("ab") as handle:
                    handle.write(b"\n")
                with mock.patch.object(bridge, "ANALYZER", target):
                    self.assertEqual(bridge.available(), (False, "parser_integrity"))

    def test_hardlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "analyzer"; self.copy_analyzer(target)
            try:
                os.link(target / bridge._PARSER, Path(temporary) / "alias")
            except OSError:
                self.skipTest("Hardlinks unavailable")
            with mock.patch.object(bridge, "ANALYZER", target):
                self.assertEqual(bridge.available(), (False, "parser_integrity"))

    @unittest.skipUnless(os.name == "nt", "Windows junction")
    def test_a_reparse_parent_is_rejected(self):
        import _winapi
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "analyzer"; self.copy_analyzer(target)
            file = target / bridge._PARSER
            outside = Path(temporary) / "outside"; outside.mkdir()
            shutil.copyfile(file, outside / "index.js")
            file.unlink(); file.parent.rmdir()
            try:
                _winapi.CreateJunction(str(outside), str(file.parent))
            except (OSError, AttributeError):
                self.skipTest("Junctions unavailable")
            with mock.patch.object(bridge, "ANALYZER", target):
                self.assertEqual(bridge.available(), (False, "parser_integrity"))


class RealWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ready, reason = bridge.available()
        if not ready:
            raise unittest.SkipTest("Run setup-js with supported Node: " + reason)

    def test_javascript_jsx_typescript_tsx_and_script_fallback(self):
        cases = [("const X=()=> <div/>;", "javascript"), ("type ID=string;enum E{A=1};namespace N{export const x=2};const y=E.A as number;", "typescript"),
                 ("const X=(p:{x:string})=><div>{p.x}</div>;", "typescript"), ("with ({x:1}) {x;}", "javascript")]
        for text, kind in cases:
            with self.subTest(kind=kind):
                doc = bridge.parse(text, kind)
                self.assertEqual(doc["nodes"][0]["type"], "Program")
                self.assertEqual(doc["parser"], "8.0.4")
        types = {n["type"] for n in bridge.parse(cases[1][0], "typescript")["nodes"]}
        self.assertTrue({"TSEnumDeclaration", "TSModuleDeclaration", "TSAsExpression"} <= types)
        self.assertNotIn("TSTypeAliasDeclaration", types)
        self.assertEqual(bridge.parse(cases[-1][0])["source_type"], "script")

    def test_target_imports_and_calls_are_inert(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "target-marker"
            source = "import fs from 'node:fs';fs.writeFileSync(" + json.dumps(str(marker)) + ",'owned');throw Error('" + CANARY + "');"
            doc = bridge.parse(source)
            self.assertFalse(marker.exists())
            self.assertIn("ThrowStatement", {n["type"] for n in doc["nodes"]})

    def test_preload_environment_is_not_inherited(self):
        bridge.close()
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "preload-marker"
            preload = Path(temporary) / "preload.mjs"
            preload.write_text("import fs from 'node:fs';fs.writeFileSync(" + json.dumps(str(marker)) + ",'owned');")
            with mock.patch.dict(os.environ, {"NODE_OPTIONS": "--allow-fs-read=* --allow-fs-write=* --import=" + preload.as_uri(),
                                             "NODE_PATH": temporary, "HTTPS_PROXY": CANARY, "NPM_TOKEN": CANARY}):
                self.assertTrue(bridge.parse("const x = 1;")["nodes"])
            self.assertFalse(marker.exists())
            self.assertFalse({"NODE_OPTIONS", "NODE_PATH", "HTTPS_PROXY", "NPM_TOKEN"} & bridge._environment(bridge._node(), bridge.ANALYZER).keys())

    def test_null_holes_template_values_and_omission_flags(self):
        doc = bridge.parse("const xs=[,1,,];const t=`owned`;const s='" + "x" * 5000 + "';const n=1e999;")
        array = next(n for n in doc["nodes"] if n["type"] == "ArrayExpression")
        self.assertEqual(array["children"]["elements"][::2], [None, None])
        template = next(n for n in doc["nodes"] if n["type"] == "TemplateElement")
        self.assertEqual(template["fields"]["cooked"], "owned")
        omitted = [n for n in doc["nodes"] if n["fields"].get("__omitted_value")]
        self.assertEqual(len(omitted), 2)
        self.assertTrue(all("value" not in n["fields"] for n in omitted))

    def test_decorators_and_utf16_offsets_are_preserved(self):
        doc = bridge.parse("@owned() class X {constructor(@inject() private x:string) {}}", "typescript")
        self.assertIn("Decorator", {n["type"] for n in doc["nodes"]})
        self.assertIn("TSParameterProperty", {n["type"] for n in doc["nodes"]})
        text = "const value='😀';"
        self.assertEqual(bridge.parse(text)["nodes"][0]["end"], len(text.encode("utf-16-le")) // 2)

    def test_valid_large_finite_number_does_not_reject_the_document(self):
        doc = bridge.parse("const value = 1.7e308;")
        literal = next(node for node in doc["nodes"] if node["type"] == "NumericLiteral")
        self.assertEqual(literal["fields"]["value"], 1.7e308)

    def test_runtime_qualified_import_alias_is_retained(self):
        doc = bridge.parse("import A = Namespace.Member; A();", "typescript")
        alias = next(node for node in doc["nodes"] if node["type"] == "TSImportEqualsDeclaration")
        qualified = doc["nodes"][alias["children"]["moduleReference"]]
        self.assertEqual(qualified["type"], "TSQualifiedName")
        self.assertEqual(doc["nodes"][qualified["children"]["left"]]["fields"]["name"], "Namespace")

    def test_syntax_deep_and_low_transport_limits_have_no_raw_diagnostics(self):
        for text in ["const " + CANARY + " = <div", "(" * 10000 + "1" + ")" * 10000]:
            with self.assertRaises(bridge.JsParserError) as error:
                bridge.parse(text)
            self.assertIn(error.exception.code, {"syntax", "limit"})
            self.assertNotIn(CANARY, str(error.exception))
        with mock.patch.object(bridge, "MAX_OUTPUT", 256), self.assertRaises(bridge.JsParserError) as error:
            bridge.parse("const x=1;")
        self.assertEqual(error.exception.code, "output_limit")
        with mock.patch.object(bridge, "TIMEOUT", .001), self.assertRaises(bridge.JsParserError) as error:
            bridge.parse("const x=1;")
        self.assertEqual(error.exception.code, "timeout")


class PoolTests(unittest.TestCase):
    copy_analyzer = IntegrityTests.copy_analyzer
    @classmethod
    def setUpClass(cls):
        ready, reason = bridge.available()
        if not ready:
            raise unittest.SkipTest("Run setup-js with supported Node: " + reason)

    def setUp(self):
        bridge.close()

    def tearDown(self):
        bridge.close()

    def test_requests_reuse_one_worker_without_caching_documents(self):
        first = bridge.parse("const value=1;")
        worker = bridge._POOL._workers[0]
        first["nodes"].clear()
        second = bridge.parse("const value=2;")
        self.assertIs(bridge._POOL._workers[0], worker)
        self.assertEqual(worker.requests, 2)
        values = [n["fields"].get("value") for n in second["nodes"] if n["type"] == "NumericLiteral"]
        self.assertEqual(values, [2])

    def test_concurrent_callers_share_at_most_two_owned_workers(self):
        def run(value):
            doc = bridge.parse("const value=" + str(value) + ";")
            return next(n["fields"]["value"] for n in doc["nodes"] if n["type"] == "NumericLiteral")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            self.assertEqual(list(executor.map(run, range(12))), list(range(12)))
        workers = bridge._POOL._workers
        self.assertGreaterEqual(len(workers), 1)
        self.assertLessEqual(len(workers), 2)
        self.assertEqual(sum(worker.requests for worker in workers), 12)
        self.assertTrue(any(worker.requests > 1 for worker in workers))
        self.assertEqual(len({worker.proc.pid for worker in workers}), len(workers))
        for worker in workers:
            self.assertFalse(worker.closed)
            self.assertFalse(worker.busy)
            self.assertFalse(bridge._exited(worker.proc))
            self.assertIsNone(worker._current)
            self.assertTrue(worker._queue.empty())

    def test_idle_request_count_and_age_retire_owned_processes(self):
        with mock.patch.object(bridge, "IDLE_SECONDS", .1), mock.patch.object(bridge, "REAP_INTERVAL", .02):
            bridge.parse("x;"); worker = bridge._POOL._workers[0]
            deadline = time.monotonic() + 2
            while worker.proc.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertIsNotNone(worker.proc.poll())
        with mock.patch.object(bridge, "MAX_REQUESTS", 2):
            bridge.parse("x;"); worker = bridge._POOL._workers[0]
            bridge.parse("y;")
            self.assertIsNotNone(worker.proc.poll())
        bridge.parse("x;"); old = bridge._POOL._workers[0]
        old.born = time.monotonic() - bridge.MAX_AGE - 1
        bridge.parse("y;")
        self.assertIsNotNone(old.proc.poll())
        self.assertIsNot(bridge._POOL._workers[0], old)

    def test_nearly_expired_worker_does_not_shorten_a_valid_requests_budget(self):
        bridge.parse("x;"); old = bridge._POOL._workers[0]
        old.born = time.monotonic() - bridge.MAX_AGE + .5
        self.assertEqual(bridge.parse("y;")["nodes"][0]["type"], "Program")
        self.assertIsNot(bridge._POOL._workers[0], old)
        self.assertIsNotNone(old.proc.poll())

    def test_integrity_failure_kills_the_old_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary); self.copy_analyzer(target)
            with mock.patch.object(bridge, "ANALYZER", target):
                bridge.parse("x;"); worker = bridge._POOL._workers[0]
                with (target / bridge._PARSER).open("ab") as handle:
                    handle.write(b"\n")
                with self.assertRaises(bridge.JsParserError) as error:
                    bridge.parse("y;")
                self.assertEqual(error.exception.code, "parser_integrity")
                self.assertIsNotNone(worker.proc.poll())
                self.assertEqual(bridge._POOL._workers, [])

    def fixture_worker(self, target, behavior):
        self.copy_analyzer(target)
        source = """let buffer='';process.stdin.on('data',chunk=>{buffer+=chunk;let at;while((at=buffer.indexOf('\\n'))>=0){const r=JSON.parse(buffer.slice(0,at));buffer=buffer.slice(at+1);BEHAVIOR}});""".replace("BEHAVIOR", behavior)
        (target / "parse.mjs").write_text(source)
        manifest = target / "integrity.json"
        record = json.loads(manifest.read_text())
        import hashlib
        record["files"]["parse.mjs"] = hashlib.sha256((target / "parse.mjs").read_bytes()).hexdigest()
        manifest.write_text(json.dumps(record))

    def test_stale_frames_overflow_and_timeouts_fail_closed_then_recover(self):
        valid = "{root:0,parser:'8.0.4',source_type:'script',nodes:[{type:'Program',line:1,start:0,end:2,fields:{},children:{body:[]}}]}"
        cases = [("console.log(JSON.stringify({v:1,nonce:r.nonce,sequence:r.sequence-1,pid:process.pid,document:" + valid + "}));", "failed", {}),
                 ("process.stderr.write('" + CANARY + "');", "failed", {}),
                 ("process.stdout.write('x'.repeat(2000)+'\\n');", "output_limit", {"MAX_OUTPUT": 256}),
                 ("while(true){}", "timeout", {"TIMEOUT": .25})]
        for behavior, expected, constants in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary); self.fixture_worker(target, behavior)
                with mock.patch.object(bridge, "ANALYZER", target), mock.patch.multiple(bridge, **{"TIMEOUT": bridge.TIMEOUT, **constants}):
                    with self.assertRaises(bridge.JsParserError) as error:
                        bridge.parse("x;")
                    self.assertEqual(error.exception.code, expected)
                    self.assertEqual(bridge._POOL._workers, [])
            self.assertEqual(bridge.parse("x;")["nodes"][0]["type"], "Program")
            bridge.close()

    def test_pool_waiters_are_bounded(self):
        behavior = "setTimeout(()=>console.log(JSON.stringify({v:1,nonce:r.nonce,sequence:r.sequence,pid:process.pid,document:{root:0,parser:'8.0.4',source_type:'script',nodes:[{type:'Program',line:1,start:0,end:2,fields:{},children:{body:[]}}]}})),200);"
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary); self.fixture_worker(target, behavior)
            with mock.patch.object(bridge, "ANALYZER", target):
                barrier = __import__('threading').Barrier(5)
                def run():
                    barrier.wait()
                    try:
                        bridge.parse("x;")
                        return "ready"
                    except bridge.JsParserError as error:
                        return error.code
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    results = list(executor.map(lambda _: run(), range(5)))
                self.assertEqual(results.count("busy"), 1)
                self.assertEqual(results.count("ready"), 4)
                self.assertLessEqual(len(bridge._POOL._workers), 2)
                bridge.close()

    def test_admission_rejects_excess_callers_even_while_pool_lock_is_held(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            with bridge._POOL._condition:
                futures = [executor.submit(bridge.parse, "x;") for _ in range(5)]
                done, pending = concurrent.futures.wait(futures, timeout=1, return_when=concurrent.futures.FIRST_COMPLETED)
                self.assertEqual(len(done), 1, "excess callers must not queue outside the pool bound")
                with self.assertRaises(bridge.JsParserError) as error:
                    next(iter(done)).result()
                self.assertEqual(error.exception.code, "busy")
            for future in pending:
                self.assertEqual(future.result()["nodes"][0]["type"], "Program")

    @unittest.skipUnless(os.name == "nt", "Windows pooled Job Object")
    def test_parent_exit_kills_a_pooled_node_worker(self):
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.restype = wintypes.HANDLE
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        def alive(pid):
            handle = api.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
            finally:
                api.CloseHandle(handle)
        python = getattr(sys, "_base_executable", sys.executable)
        with tempfile.TemporaryDirectory() as temporary:
            pidfile = Path(temporary) / "node.pid"
            code = "import sys,time;from pathlib import Path;sys.path.insert(0," + repr(str(bridge.ANALYZER.parents[1])) + ");from surface_map import js_parser as p;p.parse('x;');Path(" + repr(str(pidfile)) + ").write_text(str(p._POOL._workers[0].node_pid));time.sleep(10)"
            parent = subprocess.Popen([python, "-I", "-S", "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline = time.monotonic() + 5
                while not pidfile.exists() and parent.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(pidfile.exists())
                pid = int(pidfile.read_text()); self.assertTrue(alive(pid))
                parent.kill(); parent.communicate(timeout=5)
                deadline = time.monotonic() + 3
                while alive(pid) and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertFalse(alive(pid))
            finally:
                if parent.poll() is None:
                    parent.kill()
                parent.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
