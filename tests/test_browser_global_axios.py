"""Challenge the ambient-client inference without weakening scope or mutation checks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from surface_map import js_parser
from surface_map.analyze import Builder, analyze, parse_js


ROOT = Path(__file__).resolve().parents[1]
READY = js_parser.available()[0]
ASSUMPTION = "unverified_global_axios"


def inspect(source):
    return analyze([{"kind": "javascript", "text": source}], javascript_engine="enhanced")


def operations(report):
    return {(row["method"], row["path"]) for row in
            report["routes"] + report.get("unresolved_operations", [])}


def assumption_warnings(report):
    return [warning for warning in report["warnings"]
            if "unbound global Axios" in warning["reason"]]


def inspect_capture(*scripts):
    artifacts = [{"kind": "html", "text": '<h1>Saved page</h1><script src="/app.js"></script>'}]
    artifacts.extend({"kind": "javascript", "text": script} for script in scripts)
    return analyze(artifacts, javascript_engine="enhanced")


def capture_mutation_warnings(report):
    return [warning for warning in report["warnings"]
            if "selected JavaScript" in warning["reason"] and "global Axios" in warning["reason"]]


@unittest.skipUnless(READY, "Explicit optional parser installation and supported Node required")
class BrowserGlobalAxiosTests(unittest.TestCase):
    def assert_assumed(self, report, expected):
        self.assertEqual(operations(report), expected)
        for row in report["routes"] + report.get("unresolved_operations", []):
            self.assertEqual(row["confidence"], "inferred")
            self.assertEqual(row["assumptions"], [ASSUMPTION])
            self.assertTrue(row["evidence"])
            for evidence in row["evidence"]:
                self.assertEqual(evidence["kind"], "javascript")
                self.assertEqual(evidence["assumptions"], [ASSUMPTION])
        self.assertEqual(len(assumption_warnings(report)), 1)
        self.assertIn("not verified", assumption_warnings(report)[0]["reason"])

    def test_conventional_globals_and_aliases_retain_literal_calls_with_explicit_uncertainty(self):
        cases = [
            'axios.get("/api/orders")',
            'window.axios.get("/api/orders")',
            'globalThis.axios.get("/api/orders")',
            'self.axios.get("/api/orders")',
            'const http=window.axios;http.get("/api/orders")',
            'const {axios:http}=globalThis;http.get("/api/orders")',
            'const {get:load}=self.axios;load("/api/orders")',
            'const key="get";axios[key]("/api/orders")',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assert_assumed(inspect(source), {("GET", "/api/orders")})

    def test_derived_and_nested_instances_keep_the_assumption_and_base_url(self):
        cases = [
            'const c=axios.create({baseURL:"/api"});c.get("/orders")',
            'const a=window.axios.create({baseURL:"/api"});const b=a.create({});b.get("/orders")',
            'const {create}=globalThis.axios;const c=create({baseURL:"/api"});c.get("/orders")',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assert_assumed(inspect(source), {("GET", "/api/orders")})

    def test_unknown_method_is_retained_separately_and_does_not_become_get(self):
        report = inspect('axios.request({url:"/api/orders",method:chosen})')
        self.assert_assumed(report, {(None, "/api/orders")})
        self.assertEqual(report["routes"], [])

    def test_axios_method_rules_and_data_shapes_are_preserved_without_private_values(self):
        marker = "PRIVATE_GLOBAL_AXIOS_VALUE_6382"
        source = 'globalThis.axios.post("/api/orders",{password:' + json.dumps(marker) + ',quantity:2})'
        report = inspect(source)
        self.assert_assumed(report, {("POST", "/api/orders")})
        self.assertNotIn(marker, json.dumps(report))
        self.assertEqual(report["routes"][0]["request_shapes"], [{
            "type": "object", "properties": {"password": {"type": "string"}, "quantity": {"type": "integer"}}
        }])
        self.assert_assumed(inspect('axios.request({url:"/api/orders",method:"pAtCh"})'),
                            {("PATCH", "/api/orders")})
        self.assert_assumed(inspect('axios.get("/api/orders",{method:"POST"})'),
                            {("GET", "/api/orders")})

    def test_shadowed_names_and_nonclients_are_not_request_evidence(self):
        cases = [
            'const axios={get(){}};axios.get("/api/orders")',
            'let axios=mock;axios.get("/api/orders")',
            'var axios=mock;axios.get("/api/orders")',
            'function axios(){}axios.get("/api/orders")',
            'function run(axios){axios.get("/api/orders")}',
            'function run(window){window.axios.get("/api/orders")}',
            'const globalThis={axios:{get(){}}};globalThis.axios.get("/api/orders")',
            'import axios from "other-module";axios.get("/api/orders")',
            'const axios=require("./axios");axios.get("/api/orders")',
            'const client={get(){}};client.get("/api/orders")',
        ]
        for source in cases:
            with self.subTest(source=source):
                report = inspect(source)
                self.assertEqual(operations(report), set())
                self.assertEqual(assumption_warnings(report), [])

    def test_classic_program_bindings_mask_the_corresponding_global_property(self):
        for declaration in ('var axios=mock;', 'function axios(){}'):
            for receiver in ("window", "self", "globalThis"):
                source = declaration + receiver + '.axios.get("/api/orders")'
                with self.subTest(source=source):
                    report = inspect(source)
                    self.assertEqual(operations(report), set())
                    self.assertEqual(assumption_warnings(report), [])

    def test_lexical_and_module_bindings_do_not_fabricate_a_global_property_write(self):
        cases = [
            'let axios=mock;window.axios.get("/api/orders")',
            'const axios=mock;window.axios.get("/api/orders")',
            'export {};var axios=mock;window.axios.get("/api/orders")',
            'export {};function axios(){}window.axios.get("/api/orders")',
            'function run(axios){window.axios.get("/api/orders")}',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assert_assumed(inspect(source), {("GET", "/api/orders")})

    def test_nested_shadowing_does_not_disable_an_unrelated_outer_call(self):
        report = inspect('function run(axios){axios.get("/api/users")}axios.get("/api/orders")')
        self.assert_assumed(report, {("GET", "/api/orders")})
        report = inspect('export {};{let axios=mock;axios.get("/api/users")}window.axios.get("/api/orders")')
        self.assert_assumed(report, {("GET", "/api/orders")})

    def test_replacement_and_mutation_do_not_leave_stale_ambient_requests(self):
        changes = [
            'axios=mock;',
            'window.axios=mock;',
            'globalThis.axios=mock;',
            'self.axios=mock;',
            'delete window.axios;',
            'axios.get=mock;',
            'const c=axios;c.get=mock;',
            'axios.defaults.baseURL=external;',
            'const defaults=axios.defaults;defaults.baseURL=external;',
            'Object.assign(axios.defaults,{baseURL:"/admin"});',
            'axios.interceptors.request.use(change);',
            'const q=axios.interceptors.request;q.use(change);',
            'Object.assign(axios,{get:mock});',
            'const g=typeof window==="object"?window:globalThis;g.axios=mock;',
        ]
        for change in changes:
            for receiver in ("axios", "window.axios"):
                source = change + receiver + '.get("/api/orders")'
                with self.subTest(source=source):
                    report = inspect(source)
                    self.assertEqual(operations(report), set())
                    self.assertEqual(assumption_warnings(report), [])

    def test_nonrouting_helpers_and_response_interceptors_do_not_erase_requests(self):
        for source in (
            'axios.isAxiosError(error);axios.get("/api/orders")',
            'axios.defaults.withCredentials=true;axios.get("/api/orders")',
            'axios.interceptors.response.use(ok,retry);axios.get("/api/orders")',
        ):
            with self.subTest(source=source):
                self.assert_assumed(inspect(source), {("GET", "/api/orders")})

    def test_imported_and_required_clients_do_not_inherit_ambient_assumptions(self):
        for source in (
            'import axios from "axios";axios.get("/api/orders")',
            'const axios=require("axios");axios.get("/api/orders")',
            'window.axios=mock;import client from "axios";client.get("/api/orders")',
            'import client from "axios";const c=client.create({baseURL:"/api"});c.get("/orders")',
        ):
            with self.subTest(source=source):
                report = inspect(source)
                self.assertEqual(operations(report), {("GET", "/api/orders")})
                self.assertEqual(assumption_warnings(report), [])
                for row in report["routes"]:
                    self.assertFalse(row.get("assumptions"))
                    self.assertTrue(all(not evidence.get("assumptions") for evidence in row["evidence"]))

    def test_warning_is_deduplicated_per_source_and_absent_without_retained_evidence(self):
        report = inspect('axios.get("/api/orders");window.axios.post("/api/users",{});')
        self.assert_assumed(report, {("GET", "/api/orders"), ("POST", "/api/users")})
        for source in (
            'axios.get(dynamicTarget)',
            'axios.create({baseURL:"/api"})',
            'axios.get("relative-without-document-context")',
            'function run(){eval(code);axios.get("/api/orders")}',
            '(0,eval)(code);axios.get("/api/orders")',
        ):
            with self.subTest(source=source):
                report = inspect(source)
                self.assertEqual(operations(report), set())
                self.assertEqual(assumption_warnings(report), [])

    def test_selected_scripts_cannot_retain_stale_global_calls_in_either_input_order(self):
        calls = 'axios.get("/api/orders");window.axios.request({url:"/api/items",method:chosen});'
        changes = [
            'axios=mock;',
            'window.axios=mock;',
            'globalThis.axios=mock;',
            'self.axios=mock;',
            'delete window.axios;',
            'var axios=mock;',
            'function axios(){}',
            'axios.get=mock;',
            'const client=axios;client.post=mock;',
            'axios.defaults.baseURL=external;',
            'const defaults=axios.defaults;defaults.method=external;',
            'axios.interceptors.request.use(change);',
            'const q=axios.interceptors.request;q.use(change);',
            'register(axios);',
        ]
        for change in changes:
            for scripts in ((calls, change), (change, calls)):
                with self.subTest(scripts=scripts):
                    report = inspect_capture(*scripts)
                    self.assertEqual(operations(report), set())
                    self.assertEqual(assumption_warnings(report), [])
                    self.assertTrue(capture_mutation_warnings(report))
                    self.assertNotIn('"assumptions"', json.dumps(report))

    def test_cross_script_global_mutation_preserves_imported_and_required_clients(self):
        ambient = 'axios.get("/api/orders");'
        changed = 'window.axios=mock;'
        for explicit in ('import http from "axios";http.get("/api/users");',
                         'const http=require("axios");http.get("/api/users");'):
            for scripts in ((ambient, changed, explicit), (explicit, changed, ambient)):
                with self.subTest(scripts=scripts):
                    report = inspect_capture(*scripts)
                    self.assertEqual(operations(report), {("GET", "/api/users")})
                    self.assertTrue(capture_mutation_warnings(report))
                    self.assertEqual(assumption_warnings(report), [])
                    for row in report["routes"]:
                        self.assertFalse(row.get("assumptions"))
                        self.assertTrue(all(not item.get("assumptions") for item in row["evidence"]))

    def test_unrelated_local_or_module_shadowing_does_not_contaminate_another_script(self):
        ambient = 'axios.get("/api/orders");'
        unrelated = [
            'function run(){const axios={get(){}};axios.get("/api/users")}',
            'export {};var axios=mock;axios.get("/api/users")',
            'export {};function axios(){}axios.get("/api/users")',
            'function run(axios){axios.get("/api/users")}',
        ]
        for other in unrelated:
            for scripts in ((ambient, other), (other, ambient)):
                with self.subTest(scripts=scripts):
                    report = inspect_capture(*scripts)
                    self.assert_assumed(report, {("GET", "/api/orders")})
                    self.assertEqual(capture_mutation_warnings(report), [])

    def test_nonrouting_settings_in_another_script_preserve_ambient_evidence(self):
        ambient = 'axios.get("/api/orders");'
        for other in ('axios.defaults.withCredentials=true;',
                      'axios.interceptors.response.use(ok,retry);',
                      'axios.isAxiosError(error);'):
            for scripts in ((ambient, other), (other, ambient)):
                with self.subTest(scripts=scripts):
                    report = inspect_capture(*scripts)
                    self.assert_assumed(report, {("GET", "/api/orders")})
                    self.assertEqual(capture_mutation_warnings(report), [])

    def test_derived_global_clients_are_suppressed_when_a_sibling_script_changes_the_global(self):
        calls = 'const client=axios.create({baseURL:"/api"});client.get("/orders");'
        changed = 'axios.defaults.baseURL=external;'
        for scripts in ((calls, changed), (changed, calls)):
            with self.subTest(scripts=scripts):
                report = inspect_capture(*scripts)
                self.assertEqual(operations(report), set())
                self.assertEqual(assumption_warnings(report), [])
                self.assertTrue(capture_mutation_warnings(report))

    def test_classic_global_lexical_bindings_shadow_bare_axios_across_scripts(self):
        calls = 'axios.get("/api/orders");axios.request({url:"/api/items",method:chosen});'
        declarations = ('let axios={get(){}};', 'const axios={get(){}};', 'class axios{}')
        for declaration in declarations:
            for scripts in ((calls, declaration), (declaration, calls)):
                with self.subTest(scripts=scripts):
                    report = inspect_capture(*scripts)
                    self.assertEqual(operations(report), set())
                    self.assertEqual(assumption_warnings(report), [])
                    self.assertTrue(capture_mutation_warnings(report))

    def test_cross_script_lexical_binding_does_not_replace_explicit_global_properties(self):
        declarations = ('let axios={get(){}};', 'const axios={get(){}};', 'class axios{}')
        for declaration in declarations:
            for receiver in ("window", "globalThis", "self"):
                calls = receiver + '.axios.get("/api/orders")'
                for scripts in ((calls, declaration), (declaration, calls)):
                    with self.subTest(scripts=scripts):
                        self.assert_assumed(inspect_capture(*scripts), {("GET", "/api/orders")})

    def test_aliases_and_created_clients_keep_bare_versus_qualified_provenance_across_scripts(self):
        declaration = 'const axios={get(){}};'
        sources = [
            ('const c=axios;c.get("/api/orders")', False),
            ('const {get:load}=axios;load("/api/orders")', False),
            ('const c=axios.create({baseURL:"/api"});c.get("/orders")', False),
            ('const a=axios.create({baseURL:"/api"});const c=a.create({});c.get("/orders")', False),
            ('const c=window.axios;c.get("/api/orders")', True),
            ('const {axios:c}=globalThis;c.get("/api/orders")', True),
            ('const {get:load}=self.axios;load("/api/orders")', True),
            ('const c=window.axios.create({baseURL:"/api"});c.get("/orders")', True),
            ('const a=self.axios.create({baseURL:"/api"});const c=a.create({});c.get("/orders")', True),
        ]
        for calls, qualified in sources:
            for scripts in ((calls, declaration), (declaration, calls)):
                with self.subTest(scripts=scripts):
                    report = inspect_capture(*scripts)
                    if qualified:
                        self.assert_assumed(report, {("GET", "/api/orders")})
                    else:
                        self.assertEqual(operations(report), set())
                        self.assertEqual(assumption_warnings(report), [])
                        self.assertTrue(capture_mutation_warnings(report))

    def test_cross_script_lexical_masking_is_selective_in_a_mixed_call_source(self):
        calls = ('axios.get("/api/orders");window.axios.get("/api/users");'
                 'const c=axios.create({baseURL:"/api"});c.get("/items");'
                 'const d=globalThis.axios.create({baseURL:"/api"});d.get("/tasks");')
        declaration = 'class axios{}'
        for scripts in ((calls, declaration), (declaration, calls)):
            with self.subTest(scripts=scripts):
                report = inspect_capture(*scripts)
                self.assert_assumed(report, {("GET", "/api/users"), ("GET", "/api/tasks")})
                self.assertTrue(capture_mutation_warnings(report))

    def test_module_lexical_bindings_do_not_shadow_ambient_sibling_scripts(self):
        calls = 'axios.get("/api/orders")'
        for declaration in ('export {};let axios=mock;', 'export {};const axios=mock;', 'export {};class axios{}'):
            for scripts in ((calls, declaration), (declaration, calls)):
                with self.subTest(scripts=scripts):
                    report = inspect_capture(*scripts)
                    self.assert_assumed(report, {("GET", "/api/orders")})
                    self.assertEqual(capture_mutation_warnings(report), [])

    def test_commonjs_wrapper_uncertainty_blocks_peer_bare_assumptions_but_not_qualified_calls(self):
        # An artifact without import/export can be a classic script or wrapped
        # CommonJS. The capture does not establish its runtime wrapper scope.
        required = 'const axios=require("axios");axios.get("/api/users");'
        peer = 'axios.get("/api/orders");window.axios.get("/api/items");'
        for scripts in ((required, peer), (peer, required)):
            with self.subTest(scripts=scripts):
                report = inspect_capture(*scripts)
                self.assertEqual(operations(report), {("GET", "/api/users"), ("GET", "/api/items")})
                by_path = {row["path"]: row for row in report["routes"]}
                self.assertFalse(by_path["/api/users"].get("assumptions"))
                self.assertTrue(all(not item.get("assumptions") for item in by_path["/api/users"]["evidence"]))
                self.assertEqual(by_path["/api/items"]["assumptions"], [ASSUMPTION])
                self.assertEqual(len(assumption_warnings(report)), 1)
                self.assertTrue(capture_mutation_warnings(report))
                peer_source = "source_3" if scripts[1] == peer else "source_2"
                analysis = next(row for row in report["javascript_analysis"] if row["source"] == peer_source)
                self.assertEqual(analysis["assumed_global_axios_omissions"], {"global_lexical_binding": 1})

    def test_suppression_counts_survive_full_warning_retention_for_each_reason_and_input_order(self):
        calls = 'axios.get("/api/orders");axios.request({url:"/api/items",method:chosen});'
        changes = (("window.axios=mock;", "global_client_modified"),
                   ("const axios={get(){}};", "global_lexical_binding"))
        for change, reason in changes:
            for scripts in ((calls, change), (change, calls)):
                with self.subTest(scripts=scripts, reason=reason):
                    builder = Builder("enhanced")
                    builder.sources = [{"id": "source_1", "kind": "javascript"},
                                       {"id": "source_2", "kind": "javascript"}]
                    for index in range(32):
                        builder.warn("source_1", "Existing synthetic coverage warning " + str(index))
                    for index, source in enumerate(scripts, 1):
                        parse_js(source, "source_" + str(index), builder)
                    report = builder.finish()
                    self.assertEqual(operations(report), set())
                    self.assertEqual(len(report["warnings"]), 32)
                    self.assertEqual(capture_mutation_warnings(report), [])
                    call_source = "source_1" if scripts[0] == calls else "source_2"
                    analysis = next(row for row in report["javascript_analysis"] if row["source"] == call_source)
                    counts = analysis["assumed_global_axios_omissions"]
                    self.assertEqual(counts, {reason: 2})
                    self.assertTrue(all(type(count) is int and count > 0 for count in counts.values()))
                    self.assertTrue(any(row["category"] == "warnings" for row in report["evidence_limits"]["omissions"]))

    def test_existing_realworld_axios_fixtures_pass_without_changing_inputs_or_labels(self):
        folder = ROOT / "evals" / "realworld"
        manifests = [folder / "corpus.json", folder / "labels.json"]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in manifests}
        corpus = json.loads(manifests[0].read_text(encoding="utf-8"))
        labels = {row["id"]: row for row in json.loads(manifests[1].read_text(encoding="utf-8"))["labels"]}
        for identity in ("axios-example-get-global", "axios-example-post-global"):
            case = next(row for row in corpus["cases"] if row["id"] == identity)
            artifacts = []
            for artifact in case["artifacts"]:
                raw = (folder / artifact["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact["sha256"])
                artifacts.append({"kind": artifact["kind"], "text": raw.decode("utf-8")})
            with self.subTest(identity=identity), patch("socket.create_connection", side_effect=AssertionError("No capture replay")):
                report = analyze(artifacts, javascript_engine="enhanced")
                expected = {(row["method"], row["path"]) for row in labels[identity]["expected"]["routes"]}
                self.assert_assumed(report, expected)
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in manifests})


class GlobalAxiosAssumptionRetentionTests(unittest.TestCase):
    def test_machine_readable_assumption_survives_a_full_warning_list(self):
        builder = Builder("enhanced")
        for index in range(32):
            builder.warn("source_1", "Existing synthetic coverage warning " + str(index))
        builder.add("/api/orders", "GET", "javascript", "source_1", "line 1", assumption=ASSUMPTION)
        report = builder.finish()
        self.assertEqual(len(report["warnings"]), 32)
        self.assertEqual(report["routes"][0]["assumptions"], [ASSUMPTION])
        self.assertEqual(report["routes"][0]["evidence"][0]["assumptions"], [ASSUMPTION])
        self.assertTrue(any(row["category"] == "warnings" for row in report["evidence_limits"]["omissions"]))

    def test_aggregate_assumption_survives_full_known_or_unknown_method_evidence_lists(self):
        for unresolved in (False, True):
            with self.subTest(unresolved=unresolved):
                builder = Builder("enhanced")
                for index in range(32):
                    if unresolved:
                        builder.unresolved_operation("/api/orders", "source_1", "line " + str(index + 1))
                    else:
                        builder.add("/api/orders", "GET", "javascript", "source_1", "line " + str(index + 1))
                if unresolved:
                    builder.unresolved_operation("/api/orders", "source_2", "line 1", assumption=ASSUMPTION)
                else:
                    builder.add("/api/orders", "GET", "javascript", "source_2", "line 1", assumption=ASSUMPTION)
                report = builder.finish()
                row = report["unresolved_operations"][0] if unresolved else report["routes"][0]
                self.assertEqual(len(row["evidence"]), 32)
                self.assertTrue(all(not item.get("assumptions") for item in row["evidence"]))
                self.assertEqual(row["assumptions"], [ASSUMPTION])
                self.assertEqual(len(assumption_warnings(report)), 1)
                category = "unresolved_evidence" if unresolved else "route_evidence"
                self.assertTrue(any(item["category"] == category for item in report["evidence_limits"]["omissions"]))


if __name__ == "__main__":
    unittest.main()
