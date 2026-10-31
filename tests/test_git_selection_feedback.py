"""Known file-count refusals remain private and actionable across entry points."""
import builtins
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from surface_map import ci, git_compare
from surface_map.__main__ import main as cli_main


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("selection_feedback_action", ROOT / "scripts" / "git_ci_action.py")
ACTION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACTION)
CANARY = "PRIVATE_SELECTION_FEEDBACK_CANARY"


class GitSelectionFeedbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.repo = self.workspace / ("application_" + CANARY)
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Synthetic Reviewer")
        self.git("config", "user.email", "reviewer@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.base = self.commit({})
        self.files = [CANARY + "_page_" + str(index) + ".html" for index in range(7)]
        six = {name: "<p>" + CANARY + "</p>" for name in self.files[:6]}
        six.update({CANARY + suffix: CANARY for suffix in (".md", ".txt", ".css", ".json", ".svg")})
        self.six = self.commit(six)
        self.seven = self.commit({self.files[6]: "<p>" + CANARY + "</p>"})

    def git(self, *arguments):
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_SYSTEM=os.devnull)
        result = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false",
                                 "-c", "core.hooksPath=" + os.devnull, "-C", str(self.repo), *arguments],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=True, shell=False, timeout=15)
        return result.stdout.decode("utf-8").strip()

    def commit(self, files):
        for name, text in files.items():
            (self.repo / name).write_text(text, encoding="utf-8")
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "-m", "synthetic change")
        return self.git("rev-parse", "HEAD")

    def compare(self, base, head, **options):
        return git_compare.compare_git(self.repo, base, head, javascript_engine="basic", **options)

    def invoke_cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(["compare-git", "--repo", str(self.repo), "--base", self.base,
                             "--head", self.seven, "--javascript-engine", "basic", *extra])
        return code, out.getvalue(), err.getvalue()

    def invoke_action(self, **overrides):
        env = {"GITHUB_WORKSPACE": str(self.workspace), "SURFACE_MAP_REPOSITORY": self.repo.name,
               "SURFACE_MAP_BASE": self.base, "SURFACE_MAP_HEAD": self.seven,
               "SURFACE_MAP_ENGINE": "basic", "SURFACE_MAP_OUTPUT_DIRECTORY": "reports",
               "GITHUB_OUTPUT": str(self.workspace / "outputs"),
               "GITHUB_STEP_SUMMARY": str(self.workspace / "summary"), **overrides}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ACTION.main(env)
        return code, out.getvalue(), err.getvalue()

    def assert_private_refusal(self, result):
        code, out, err = result
        self.assertEqual((code, out), (2, ""))
        self.assertNotIn(CANARY, err)
        self.assertNotIn(str(self.workspace), err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("policy-passed", err)
        for target in ("reports", "outputs", "summary"):
            self.assertFalse((self.workspace / target).exists())

    def test_six_changed_web_files_succeed_and_seven_refuse_without_counting_other_extensions(self):
        result = self.compare(self.base, self.six)
        self.assertTrue(result["analysis_performed"])
        self.assertEqual(result["selection"]["selected_files"], 6)
        self.assertEqual(result["selection"]["unsupported_changed_files"], 5)
        self.assertNotIn(CANARY, ci.json_output(result) + git_compare.markdown(result))
        with self.assertRaises(git_compare.GitSelectionLimitError) as caught:
            self.compare(self.base, self.seven)
        self.assertEqual((caught.exception.count, caught.exception.mode), (7, "changed_web_files"))
        self.assertEqual(str(caught.exception), str(git_compare.GitCompareError()))
        # The limit counts changed files, not every supported file in the tree.
        self.assertEqual(self.compare(self.six, self.seven)["selection"]["selected_files"], 1)

    def test_explicit_seven_refuse_and_explicit_six_preserve_outside_selection_count(self):
        with self.assertRaises(git_compare.GitSelectionLimitError) as caught:
            self.compare(self.base, self.seven, files=self.files)
        self.assertEqual((caught.exception.count, caught.exception.mode), (7, "explicit_files"))
        self.assertNotIn(CANARY, str(caught.exception) + repr(caught.exception))
        result = self.compare(self.base, self.seven, files=self.files[:6])
        self.assertEqual(result["selection"]["selected_files"], 6)
        self.assertEqual(result["selection"]["supported_changed_files_not_selected"], 1)

    def test_cli_reports_only_count_and_selection_guidance_for_both_modes(self):
        for extra, label in (([], "Changed supported web files: 7"),
                             (["--files", *self.files], "Explicit selection entries: 7")):
            with self.subTest(mode=label):
                result = self.invoke_cli(*extra)
                self.assert_private_refusal(result)
                self.assertIn(label, result[2])
                self.assertIn("--files", result[2])
                self.assertIn("Nothing was analyzed or silently truncated", result[2])

    def test_action_reports_count_and_files_input_without_any_success_channels(self):
        for selected, label in (("", "Changed supported web files: 7"),
                                ("\n".join(self.files), "Explicit selection entries: 7")):
            with self.subTest(mode=label):
                result = self.invoke_action(SURFACE_MAP_FILES=selected)
                self.assert_private_refusal(result)
                self.assertIn(label, result[2])
                self.assertIn("Action files input", result[2])

    def test_invalid_commit_after_count_refusal_does_not_reuse_selection_diagnostics(self):
        self.assert_private_refusal(self.invoke_cli())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(["compare-git", "--repo", str(self.repo), "--base", CANARY,
                             "--head", self.seven, "--files", *self.files])
        self.assert_private_refusal((code, out.getvalue(), err.getvalue()))
        self.assertNotIn("selection entries:", err.getvalue())
        self.assertNotIn("Changed supported web files:", err.getvalue())
        result = self.invoke_action(SURFACE_MAP_BASE=CANARY, SURFACE_MAP_FILES="\n".join(self.files))
        self.assert_private_refusal(result)
        self.assertNotIn("selection entries:", result[2])
        self.assertNotIn("Changed supported web files:", result[2])


class SelectionFeedbackProjectionTests(unittest.TestCase):
    def test_action_import_failures_keep_the_original_private_error_boundary(self):
        original_import = builtins.__import__
        for blocked in ("surface_map.ci", "surface_map.git_compare", "surface_map.source_io", "scripts.ci_action"):
            with self.subTest(module=blocked), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                env = {"GITHUB_WORKSPACE": str(workspace), "SURFACE_MAP_OUTPUT_DIRECTORY": "reports",
                       "GITHUB_OUTPUT": str(workspace / "outputs"),
                       "GITHUB_STEP_SUMMARY": str(workspace / "summary")}

                def reject_import(name, *args, **kwargs):
                    if name == blocked:
                        raise ImportError(CANARY + str(workspace))
                    return original_import(name, *args, **kwargs)

                out, err = io.StringIO(), io.StringIO()
                with patch("builtins.__import__", side_effect=reject_import), \
                        patch.object(sys, "path", sys.path.copy()), \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    module = importlib.util.module_from_spec(SPEC)
                    SPEC.loader.exec_module(module)
                    code = module.main(env)
                self.assertEqual((code, out.getvalue()), (2, ""))
                self.assertIn("Surface Map Git comparison rejected", err.getvalue())
                self.assertNotIn(CANARY, err.getvalue())
                self.assertNotIn(str(workspace), err.getvalue())
                self.assertNotIn("Traceback", err.getvalue())
                self.assertEqual(list(workspace.iterdir()), [])

    def test_only_known_modes_and_bounded_integer_counts_are_retained(self):
        for count, mode in ((True, "explicit_files"), (6, "explicit_files"),
                            (git_compare.MAX_LIST_BYTES + 1, "changed_web_files"),
                            (CANARY, "explicit_files"), (7, CANARY), (7, [])):
            with self.subTest(kind=type(count).__name__):
                with self.assertRaises(git_compare.GitCompareError) as caught:
                    git_compare.GitSelectionLimitError(count, mode)
                self.assertNotIn(CANARY, str(caught.exception))

    def test_recovery_projection_does_not_trust_mutated_metadata_or_other_errors(self):
        generic = str(git_compare.GitCompareError())
        error = git_compare.GitSelectionLimitError(7, "explicit_files")
        for key, value in (("count", CANARY), ("count", True), ("mode", CANARY), ("mode", [])):
            with self.subTest(field=key):
                original = getattr(error, key)
                setattr(error, key, value)
                self.assertEqual(git_compare.selection_limit_message(error), generic)
                setattr(error, key, original)
        self.assertEqual(git_compare.selection_limit_message(ValueError(CANARY)), generic)
        self.assertEqual(git_compare.selection_limit_message(error, action=CANARY), generic)


if __name__ == "__main__":
    unittest.main()
