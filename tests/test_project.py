import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from surface_map.analyze import ArtifactError
from surface_map.project import (MAX_EDGES, MAX_FILE_BYTES, MAX_TOTAL_BYTES,
                                 inspect_project, selected_project)
from surface_map.source import source_inventory

CANARY = "PRIVATE_PROJECT_CANARY_7731"


def source(path="main.py", content="import requests\n", kind="python"):
    return {"kind": kind, "content": content, "logical_path": path}


class ProjectTests(unittest.TestCase):
    def test_grouping_preserves_global_ids_and_caps_merged_evidence(self):
        report = inspect_project([source(f"module_{number}.py") for number in range(8)], javascript_engine="basic")
        self.assertEqual([row["id"] for row in report["sources"]], [f"source_{n}" for n in range(1, 9)])
        self.assertEqual(report["coverage"]["groups"], 2)
        self.assertFalse(report["coverage"]["partial"])
        dep = next(row for row in report["dependencies"] if row["name"] == "requests")
        self.assertEqual(len(dep["evidence"]), 5)
        self.assertGreater(report["omissions"]["inventory_evidence_limit"], 0)

    def test_later_manifest_keeps_stronger_evidence_with_global_source(self):
        files = [source(f"module_{n}.py") for n in range(6)]
        files.append(source("requirements.txt", "requests>=2\n", "requirements"))
        report = inspect_project(files, javascript_engine="basic")
        dep = next(row for row in report["dependencies"] if row["name"] == "requests")
        self.assertEqual(dep["confidence"], "declared")
        self.assertEqual(dep["evidence"], [{"source": "source_7", "location": "line 1"}])
        self.assertGreater(report["omissions"]["superseded_evidence"], 0)

    def test_rejections_and_warnings_use_global_ids_without_source_text(self):
        files = [source(f"module_{n}.py") for n in range(6)]
        files.append(source(CANARY + ".py", "def " + CANARY + "(:"))
        report = inspect_project(files, javascript_engine="basic")
        self.assertEqual(report["sources"][-1]["status"], "rejected")
        self.assertTrue(any("source_7:" in warning for warning in report["warnings"]))
        self.assertNotIn(CANARY, json.dumps(report))
        self.assertEqual(report["coverage"]["rejected_files"], 1)

    def test_path_content_and_unknown_modules_never_escape_output(self):
        files = [source(CANARY + ".js", 'import "./other.js"; import "' + CANARY + '";\n'
                        'const note="' + CANARY + '";', "javascript"),
                 source("other.js", "export const value=1;", "javascript")]
        report = inspect_project(files, javascript_engine="basic")
        rendered = json.dumps(report)
        for value in (CANARY, "other.js", "./other.js"):
            self.assertNotIn(value, rendered)
        self.assertEqual(report["graph"]["edges"][0]["target"], "source_2")

    def test_paths_must_be_normalized_and_portable(self):
        for path in ("../main.py", "src/../main.py", "/main.py", "C:/main.py", "src\\main.py",
                     "./main.py", "src//main.py", "main.py:stream", "main.py?x=1", "main%2epy",
                     "main.py\x00", "CON.py", "src./main.py", "~/main.py", "main.py/"):
            with self.subTest(path=path), self.assertRaises(ArtifactError):
                inspect_project([source(path)], javascript_engine="basic")

    def test_private_and_generated_paths_are_rejected_before_inspection(self):
        for path in (".env.py", ".aws/main.py", ".ssh/main.py", ".config/main.py", ".git/main.py",
                     "credentials.py", "secret.py", "api_keys.py", "service-account.py", "tokens.py",
                     "keys/main.py", "private/main.py", "node_modules/main.py"):
            with self.subTest(path=path), patch("surface_map.project.source_inventory") as inventory:
                with self.assertRaises(ArtifactError):
                    inspect_project([source(path)], javascript_engine="basic")
                inventory.assert_not_called()

    def test_duplicate_case_aliases_and_kind_mismatches_are_rejected(self):
        for files in ([source(), source()], [source("Main.py"), source("main.py")],
                      [source("main.js")], [{**source(), "extra": CANARY}]):
            with self.subTest(files=files), self.assertRaises(ArtifactError):
                inspect_project(files, javascript_engine="basic")

    def test_invalid_types_counts_and_unicode_are_rejected(self):
        for files in ([], {}, [source()] * 25, [None], [source(content="\ud800")],
                      [source(content=42)], [source(kind="binary")]):
            with self.subTest(files=files), self.assertRaises(ArtifactError):
                inspect_project(files, javascript_engine="basic")
        for engine in ([], None, "wrong"):
            with self.assertRaises(ArtifactError):
                inspect_project([source()], javascript_engine=engine)

    def test_byte_limits_are_utf8_and_apply_before_any_parser(self):
        cases = [[source(content="x" * (MAX_FILE_BYTES + 1))],
                 [source(content="é" * (MAX_FILE_BYTES // 2 + 1))],
                 [source(f"module_{n}.py", "x" * MAX_FILE_BYTES) for n in range(MAX_TOTAL_BYTES // MAX_FILE_BYTES + 1)]]
        for files in cases:
            with patch("surface_map.project.source_inventory") as inventory, self.assertRaises(ArtifactError):
                inspect_project(files, javascript_engine="basic")
            inventory.assert_not_called()

    def test_group_byte_limit_remains_one_mebibyte(self):
        files = [source(f"module_{n}.py", "#" + "x" * (MAX_FILE_BYTES - 1)) for n in range(3)]
        with patch("surface_map.project.source_inventory", wraps=source_inventory) as inventory:
            report = inspect_project(files, javascript_engine="basic")
        self.assertEqual(report["coverage"]["groups"], 2)
        self.assertEqual([len(call.args[0]) for call in inventory.call_args_list], [2, 1])

    def test_graph_supports_exact_literal_import_reexport_and_dynamic_import(self):
        files = [source("src/main.js", 'import "../shared.js";\nexport {x} from "./part.ts";\n'
                        'import("./part.ts");', "javascript"),
                 source("shared.js", "export const x=1;", "javascript"),
                 source("src/part.ts", "export const x=1;", "typescript")]
        report = inspect_project(files, javascript_engine="basic")
        edges = report["graph"]["edges"]
        self.assertEqual([(e["source"], e["target"], e["line"], e["relation"]) for e in edges],
                         [("source_1", "source_2", 1, "static_import"),
                          ("source_1", "source_3", 2, "static_reexport"),
                          ("source_1", "source_3", 3, "dynamic_import_literal")])

    def test_graph_does_not_guess_extensions_follow_external_or_escape_root(self):
        code = 'import "./other"; import "../../outside.js"; import "react"; import "https://example.invalid/x.js";'
        code += 'import "./other.js?private=value"; import "./server.py";'
        files = [source("main.js", code, "javascript"), source("other.js", "", "javascript"), source("server.py")]
        graph = inspect_project(files, javascript_engine="basic")["graph"]
        self.assertEqual(graph["edges"], [])
        self.assertEqual(graph["omissions"]["outside_root"], 1)
        self.assertEqual(graph["omissions"]["missing_selected_target"], 1)
        self.assertEqual(graph["omissions"]["external_or_package"], 2)
        self.assertEqual(graph["omissions"]["unsupported_reference"], 2)

    def test_graph_ignores_comments_decoys_require_and_computed_imports(self):
        code = '// import "./other.js";\nconst note=\'import "./other.js"\';\n'
        code += 'require("./other.js"); import(prefix + "/other.js"); import(`./${name}.js`);'
        files = [source("main.js", code, "javascript"), source("other.js", "", "javascript")]
        self.assertEqual(inspect_project(files, javascript_engine="basic")["graph"]["edges"], [])

    def test_cycles_are_finite_and_edge_overflow_is_counted(self):
        files = [source("one.js", 'import "./two.js";\n' * (MAX_EDGES + 5), "javascript"),
                 source("two.js", 'import "./one.js";', "javascript")]
        graph = inspect_project(files, javascript_engine="basic")["graph"]
        self.assertEqual(len(graph["edges"]), MAX_EDGES)
        self.assertEqual(graph["omissions"]["edge_limit"], 6)
        self.assertTrue(graph["truncated"])

    def test_interior_spaces_in_ordinary_paths_are_supported(self):
        files = [source("main.js", 'import "./other module.js";', "javascript"),
                 source("other module.js", "", "javascript")]
        graph = inspect_project(files, javascript_engine="basic")["graph"]
        self.assertEqual(graph["edges"][0]["target"], "source_2")

    def test_graph_parser_failure_is_explicitly_partial_without_error_text(self):
        with patch("surface_map.references._iter_module_specifiers", side_effect=ArtifactError(CANARY)):
            report = inspect_project([source("main.js", "", "javascript")], javascript_engine="basic")
        self.assertTrue(report["coverage"]["partial"])
        self.assertEqual(report["graph"]["omissions"]["unparsed_sources"], 1)
        self.assertNotIn(CANARY, json.dumps(report))

    def test_all_twenty_four_files_are_supported_in_bounded_groups(self):
        report = inspect_project([source(f"module_{n}.py", "") for n in range(24)], javascript_engine="basic")
        self.assertEqual(report["coverage"]["inspected_files"], 24)
        self.assertEqual(report["coverage"]["groups"], 4)

    def test_existing_expired_or_invalid_deadlines_reject_without_parsing(self):
        for deadline in (True, float("nan"), float("inf"), "soon", time.monotonic() - 1):
            with self.subTest(deadline=deadline), self.assertRaises(ArtifactError):
                inspect_project([source()], deadline=deadline)

    def test_shared_deadline_stops_later_groups_and_marks_omissions(self):
        clock = [0.0]
        def slow_group(*args, **kwargs):
            result = source_inventory(*args, **kwargs)
            clock[0] = 11.0
            return result
        with patch("surface_map.project.time.monotonic", side_effect=lambda: clock[0]), \
                patch("surface_map.project.source_inventory", side_effect=slow_group):
            report = inspect_project([source(f"module_{n}.py") for n in range(8)], javascript_engine="basic")
        self.assertEqual(report["coverage"]["inspected_files"], 6)
        self.assertEqual(report["coverage"]["omitted_files"], 2)
        self.assertTrue(report["coverage"]["partial"])

    def test_source_is_not_executed_and_no_network_or_process_is_started(self):
        code = 'import os\nos.system("' + CANARY + '")\nraise RuntimeError("' + CANARY + '")\n'
        with patch.object(socket.socket, "connect", side_effect=AssertionError("No network")), \
                patch.object(subprocess, "Popen", side_effect=AssertionError("No process")):
            report = inspect_project([source(content=code)], javascript_engine="basic")
        self.assertEqual(report["sources"][0]["status"], "parsed")
        self.assertNotIn(CANARY, json.dumps(report))

    def test_selected_files_require_explicit_root_and_never_walk(self):
        with self.assertRaises(ArtifactError):
            selected_project(["main.py"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_bytes(b"import requests\n")
            with patch.object(Path, "rglob", side_effect=AssertionError("No walk")), \
                    patch.object(Path, "glob", side_effect=AssertionError("No walk")):
                selected = selected_project(["src/main.py"], root=root)
            self.assertEqual(selected, [source("src/main.py")])

    def test_outside_and_private_selections_are_rejected_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for value in ("../outside.py", root.parent / "outside.py", ".env.py", "credentials.py"):
                with self.subTest(value=value), patch("surface_map.project.read_ordinary") as reader:
                    with self.assertRaises(ArtifactError):
                        selected_project([value], root=root)
                    reader.assert_not_called()

    def test_ordinary_file_guard_errors_do_not_echo_paths(self):
        with patch("surface_map.project.read_ordinary", side_effect=ArtifactError("ordinary guard")):
            with self.assertRaises(ArtifactError) as caught:
                selected_project([CANARY + ".py"], root=Path.cwd())
        self.assertNotIn(CANARY, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
