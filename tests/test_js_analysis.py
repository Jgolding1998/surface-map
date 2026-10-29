import json
import unittest
from unittest import mock

from surface_map import js_parser
from surface_map.analyze import ArtifactError, analyze
from surface_map.export import compare
from surface_map.server import bundle
from surface_map.source_io import inspect_sources


READY = js_parser.available()[0]


def report(text, kind="javascript"):
    return analyze([{"kind": kind, "text": text}], javascript_engine="enhanced")


def facts(text, kind="javascript"):
    value = report(text, kind)
    return {(row["method"], row["path"]) for row in value["routes"] + value.get("unresolved_operations", [])}


@unittest.skipUnless(READY, "Explicit optional parser installation and supported Node required")
class EnhancedAnalysisTests(unittest.TestCase):
    def test_closures_aliases_templates_and_computed_methods(self):
        examples = [
            ('const path="/api/orders"; function run(){fetch(path)}', "GET", "/api/orders"),
            ('const {fetch: send}=globalThis; send(`/api/${"users"}`, {method:"POST"})', "POST", "/api/users"),
            ('const send=window.fetch.bind(window); send("/api/items")', "GET", "/api/items"),
            ('const config={method:"PATCH"}; fetch("/api/orders", {...config})', "PATCH", "/api/orders"),
            ('fetch("/api/".concat("users"), {["method"]:"DELETE"})', "DELETE", "/api/users"),
        ]
        for text, method, path in examples:
            with self.subTest(text=text):
                self.assertEqual(facts(text), {(method, path)})

    def test_scopes_do_not_disable_unrelated_global_calls(self):
        self.assertEqual(facts('function run(fetch){fetch("/api/users")} fetch("/api/orders")'), {("GET", "/api/orders")})
        self.assertEqual(facts('export {}; {let fetch=mock; fetch("/api/users")} fetch("/api/orders")'), {("GET", "/api/orders")})

    def test_shadowed_and_reassigned_clients_are_not_network_evidence(self):
        for text in [
            'fetch("/api/orders"); var fetch=mock;',
            'function run(fetch){fetch("/api/orders")}',
            'let target="/api/orders"; target=external; fetch(target)',
            'const f=fetch; f=mock; f("/api/orders")',
            'window.fetch=mock; window.fetch("/api/orders")',
            'function run(){eval(code); fetch("/api/orders")}',
            'with(scope){fetch("/api/orders")}',
            'const view=<p>fetch("/api/orders")</p>;',
            'import fetch = mock.fetch; fetch("/api/orders")',
        ]:
            with self.subTest(text=text):
                self.assertEqual(facts(text, "typescript" if "import fetch =" in text else "javascript"), set())

    def test_unknown_methods_survive_without_fabricated_openapi_get(self):
        value = report('fetch("/api/orders", {method: chosen})')
        self.assertEqual(value["routes"], [])
        self.assertEqual(value["unresolved_operations"][0]["method"], None)
        exports = bundle(value)
        self.assertEqual(exports["openapi"]["paths"], {})
        self.assertEqual(exports["contract"]["unresolved_operations"][0]["path"], "/api/orders")
        difference = compare(report('fetch("/api/users", {method: chosen})'), value)
        self.assertEqual(difference["unresolved_added"][0]["path"], "/api/orders")

    def test_spread_order_and_mutation_invalidate_method(self):
        for text in [
            'fetch("/api/orders", {method:"POST", ...options})',
            'const opts={method:"POST"}; opts.method=chosen; fetch("/api/orders",opts)',
            'const opts={method:"POST"}; mutate(opts); fetch("/api/orders",opts)',
            'const opts={method:"POST", change(){}}; opts.change(); fetch("/api/orders",opts)',
        ]:
            with self.subTest(text=text):
                self.assertEqual(facts(text), {(None, "/api/orders")})
        self.assertEqual(facts('fetch("/api/orders", {...options, method:"POST"})'), {("POST", "/api/orders")})

    def test_request_method_is_inherited_when_override_absent(self):
        for options in ("", ", {}", ", undefined", ", null"):
            self.assertEqual(facts('const req=new Request("/api/orders",{method:"DELETE"}); fetch(req' + options + ')'), {("DELETE", "/api/orders")})
        self.assertEqual(facts('const req=new Request("/api/orders",{method:"POST"}); fetch(new Request(req,{}))'), {("POST", "/api/orders")})

    def test_destructured_defaults_do_not_turn_explicit_methods_into_get(self):
        for binding in ('const {method="POST"}={};', 'const [method="POST"]=[];', 'const {nested:{method="POST"}={}}={};'):
            self.assertEqual(facts(binding + ' fetch("/api/items",{method})'), {("POST", "/api/items")})
        self.assertEqual(facts('const {method="POST"}={method:chosen}; fetch("/api/items",{method})'), {(None, "/api/items")})

    def test_mutated_or_escaped_url_is_not_a_stale_target(self):
        for change in ('target.pathname="/admin";', 'change(target);', 'target.searchParams.delete("q");'):
            self.assertEqual(facts('const target=new URL("https://example.invalid/api/items"); ' + change + ' fetch(target);'), set())
            self.assertEqual(facts('const target=new URL("https://example.invalid/api/items"); ' + change + ' fetch(target.href);'), set())

    def test_xhr_and_imported_fetch(self):
        self.assertEqual(facts('const req=new XMLHttpRequest(); req.open("POST","/api/orders"); req.send(body)'), {("POST", "/api/orders")})
        self.assertEqual(facts('import {fetch as send} from "undici"; send("/api/orders")'), {("GET", "/api/orders")})

    def test_axios_base_paths_overrides_and_inherited_defaults(self):
        prefix = 'import axios from "axios"; '
        cases = [
            ('const c=axios.create({baseURL:"https://example.invalid/api"}); c.get("/orders")', "GET", "/api/orders"),
            ('const c=axios.create({baseURL:"/api",method:"POST"}); c.request({url:"/orders"})', "POST", "/api/orders"),
            ('const a=axios.create({baseURL:"/api"}); const b=a.create({}); b.get("/orders")', "GET", "/api/orders"),
            ('const c=axios.create({baseURL:"/api"}); c.get("/orders",{baseURL:"/v1"})', "GET", "/v1/orders"),
            ('const c=axios.create({baseURL:"/api"}); c.get("https://example.invalid/users")', "GET", "/users"),
        ]
        for source, method, path in cases:
            with self.subTest(source=source):
                self.assertEqual(facts(prefix + source), {(method, path)})
        self.assertEqual(facts(prefix + 'const c=axios.create({baseURL:external}); c.get("/orders")'), set())
        self.assertEqual(facts(prefix + 'const c=axios.create({baseURL:"/api"}); c.defaults.baseURL=external; c.get("/orders")'), set())
        self.assertEqual(facts(prefix + 'const c=axios.create({baseURL:"/api"}); c.interceptors.request.use(change); c.get("/orders")'), set())
        self.assertEqual(facts(prefix + 'axios.defaults.baseURL="/api"; const c=axios.create({}); c.get("/orders")'), set())
        for change in ('Object.assign(c.defaults,{baseURL:"/admin"});', 'const defaults=c.defaults; Object.assign(defaults,{baseURL:"/admin"});', 'const defaults=c.defaults; defaults.baseURL="/admin";'):
            self.assertEqual(facts(prefix + 'const c=axios.create({baseURL:"/api"}); ' + change + ' c.get("/users");'), set())

    def test_absolute_url_policy_does_not_ignore_false_or_unknown(self):
        prefix = 'import axios from "axios"; '
        value = facts(prefix + 'const c=axios.create({baseURL:"/api",allowAbsoluteUrls:false}); c.get("https://example.invalid/users")')
        self.assertNotIn(("GET", "/users"), value)
        self.assertTrue(value)
        self.assertEqual(facts(prefix + 'const c=axios.create({baseURL:"/api",allowAbsoluteUrls:chosen}); c.get("https://example.invalid/users")'), set())

    def test_runtime_typescript_and_jsx_remain_scoped(self):
        self.assertEqual(facts('const route:string="/api/orders"; fetch(route as string)', "typescript"), {("GET", "/api/orders")})
        self.assertEqual(facts('const view=<p>{fetch("/api/orders")}</p>'), {("GET", "/api/orders")})

    def test_curated_imports_include_dynamic_and_exclude_shadowed_require(self):
        def libraries(text, kind="javascript"):
            value = inspect_sources([{"kind": kind, "content": text}], javascript_engine="enhanced")
            self.assertEqual(value["sources"][0]["engine"], "babel-8.0.4")
            return {item["name"] for item in value["dependencies"]}
        self.assertEqual(libraries('import("react"); const other=require("axios")'), {"react", "axios"})
        self.assertEqual(libraries('function run(require){require("react")}'), set())
        self.assertEqual(libraries('require=mock; require("react")'), set())
        self.assertEqual(libraries('eval(code); require("react")'), set())
        self.assertEqual(libraries('with(scope){require("react")}'), set())
        self.assertEqual(libraries('import c = require("axios");', "typescript"), {"axios"})

    def test_import_evidence_cap_rejects_instead_of_silently_losing_dependencies(self):
        text = 'import "react";\n' * 801
        inventory = inspect_sources([{"kind": "javascript", "content": text}], javascript_engine="enhanced")
        self.assertEqual(inventory["sources"][0]["status"], "rejected")
        self.assertEqual(inventory["dependencies"], [])
        self.assertTrue(inventory["warnings"])

    def test_exports_redact_raw_ast_url_and_body_values(self):
        marker = "PRIVATE" + "_AST_VALUE_789432"
        text = 'fetch("https://' + marker + '.invalid/api/orders?token=' + marker + '",{method:"POST",body:JSON.stringify({password:"' + marker + '"})})'
        exports = bundle(report(text))
        self.assertNotIn(marker, json.dumps(exports))
        self.assertNotIn("nodes", json.dumps(exports))
        self.assertEqual(exports["report"]["routes"][0]["request_shapes"], [{"type": "object", "properties": {"password": {"type": "string"}}}])

    def test_syntax_failure_is_fixed_and_does_not_echo_source(self):
        marker = "PRIVATE" + "_BAD_AST_5531"
        with self.assertRaises(ArtifactError) as caught:
            report('const ' + marker + ' = ;')
        self.assertNotIn(marker, str(caught.exception))
        self.assertIn("syntax", str(caught.exception))

    def test_array_mutation_spreads_and_noncanonical_indices_are_not_stale_targets(self):
        for source in [
            'const a=["/api/orders"]; a[0]="/api/users"; fetch(a[0]);',
            'const a=["/api/orders"]; fetch(a["00"]);',
            'const a=[...list,"/api/orders"]; fetch(a[1]);',
            'const a=[,"/api/orders"]; fetch(a[0]);',
        ]:
            self.assertEqual(facts(source), set())

    def test_unresolved_aliases_and_constructor_escapes_invalidate_options(self):
        prefix = 'const opts={method:"POST"}; '
        for change in [
            'let alias; alias=opts; alias.method="DELETE";',
            'const list=[opts]; list[index].method="DELETE";',
            'function get(){return opts} get().method="DELETE";',
            'new Configurer(opts);',
            'const get=()=>opts; get().method="DELETE";',
        ]:
            self.assertEqual(facts(prefix + change + ' fetch("/api/orders",opts)'), {(None, "/api/orders")})

    def test_aliased_interceptor_and_wrong_default_module_are_not_network_calls(self):
        self.assertEqual(facts('import axios from "axios"; const c=axios.create({baseURL:"/api"}); const q=c.interceptors.request; q.use(change); c.get("/orders");'), set())
        self.assertEqual(facts('import client from "undici"; client("/api/orders")'), set())
        self.assertEqual(facts('const client=require("undici"); client.fetch("https://example.invalid/api/orders")'), {("GET", "/api/orders")})

    def test_wire_methods_do_not_apply_python_unicode_or_patch_normalization(self):
        for method, expected in [("post", "POST"), ("PATCH", "PATCH"), ("patch", None), ("poſt", None)]:
            self.assertEqual(facts('fetch("/api/orders",{method:' + json.dumps(method) + '})'), {(expected, "/api/orders")})
            self.assertEqual(facts('const r=new XMLHttpRequest(); r.open(' + json.dumps(method) + ',"/api/orders")'), {(expected, "/api/orders")})

    def test_pattern_writes_type_wrappers_and_loops_invalidate_options(self):
        for change in ('[opts.method]=values;', '({value:opts.method}=values);', 'for(opts.method of values){}'):
            self.assertEqual(facts('const opts={method:"POST"}; ' + change + ' fetch("/api/orders",opts)'), {(None, "/api/orders")})
        self.assertEqual(facts('let target="/api/orders"; (target as string)=external; fetch(target)', "typescript"), set())

    def test_runtime_namespace_class_and_static_block_scopes(self):
        self.assertEqual(facts('namespace N { const target="/api/orders"; } fetch(target)', "typescript"), set())
        self.assertEqual(facts('const C=class fetch { static run(){fetch("/api/orders")} }'), set())
        self.assertEqual(facts('class C { static {var fetch=mock;} } fetch("/api/orders")'), {("GET", "/api/orders")})

    def test_builtin_patches_and_indirect_eval_disable_uncertain_calls(self):
        for prefix in ('XMLHttpRequest.prototype.open=mock;', 'global.fetch=mock;', '(0,eval)(code);', 'new Function(code);'):
            source = prefix + 'const r=new XMLHttpRequest(); r.open("GET","/api/orders")' if prefix.startswith("XML") else prefix + 'fetch("/api/orders")'
            self.assertEqual(facts(source), set())
        self.assertEqual(facts('Object.prototype.method="POST"; fetch("/api/orders",{})'), {(None, "/api/orders")})

    def test_argument_spreads_and_detached_xhr_are_not_assumed_calls(self):
        self.assertEqual(facts('const r=new XMLHttpRequest(); r.open(...["POST","/api/users"],"/api/orders")'), set())
        self.assertEqual(facts('const r=new XMLHttpRequest(); const open=r.open; open("GET","/api/orders")'), set())

    def test_string_expansion_is_bounded_before_join(self):
        source = 'const part="' + "x" * 4096 + '"; fetch("".concat(' + ','.join(['part'] * 12000) + '));'
        self.assertEqual(facts(source), set())

    def test_curated_subpath_imports_do_not_export_private_specifiers(self):
        marker = "PRIVATE" + "_MODULE_8842"
        inventory = inspect_sources([{"kind": "javascript", "content": 'import "react/jsx-runtime"; import "./' + marker + '"; import("https://example.invalid/' + marker + '");'}], javascript_engine="enhanced")
        self.assertEqual([row["name"] for row in inventory["dependencies"]], ["react"])
        self.assertNotIn(marker, json.dumps(inventory))

    def test_unrelated_builtin_calls_and_navigation_do_not_erase_routes(self):
        for prefix in ('window.addEventListener("load",init);', 'window.location.href="/login";', 'Object.keys(x);'):
            self.assertEqual(facts(prefix + 'fetch("/api/orders",{headers:{}})'), {("GET", "/api/orders")})
        value = report('JSON.parse(text); fetch("/api/orders",{method:"POST",body:JSON.stringify({count:1})})')
        self.assertEqual(value["routes"][0]["request_shapes"], [{"type": "object", "properties": {"count": {"type": "integer"}}}])

    def test_nonrouting_client_configuration_does_not_erase_requests(self):
        prefix = 'import axios from "axios"; '
        for text in ('axios.get("/api/orders"); axios.isAxiosError(error);',
                     'axios.defaults.withCredentials=true; axios.get("/api/orders");',
                     'const c=axios.create({baseURL:"/api"}); c.interceptors.response.use(ok,retry); c.get("/orders");'):
            self.assertEqual(facts(prefix + text), {("GET", "/api/orders")})
        for change in ('r.onload=done;', 'r.upload.onprogress=progress;', 'r.upload.addEventListener("progress",done);'):
            self.assertEqual(facts('const r=new XMLHttpRequest();' + change + 'r.open("GET","/api/orders")'), {("GET", "/api/orders")})
        self.assertEqual(facts('const r=new XMLHttpRequest();r.upload.onprogress=done; register(r); r.open("GET","/api/orders")'), set())

    def test_composite_aliases_and_other_escape_sites_invalidate_options(self):
        for change in [
            'const o=flag||opts; o.method="DELETE";',
            'const o=flag?opts:other; o.method="DELETE";',
            'const b=[...[opts]]; b[0].method="DELETE";',
            'for(const o of [opts]) o.method="DELETE";',
            'function f(o=opts){o.method="DELETE"} f();',
            'class A { o=opts; m(){this.o.method="DELETE"} }',
            'function Form({options}){options.method="PUT"} const view=<Form options={opts}/>;',
        ]:
            self.assertEqual(facts('const opts={method:"POST"};' + change + 'fetch("/api/orders",opts)'), {(None, "/api/orders")})
        self.assertEqual(facts('const opts={method:"POST",get ready(){this.method="DELETE"; return 1}}; if(opts.ready)fetch("/api/orders",opts)'), {(None, "/api/orders")})

    def test_axios_default_url_is_not_coerced_to_undefined(self):
        prefix = 'import axios from "axios"; '
        self.assertEqual(facts(prefix + 'const c=axios.create({baseURL:"/api",url:"/orders"});c.request({method:"post"});'), {("POST", "/api")})
        self.assertEqual(facts(prefix + 'const c=axios.create({baseURL:"/api",url:"/orders"});c.get(undefined);'), {("GET", "/api")})
        self.assertEqual(facts(prefix + 'const c=axios.create({baseURL:"/api"});c.request({method:"post"});c.get(undefined);'), {("POST", "/api"), ("GET", "/api")})
        self.assertEqual(facts(prefix + 'const c=axios.create({baseURL:""});c.get("orders");'), set())
        self.assertEqual(facts(prefix + 'const a=axios.create({baseURL:"/api",method:"POST"});const b=a.create({});b.request({url:"/orders"});'), {("GET", "/api/orders")})

    def test_qualified_and_aliased_builtin_mutation_still_disables_clients(self):
        for prefix in ('window.XMLHttpRequest.prototype.open=hook;', 'const proto=XMLHttpRequest.prototype;proto.open=hook;'):
            self.assertEqual(facts(prefix + 'const r=new XMLHttpRequest();r.open("GET","/api/orders")'), set())
        self.assertEqual(facts('const g=typeof window==="object"?window:globalThis;g.fetch=stub;fetch("/api/orders")'), set())
        for prefix in ('Object.defineProperty(Object.prototype,"method",{value:"POST"});', '({}).__proto__.method="POST";', 'Object.getPrototypeOf({}).method="POST";'):
            self.assertEqual(facts(prefix + 'fetch("/api/orders",{})'), {(None, "/api/orders")})

    def test_readonly_prototype_idioms_do_not_pollute_new_options(self):
        for prefix in ('Object.prototype.hasOwnProperty.call(obj,"key");', 'Object.prototype.toString.call(obj);', 'Object.setPrototypeOf(obj,{method:"POST"});', 'class A {m(){return this.constructor.create()}}'):
            self.assertEqual(facts(prefix + 'fetch("/api/orders",{headers:{}})'), {("GET", "/api/orders")})
        self.assertEqual(facts('import axios from "axios";function inherits(a,b){Object.setPrototypeOf(a,b)}axios.get("/api/orders",{params:{q:1}})'), {("GET", "/api/orders")})

    def test_axios_url_object_is_not_the_string_overload(self):
        self.assertEqual(facts('import axios from "axios";axios(new URL("https://example.invalid/api/orders"))'), set())

    def test_fetch_module_exports_are_not_assumed_interchangeable(self):
        self.assertEqual(facts('import {fetch as send} from "node-fetch";send("https://example.invalid/api/orders")'), set())
        self.assertEqual(facts('import send from "node-fetch";send("https://example.invalid/api/orders")'), {("GET", "/api/orders")})

    def test_rest_and_default_alias_writes_escape_original_objects(self):
        for change in ('const {o=opts}=props;o.method="DELETE";', 'const {...rest}={o:opts};rest.o.method="DELETE";'):
            self.assertEqual(facts('const opts={method:"POST"};' + change + 'fetch("/api/orders",opts)'), {(None, "/api/orders")})
        self.assertEqual(facts('const opts={method:"POST"}; const holder={inner:opts,get ready(){this.inner.method="DELETE";return 1}};if(holder.ready)fetch("/api/orders",opts)'), {(None, "/api/orders")})
        self.assertEqual(facts('const opts={method:"POST"}; const view=<Form>{opts}</Form>;fetch("/api/orders",opts)'), {(None, "/api/orders")})

    def test_axios_string_config_mutation_cannot_create_stale_later_targets(self):
        self.assertEqual(facts('import axios from "axios";const cfg={url:"/api/users"};axios("/api/orders",cfg);axios(cfg)'), {("GET", "/api/orders")})


class EngineSelectionTests(unittest.TestCase):
    def test_explicit_basic_does_not_start_parser(self):
        with mock.patch.object(js_parser, "parse", side_effect=AssertionError("Parser must stay idle")):
            value = analyze([{"kind": "javascript", "text": 'fetch("/api/orders")'}], javascript_engine="basic")
        self.assertEqual(value["javascript_analysis"], [{"source": "source_1", "engine": "basic"}])

    def test_missing_parser_fallback_is_visible_and_required_mode_rejects(self):
        with mock.patch.object(js_parser, "available", return_value=(False, "parser_missing")):
            value = analyze([{"kind": "javascript", "text": 'fetch("/api/orders")'}])
            self.assertEqual(value["javascript_analysis"][0]["reason"], "parser_missing")
            self.assertTrue(value["warnings"])
            with self.assertRaises(ArtifactError):
                report('fetch("/api/orders")')

    def test_invalid_engine_does_not_silently_choose_another(self):
        for mode in ("other", None, []):
            with self.assertRaises(ArtifactError):
                analyze([{"kind": "javascript", "text": ""}], javascript_engine=mode)

    def test_parser_failure_does_not_become_weaker_successful_evidence(self):
        text = 'const invalid = ; fetch("/api/orders")'
        with mock.patch.object(js_parser, "available", return_value=(True, "ready")), mock.patch.object(js_parser, "parse", side_effect=js_parser.JsParserError("syntax")):
            with self.assertRaises(ArtifactError):
                analyze([{"kind": "javascript", "text": text}])
            inventory = inspect_sources([{"kind": "javascript", "content": text}])
            self.assertEqual(inventory["sources"][0]["status"], "rejected")
            self.assertEqual(inventory["dependencies"], [])


if __name__ == "__main__":
    unittest.main()
