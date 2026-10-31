import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("surface_map_ci_action", ROOT / "scripts" / "ci_action.py")
ACTION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACTION)
CANARY = "PRIVATE_ACTION_CANARY_9284"


def capture(path):
    return {"artifacts": [{"kind": "html", "text": '<a href="' + path + '">Saved link</a>'}]}


class CIActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.workspace = self.directory / "workspace"
        self.workspace.mkdir()
        self.before = self.workspace / "before.json"
        self.after = self.workspace / "after.json"
        self.before.write_text(json.dumps(capture("/dashboard")), encoding="utf-8")
        self.after.write_text(json.dumps(capture("/login")), encoding="utf-8")
        self.github_output = self.directory / "github-output.txt"
        self.summary = self.directory / "summary.md"
        self.env = {"GITHUB_WORKSPACE": str(self.workspace),
                    "GITHUB_OUTPUT": str(self.github_output),
                    "GITHUB_STEP_SUMMARY": str(self.summary),
                    "SURFACE_MAP_BEFORE": "before.json", "SURFACE_MAP_AFTER": "after.json",
                    "SURFACE_MAP_ENGINE": "basic", "SURFACE_MAP_FAIL_ON": "",
                    "SURFACE_MAP_OUTPUT_DIRECTORY": "reports"}

    def tearDown(self):
        self.temporary.cleanup()

    def invoke(self, **overrides):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = ACTION.main({**self.env, **overrides})
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_private_rejection(self, result):
        code, stdout, stderr = result
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("comparison rejected", stderr)
        for private in (CANARY, str(self.workspace), str(self.directory), "Traceback"):
            self.assertNotIn(private, stderr)
        self.assertFalse(self.github_output.exists())
        self.assertFalse(self.summary.exists())

    def symlink(self, link, target, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks is unavailable on this host")

    def test_advisory_writes_minimized_reports_action_outputs_and_job_summary(self):
        code, stdout, stderr = self.invoke()
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("advisory report", stdout)
        report_json = self.workspace / "reports" / "comparison.json"
        report_markdown = self.workspace / "reports" / "comparison.md"
        result = json.loads(report_json.read_text(encoding="utf-8"))
        self.assertEqual(result["comparison"]["added"][0]["path"], "/login")
        self.assertEqual(result["comparison"]["removed"][0]["path"], "/dashboard")
        self.assertTrue(result["policy"]["passed"])
        output = self.github_output.read_text(encoding="utf-8")
        self.assertIn("report-json=" + str(report_json), output)
        self.assertIn("report-markdown=" + str(report_markdown), output)
        self.assertIn("policy-passed=\n", output)
        self.assertIn("policy-status=advisory\n", output)
        self.assertEqual(self.summary.read_text(encoding="utf-8"), report_markdown.read_text(encoding="utf-8") + "\n")

    def test_policy_failure_keeps_reports_and_summary_with_exit_one(self):
        code, stdout, stderr = self.invoke(SURFACE_MAP_FAIL_ON="added,absent")
        self.assertEqual((code, stderr), (1, ""))
        self.assertIn("policies failed", stdout)
        report = json.loads((self.workspace / "reports" / "comparison.json").read_text(encoding="utf-8"))
        self.assertEqual(report["policy"]["matched"], ["added", "absent"])
        self.assertIn("policy-passed=false\n", self.github_output.read_text(encoding="utf-8"))
        self.assertIn("selected policy matched", self.summary.read_text(encoding="utf-8"))

    def test_existing_github_environment_files_are_appended(self):
        self.github_output.write_text("existing-output=yes\n", encoding="utf-8")
        self.summary.write_text("Existing summary\n", encoding="utf-8")
        self.assertEqual(self.invoke()[0], 0)
        self.assertTrue(self.github_output.read_text(encoding="utf-8").startswith("existing-output=yes\n"))
        self.assertTrue(self.summary.read_text(encoding="utf-8").startswith("Existing summary\n"))

    def test_reports_work_without_github_output_or_summary_files(self):
        self.assertEqual(self.invoke(GITHUB_OUTPUT="", GITHUB_STEP_SUMMARY="")[0], 0)
        self.assertTrue((self.workspace / "reports" / "comparison.json").is_file())
        self.assertFalse(self.github_output.exists())
        self.assertFalse(self.summary.exists())

    def test_existing_output_directory_is_refused_without_overwriting_caller_files(self):
        output = self.workspace / "reports"
        output.mkdir()
        preserved = output / "comparison.md"
        preserved.write_text(CANARY, encoding="utf-8")
        self.assert_private_rejection(self.invoke())
        self.assertEqual(preserved.read_text(encoding="utf-8"), CANARY)
        self.assertFalse((output / "comparison.json").exists())

    def test_report_writer_exclusively_creates_files_even_after_directory_reservation(self):
        target = self.workspace / "existing.json"
        target.write_text(CANARY, encoding="utf-8")
        with self.assertRaises((ValueError, OSError)):
            ACTION._write_report(target, "replacement")
        self.assertEqual(target.read_text(encoding="utf-8"), CANARY)

    def test_input_and_output_traversal_absolute_paths_and_newlines_are_rejected(self):
        invalid = ("../" + CANARY, "/" + CANARY, str(self.before), "C:/" + CANARY,
                   "nested/../../" + CANARY, "nested\\" + CANARY, "x\n" + CANARY)
        for name in ("SURFACE_MAP_BEFORE", "SURFACE_MAP_AFTER", "SURFACE_MAP_OUTPUT_DIRECTORY"):
            for value in invalid:
                with self.subTest(name=name, value=value):
                    self.assert_private_rejection(self.invoke(**{name: value}))

    def test_missing_or_invalid_workspace_is_minimized(self):
        for value in ("", str(self.directory / CANARY), str(self.workspace) + "\n" + CANARY):
            with self.subTest(value=value):
                self.assert_private_rejection(self.invoke(GITHUB_WORKSPACE=value))

    def test_malformed_or_extra_key_capture_and_unknown_policy_are_minimized(self):
        for value in ("{" + CANARY, json.dumps({"artifacts": [], CANARY: CANARY}),
                      '{"artifacts":[],"artifacts":[]}'):
            with self.subTest(value=value):
                self.before.write_text(value, encoding="utf-8")
                self.assert_private_rejection(self.invoke())
                self.assertFalse((self.workspace / "reports").exists())
        self.before.write_text(json.dumps(capture("/dashboard")), encoding="utf-8")
        self.assert_private_rejection(self.invoke(SURFACE_MAP_FAIL_ON=CANARY))

    def test_input_symlink_is_rejected_without_reading_its_target(self):
        target = self.directory / (CANARY + ".json")
        target.write_text(json.dumps(capture("/dashboard")), encoding="utf-8")
        self.symlink(self.workspace / "linked.json", target)
        self.assert_private_rejection(self.invoke(SURFACE_MAP_BEFORE="linked.json"))
        self.assertFalse((self.workspace / "reports").exists())

    def test_output_directory_symlink_is_rejected(self):
        target = self.directory / CANARY
        target.mkdir()
        self.symlink(self.workspace / "reports", target, directory=True)
        self.assert_private_rejection(self.invoke())
        self.assertEqual(list(target.iterdir()), [])

    def test_output_file_symlink_is_rejected_without_overwriting_target(self):
        output = self.workspace / "reports"
        output.mkdir()
        target = self.directory / CANARY
        target.write_text(CANARY, encoding="utf-8")
        self.symlink(output / "comparison.json", target)
        self.assert_private_rejection(self.invoke())
        self.assertEqual(target.read_text(encoding="utf-8"), CANARY)

    def test_output_hardlink_cannot_overwrite_a_capture(self):
        output = self.workspace / "reports"
        output.mkdir()
        original = self.before.read_bytes()
        try:
            os.link(self.before, output / "comparison.json")
        except (OSError, NotImplementedError):
            self.skipTest("Creating hardlinks is unavailable on this host")
        self.assert_private_rejection(self.invoke())
        self.assertEqual(self.before.read_bytes(), original)

    def test_report_path_cannot_overwrite_an_input_capture(self):
        output = self.workspace / "reports"
        output.mkdir()
        path = output / "comparison.json"
        original = json.dumps(capture("/dashboard")).encode()
        path.write_bytes(original)
        self.assert_private_rejection(self.invoke(SURFACE_MAP_BEFORE="reports/comparison.json"))
        self.assertEqual(path.read_bytes(), original)

    def test_incomparable_captures_fail_before_creating_reports(self):
        document = capture("/login")
        document["document_url"] = "https://" + CANARY + ".invalid/"
        self.after.write_text(json.dumps(document), encoding="utf-8")
        self.assert_private_rejection(self.invoke())
        self.assertFalse((self.workspace / "reports").exists())

    def test_captured_code_is_never_executed_or_copied_to_reports(self):
        sentinel = self.directory / (CANARY + ".txt")
        script = "fetch('/api/orders'); require('fs').writeFileSync(" + json.dumps(str(sentinel)) + "," + json.dumps(CANARY) + ");"
        document = {"artifacts": [{"kind": "javascript", "text": script}]}
        for path in (self.before, self.after):
            path.write_text(json.dumps(document), encoding="utf-8")
        code, stdout, stderr = self.invoke(SURFACE_MAP_FAIL_ON="changed,added,absent")
        self.assertEqual((code, stderr), (0, ""))
        self.assertFalse(sentinel.exists())
        reports = "".join(path.read_text(encoding="utf-8") for path in (self.workspace / "reports").iterdir())
        self.assertNotIn(CANARY, reports + stdout + stderr)

    def test_absolute_action_entrypoint_does_not_import_from_callers_checkout(self):
        sentinel = self.workspace / "executed.txt"
        (self.workspace / "surface_map.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(sentinel)) + ").write_text(" + repr(CANARY) + ")\nraise RuntimeError(" + repr(CANARY) + ")\n",
            encoding="utf-8")
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "ci_action.py")],
                                cwd=self.workspace, env={**os.environ, **self.env},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sentinel.exists())
        self.assertNotIn(CANARY, result.stdout + result.stderr)
        self.assertTrue((self.workspace / "reports" / "comparison.json").is_file())

    def test_isolated_action_startup_ignores_pythonpath_sitecustomize(self):
        sentinel = self.workspace / "startup-executed.txt"
        (self.workspace / "sitecustomize.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(sentinel)) + ").write_text('executed')\n",
            encoding="utf-8")
        result = subprocess.run([sys.executable, "-I", "-S", str(ROOT / "scripts" / "ci_action.py")],
                                cwd=self.workspace, env={**os.environ, **self.env, "PYTHONPATH": str(self.workspace)},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sentinel.exists())
        action_source = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.assertIn('python -I -S "$GITHUB_ACTION_PATH/scripts/ci_action.py"', action_source)

    def test_absolute_cli_launcher_works_outside_the_tool_checkout(self):
        result = subprocess.run([sys.executable, str(ROOT / "run_ci.py"), "compare",
                                 "--before", str(self.before), "--after", str(self.after),
                                 "--javascript-engine", "basic", "--fail-on", "added"],
                                cwd=self.workspace, env=os.environ.copy(),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["policy"]["matched"], ["added"])
        self.assertFalse(self.github_output.exists())


if __name__ == "__main__":
    unittest.main()
