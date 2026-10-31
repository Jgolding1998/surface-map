import contextlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from surface_map import mcp_server
from surface_map.__main__ import main
from surface_map.analyze import ArtifactError
from surface_map.server import MAX_REQUEST, make_server

CANARY = "PRIVATE_INTERFACE_CANARY_8427"
CAPTURE = [{"kind": "html", "text": '<form action="/api/orders" method="post"><input name="id"></form>'}]
PLAN = {"format": "test-minimized-plan", "operations": []}
FILES = {"README.md": "Review this local example.\n"}
HAS_MCP = importlib.util.find_spec("mcp") is not None
ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def rebuild_backend():
    backend = ModuleType("surface_map.rebuild")
    backend.plan_rebuild = Mock(return_value=PLAN)
    backend.scaffold_files = Mock(return_value=FILES)
    backend.write_scaffold = Mock()
    with patch.dict(sys.modules, {"surface_map.rebuild": backend}):
        yield backend


class RebuildInterfaceTests(unittest.TestCase):
    def test_mcp_contracts_use_supplied_contents_and_strict_schemas(self):
        specs = mcp_server.tool_specs()
        for name in ("plan_rebuild", "inspect_project"):
            self.assertFalse(specs[name][1]["additionalProperties"])
            self.assertNotIn("url", specs[name][1]["properties"])
            self.assertNotIn("path", specs[name][1]["properties"])
        project = specs["inspect_project"][1]["properties"]["files"]
        self.assertEqual(project["maxItems"], 24)
        self.assertEqual(set(project["items"]["required"]), {"kind", "content", "logical_path"})
        self.assertFalse(project["items"]["additionalProperties"])
        scaffold = specs["plan_rebuild"][1]["properties"]["include_scaffold"]
        self.assertEqual(scaffold, {"type": "boolean", "default": False,
                                   "description": scaffold["description"]})

    def test_mcp_rebuild_dispatch_and_scaffold_are_explicit_and_do_not_write(self):
        with rebuild_backend() as backend:
            args = {"artifacts": CAPTURE, "javascript_engine": "basic", "document_url": "https://example.invalid/"}
            self.assertEqual(mcp_server._execute("plan_rebuild", args), PLAN)
            backend.scaffold_files.assert_not_called()
            call = backend.plan_rebuild.call_args
            self.assertEqual(call.args, (CAPTURE,))
            self.assertEqual(call.kwargs["document_url"], args["document_url"])
            self.assertIn("deadline", call.kwargs)
            self.assertEqual(mcp_server._execute("plan_rebuild", {**args, "include_scaffold": True}),
                             {"plan": PLAN, "files": FILES})
            backend.write_scaffold.assert_not_called()

    def test_mcp_project_dispatch_is_offline_and_minimized(self):
        files = [{"kind": "python", "content": "import requests\n# " + CANARY, "logical_path": CANARY + ".py"}]
        with patch.object(mcp_server, "capture_public_url", side_effect=AssertionError("No fetch")), \
                patch("socket.create_connection", side_effect=AssertionError("No network")):
            report = mcp_server._execute("inspect_project", {"files": files, "javascript_engine": "basic"})
        self.assertEqual(report["format"], "surface-map-project-v1")
        self.assertNotIn(CANARY, json.dumps(report))

    def test_limits_disclose_new_bounds_without_changing_existing_web_limit(self):
        limits = mcp_server.limits()
        self.assertEqual(limits["project"]["max_files"], 24)
        self.assertEqual(limits["project"]["total_bytes"], 4 * 1024 * 1024)
        self.assertEqual(limits["rebuild"]["total_bytes"], limits["web"]["total_bytes"])
        self.assertEqual(limits["web"]["max_artifacts"], 6)

    def test_cli_project_requires_root_and_keeps_paths_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (CANARY + ".py")
            path.write_bytes(b"import requests\n")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["project", "--root", directory, "--files", path.name, "--javascript-engine", "basic"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["coverage"]["inspected_files"], 1)
            self.assertNotIn(CANARY, stdout.getvalue() + stderr.getvalue())
            self.assertNotIn(directory, stdout.getvalue() + stderr.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["project", "--files", CANARY + ".py"])
        self.assertEqual(caught.exception.code, 2)

    def test_cli_rebuild_reads_bounded_capture_and_writes_only_on_request(self):
        with tempfile.TemporaryDirectory() as directory, rebuild_backend() as backend:
            path = Path(directory) / "capture.json"
            path.write_text(json.dumps({"artifacts": CAPTURE}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(["rebuild", "--capture", str(path), "--javascript-engine", "basic"]), 0)
            self.assertEqual(json.loads(stdout.getvalue()), PLAN)
            backend.write_scaffold.assert_not_called()
            destination = Path(directory) / "new-scaffold"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["rebuild", "--capture", str(path), "--scaffold", str(destination)]), 0)
            backend.write_scaffold.assert_called_once_with(PLAN, destination)

    def test_cli_rebuild_rejects_extra_keys_duplicate_keys_and_existing_destination_privately(self):
        with tempfile.TemporaryDirectory() as directory, rebuild_backend() as backend:
            path = Path(directory) / (CANARY + ".json")
            for text in ('{"artifacts":[],"artifacts":[]}', json.dumps({"artifacts": CAPTURE, CANARY: CANARY})):
                path.write_text(text, encoding="utf-8")
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    self.assertEqual(main(["rebuild", "--capture", str(path)]), 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertNotIn(CANARY, stderr.getvalue())
            backend.plan_rebuild.assert_not_called()
            path.write_text(json.dumps({"artifacts": CAPTURE}), encoding="utf-8")
            backend.write_scaffold.side_effect = FileExistsError(CANARY)
            with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(main(["rebuild", "--capture", str(path), "--scaffold", directory]), 2)
            self.assertNotIn(CANARY, stdout.getvalue() + stderr.getvalue())

    def test_cli_unexpected_backend_failure_never_emits_a_traceback(self):
        with tempfile.TemporaryDirectory() as directory, rebuild_backend() as backend:
            path = Path(directory) / "capture.json"
            path.write_text(json.dumps({"artifacts": CAPTURE}), encoding="utf-8")
            backend.plan_rebuild.side_effect = RuntimeError(CANARY)
            with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(main(["rebuild", "--capture", str(path)]), 2)
            self.assertNotIn(CANARY, stdout.getvalue() + stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())


class RebuildHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(0)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.port = cls.server.server_address[1]
        cls.origin = f"http://127.0.0.1:{cls.port}"
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=5)
        connection.request("GET", "/")
        cls.token = re.search(rb'"([0-9a-f]{64})"', connection.getresponse().read()).group(1).decode()
        connection.close()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(1)

    def request(self, path, payload=None, extra=None):
        headers = {"Origin": self.origin, "Content-Type": "application/json", "X-Surface-Token": self.token, **(extra or {})}
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request("GET" if payload is None else "POST", path,
                               None if payload is None else json.dumps(payload).encode(), headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_rebuild_plan_and_optional_files_dispatch_without_disk_writes(self):
        with rebuild_backend() as backend:
            status, headers, body = self.request("/api/rebuild", {"artifacts": CAPTURE, "javascript_engine": "basic"})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"plan": PLAN})
            self.assertEqual(headers["Cache-Control"], "no-store")
            backend.scaffold_files.assert_not_called()
            status, _, body = self.request("/api/rebuild", {"artifacts": CAPTURE, "include_scaffold": True})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"plan": PLAN, "files": FILES})
            backend.write_scaffold.assert_not_called()

    def test_project_accepts_24_files_and_returns_minimized_inventory(self):
        files = [{"kind": "python", "content": "import requests\n", "logical_path": f"{CANARY}_{n}.py"} for n in range(24)]
        status, _, body = self.request("/api/project", {"files": files, "javascript_engine": "basic"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["inventory"]["coverage"]["selected_files"], 24)
        self.assertNotIn(CANARY.encode(), body)

    def test_project_four_mebibyte_selection_uses_large_envelope(self):
        files = [{"kind": "python", "content": "#" + "x" * (512 * 1024 - 1), "logical_path": f"module_{n}.py"} for n in range(8)]
        status, _, body = self.request("/api/project", {"files": files, "javascript_engine": "basic"})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["inventory"]["coverage"]["selected_files"], 8)

    def test_new_routes_keep_host_origin_token_checks(self):
        for route, payload in (("/api/rebuild", {"artifacts": CAPTURE}), ("/api/project", {"files": []})):
            for headers in ({"Origin": "https://foreign.invalid"}, {"Host": "foreign.invalid"},
                            {"X-Surface-Token": ""}, {"Sec-Fetch-Site": "cross-site"}):
                with self.subTest(route=route, headers=headers):
                    self.assertEqual(self.request(route, payload, headers)[0], 403)

    def test_invalid_scaffold_flags_shapes_and_project_paths_are_private(self):
        for payload in ({"artifacts": CAPTURE, "include_scaffold": value} for value in (1, "true", [], None)):
            self.assertEqual(self.request("/api/rebuild", payload)[0], 422)
        for route, payload in (("/api/rebuild", {"artifacts": CAPTURE, CANARY: CANARY}),
                               ("/api/project", {"files": [{"kind": "python", "content": CANARY, "logical_path": "../" + CANARY + ".py"}]})):
            status, _, body = self.request(route, payload)
            self.assertEqual(status, 422)
            self.assertNotIn(CANARY.encode(), body)

    def test_new_route_backend_exceptions_are_minimized(self):
        with rebuild_backend() as backend:
            for error, expected_status in ((ArtifactError(CANARY), 422), (RuntimeError(CANARY), 500)):
                backend.plan_rebuild.side_effect = error
                status, _, body = self.request("/api/rebuild", {"artifacts": CAPTURE})
                self.assertEqual(status, expected_status)
                self.assertNotIn(CANARY.encode(), body)

    def test_new_limits_and_output_cap_are_exposed(self):
        status, _, body = self.request("/api/limits")
        self.assertEqual(status, 200)
        limits = json.loads(body)
        self.assertEqual(limits["project"]["max_files"], 24)
        self.assertEqual(limits["project_request_bytes"], MAX_REQUEST)
        with rebuild_backend(), patch("surface_map.server.MAX_RESPONSE", 10):
            self.assertEqual(self.request("/api/rebuild", {"artifacts": CAPTURE})[0], 413)

    def test_actual_rebuild_backend_derives_plan_from_artifacts_only(self):
        status, _, body = self.request("/api/rebuild", {"artifacts": CAPTURE, "javascript_engine": "basic"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["plan"]["format"], "surface-map-rebuild-v1")
        for key in ("plan", "operations", "destination", "query"):
            status, _, body = self.request("/api/rebuild", {"artifacts": CAPTURE, key: CANARY})
            self.assertEqual(status, 422)
            self.assertNotIn(CANARY.encode(), body)


@unittest.skipUnless(HAS_MCP, "optional MCP SDK not installed")
class RebuildMCPTests(unittest.IsolatedAsyncioTestCase):
    @contextlib.asynccontextmanager
    async def stdio_session(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        env = {"PYTHONSAFEPATH": "1", **{key: os.environ[key] for key in ("SURFACE_MAP_NODE",) if key in os.environ}}
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "run_mcp.py")],
                                       cwd=tempfile.gettempdir(), env=env)
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    yield client
            log.seek(0)
            self.assertNotIn(CANARY, log.read())

    async def test_new_tools_are_read_only_and_validate_without_fetching(self):
        from mcp import Client
        async with Client(mcp_server.create_server()) as client:
            discovered = await client.list_tools()
            for tool in discovered.tools:
                if tool.name in {"plan_rebuild", "inspect_project"}:
                    self.assertTrue(tool.annotations.read_only_hint)
                    self.assertFalse(tool.annotations.open_world_hint)
            with rebuild_backend(), patch.object(mcp_server, "capture_public_url", side_effect=AssertionError("No fetch")):
                result = await client.call_tool("plan_rebuild", {"artifacts": CAPTURE, "include_scaffold": True})
                self.assertFalse(result.is_error)
                self.assertEqual(json.loads(result.content[0].text), {"plan": PLAN, "files": FILES})
                invalid = await client.call_tool("plan_rebuild", {"artifacts": CAPTURE, "include_scaffold": CANARY})
                self.assertTrue(invalid.is_error)
                self.assertNotIn(CANARY, invalid.model_dump_json())


    async def test_real_stdio_project_rebuild_privacy_and_unknown_keys(self):
        files = [{"kind": "python", "content": "import requests\n# " + CANARY,
                  "logical_path": f"{CANARY}_{n}.py"} for n in range(24)]
        captures = [{"kind": "html", "text": '<form action="/api/orders/' + CANARY + '?token=' + CANARY
                     + '" method="post"><input name="id" value="' + CANARY + '"></form>'}]
        async with self.stdio_session() as client:
            result = await client.call_tool("inspect_project", {"files": files, "javascript_engine": "basic"})
            self.assertFalse(result.is_error, result.content)
            self.assertEqual(json.loads(result.content[0].text)["coverage"]["selected_files"], 24)
            self.assertNotIn(CANARY, result.model_dump_json())
            result = await client.call_tool("plan_rebuild", {"artifacts": captures, "javascript_engine": "basic"})
            self.assertFalse(result.is_error, result.content)
            self.assertEqual(json.loads(result.content[0].text)["format"], "surface-map-rebuild-v1")
            self.assertNotIn(CANARY, result.model_dump_json())
            for tool, args in (("plan_rebuild", {"artifacts": captures, "plan": {"operations": CANARY}}),
                               ("plan_rebuild", {"artifacts": captures, "query": CANARY}),
                               ("inspect_project", {"files": files, "root": CANARY}),
                               ("inspect_project", {"files": files + [files[0]]})):
                invalid = await client.call_tool(tool, args)
                self.assertTrue(invalid.is_error)
                self.assertNotIn(CANARY, invalid.model_dump_json())

    async def test_real_stdio_scaffold_is_minimized_text_and_does_not_accept_plans(self):
        captures = [{"kind": "html", "text": '<form action="/api/orders/' + CANARY + '" method="post"></form>'}]
        async with self.stdio_session() as client:
            result = await client.call_tool("plan_rebuild", {"artifacts": captures, "javascript_engine": "basic",
                                                            "include_scaffold": True})
            self.assertFalse(result.is_error, result.content)
            value = json.loads(result.content[0].text)
            self.assertEqual(set(value), {"plan", "files"})
            self.assertTrue(value["files"])
            self.assertTrue(all(isinstance(name, str) and isinstance(text, str) for name, text in value["files"].items()))
            self.assertNotIn(CANARY, result.model_dump_json())


if __name__ == "__main__":
    unittest.main()
