import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from surface_map import ci, git_compare


CANARY = "PRIVATE_GIT_COMPARISON_CANARY_84921"


class GitComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repository"
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Synthetic Tester")
        self.git("config", "user.email", "tester@example.invalid")
        self.git("config", "core.autocrlf", "false")

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args, data=None):
        env = dict(os.environ)
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_SYSTEM": os.devnull})
        result = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false",
                                 "-c", "core.hooksPath=" + os.devnull, "-C", str(self.repo), *args],
                                input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, shell=False, check=True, timeout=15)
        return result.stdout.decode("utf-8").strip()

    def write(self, name, contents):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, bytes):
            target.write_bytes(contents)
        else:
            target.write_text(contents, encoding="utf-8")

    def commit(self, values=None):
        for name, value in (values or {}).items():
            if value is None:
                (self.repo / name).unlink()
            else:
                self.write(name, value)
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "-m", "synthetic change")
        return self.git("rev-parse", "HEAD")

    def compare(self, base, head, **options):
        return git_compare.compare_git(self.repo, base, head, javascript_engine="basic", **options)

    def rejected(self, base, head, **options):
        with self.assertRaises(git_compare.GitCompareError) as caught:
            self.compare(base, head, **options)
        self.assertNotIn(CANARY, str(caught.exception))
        self.assertNotIn(str(self.repo), str(caught.exception))

    def test_automatically_collects_form_method_hidden_field_and_link_changes(self):
        base = self.commit({"pages/client.html": '<a href="/dashboard">Home</a><form method="get" action="/api/orders"><input name="order_id"></form>'})
        head = self.commit({"pages/client.html": '<a href="/reports">Reports</a><form method="post" action="/api/orders"><input name="order_id"><input type="hidden" name="quantity" value="' + CANARY + '"></form>'})
        result = self.compare(base, head)
        self.assertTrue(result["analysis_performed"])
        delta = result["evidence_comparison"]["comparison"]
        self.assertIn(("POST", "/api/orders"), {(x["method"], x["path"]) for x in delta["added"]})
        self.assertIn(("GET", "/api/orders"), {(x["method"], x["path"]) for x in delta["removed"]})
        self.assertIn("/reports", {x["path"] for x in delta["added"]})
        self.assertIn("/dashboard", {x["path"] for x in delta["removed"]})
        output = ci.json_output(result) + git_compare.markdown(result)
        for private in (CANARY, "pages/client.html", str(self.repo)):
            self.assertNotIn(private, output)
        self.assertEqual(result["policy"]["status"], "advisory")

    def test_form_field_change_is_reported(self):
        base = self.commit({"form.html": '<form method="post" action="/api/orders"><input name="order_id"></form>'})
        head = self.commit({"form.html": '<form method="post" action="/api/orders"><input name="order_id"><input name="quantity" type="hidden"></form>'})
        result = self.compare(base, head, fail_on="changed")
        self.assertEqual(result["policy"]["matched"], ["changed"])
        self.assertEqual(result["evidence_comparison"]["comparison"]["changed"][0]["path"], "/api/orders")

    def test_explicit_unchanged_control_has_no_evidence_changes(self):
        base = self.commit({"page.html": '<a href="/dashboard">Home</a>'})
        head = self.commit({"notes.md": "documentation only"})
        result = self.compare(base, head, files=["page.html"])
        self.assertEqual(result["selection"]["mode"], "explicit_files")
        self.assertEqual(result["selection"]["selected_files"], 1)
        self.assertTrue(result["analysis_performed"])
        for key in ("added", "removed", "changed"):
            self.assertEqual(result["evidence_comparison"]["comparison"][key], [])

    def test_explicit_scope_reports_supported_changes_not_selected(self):
        base = self.commit({"notes.md": "before"})
        values = {str(index) + ".html": '<a href="/dashboard">Home</a>' for index in range(20)}
        values["notes.md"] = "after"
        head = self.commit(values)
        result = self.compare(base, head, files=["0.html"])
        self.assertEqual(result["selection"]["supported_changed_files_not_selected"], 19)
        self.assertEqual(result["selection"]["unsupported_changed_files"], 1)
        self.assertIn("outside this selection: 19", git_compare.markdown(result))

    def test_unsupported_weird_git_paths_are_counted_without_decoding(self):
        base = self.commit({"page.html": '<a href="/dashboard">Home</a>'})
        self.write("page.html", '<a href="/reports">Reports</a>')
        self.git("add", "--", "page.html")
        oid = self.git("hash-object", "-w", "--stdin", data=b"unsupported contents")
        names = [b"colon:name.txt", b"line\nbreak.txt", b"nonutf8-\xff.txt", b".js", b"js"]
        info = b"".join(b"100644 " + oid.encode("ascii") + b"\t" + name + b"\x00" for name in names)
        self.git("-c", "core.protectNTFS=false", "update-index", "-z", "--index-info", data=info)
        self.git("commit", "--quiet", "-m", "unsupported Git paths")
        result = self.compare(base, self.git("rev-parse", "HEAD"))
        self.assertEqual(result["selection"]["unsupported_changed_files"], 5)
        self.assertEqual(result["selection"]["selected_files"], 1)
        self.assertEqual(result["evidence_comparison"]["comparison"]["added"][0]["path"], "/reports")

    def test_same_commit_automatic_mode_is_explicitly_not_applicable(self):
        base = self.commit({"page.html": '<a href="/dashboard">Home</a>'})
        result = self.compare(base, base)
        self.assertFalse(result["analysis_performed"])
        self.assertIsNone(result["policy"]["passed"])
        self.assertEqual(result["policy"]["status"], "not_applicable")
        self.assertIn("not an all-clear", git_compare.markdown(result))

    def test_docs_only_changes_have_no_fake_capture(self):
        base = self.commit({"notes.md": "before"})
        head = self.commit({"notes.md": "after"})
        result = self.compare(base, head, fail_on="warnings")
        self.assertEqual(result["selection"]["unsupported_changed_files"], 1)
        self.assertIsNone(result["evidence_comparison"])
        self.assertEqual(result["selection"]["selected_files"], 0)

    def test_added_and_deleted_files_keep_corresponding_generic_slots(self):
        base = self.commit({"old.html": '<a href="/dashboard">Home</a>'})
        head = self.commit({"old.html": None, "new.html": '<a href="/reports">Reports</a>'})
        result = self.compare(base, head)
        self.assertEqual(result["selection"]["selected_files"], 2)
        self.assertEqual(result["selection"]["missing_base_files"], 1)
        self.assertEqual(result["selection"]["missing_head_files"], 1)
        delta = result["evidence_comparison"]["comparison"]
        self.assertEqual(delta["added"][0]["evidence"][0]["source"], "source_1")
        self.assertEqual(delta["removed"][0]["evidence"][0]["source"], "source_2")

    def test_javascript_added_side_empty_artifact_is_comparable(self):
        base = self.commit({"notes.md": "before"})
        head = self.commit({"client.js": "fetch('/api/orders');"})
        result = self.compare(base, head)
        self.assertEqual(result["evidence_comparison"]["comparison"]["added"][0]["path"], "/api/orders")

    def test_divergent_base_uses_merge_base_not_latest_base_tip(self):
        common = self.commit({"page.html": '<a href="/dashboard">Home</a>'})
        self.git("branch", "proposed")
        requested_base = self.commit({"page.html": '<a href="/account">Account</a>'})
        self.git("checkout", "--quiet", "proposed")
        head = self.commit({"page.html": '<a href="/reports">Reports</a>'})
        result = self.compare(requested_base, head)
        self.assertEqual(result["requested_base_commit"], requested_base)
        self.assertEqual(result["base_commit"], common)
        absent = result["evidence_comparison"]["comparison"]["removed"]
        self.assertEqual(absent[0]["path"], "/dashboard")

    def test_multiple_merge_bases_are_rejected_as_ambiguous(self):
        common = self.commit({"page.html": '<a href="/dashboard">Home</a>'})
        left = self.commit({"page.html": '<a href="/reports">Reports</a>'})
        left_tree = self.git("rev-parse", left + "^{tree}")
        self.git("checkout", "--quiet", common)
        right = self.commit({"page.html": '<a href="/account">Account</a>'})
        right_tree = self.git("rev-parse", right + "^{tree}")
        left_merge = self.git("commit-tree", left_tree, "-p", left, "-p", right, "-m", "left merge")
        right_merge = self.git("commit-tree", right_tree, "-p", right, "-p", left, "-m", "right merge")
        self.assertEqual(len(self.git("merge-base", "--all", left_merge, right_merge).splitlines()), 2)
        self.rejected(left_merge, right_merge)

    def test_bare_repository_objects_can_be_compared_without_checkout(self):
        base = self.commit({"page.html": '<a href="/dashboard">Home</a>'})
        head = self.commit({"page.html": '<a href="/reports">Reports</a>'})
        bare = self.root / "objects-only.git"
        self.git("clone", "--bare", "--quiet", "--no-hardlinks", str(self.repo), str(bare))
        result = git_compare.compare_git(bare, base, head, javascript_engine="basic")
        self.assertTrue(result["analysis_performed"])
        self.assertEqual(result["evidence_comparison"]["comparison"]["added"][0]["path"], "/reports")

    def test_git_case_distinct_paths_are_read_from_tree_not_filesystem(self):
        base = self.commit({"A.html": '<a href="/dashboard">Home</a>'})
        oid = self.git("hash-object", "-w", "--stdin", data=b'<a href="/reports">Reports</a>')
        self.git("-c", "core.ignorecase=false", "update-index", "--add", "--cacheinfo", "100644," + oid + ",a.html")
        self.git("commit", "--quiet", "-m", "case-distinct entry")
        head = self.git("rev-parse", "HEAD")
        result = self.compare(base, head, files=["a.html", "A.html"])
        self.assertEqual(result["selection"]["selected_files"], 2)
        self.assertEqual(result["selection"]["missing_base_files"], 1)
        self.assertEqual(result["evidence_comparison"]["comparison"]["added"][0]["evidence"][0]["source"], "source_2")

    def test_invalid_or_noncommit_object_ids_are_rejected(self):
        base = self.commit({"page.html": "before"})
        blob = self.git("rev-parse", "HEAD:page.html")
        tree = self.git("rev-parse", "HEAD^{tree}")
        self.git("tag", "-a", "synthetic-tag", "-m", "tag")
        tag = self.git("rev-parse", "synthetic-tag")
        for value in ("HEAD", "--help", "-c", base[:12], base + ":page.html", "a" * 64, blob, tree, tag):
            with self.subTest(value_length=len(value)):
                self.rejected(value, base)

    def test_missing_or_invalid_explicit_paths_never_become_globs(self):
        base = self.commit({"page.html": "before"})
        for name in ("../page.html", "/page.html", "C:/page.html", "dir\\page.html", ":(glob)*.html",
                     "page.html\n" + CANARY, "./page.html", ".git/config.html", "missing.html", "*.html"):
            with self.subTest(path_length=len(name)):
                self.rejected(base, base, files=[name])
        for paths in ([], ["page.html", "page.html"], ["notes.md"]):
            self.rejected(base, base, files=paths)

    def test_more_than_six_changed_files_fail_closed(self):
        base = self.commit({"notes.md": "before"})
        head = self.commit({str(i) + ".html": "<p>content</p>" for i in range(7)})
        self.rejected(base, head)

    def test_blob_byte_limit_and_total_limit_fail_closed(self):
        base = self.commit({"page.html": "before"})
        head = self.commit({"page.html": "x" * 64})
        with patch.object(git_compare, "MAX_HTML_BYTES", 32):
            self.rejected(base, head)
        with patch.object(git_compare, "MAX_TOTAL_BYTES", 32):
            self.rejected(base, head)

    def test_invalid_utf8_and_lfs_pointer_fail_closed(self):
        base = self.commit({"page.html": "before"})
        invalid = self.commit({"page.html": b"\xff\xfe"})
        self.rejected(base, invalid)
        pointer = self.commit({"page.html": "version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 100\n"})
        self.rejected(base, pointer)

    def test_nul_blob_is_rejected_and_utf8_bom_is_supported(self):
        base = self.commit({"page.html": '<a href="/dashboard">Home</a>'})
        binary = self.commit({"page.html": b'<a href="/reports">Reports</a>\x00'})
        self.rejected(base, binary)
        bom = self.commit({"page.html": b'\xef\xbb\xbf<a href="/reports">Reports</a>'})
        result = self.compare(base, bom)
        self.assertEqual(result["evidence_comparison"]["comparison"]["added"][0]["path"], "/reports")

    def test_symlink_blob_and_gitlink_type_changes_are_rejected(self):
        base = self.commit({"page.html": "<p>before</p>"})
        blob = self.git("hash-object", "-w", "--stdin", data=b"private/target")
        self.git("update-index", "--cacheinfo", "120000," + blob + ",page.html")
        self.git("commit", "--quiet", "-m", "synthetic symlink")
        symlink = self.git("rev-parse", "HEAD")
        self.rejected(base, symlink)
        self.git("update-index", "--cacheinfo", "160000," + base + ",page.html")
        self.git("commit", "--quiet", "-m", "synthetic gitlink")
        self.rejected(base, self.git("rev-parse", "HEAD"))

    def test_unrelated_repositories_and_nested_nonrepo_are_rejected(self):
        base = self.commit({"page.html": "before"})
        child = self.repo / "nested"
        child.mkdir()
        with self.assertRaises(git_compare.GitCompareError):
            git_compare.compare_git(child, base, base, javascript_engine="basic")

    def test_worktree_gitfile_redirect_is_rejected(self):
        base = self.commit({"page.html": "before"})
        linked = self.root / "linked"
        linked.mkdir()
        (linked / ".git").write_text("gitdir: " + str(self.repo / ".git"), encoding="utf-8")
        with self.assertRaises(git_compare.GitCompareError):
            git_compare.compare_git(linked, base, base, javascript_engine="basic")

    def test_checkout_sparse_cleanup_inactive_worktree_config_is_ignored(self):
        base = self.commit({"page.html": '<a href="/dashboard">Home</a>'})
        head = self.commit({"page.html": '<a href="/reports">Reports</a>'})
        self.git("sparse-checkout", "init", "--cone")
        self.git("sparse-checkout", "disable")
        self.git("config", "--local", "--unset-all", "extensions.worktreeConfig")
        inactive = self.repo / ".git/config.worktree"
        self.assertTrue(inactive.is_file())
        # This deliberately invalid inactive configuration would make Git fail
        # if it were parsed. Its contents must stay unread and unexported.
        inactive.write_text(CANARY + " is not valid Git configuration\n", encoding="utf-8")
        result = self.compare(base, head)
        self.assertTrue(result["analysis_performed"])
        self.assertEqual(result["evidence_comparison"]["comparison"]["added"][0]["path"], "/reports")
        self.assertNotIn(CANARY, ci.json_output(result) + git_compare.markdown(result))
        # Any primary declaration of the extension remains unsupported, even
        # false, before the untrusted Git directory is selected.
        config = self.repo / ".git/config"
        ordinary = config.read_text(encoding="utf-8")
        for value in ("true", "false"):
            config.write_text(ordinary + "\n[extensions] worktreeConfig = " + value + "\n", encoding="utf-8")
            self.rejected(base, head)
        config.write_text(ordinary, encoding="utf-8")

    def test_alternates_and_include_config_are_rejected_without_following(self):
        base = self.commit({"page.html": "before"})
        alternates = self.repo / ".git/objects/info/alternates"
        alternates.write_text(str(self.root / CANARY), encoding="utf-8")
        self.rejected(base, base)
        alternates.unlink()
        with (self.repo / ".git/config").open("a", encoding="utf-8") as handle:
            handle.write('\n[include]\n path = "' + CANARY + '"\n')
        self.rejected(base, base)

    def test_promisor_repo_is_rejected_before_any_git_process(self):
        base = self.commit({"page.html": "before"})
        self.git("config", "remote.origin.promisor", "true")
        with patch.object(git_compare.subprocess, "Popen", side_effect=AssertionError("must not start")):
            self.rejected(base, base)

    def test_same_line_git_config_keys_are_parsed_and_rejected(self):
        base = self.commit({"page.html": "before"})
        config = self.repo / ".git/config"
        original = config.read_text(encoding="utf-8")
        for setting in ('[extensions] worktreeConfig = true\n',
                        '[extensions] partialClone = origin\n',
                        '[remote "origin"] promisor = true\n',
                        '[core] worktree = ' + CANARY + '\n'):
            with self.subTest(setting_key=setting.split("=")[0]):
                config.write_text(original + "\n" + setting, encoding="utf-8")
                self.rejected(base, base)
        config.write_text(original, encoding="utf-8")

    def test_grafts_and_shallow_metadata_are_rejected_before_git(self):
        base = self.commit({"page.html": "before"})
        for name in ("info/grafts", "shallow"):
            target = self.repo / ".git" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(base + "\n", encoding="utf-8")
            with patch.object(git_compare.subprocess, "Popen", side_effect=AssertionError("must not start")):
                self.rejected(base, base)
            target.unlink()

    def test_git_binary_lookup_never_prefers_current_directory(self):
        base = self.commit({"page.html": "before"})
        planted = self.root / ("git.exe" if os.name == "nt" else "git")
        planted.write_bytes(b"this is not the installed Git executable")
        planted.chmod(0o700)
        original = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, {"PATH": os.pathsep.join((".", str(self.root), os.environ["PATH"]))}):
                binary, search = git_compare._git_binary(self.repo)
                self.assertNotEqual(Path(binary), planted)
                self.assertNotIn(str(self.root), search.split(os.pathsep))
                self.assertEqual(self.compare(base, base)["policy"]["status"], "not_applicable")
        finally:
            os.chdir(original)

    def test_parent_environment_injection_and_secrets_are_not_forwarded(self):
        base = self.commit({"page.html": '<a href="/dashboard">Home</a>'})
        head = self.commit({"page.html": '<a href="/reports">Reports</a>'})
        real = subprocess.Popen
        seen = []

        def checked(*args, **kwargs):
            env = kwargs["env"]
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("GIT_TRACE", env)
            self.assertNotIn("GIT_CONFIG_COUNT", env)
            self.assertNotIn("GIT_EXEC_PATH", env)
            self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")
            self.assertEqual(env["GIT_NO_REPLACE_OBJECTS"], "1")
            self.assertNotEqual(Path(kwargs["cwd"]), self.repo)
            self.assertTrue(Path(args[0][0]).is_absolute())
            seen.append(True)
            return real(*args, **kwargs)

        with patch.dict(os.environ, {"OPENAI_API_KEY": CANARY, "GIT_TRACE": str(self.root / "trace"),
                                     "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
                                     "GIT_CONFIG_VALUE_0": CANARY, "GIT_EXEC_PATH": str(self.repo)}), \
                patch.object(git_compare.subprocess, "Popen", side_effect=checked):
            result = self.compare(base, head)
        self.assertTrue(seen)
        self.assertFalse((self.root / "trace").exists())
        self.assertNotIn(CANARY, ci.json_output(result))

    def test_git_replace_objects_do_not_change_analyzed_commits(self):
        base = self.commit({"page.html": '<a href="/dashboard">Home</a>'})
        head = self.commit({"page.html": '<a href="/reports">Reports</a>'})
        other = self.commit({"page.html": '<a href="/account">Account</a>'})
        self.git("replace", head, other)
        result = self.compare(base, head)
        paths = {x["path"] for x in result["evidence_comparison"]["comparison"]["added"]}
        self.assertEqual(paths, {"/reports"})

    def test_fsmonitor_hook_and_external_diff_are_never_executed(self):
        base = self.commit({"page.html": '<a href="/dashboard">Home</a>'})
        head = self.commit({"page.html": '<a href="/reports">Reports</a>'})
        marker = self.root / "executed"
        hook = self.root / "hook.sh"
        hook.write_text('#!/bin/sh\necho BAD > "' + str(marker).replace("\\", "/") + '"\n', encoding="utf-8")
        hook.chmod(0o700)
        self.git("config", "core.fsmonitor", str(hook))
        self.git("config", "diff.external", str(hook))
        self.git("config", "core.hooksPath", str(self.root))
        self.assertTrue(self.compare(base, head)["analysis_performed"])
        self.assertFalse(marker.exists())

    def test_subprocess_output_limit_and_deadline_errors_are_fixed(self):
        base = self.commit({"page.html": "before"})
        head = self.commit({"page.html": "after"})
        oid = self.git("rev-parse", "HEAD:page.html")
        with self.assertRaises(git_compare.GitCompareError):
            git_compare._Git(self.repo).run("cat-file", "blob", oid, limit=1)
        with patch.object(git_compare, "GIT_SECONDS", 0):
            self.rejected(base, head)

    def test_markdown_rejects_injected_commit_metadata(self):
        base = self.commit({"page.html": "before"})
        result = self.compare(base, base)
        result["head_commit"] = "\n" + CANARY
        with self.assertRaises(git_compare.GitCompareError):
            git_compare.markdown(result)


if __name__ == "__main__":
    unittest.main()
