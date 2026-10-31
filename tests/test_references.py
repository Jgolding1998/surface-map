import base64
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from surface_map.analyze import ArtifactError, SEGMENTS, analyze
from surface_map.references import _iter_module_specifiers, inventory_references


def script(text, kind='javascript'):
    return {'kind': kind, 'text': text}


def inventory(text, **kwargs):
    return inventory_references([script(text)], **kwargs)


class ReferenceTests(unittest.TestCase):
    def test_literals_remain_candidates_without_http_method_or_route_claims(self):
        result = inventory('const targets = ["/api/items", "/api/tasks?q=private-value", "/api/items"];')
        self.assertEqual([row['path'] for row in result['candidates']], ['/api/items', '/api/tasks'])
        for row in result['candidates']:
            self.assertIsNone(row['method'])
            self.assertEqual(row['confidence'], 'candidate')
            self.assertEqual(row['reason'], 'string_reference')
            self.assertNotIn('origin_relations', row)
        self.assertEqual(result['candidates'][1]['query_fields'], ['q'])
        self.assertNotIn('routes', result)
        self.assertNotIn('private-value', json.dumps(result))

    def test_comments_regex_templates_escaped_strings_jsx_and_nested_source_are_not_strings(self):
        texts = ['// "/api/orders"', '/* "/api/orders" */', r'const x=/"\/api\/orders"/;',
                 'const x=`/api/orders`;', r'const x="\/api\/orders";',
                 'const x=<p>"/api/orders"</p>;', 'const x=\'fetch("/api/orders")\';',
                 'const x="/api/orders\n', 'const x=`${"/api/orders"}`;']
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(inventory(text)['candidates'], [])

    def test_source_ids_lines_and_base64_are_preserved_without_input_mutation(self):
        encoded = base64.b64encode(b'\nconst value: string = "/api/items";').decode('ascii')
        artifacts = [{'kind': 'html', 'text': ''}, {'kind': 'typescript', 'text_b64': encoded}]
        before = deepcopy(artifacts)
        result = inventory_references(artifacts)
        self.assertEqual(artifacts, before)
        self.assertEqual(result['sources'], [{'id': 'source_2', 'kind': 'typescript'}])
        self.assertEqual(result['candidates'][0]['evidence'], [
            {'source': 'source_2', 'location': 'line 2', 'kind': 'javascript'}])

    def test_relative_candidates_need_context_and_base_cannot_be_overridden(self):
        text = 'const values=["./api/items", "../api/tasks", "api/users"];'
        result = inventory(text)
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['limits']['omitted_occurrences']['relative_strings_without_base'], 3)
        artifacts = [{'kind': 'html', 'text': '<base href="https://base-canary.invalid/public/">'}, script(text)]
        kwargs = {'document_url': 'https://document-canary.invalid/page'}
        result = inventory_references(artifacts, **kwargs)
        self.assertEqual([row['path'] for row in result['candidates']],
                         ['/public/api/items', '/api/tasks', '/public/api/users'])
        self.assertTrue(all(row['origin_relations'] == ['cross_origin'] for row in result['candidates']))
        self.assertEqual(result, inventory_references(artifacts, base_url='https://base-canary.invalid/public/', **kwargs))
        for value in ('https://override-canary.invalid/', 'https://user:password@base-canary.invalid/', None):
            with self.subTest(value=value), self.assertRaises(ArtifactError):
                inventory_references(artifacts, base_url=value, **kwargs)
        for canary in ('document-canary', 'base-canary', 'override-canary'):
            self.assertNotIn(canary, json.dumps(result))

    def test_unknown_base_does_not_fabricate_relative_targets(self):
        artifacts = [{'kind': 'html', 'text': '<base href="data:text/plain,x">'},
                     script('const x=["api/items", "/api/tasks"];')]
        result = inventory_references(artifacts, document_url='https://document-canary.invalid/page', base_url=None)
        self.assertEqual([row['path'] for row in result['candidates']], ['/api/tasks'])
        self.assertEqual(result['candidates'][0]['origin_relations'], ['unknown'])

    def test_private_hosts_values_names_and_credentials_are_not_returned(self):
        result = inventory('const x=["https://host-canary.invalid/api/secret-canary?private-field-canary=secret-value",'
                           '"https://user-canary:password-canary@host-canary.invalid/api/orders",'
                           '"file:///secret-canary", "data:text/plain,secret-canary", "///host-canary/api/items"];')
        self.assertEqual(len(result['candidates']), 1)
        row = result['candidates'][0]
        self.assertEqual(row['path'], '/api/{segment_2}')
        self.assertEqual(row['query_fields'], ['field_1'])
        for value in ('host-canary', 'secret-canary', 'private-field-canary', 'secret-value', 'user-canary', 'password-canary'):
            self.assertNotIn(value, json.dumps(result))

    def test_static_and_dynamic_literal_module_references_are_separate_and_opaque(self):
        text = '\n'.join(['import "./file-canary.js";', 'import name from "@package-canary/internal";',
                          'import * as ns from "../another-canary.js";', 'import {a as b,c} from "/module-canary.js";',
                          'export {a as b} from "https://host-canary.invalid/lib-canary.js";',
                          'export * from "other-package-canary";', 'const x=import("./dynamic-canary.js");'])
        result = inventory(text)
        self.assertEqual(result['candidates'], [])
        self.assertEqual(len(result['module_references']), 7)
        self.assertEqual([row['relation'] for row in result['module_references']],
                         ['static_import'] * 4 + ['static_reexport'] * 2 + ['dynamic_import_literal'])
        self.assertEqual(result['module_references'][1]['specifier_kind'], 'package')
        self.assertEqual([row['id'] for row in result['module_references']], [f'module_{n}' for n in range(1, 8)])
        for value in ('file-canary', 'package-canary', 'another-canary', 'module-canary', 'host-canary', 'lib-canary', 'dynamic-canary'):
            self.assertNotIn(value, json.dumps(result))
        self.assertEqual(list(_iter_module_specifiers('import x from "./private.js";')),
                         [('./private.js', 1, 'static_import')])

    def test_dynamic_expressions_properties_comments_and_require_are_not_module_edges(self):
        texts = ['import("./file.js" + suffix);', 'import(name);', 'import(`./file.js`);',
                 'import("./file.js", options);', 'loader.import("./file.js");',
                 'loader?.import("./file.js");', 'new importer.import("./file.js");',
                 'require("./file.js");', 'function require(x){}; require("./file.js");',
                 '// import x from "./file.js";', 'const text=\'import x from "./file.js"\';',
                 'const obj = {import: "./file.js"};', 'const bad =\nimport x from "./file.js";',
                 'import thing + other from "./file.js";', 'import "./file.js" + suffix;',
                 r'import "./escaped\x2ejs";', 'function f(){import x from "./file.js";}']
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(inventory(text)['module_references'], [])

    def test_type_only_import_is_a_syntax_reference_not_a_runtime_claim(self):
        result = inventory_references([script('import type {Thing} from "./private-types.ts";', 'typescript')])
        self.assertEqual(len(result['module_references']), 1)
        self.assertEqual(result['module_references'][0]['confidence'], 'candidate')

    def test_candidate_module_and_evidence_caps_count_discarded_occurrences(self):
        paths = [f'/{a}/{b}' for a in sorted(SEGMENTS) if '.' not in a for b in sorted(SEGMENTS) if '.' not in b][:105]
        result = inventory('\n'.join(f'const x{i}="{path}";' for i, path in enumerate(paths)))
        self.assertEqual(len(result['candidates']), 100)
        self.assertEqual(result['limits']['omitted_occurrences']['candidate_rows'], 5)
        result = inventory('\n'.join(f'import "./private-{i}.js";' for i in range(105)))
        self.assertEqual(len(result['module_references']), 100)
        self.assertEqual(result['limits']['omitted_occurrences']['module_rows'], 5)
        result = inventory('\n'.join(['"/api/items";'] * 20 + ['import "./same.js";'] * 20))
        self.assertEqual(len(result['candidates'][0]['evidence']), 16)
        self.assertEqual(len(result['module_references'][0]['evidence']), 16)
        self.assertEqual(result['limits']['omitted_occurrences']['candidate_evidence'], 4)
        self.assertEqual(result['limits']['omitted_occurrences']['module_evidence'], 4)
        self.assertTrue(result['limits']['truncated'])

    def test_origin_aggregate_survives_evidence_cap(self):
        text = '\n'.join(['"/api/items";'] * 16 + ['"https://external-canary.invalid/api/items";'])
        result = inventory_references([{'kind': 'html', 'text': ''}, script(text)],
                                      document_url='https://document-canary.invalid/page')
        self.assertEqual(result['candidates'][0]['origin_relations'], ['cross_origin', 'same_origin'])
        self.assertEqual(len(result['candidates'][0]['evidence']), 16)
        self.assertNotIn('canary', json.dumps(result))

    def test_strict_input_validation_and_deadline(self):
        for artifacts in ([], [script('')] * 7, [script('x' * (512 * 1024 + 1))],
                          [{'kind': 'javascript', 'text': '\ud800'}], [{'kind': 'javascript', 'text_b64': '%%%'}]):
            with self.subTest(artifacts_type=type(artifacts)), self.assertRaises(ArtifactError):
                inventory_references(artifacts)
        for deadline in (-1, float('nan'), float('inf'), 'not-time', True):
            with self.subTest(deadline=deadline), self.assertRaises(ArtifactError):
                inventory('"/api/items";', deadline=deadline)
        with patch('surface_map.references.time.monotonic', side_effect=[0, 0, 9]), self.assertRaises(ArtifactError):
            inventory('"/api/items";')
        with self.assertRaises(ArtifactError):
            inventory('"/api/items";', base_url='https://override.invalid/')

    def test_default_analysis_has_no_reference_inventory(self):
        report = analyze([script('const path="/api/items";')], javascript_engine='basic')
        self.assertNotIn('references', report)
        self.assertNotIn('candidates', report)
        self.assertNotIn('module_references', report)
