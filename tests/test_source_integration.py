import contextlib
import http.client
import io
import json
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from surface_map.__main__ import main
from surface_map.analyze import ArtifactError
from surface_map.server import make_server
from surface_map.source_io import _windows_local_path, capture_sources, compare_sources, inspect_sources, read_ordinary, selected_sources, source_examples, source_kind

CANARY = "PRIVATE" + "_SOURCE_SELECTION_8452"
EMAIL = "person" + chr(64) + "example.invalid"


class SourceInputTests(unittest.TestCase):
    def test_manifest_names_and_source_extensions_are_curated(self):
        for name, kind in [("requirements.txt", "requirements"), ("pyproject.toml", "pyproject"), ("package.json", "package"), ("Cargo.toml", "cargo"), ("example.py", "python"), ("example.js", "javascript"), ("example.mjs", "javascript"), ("example.cjs", "javascript"), ("example.jsx", "javascript"), ("example.tsx", "typescript"), ("example.rs", "rust"), ("example.sh", "shell")]:
            self.assertEqual(source_kind(name), kind)
        for name in (".env", ".py", ".js", ".ts", ".rs", ".sh", "py", "rs", "sh", "private.json", "private.toml", "__proto__", "constructor", "files.zip"):
            self.assertIsNone(source_kind(name))

    def test_javascript_imports_keep_javascript_language_and_minimized_evidence(self):
        report = inspect_sources([{"kind": "javascript", "content": "const note = '" + CANARY + "';\nconst ui = require('react');\nimport axios from 'axios';\n"}])
        self.assertEqual([item["name"] for item in report["languages"]], ["javascript"], report["warnings"])
        self.assertEqual({item["name"] for item in report["dependencies"]}, {"react", "axios"})
        self.assertNotIn(CANARY, json.dumps(report))
        self.assertEqual(report["dependencies"][0]["evidence"][0]["location"], "line 3")

    def test_explicit_selected_files_return_generic_source_inventory_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.json"
            path.write_text(json.dumps({"name": CANARY, "author": EMAIL, "dependencies": {"react": "1.2.3", CANARY: "file:/private/example"}, "scripts": {"start": "echo " + CANARY}}), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch("surface_map.__main__.make_server", side_effect=AssertionError("Server forbidden")):
                result = main(["inspect", "--files", str(path)])
            self.assertEqual(result, 0)
            self.assertEqual(stderr.getvalue(), "")
            inventory = json.loads(stdout.getvalue())
            self.assertIn("react", [entry["name"] for entry in inventory["dependencies"]])
            for forbidden in (CANARY, EMAIL, "1.2.3", "package.json", "file:/private/example"):
                self.assertNotIn(forbidden, stdout.getvalue())
            self.assertEqual(inventory["sources"][0]["id"], "source_1")

    def test_capture_cli_and_selected_files_have_identical_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "example.py"
            source.write_text("import fastapi\n", encoding="utf-8")
            capture = root / "selection.json"
            capture.write_text(json.dumps({"artifacts": selected_sources([source])}), encoding="utf-8")
            self.assertEqual(inspect_sources(capture_sources(capture)), inspect_sources(selected_sources([source])))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["inspect", "--capture", str(capture)]), 0)
            self.assertEqual(json.loads(stdout.getvalue()), inspect_sources(selected_sources([source])))

    def test_cli_errors_never_echo_input_paths_or_bad_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (CANARY + ".json")
            for content in ('{"artifacts":[],"artifacts":[]}', '{"' + CANARY + '":"' + EMAIL + '"}', '{"artifacts":' + '[' * 100 + '0' + ']' * 100 + '}'):
                path.write_text(content, encoding="utf-8")
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    self.assertEqual(main(["inspect", "--capture", str(path)]), 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertNotIn(CANARY, stderr.getvalue())
                self.assertNotIn(EMAIL, stderr.getvalue())

    def test_unsupported_file_is_rejected_without_reading_it(self):
        with patch("surface_map.source_io.read_ordinary", side_effect=AssertionError("Must not read")), self.assertRaises(ArtifactError):
            selected_sources([Path(CANARY + ".env")])

    def test_file_byte_and_utf8_bounds_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.py"
            path.write_bytes(b"x" * (512 * 1024 + 1))
            with self.assertRaises(ArtifactError):
                selected_sources([path])
            path.write_bytes(b"\xff")
            with self.assertRaises(ArtifactError):
                selected_sources([path])
            with self.assertRaises(ArtifactError):
                read_ordinary(Path(directory), 100)

    def test_selected_files_stop_reading_once_combined_limit_is_exceeded(self):
        paths = [Path("source_" + str(number) + ".py") for number in range(6)]
        with patch("surface_map.source_io.read_ordinary", return_value="x" * (512 * 1024)) as reader, self.assertRaises(ArtifactError):
            selected_sources(paths)
        self.assertEqual(reader.call_count, 3)

    def test_reparse_points_are_not_followed(self):
        info = SimpleNamespace(st_mode=0o100600, st_file_attributes=0x400)
        with patch.object(Path, "lstat", return_value=info), patch("os.open", side_effect=AssertionError("Must not open")), self.assertRaises(ArtifactError):
            read_ordinary(Path("synthetic.py"), 100)

    def test_windows_network_namespaces_and_alternate_streams_are_rejected_lexically(self):
        for path in (r"\\host.invalid\share\example.py", "//host.invalid/share/example.py", r"\\?\C:\example.py", r"\\.\NUL", r"\??\C:\example.py", r"example.txt:hidden.py", r"C:\example.txt:hidden.py"):
            self.assertFalse(_windows_local_path(path))
            if os.name == "nt":
                with patch.object(Path, "lstat", side_effect=AssertionError("Network or stream must not be accessed")), self.assertRaises(ArtifactError):
                    read_ordinary(path, 100)
        for path in ("example.py", r"C:\selected\example.py", r".\selected\example.py"):
            self.assertTrue(_windows_local_path(path))

    @unittest.skipUnless(os.name == "nt", "Windows drive fixture")
    def test_windows_relative_unc_and_mapped_network_drives_are_rejected_before_stat(self):
        with patch("os.getcwd", return_value=r"\\host.invalid\share\selected"), patch.object(Path, "lstat", side_effect=AssertionError("UNC must not be touched")), self.assertRaises(ArtifactError):
            read_ordinary("requirements.txt", 100)
        for drive_type in (0, 1, 4):
            with patch("surface_map.source_io._windows_drive_type", return_value=drive_type), patch.object(Path, "lstat", side_effect=AssertionError("Remote or unknown drive must not be touched")), self.assertRaises(ArtifactError):
                read_ordinary(r"Z:\selected\example.py", 100)

    def test_parent_reparse_point_is_rejected_before_accessing_a_descendant(self):
        path = Path.cwd() / "synthetic-link" / "example.py"
        calls = []

        def inspect(component):
            calls.append(component)
            return SimpleNamespace(st_mode=0o040600, st_file_attributes=0x400 if component == path.parent else 0)

        with patch.object(Path, "lstat", inspect), patch("surface_map.source_io._windows_drive_type", return_value=3), patch("os.open", side_effect=AssertionError("Must not open")), self.assertRaises(ArtifactError):
            read_ordinary(path, 100)
        self.assertEqual(calls, [*reversed(path.parents)])
        self.assertNotIn(path, calls)

    @unittest.skipUnless(os.name == "nt", "Windows alternate-stream fixture")
    def test_existing_ntfs_alternate_stream_is_not_read(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "example.txt"
            base.write_text("ordinary file", encoding="utf-8")
            stream = str(base) + ":hidden.py"
            try:
                Path(stream).write_text("import fastapi\n", encoding="utf-8")
            except OSError:
                self.skipTest("Alternate streams unavailable on the temporary filesystem")
            with patch.object(Path, "lstat", side_effect=AssertionError("Stream must be rejected before stat")), self.assertRaises(ArtifactError):
                selected_sources([Path(stream)])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO fixture")
    def test_fifo_is_rejected_before_open(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fifo.py"
            os.mkfifo(path)
            with patch("os.open", side_effect=AssertionError("Must not open FIFO")), self.assertRaises(ArtifactError):
                read_ordinary(path, 100)

    def test_sources_are_not_executed_and_dependency_includes_are_not_opened(self):
        artifacts = [{"kind": "python", "content": "import os\nos.system('" + CANARY + "')\nimport fastapi\n"},
                     {"kind": "requirements", "content": "-r /private/" + CANARY + "\nfastapi\n"}]
        with patch("os.system", side_effect=AssertionError("Execution forbidden")), patch("socket.create_connection", side_effect=AssertionError("Network forbidden")), patch("subprocess.Popen", side_effect=AssertionError("Process forbidden")):
            result = inspect_sources(artifacts)
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertIn("fastapi", [entry["name"] for entry in result["dependencies"]])

    def test_source_comparison_tracks_labels_and_confidence_without_values(self):
        before = inspect_sources([{"kind": "requirements", "content": "fastapi\n"}])
        after = inspect_sources([{"kind": "python", "content": "import fastapi\nimport sounddevice\n"}])
        result = compare_sources(before, after)
        self.assertEqual([item["name"] for item in result["groups"]["dependencies"]["added"]], ["sounddevice"])
        self.assertEqual(result["groups"]["dependencies"]["changed"][0]["previous_confidence"], "declared")
        self.assertEqual(result["groups"]["dependencies"]["changed"][0]["confidence"], "inferred")

    def test_rejected_source_cannot_create_a_false_absence_comparison(self):
        valid = inspect_sources(source_examples())
        invalid = inspect_sources([{"kind": "python", "content": "def : " + CANARY}])
        self.assertEqual(invalid["sources"][0]["status"], "rejected")
        with self.assertRaises(ArtifactError):
            compare_sources(valid, invalid)

    def test_invalid_cli_arguments_do_not_echo_tokens(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            main(["--port", CANARY])
        self.assertEqual(error.exception.code, 2)
        self.assertNotIn(CANARY, stderr.getvalue())


class SourceHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(0)
        cls.port = cls.server.server_port
        cls.origin = "http://127.0.0.1:" + str(cls.port)
        cls.worker = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.02})
        cls.worker.start()
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=2)
        connection.request("GET", "/")
        cls.token = re.search(rb'name="surface-token" content="([a-f0-9]+)"', connection.getresponse().read()).group(1).decode()
        connection.close()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(1)

    def post(self, path, payload, extra=None):
        headers = {"Origin": self.origin, "Content-Type": "application/json", "X-Surface-Token": self.token, **(extra or {})}
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request("POST", path, json.dumps(payload).encode(), headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_source_endpoint_and_demo_return_minimized_evidence(self):
        data = {"name": CANARY, "private": EMAIL, "dependencies": {"react": "1.2.3"}}
        status, headers, body = self.post("/api/source", {"artifacts": [{"kind": "package", "content": json.dumps(data)}]})
        self.assertEqual(status, 200)
        self.assertNotIn(CANARY.encode(), body)
        self.assertNotIn(EMAIL.encode(), body)
        self.assertNotIn(b"1.2.3", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(json.loads(body)["inventory"]["dependencies"][0]["name"], "react")
        self.assertEqual(self.post("/api/source-demo", {})[0], 200)

    def test_source_endpoints_keep_origin_host_and_session_checks(self):
        for headers in ({"Origin": "https://foreign.invalid"}, {"Host": "foreign.invalid"}, {"X-Surface-Token": ""}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.post("/api/source-demo", {}, headers)[0], 403)

    def test_source_request_cannot_supply_paths_urls_or_filenames(self):
        for payload in ({"path": CANARY}, {"url": "https://" + CANARY + ".invalid"}, {"artifacts": [{"kind": "python", "content": "pass", "filename": EMAIL}]}):
            status, _, body = self.post("/api/source", payload)
            self.assertEqual(status, 422)
            self.assertNotIn(CANARY.encode(), body)
            self.assertNotIn(EMAIL.encode(), body)

    def test_source_compare_is_static_and_rejected_inputs_do_not_diff(self):
        before = [{"kind": "requirements", "content": "fastapi\n"}]
        after = [{"kind": "requirements", "content": "fastapi\nsounddevice\n"}]
        status, _, body = self.post("/api/source-compare", {"baseline": before, "candidate": after})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["diff"]["groups"]["dependencies"]["added"][0]["name"], "sounddevice")
        after = [{"kind": "python", "content": "def : " + CANARY}]
        self.assertEqual(self.post("/api/source-compare", {"baseline": before, "candidate": after})[0], 422)

    def test_source_response_obeys_shared_output_cap(self):
        with patch("surface_map.server.MAX_RESPONSE", 100):
            status, _, body = self.post("/api/source-demo", {})
        self.assertEqual(status, 413)
        self.assertLess(len(body), 200)
