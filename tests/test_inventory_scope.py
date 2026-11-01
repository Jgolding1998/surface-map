"""Inventory success must not be presented as full behavior/security coverage."""
import json
import unittest

from surface_map.source import source_inventory
from surface_map.project import inspect_project


class InventoryScopeTests(unittest.TestCase):
    def test_public_parser_mcp_browser_and_git_libraries_are_inventory_hints_only(self):
        packages = {"@babel/parser": "babel-parser", "@babel/types": "babel-types",
                    "@modelcontextprotocol/client": "modelcontextprotocol-client",
                    "@modelcontextprotocol/server": "modelcontextprotocol-server",
                    "@modelcontextprotocol/sdk": "modelcontextprotocol-sdk",
                    "playwright-core": "playwright", "isomorphic-git": "isomorphic-git"}
        for kind, content, confidence in (
            ("package", json.dumps({"dependencies": {name: "PRIVATE_VERSION_CANARY" for name in packages}}), "declared"),
            ("typescript", "\n".join(f'import "{name}";' for name in packages), "inferred"),
        ):
            report = source_inventory([{"kind": kind, "content": content}], javascript_engine="basic")
            self.assertEqual({row["name"] for row in report["dependencies"]}, set(packages.values()))
            self.assertEqual({row["confidence"] for row in report["dependencies"]}, {confidence})
            self.assertIn("version-control-library", {row["name"] for row in report["capabilities"]})
            self.assertFalse(report["analysis_scope"]["security_audited"])
            self.assertNotIn("CANARY", json.dumps(report))

    def test_successful_python_inventory_does_not_claim_git_or_security_analysis(self):
        content = 'import subprocess\nimport private_CANARY_module\nsubprocess.run(["git", "reset", "--hard"])\n'
        report = source_inventory([{"kind": "python", "content": content}], javascript_engine="basic")
        self.assertEqual(report["sources"][0]["status"], "parsed")
        self.assertEqual(report["sources"][0]["unknown_dependencies"], 0)
        scope = report["analysis_scope"]
        for key in ("whole_repository", "runtime_verified", "control_flow_analyzed", "security_audited", "version_control_semantics_analyzed"):
            self.assertIs(scope[key], False)
        self.assertIn("parser defect", scope["parsed_status"])
        self.assertIn("manifest declarations", scope["unknown_dependencies"])
        self.assertNotIn("CANARY", json.dumps(report))
        self.assertNotIn("reset", json.dumps(report))

    def test_complete_selected_graph_still_reports_semantic_limits(self):
        report = inspect_project([
            {"kind": "typescript", "logical_path": "main.ts", "content": 'import "./worker.ts";'},
            {"kind": "typescript", "logical_path": "worker.ts", "content": 'export const ready = true;'},
        ], javascript_engine="basic")
        self.assertFalse(report["coverage"]["partial"])
        self.assertEqual(len(report["graph"]["edges"]), 1)
        self.assertFalse(report["analysis_scope"]["runtime_verified"])
        self.assertIn("false does not mean", report["analysis_scope"]["partial_status"])

    def test_unknown_manifest_declarations_are_distinct_from_omitted_imports(self):
        report = source_inventory([
            {"kind": "package", "content": '{"dependencies":{"private_CANARY_package":"1"}}'},
            {"kind": "javascript", "content": 'import "private_CANARY_package";'},
        ], javascript_engine="basic")
        self.assertEqual([row["unknown_dependencies"] for row in report["sources"]], [1, 0])
        self.assertEqual(report["dependencies"], [])
        self.assertNotIn("CANARY", json.dumps(report))

    def test_gitpython_and_simple_git_are_hints_without_a_safety_verdict(self):
        for kind, content, label in (
            ("python", "import git\n", "gitpython"),
            ("requirements", "GitPython==3.1.46\n", "gitpython"),
            ("javascript", 'import git from "simple-git";', "simple-git"),
        ):
            report = source_inventory([{"kind": kind, "content": content}], javascript_engine="basic")
            self.assertEqual([row["name"] for row in report["dependencies"]], [label])
            self.assertFalse(report["analysis_scope"]["version_control_semantics_analyzed"])

    def test_long_omitted_import_does_not_become_an_unknown_manifest_count(self):
        report = source_inventory([{"kind": "javascript", "content": 'import "' + "x" * 215 + '";'}], javascript_engine="basic")
        self.assertEqual(report["sources"][0]["unknown_dependencies"], 0)

