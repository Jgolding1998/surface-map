import ast
import contextlib
import io
import json
import sys
import threading
import time
import unittest
from unittest import mock

from surface_map import source as source_module
from surface_map.analyze import ArtifactError
from surface_map.source import FORMAT, MAX_FILE_BYTES, SOURCE_KINDS, source_inventory


CANARY = "PRIVATE" + "-CANARY-51730284"
EMAIL = "maintainer" + chr(64) + "example.invalid"


def one(kind, content):
    return source_inventory([{"kind": kind, "content": content}])


def names(report, field):
    return {item["name"] for item in report[field]}


def dependency(report, name):
    return next(item for item in report["dependencies"] if item["name"] == name)


class SourceInventoryTests(unittest.TestCase):
    def test_nested_require_binding_analysis_is_bounded(self):
        text = "const " + "[" * 6000 + "require," * 2000 + "x" + "]" * 6000 + "=xs;"
        report = one("javascript", text)
        self.assertEqual(report["sources"][0]["status"], "rejected")
        self.assertEqual(report["dependencies"], [])
        self.assertIn("binding analysis exceeded its work limit", " ".join(report["warnings"]))

    def test_mixed_sources_report_fixed_labels_and_confidence(self):
        report = source_inventory([
            {"kind": "requirements", "content": "numpy>=1.24\nollama\nsounddevice==0.4.6 ; sys_platform != 'win32'\n"},
            {"kind": "python", "content": "import os\nimport vosk\nfrom cv2 import imread\ndef run():\n    import pyautogui\n"},
            {"kind": "package", "content": json.dumps({"dependencies": {"react": "^18.2.0", "zustand": "^4"}, "devDependencies": {"typescript": "~5.4", "vite": "5"}})},
            {"kind": "typescript", "content": "import { invoke } from '@tauri-apps/api/core'\nimport axios from 'axios'\n"},
            {"kind": "cargo", "content": "[package]\nname = 'app'\n[dependencies]\nserde = { version = '1', features = ['derive'] }\ntokio = '1'\n"},
            {"kind": "shell", "content": "python -m venv .venv\npip install -r requirements.txt\nnpm ci\n"},
        ])
        self.assertEqual(report["format"], FORMAT)
        self.assertEqual([source["id"] for source in report["sources"]], [f"source_{n}" for n in range(1, 7)])
        self.assertTrue(all(source["status"] == "parsed" for source in report["sources"]))
        self.assertEqual(names(report, "languages"), {"python", "javascript", "typescript", "rust", "shell"})
        self.assertEqual(dependency(report, "numpy")["confidence"], "declared")
        self.assertEqual(dependency(report, "numpy")["evidence"], [{"source": "source_1", "location": "line 1"}])
        self.assertEqual(dependency(report, "vosk")["confidence"], "inferred")
        self.assertEqual(dependency(report, "vosk")["evidence"], [{"source": "source_2", "location": "line 2"}])
        self.assertEqual(dependency(report, "opencv-python")["confidence"], "inferred")
        self.assertEqual(dependency(report, "pyautogui")["evidence"][0]["location"], "line 5")
        self.assertEqual(dependency(report, "tauri")["ecosystem"], "npm")
        self.assertEqual(dependency(report, "axios")["confidence"], "inferred")
        self.assertEqual(dependency(report, "tokio")["ecosystem"], "crates")
        self.assertTrue({"local-llm-client", "speech-recognition-library", "audio-io-library", "desktop-automation-library", "desktop-app-framework", "web-ui-framework", "async-runtime"} <= names(report, "capabilities"))
        self.assertEqual(names(report, "install_hints"), {"python-virtualenv", "pip-install", "npm-install", "cargo-build"})

    def test_jsx_text_cannot_fabricate_a_dependency_from_a_code_example(self):
        content = "export const Help = () => <p>Here's the client: <code>{'import Client from \"openai\"'}</code></p>;"
        for kind in ("javascript", "typescript"):
            report = one(kind, content)
            self.assertEqual(report["dependencies"], [])
            self.assertEqual(report["capabilities"], [])
            self.assertTrue(report["warnings"])

    def test_division_after_expression_parentheses_preserves_literal_imports(self):
        for content in ('const mid=(lo+hi)/2, client=require("openai");', 'function average(a,b){return(a+b)/2} const client=require("openai");'):
            self.assertEqual(names(one("javascript", content), "dependencies"), {"openai"})
        report = one("javascript", "if (ok) /'/.test(value); const text = 'import Client from \"openai\"'")
        self.assertEqual(report["dependencies"], [])
        self.assertTrue(report["warnings"])

    def test_locally_bound_require_is_not_claimed(self):
        for content in ['const require=value=>value; require("openai");', 'function load(require){ return require("openai"); }']:
            with self.subTest(content=content):
                report = one("javascript", content)
                self.assertEqual(report["dependencies"], [])
                self.assertIn("source_1: a locally bound require was found; require calls were not claimed", report["warnings"])
        self.assertEqual(names(one("javascript", 'import Client from "openai"; const require=x=>x; require("axios");'), "dependencies"), {"openai"})
        for content in ['const r = require; const a = require("axios");', 'module.require = x; const a = require("axios");']:
            self.assertEqual(names(one("javascript", content), "dependencies"), {"axios"})

    def test_multiline_jsx_child_text_cannot_claim_imports(self):
        for content in ['const H=()=> (\n<pre>\n require("openai")\n</pre>\n);', 'const H=()=> <code>\nimport Client from "openai"\n</code>;']:
            with self.subTest(content=content):
                report = one("typescript", content)
                self.assertEqual(report["dependencies"], [])
                self.assertTrue(report["warnings"])
        for content in ['const id = <T>(x: T) => x;\nconst c = require("openai");', 'const n = <number>value;\nimport Client from "openai";',
                        'const m = new Map<string, number>();\nconst c = require("openai");', 'export const H=()=> <p>\n hi \n</p>;\nconst c = require("openai");']:
            with self.subTest(content=content):
                self.assertEqual(names(one("typescript", content), "dependencies"), {"openai"})

    def test_jsx_tag_scanning_stays_within_work_limits(self):
        for unit in ['(<a {"', "=<a>", "(<a>x</a>);\n"]:
            with self.subTest(unit=unit):
                report = one("typescript", unit * (200 * 1024 // len(unit)))
                self.assertEqual(report["sources"][0]["status"], "parsed")

    def test_google_sdk_import_requires_a_complete_module_component(self):
        for module in ("google.generativeai_private", "google.generativeaiclient", "google.generativeai2"):
            for statement in ("import " + module, "from " + module + " import Client"):
                with self.subTest(statement=statement):
                    report = one("python", statement + "\n")
                    self.assertNotIn("google-generativeai", names(report, "dependencies"))
                    self.assertNotIn("hosted-llm-api-client", names(report, "capabilities"))
        for statement in ("import google.generativeai as genai", "from google.generativeai import GenerativeModel", "import google.generativeai.types"):
            with self.subTest(statement=statement):
                report = one("python", statement + "\n")
                self.assertEqual(names(report, "dependencies"), {"google-generativeai"})
                self.assertEqual(dependency(report, "google-generativeai")["evidence"], [{"source": "source_1", "location": "line 1"}])

    def test_shell_quoted_or_commented_heredoc_text_does_not_hide_later_commands(self):
        for prefix in ("# usage: cat <<EOF for notes", 'echo "write <<END yourself"', "echo $((1<<SHIFT))", "echo a'b <<EOF c'", 'echo --x="a <<EOF b"', "(( x << y ))"):
            with self.subTest(prefix=prefix):
                report = one("shell", prefix + "\npip install example\nEOF\n")
                self.assertEqual(names(report, "install_hints"), {"pip-install"})
        report = one("shell", "pip install example\ncat <<EOF\nunclosed body\n")
        self.assertEqual(report["sources"][0]["status"], "rejected")
        self.assertEqual(report["install_hints"], [])
        self.assertTrue(report["warnings"])

    def test_shell_expansion_redirection_ambiguity_rejects_without_partial_hints(self):
        prefixes = [r"echo ${x#<<EOF }", r"echo ${x:-a <<EOF b}", r"echo $'a\' <<EOF ' \\'", r"echo `printf '<<EOF'`", r"echo $(printf '<<EOF')", 'echo "$(printf \'%s\' " <<EOF ")"', 'echo "${x:-" <<EOF "}"', 'echo "$((1<<2))"']
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                report = one("shell", "pip install example\n" + prefix + "\ncargo build\nEOF\n")
                self.assertEqual(report["sources"][0]["status"], "rejected")
                self.assertEqual(report["install_hints"], [])
                self.assertEqual(report["warnings"], ["source_1: shell expansions with redirection text are unsupported"])

    def test_shell_heredoc_delimiter_whitespace_is_significant(self):
        report = one("shell", "cat <<EOF\n EOF\npip install example\nEOF\ncargo build\n")
        self.assertEqual(names(report, "install_hints"), {"cargo-build"})
        report = one("shell", "cat <<-'EOF'\n\tpip install example\n\tEOF\ncargo build\n")
        self.assertEqual(names(report, "install_hints"), {"cargo-build"})

    def test_yarn_script_commands_are_not_install_hints(self):
        for command in ("yarn build", "yarn test", "yarn run install", "yarn --version"):
            self.assertEqual(one("shell", command)["install_hints"], [], command)
        for command in ("yarn", "yarn --frozen-lockfile", "yarn install --frozen-lockfile"):
            self.assertEqual(names(one("shell", command), "install_hints"), {"yarn-install"}, command)

    def test_declared_overrides_inferred_for_same_dependency(self):
        report = source_inventory([{"kind": "python", "content": "import numpy\n"}, {"kind": "requirements", "content": "numpy\n"}])
        item = dependency(report, "numpy")
        self.assertEqual(item["confidence"], "declared")
        self.assertEqual(item["evidence"], [{"source": "source_2", "location": "line 1"}])
        capability = next(entry for entry in report["capabilities"] if entry["name"] == "numerical-computing-library")
        self.assertEqual(capability["confidence"], "declared")

    def test_pyproject_sections_and_unknown_count(self):
        report = one("pyproject", "\n".join([
            "[project]", f"name = '{CANARY}'", "dependencies = ['fastapi>=0.110', 'uvicorn[standard]', 'acme-internal-thing']",
            "[project.optional-dependencies]", f"{CANARY} = ['torch']",
            "[project.scripts]", f"{CANARY} = 'pkg.cli:main'",
            "[build-system]", "requires = ['hatchling']", "build-backend = 'hatchling.build'",
            "[tool.poetry.dependencies]", "python = '^3.11'", "openai = '1'", "local = { path = '../local' }",
        ]))
        self.assertEqual({"fastapi", "uvicorn", "torch", "hatchling", "openai"}, names(report, "dependencies"))
        self.assertEqual(dependency(report, "torch")["evidence"][0]["location"], "project.optional-dependencies")
        self.assertEqual(report["sources"][0]["unknown_dependencies"], 1)
        self.assertEqual(names(report, "install_hints"), {"python-console-entry-point", "python-build-backend"})
        self.assertIn("source_1: non-registry dependency source ignored", report["warnings"])
        self.assertNotIn(CANARY, json.dumps(report))

    def test_cargo_workspace_inheritance_is_marked_unresolved(self):
        report = one("cargo", "\n".join([
            "[package]", "name = 'x'", "version.workspace = true",
            "[dependencies]", "serde = { workspace = true }", "renamed = { package = 'reqwest', version = '0.12' }", f"{CANARY} = {{ git = 'https://{CANARY}.invalid' }}",
            "[target.'cfg(windows)'.dependencies]", "clap = '4'",
            "[workspace]", "members = ['a']",
        ]))
        self.assertEqual(dependency(report, "serde")["evidence"][0]["location"], "dependencies (workspace-inherited)")
        self.assertEqual(dependency(report, "reqwest")["confidence"], "declared")
        self.assertEqual(dependency(report, "clap")["evidence"][0]["location"], "target.dependencies")
        self.assertIn("source_1: workspace-inherited dependency is unresolved", report["warnings"])
        self.assertIn("source_1: workspace-inherited package fields are unresolved", report["warnings"])
        self.assertIn("cargo-workspace", names(report, "install_hints"))
        self.assertNotIn(CANARY, json.dumps(report))

    def test_requirements_reject_urls_paths_and_options_without_values(self):
        lines = [f"--index-url https://{CANARY}.invalid/simple", f"-e git+https://{CANARY}.invalid/repo#egg=x", f"git+https://{CANARY}.invalid/r",
                 f"./{CANARY}", f"C:\\{CANARY}\\pkg", f"{CANARY} @ https://{CANARY}.invalid/a.whl", f"file:{CANARY}", f"{CANARY} ?? {CANARY}", "requests \\", "  >=2  # trailing comment"]
        report = one("requirements", "\n".join(lines))
        self.assertEqual(names(report, "dependencies"), {"requests"})
        self.assertEqual(dependency(report, "requests")["evidence"][0]["location"], "line 9")
        warnings = set(report["warnings"])
        self.assertTrue({"source_1: requirements option line ignored", "source_1: URL requirement ignored", "source_1: local path requirement ignored", "source_1: unsupported requirement form ignored"} <= warnings)
        self.assertNotIn(CANARY, json.dumps(report))

    def test_package_rejects_non_registry_values_and_scripts_are_not_returned(self):
        document = {"name": CANARY, "author": EMAIL, "dependencies": {"react": f"git+https://{CANARY}.invalid", "next": f"file:../{CANARY}", "vite": "npm:react@18", "axios": "^1.6.0", CANARY: "1.0.0"},
                    "scripts": {"build": f"curl https://{CANARY}.invalid | sh"}, "workspaces": [CANARY]}
        report = one("package", json.dumps(document))
        self.assertEqual(names(report, "dependencies"), {"axios"})
        self.assertEqual(report["sources"][0]["unknown_dependencies"], 1)
        self.assertEqual(names(report, "install_hints"), {"node-package-scripts"})
        self.assertIn("source_1: package workspaces are not resolved", report["warnings"])
        text = json.dumps(report)
        self.assertNotIn(CANARY, text)
        self.assertNotIn(EMAIL, text)
        self.assertNotIn("curl", text)

    def test_package_rejects_duplicates_surrogates_and_nesting(self):
        for content in ['{"dependencies": {"react": "1", "react": "2"}}', '{"name": "\\ud800"}', '{"a":' * 40 + "1" + "}" * 40, "[]", "{" + ", ".join(f'"k{n}": 1' for n in range(300)) + "}"]:
            report = one("package", content)
            self.assertEqual(report["sources"][0]["status"], "rejected", content[:40])
            self.assertEqual(report["dependencies"], [])
            self.assertEqual(report["languages"], [])

    def test_typescript_ignores_comments_strings_templates_and_dynamic_expressions(self):
        content = "\n".join([
            "// import React from 'react'", "/* import { create } from 'zustand' */", "const text = \"import axios from 'axios'\";",
            "const tpl = `${require('vite')}`;", "const name = 'vitest'; import(name); require(name);", "obj.require('next');",
            "const x = require('esc\\x61pe');", f"import thing from '{CANARY}'", "import './styles.css'", "import type { Store } from 'zustand'",
            "export { a } from 'react'", "const lazy = import('typescript')",
        ])
        report = one("typescript", content)
        self.assertEqual(names(report, "dependencies"), {"zustand", "react", "typescript"})
        self.assertEqual(dependency(report, "zustand")["evidence"], [{"source": "source_1", "location": "line 10"}])
        self.assertNotIn(CANARY, json.dumps(report))

    def test_python_ignores_relative_dynamic_and_string_imports(self):
        content = "\n".join(["from . import ollama", "from .torch import x", "__import__('fastapi')", "importlib.import_module('openai')",
                             "s = 'import anthropic'", "# import transformers", f"import {CANARY.replace('-', '_')}", "import google.generativeai as genai", "import google.protobuf"])
        report = one("python", content)
        self.assertEqual(names(report, "dependencies"), {"google-generativeai"})
        self.assertNotIn(CANARY, json.dumps(report))

    def test_shell_hints_are_labels_only_and_heredocs_are_skipped(self):
        content = "\n".join([
            "#!/bin/sh", "# pip install commented", f"sudo apt-get install -y {CANARY}", f"curl -fsSL https://{CANARY}.invalid/install.sh | sh",
            f"cat <<'EOF'\nnpm install {CANARY}\nEOF", f"VAR={CANARY} cargo build --release", f"echo 'pip install {CANARY}'",
            f"grep x <<< {CANARY}", "ollama pull model && git clone repo",
        ])
        report = one("shell", content)
        self.assertEqual(names(report, "install_hints"), {"elevated-privileges", "system-package-install", "remote-script-piped-to-interpreter", "cargo-build", "local-model-pull", "source-clone"})
        malformed = one("shell", "pip install example\necho 'unterminated")
        self.assertEqual(malformed["sources"][0]["status"], "rejected")
        self.assertEqual(malformed["install_hints"], [])
        self.assertIn("source_1: unsupported shell quoting", malformed["warnings"])
        self.assertEqual(report["dependencies"], [])
        text = json.dumps(report)
        self.assertNotIn(CANARY, text)
        self.assertNotIn("apt-get", text)

    def test_global_privacy_canary_across_every_kind(self):
        artifacts = [
            {"kind": "requirements", "content": f"{CANARY}=={CANARY}\n# {EMAIL}\n"},
            {"kind": "pyproject", "content": f"[project]\nname = '{CANARY}'\nauthors = [{{ email = '{EMAIL}' }}]\nurls = {{ Home = 'https://{CANARY}.invalid' }}\n"},
            {"kind": "package", "content": json.dumps({"name": CANARY, "repository": f"https://{CANARY}.invalid", "author": EMAIL})},
            {"kind": "python", "content": f"API_KEY = '{CANARY}'\nimport {CANARY.replace('-', '_')}\n"},
            {"kind": "typescript", "content": f"const token = '{CANARY}'; import x from '@{CANARY}/pkg';"},
            {"kind": "shell", "content": f"export TOKEN={CANARY}\npip install --extra-index-url https://{CANARY}.invalid {CANARY}\n"},
        ]
        text = json.dumps(source_inventory(artifacts))
        for secret in (CANARY, EMAIL, "invalid", "TOKEN", "API_KEY", "https"):
            self.assertNotIn(secret, text)

    def test_envelope_validation(self):
        bad = [None, [], [{"kind": "python", "content": ""}] * 7, [{"kind": "python"}], [{"kind": "python", "content": "", "path": "x"}],
               [{"kind": "source-json", "content": ""}], [{"kind": "python", "content": b""}], [{"kind": "python", "content": "\ud800"}],
               [{"kind": "python", "content": "x" * (MAX_FILE_BYTES + 1)}], [{"kind": "python", "content": "\u00e9" * (MAX_FILE_BYTES // 2 + 1)}],
               [{"kind": "python", "content": "#" * MAX_FILE_BYTES}] * 3]
        for artifacts in bad:
            with self.assertRaises(ArtifactError):
                source_inventory(artifacts)
        self.assertEqual(set(SOURCE_KINDS), {"requirements", "pyproject", "package", "cargo", "python", "javascript", "typescript", "rust", "shell"})

    def test_malicious_nesting_is_rejected_safely(self):
        cases = [
            ("python", "x = " + "-" * 200000 + "1\n"), ("python", "x = " + "(" * 300 + "1" + ")" * 300), ("python", "x" + ".a" * 100000),
            ("python", "".join("    " * depth + "if x:\n" for depth in range(30)) + "    " * 30 + "import numpy\n"),
            ("python", "import numpy\n" + "pass\n" * 20001), ("python", "def (:\n"),
            ("pyproject", "a = " + "[" * 100000), ("pyproject", "a = " + "{ b = " * 50 + "1" + " }" * 50), ("pyproject", "[project\n"),
            ("cargo", "x = " + "[" * 50 + "]" * 50), ("package", "[" * 100000),
        ]
        for kind, content in cases:
            started = time.monotonic()
            report = one(kind, content)
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(report["sources"][0]["status"], "rejected", (kind, content[:30]))
            self.assertEqual(report["dependencies"], [])
            self.assertEqual(report["capabilities"], [])
            self.assertTrue(report["warnings"])

    def test_rejected_source_does_not_hide_others_and_strings_in_toml_do_not_trip_depth(self):
        report = source_inventory([{"kind": "python", "content": "import numpy\n" + "pass\n" * 20001}, {"kind": "pyproject", "content": "[project]\ndescription = '" + "[" * 100 + "'\ndependencies = ['numpy']\n"}])
        self.assertEqual([source["status"] for source in report["sources"]], ["rejected", "parsed"])
        self.assertEqual(dependency(report, "numpy")["evidence"], [{"source": "source_2", "location": "project.dependencies"}])

    def test_large_inputs_stay_bounded(self):
        started = time.monotonic()
        report = source_inventory([
            {"kind": "typescript", "content": "import a from 'react';\n" * 20000},
            {"kind": "shell", "content": "pip install x\n" * 30000},
        ])
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(len(dependency(report, "react")["evidence"]), 5)
        self.assertEqual(len(report["install_hints"][0]["evidence"]), 5)


    def test_requirement_specifiers_have_no_catastrophic_backtracking(self):
        for pattern in ("!1", "=1", "~=1 ,", "1!=", "= 1 "):
            started = time.monotonic()
            report = one("requirements", "numpy" + pattern * 50000 + " ?")
            self.assertLess(time.monotonic() - started, 2, pattern)
            self.assertEqual(report["dependencies"], [])
        report = one("requirements", "numpy (>=1.2)\nscipy~=1.11,!=1.11.2 ; python_version >= '3.9'\ntorch==2.1.0+cpu\n")
        self.assertEqual(names(report, "dependencies"), {"numpy", "scipy", "torch"})

    def test_python_syntax_warnings_are_not_written_to_stderr(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            report = one("python", "x = '\\p" + CANARY + "'\nimport numpy\n")
        self.assertEqual(names(report, "dependencies"), {"numpy"})
        self.assertEqual(stderr.getvalue(), "")


    def test_rust_use_extern_and_root_paths_map_to_curated_crates_only(self):
        content = "\n".join([
            "#![cfg_attr(not(debug_assertions), windows_subsystem = \"windows\")]",
            "use serde::{Deserialize, Serialize};", "use ::anyhow::Result;", "use { thiserror::Error, rayon };",
            "extern crate hyper as h;", f"use {CANARY.replace('-', '_').lower()}::thing;",
            "fn main() {", "    tauri_plugin_shell::init();", "    tokio::spawn(async {});", "    let _ = crate::reqwest::x();", "    a.clap::y();", "}",
        ])
        report = one("rust", content)
        self.assertEqual(report["sources"][0]["status"], "parsed")
        self.assertEqual(names(report, "dependencies"), {"serde", "anyhow", "thiserror", "rayon", "hyper", "tauri", "tokio"})
        self.assertTrue(all(item["confidence"] == "inferred" and item["ecosystem"] == "crates" for item in report["dependencies"]))
        self.assertEqual(dependency(report, "tauri")["evidence"], [{"source": "source_1", "location": "line 8"}])
        self.assertEqual(names(report, "languages"), {"rust"})
        self.assertNotIn(CANARY.lower().replace("-", "_"), json.dumps(report))

    def test_rust_comments_strings_macros_attributes_and_local_modules_are_not_claimed(self):
        content = "\n".join([
            "// use tokio;", "/* outer /* use reqwest; */ use axum; */", "#[derive(serde::Serialize)]", "struct A;",
            "const S: &str = \"use clap::Parser; serde_json::to_string\";", "const R: &str = r#\"use sqlx; \" still raw \"#;",
            "const B: &[u8] = br##\"use diesel;\"##;", "const C: char = '\"';", "const Q: u8 = b'\\'';",
            "fn f<'a>(x: &'a str) { println!(\"{}\", rusqlite::VERSION); log::info!(\"x\"); }",
            "macro_rules! m { () => { tracing::x() } }", "mod anyhow;", "use anyhow::Result;", "fn g() { anyhow::bail(); }",
            "let r#tokio = 1;",
        ])
        report = one("rust", content)
        self.assertEqual(report["sources"][0]["status"], "parsed")
        self.assertEqual(report["dependencies"], [])

    def test_rust_inferred_is_upgraded_by_cargo_declaration(self):
        report = source_inventory([{"kind": "rust", "content": "use tokio::net;\n"}, {"kind": "cargo", "content": "[dependencies]\ntokio = '1'\n"}])
        self.assertEqual(dependency(report, "tokio")["confidence"], "declared")
        self.assertEqual(dependency(report, "tokio")["evidence"], [{"source": "source_2", "location": "dependencies"}])

    def test_rust_unterminated_and_hostile_lexemes_stay_bounded(self):
        for content in ["r###\"" + "x" * 100000, "/* " * 50000, "'" * 100000, "\"\\" * 100000, "a::" * 100000, "#[" * 50000, "m!(" * 50000]:
            started = time.monotonic()
            report = one("rust", content)
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(report["dependencies"], [])
        report = one("rust", "use tokio;\n" + ";" * 300001)
        self.assertEqual(report["sources"][0]["status"], "rejected")
        self.assertEqual(report["dependencies"], [])

    def test_python_counts_every_ast_node_and_rejects_flat_expression_floods(self):
        for content in ["import numpy\nx = [" + "0," * 25000 + "]\n", "import numpy\nx = {" + ", ".join(f"{n}: {n}" for n in range(12000)) + "}\n",
                        "import numpy\nx = " + "+".join("1" for _ in range(30000)) + "\n", "import numpy\n" + "x = 1\n" * 70000]:
            report = one("python", content)
            self.assertEqual(report["sources"][0]["status"], "rejected")
            self.assertEqual(report["dependencies"], [])
            self.assertEqual(len(report["warnings"]), 1)
        # The flat list fits the lexical pre-pass, so this rejection proves the full AST node count.
        report = one("python", "import numpy\nx = [" + "0," * 25000 + "]\n")
        self.assertEqual(report["warnings"], ["source_1: document structure exceeds the supported limit"])

    def test_python_realistic_nesting_is_accepted(self):
        content = "\n".join([
            "import os", "class Service:", "    def run(self, items):", "        for item in items:", "            if item:",
            "                try:", "                    with open(item) as handle:", "                        import numpy",
            "                        value = {'a': [handle.read().strip().split(',')[0]]}", "                except OSError:", "                    pass",
        ] + [f"def helper_{n}(a, b=1):\n    return [x * b for x in a if x]\n" for n in range(150)])
        report = one("python", content)
        self.assertEqual(report["sources"][0]["status"], "parsed")
        self.assertEqual(dependency(report, "numpy")["evidence"], [{"source": "source_1", "location": "line 8"}])

    def test_concurrent_python_parsing_never_leaks_syntax_warnings(self):
        original_parse = ast.parse

        def slow_parse(*args, **kwargs):
            # Widen the window between filter install and parse so an unserialized warning context would race.
            time.sleep(0.002)
            return original_parse(*args, **kwargs)

        stderr, failures = io.StringIO(), []

        def worker(seed):
            try:
                for index in range(15):
                    report = one("python", f"x = '\\q{CANARY}{seed}{index}'\nimport numpy\n")
                    if names(report, "dependencies") != {"numpy"}:
                        failures.append(seed)
            except Exception as error:  # surfaced through the assertion below
                failures.append(repr(error))

        interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            with mock.patch.object(source_module.ast, "parse", slow_parse), contextlib.redirect_stderr(stderr):
                threads = [threading.Thread(target=worker, args=(seed,)) for seed in range(6)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
        finally:
            sys.setswitchinterval(interval)
        self.assertEqual(failures, [])
        self.assertEqual(stderr.getvalue(), "")

    def test_typescript_regex_literals_cannot_spoof_imports(self):
        spoofs = [
            "const r = /'/; const s = ' import x from \"react\" '",
            "const r = /\"/; const s = \" import x from 'react' \"",
            "const r = /`/; const t = `\nimport x from 'react'\n`",
            "if (ok) /'/.test(v); const s = ' import x from \"react\" '",
            "const r = /[/'\"]/; const s = ' require(\"react\") '",
            "const t = `${await import('react')}`",
        ]
        for content in spoofs:
            self.assertEqual(one("typescript", content)["dependencies"], [], content)
        content = "\n".join(["import a from 'zustand'", "const pattern = /^\\/api\\/'v1'/g;", "const ratio = total / count / 2;", "import b from 'axios'", "export { c } from 'vite'"])
        report = one("typescript", content)
        self.assertEqual(names(report, "dependencies"), {"zustand", "axios", "vite"})
        self.assertEqual(dependency(report, "vite")["evidence"], [{"source": "source_1", "location": "line 5"}])

    def test_typescript_ambiguous_slash_taints_only_affected_lines(self):
        content = "\n".join(["const v = (a) / 2; import x from 'react'", "import y from 'axios'"])
        report = one("typescript", content)
        self.assertEqual(names(report, "dependencies"), {"react", "axios"})

if __name__ == "__main__":
    unittest.main()
