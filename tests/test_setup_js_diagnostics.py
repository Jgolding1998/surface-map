"""Fixed first-run setup guidance without installing or running any package."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from surface_map import js_parser
from surface_map.__main__ import main


CANARY = "PRIVATE_SETUP_DIAGNOSTIC_CANARY"


class SetupDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="surface-setup-synthetic-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / CANARY
        self.analyzer = self.root / "analyzer"
        self.analyzer.mkdir(parents=True)
        self.payloads = {
            "parse.mjs": b"// reviewed synthetic parser fixture\n",
            "package.json": b'{"private":true}\n',
            "package-lock.json": b'{"lockfileVersion":3}\n',
            "node_modules/@babel/parser/lib/index.js": b"// synthetic installed parser\n",
            "node_modules/@babel/parser/package.json": b'{"version":"8.0.4"}\n',
        }
        record = {"format": 1, "parser": "8.0.4", "files": {
            name: hashlib.sha256(data).hexdigest() for name, data in self.payloads.items()}}
        (self.analyzer / "integrity.json").write_text(json.dumps(record), encoding="utf-8")
        for name, data in self.payloads.items():
            if not name.startswith("node_modules/"):
                (self.analyzer / name).write_bytes(data)
        self.node = self.root / "node" / ("node.exe" if os.name == "nt" else "node")
        self.node.parent.mkdir()
        self.node.write_bytes(b"synthetic executable placeholder; never run")
        self.npm = self.node.parent / "node_modules/npm/bin/npm-cli.js"
        self.npm.parent.mkdir(parents=True)
        self.npm.write_bytes(b"// synthetic npm placeholder; never run\n")
        patched = patch.object(js_parser, "ANALYZER", self.analyzer)
        patched.start()
        self.addCleanup(patched.stop)

    def invoke(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                patch.object(js_parser.subprocess, "Popen", side_effect=AssertionError("No setup subprocess may run in this test")):
            code = main(["setup-js"])
        return code, output.getvalue(), errors.getvalue()

    def assert_failure(self, result, expected):
        code, output, errors = result
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn(expected, errors)
        self.assertIn("python -m surface_map setup-js", errors)
        for private in (CANARY, str(self.root), "Traceback"):
            self.assertNotIn(private, errors)

    def test_missing_node_explains_supported_install_and_absolute_override(self):
        with patch.object(js_parser, "_node", side_effect=js_parser.JsParserError("node_missing")), \
                patch.object(js_parser, "_run") as run:
            result = self.invoke()
        self.assert_failure(result, "Node.js")
        self.assertIn("24.22.1", result[2])
        self.assertIn("24.x", result[2])
        self.assertIn("SURFACE_MAP_NODE", result[2])
        self.assertIn("absolute", result[2])
        run.assert_not_called()

    def test_unsupported_node_does_not_suggest_accepting_a_new_major(self):
        with patch.object(js_parser, "_node", return_value=self.node), \
                patch.object(js_parser, "_check_node", side_effect=js_parser.JsParserError("node_unsupported")), \
                patch.object(js_parser, "_run") as run:
            result = self.invoke()
        self.assert_failure(result, "24.22.1")
        self.assertIn("24.x", result[2])
        run.assert_not_called()

    def test_missing_bundled_npm_has_recovery_without_path_or_shell_fallback(self):
        self.npm.unlink()
        with patch.object(js_parser, "_node", return_value=self.node), \
                patch.object(js_parser, "_check_node"), patch.object(js_parser, "_run") as run:
            result = self.invoke()
        self.assert_failure(result, "npm")
        self.assertIn("Node.js", result[2])
        run.assert_not_called()

    def test_real_integrity_refusal_precedes_node_or_install(self):
        (self.analyzer / "parse.mjs").write_bytes(CANARY.encode())
        with patch.object(js_parser, "_node") as node, patch.object(js_parser, "_run") as run:
            result = self.invoke()
        self.assert_failure(result, "fresh")
        self.assertIn("reviewed", result[2].lower())
        node.assert_not_called()
        run.assert_not_called()
        self.assertEqual((self.analyzer / "parse.mjs").read_bytes(), CANARY.encode())

    def test_local_npm_config_refusal_remains_intact_and_is_explained(self):
        config = self.analyzer / ".npmrc"
        config.write_text(CANARY, encoding="utf-8")
        with patch.object(js_parser, "_node", return_value=self.node), \
                patch.object(js_parser, "_check_node"), patch.object(js_parser, "_run") as run:
            result = self.invoke()
        self.assert_failure(result, "fresh")
        run.assert_not_called()
        self.assertEqual(config.read_text(), CANARY)

    def test_dangling_node_modules_link_is_refused_before_the_installer(self):
        modules = self.analyzer / "node_modules"
        target = self.root / "synthetic-module-target"
        target.mkdir()
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(modules))
            self.addCleanup(modules.rmdir)
        else:
            modules.symlink_to(target, target_is_directory=True)
            self.addCleanup(modules.unlink)
        target.rmdir()
        self.assertFalse(modules.exists())
        info = modules.lstat()
        self.assertTrue(info.st_file_attributes & 0x400 if os.name == "nt" else modules.is_symlink())
        with patch.object(js_parser, "_node", return_value=self.node), \
                patch.object(js_parser, "_check_node"), \
                patch.object(js_parser, "_run", side_effect=js_parser.JsParserError("failed")) as run:
            result = self.invoke()
        self.assert_failure(result, "fresh")
        run.assert_not_called()
        self.assertFalse(target.exists())

    def test_installer_errors_never_disclose_private_exception_text(self):
        for error in (OSError(CANARY), js_parser.JsParserError("failed"), js_parser.JsParserError("output_limit")):
            with self.subTest(kind=type(error).__name__), \
                    patch.object(js_parser, "_node", return_value=self.node), \
                    patch.object(js_parser, "_check_node"), patch.object(js_parser, "_run", side_effect=error) as run:
                result = self.invoke()
            self.assert_failure(result, "setup")
            self.assertEqual(run.call_count, 1)
            args = run.call_args.args[0]
            self.assertEqual(args[:3], [str(self.node), str(self.npm), "ci"])
            self.assertIn("--ignore-scripts", args)
            self.assertIn("--registry=https://registry.npmjs.org", args)
            self.assertIn("--bin-links=false", args)
            self.assertEqual(run.call_args.kwargs["timeout"], 90)

    def test_timeout_has_specific_recovery_without_disclosing_output(self):
        with patch.object(js_parser, "_node", return_value=self.node), \
                patch.object(js_parser, "_check_node"), \
                patch.object(js_parser, "_run", side_effect=js_parser.JsParserError("timeout")):
            result = self.invoke()
        self.assert_failure(result, "time limit")

    def test_success_is_announced_only_after_installed_integrity_passes(self):
        def installed(*args, **kwargs):
            for name, data in self.payloads.items():
                if name.startswith("node_modules/"):
                    target = self.analyzer / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            return b""
        with patch.object(js_parser, "_node", return_value=self.node), \
                patch.object(js_parser, "_check_node"), patch.object(js_parser, "_run", side_effect=installed) as run:
            code, output, errors = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("ready", output.lower())
        self.assertEqual(errors, "")
        self.assertEqual(run.call_count, 1)
        js_parser._integrity()

    def test_successful_installer_exit_cannot_hide_missing_parser_files(self):
        with patch.object(js_parser, "_node", return_value=self.node), \
                patch.object(js_parser, "_check_node"), patch.object(js_parser, "_run", return_value=b""):
            result = self.invoke()
        self.assert_failure(result, "fresh")


if __name__ == "__main__":
    unittest.main()
