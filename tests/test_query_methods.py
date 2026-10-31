import json
import unittest

from surface_map.analyze import analyze, safe_path, shape
from surface_map.export import compare, openapi
from surface_map.js_parser import available, close


class QueryAndMethodTests(unittest.TestCase):
    def tearDown(self):
        close()

    def test_repeated_query_keys_are_one_field_and_one_export_parameter(self):
        report = analyze([{'kind': 'html', 'text': '<a href="/api/items?id=1&amp;id=2">items</a>'}])
        self.assertEqual(report['routes'][0]['query_fields'], ['id'])
        self.assertEqual([p['name'] for p in openapi(report)['paths']['/api/items']['get']['parameters']], ['id'])
        self.assertEqual(safe_path('/api/items?private_label=1&private_label=2')[1], ['field_1'])

    def test_reordering_mixed_query_keys_does_not_change_capture(self):
        a = '<a href="/api/items?zz_private=canary_a&amp;page=1">items</a>'
        b = '<a href="/api/items?page=7&amp;zz_private=canary_b">items</a>'
        before, after = [analyze([{'kind': 'html', 'text': text}]) for text in (a, b)]
        self.assertEqual(before['routes'][0]['query_fields'], after['routes'][0]['query_fields'])
        self.assertEqual(compare(before, after)['changed'], [])
        self.assertNotIn('zz_private', json.dumps(before))
        self.assertNotIn('canary_a', json.dumps(before))

    def test_case_distinct_keys_remain_distinct_and_percent_encoded_duplicates_merge(self):
        self.assertEqual(safe_path('/api/items?id=1&ID=2')[1], ['id', 'id_2'])
        self.assertEqual(safe_path('/api/items?id=1&%69%64=2')[1], ['id'])
        self.assertEqual(safe_path('/api/items?x=1&X=2')[1], ['field_1', 'field_2'])

    def test_link_and_get_form_share_sorted_field_aliases(self):
        text = ('<a href="/api/items?zz_private=x&amp;id=1">items</a>'
                '<form action="/api/items"><input name="zz_private"><input name="id"></form>')
        report = analyze([{'kind': 'html', 'text': text}])
        self.assertEqual(len(report['routes']), 1)
        self.assertEqual(report['routes'][0]['query_fields'], ['field_2', 'id'])
        self.assertEqual({e['kind'] for e in report['routes'][0]['evidence']}, {'link', 'form'})
        self.assertNotIn('zz_private', json.dumps(report))

    def test_form_field_reordering_does_not_change_aliases_or_shape(self):
        for method in ('get', 'post'):
            with self.subTest(method=method):
                inputs = ('<input name="zz_private"><input name="id"><input name="ID">',
                          '<input name="ID"><input name="id"><input name="zz_private">')
                reports = [analyze([{'kind': 'html', 'text': '<form action="/api/items" method="' + method + '">' + controls + '</form>'}])
                           for controls in inputs]
                self.assertEqual(compare(*reports)['changed'], [])

    def test_form_mapping_preserves_retention_and_duplicate_limits(self):
        controls = ''.join('<input name="private_' + str(i) + '">' for i in range(32))
        controls += '<input name="private_0">' * 5 + '<input name="discarded_private">' * 2
        report = analyze([{'kind': 'html', 'text': '<form action="/api/items" method="post">' + controls + '</form>'}])
        fields = report['routes'][0]['request_shapes'][0]['properties']
        self.assertEqual(len(fields), 32)
        omissions = report['evidence_limits']['omissions']
        self.assertEqual([(o['category'], o['limit'], o['omitted_occurrences']) for o in omissions], [('form_fields', 32, 2)])
        self.assertNotIn('discarded_private', json.dumps(report))

    def test_query_pair_cap_applies_before_deduplication(self):
        self.assertEqual(safe_path('/api/items?' + '&'.join(['id=1'] * 64))[1], ['id'])
        self.assertIsNone(safe_path('/api/items?' + '&'.join(['id=1'] * 65)))

    def test_unknown_json_fields_keep_their_types_when_key_order_changes(self):
        a = {'z_private': {'x_private': 1, 'a_private': 'x'}, 'a_private': True, 'id': 3}
        b = {'id': 8, 'a_private': False, 'z_private': {'a_private': 'y', 'x_private': 2}}
        self.assertEqual(shape(a), shape(b))
        self.assertNotIn('private', json.dumps(shape(a)))
        self.assertEqual(shape(a)['properties']['field_1'], {'type': 'boolean'})
        self.assertEqual(shape(a)['properties']['field_3']['properties']['field_2'], {'type': 'integer'})

    def test_reordered_large_object_selects_same_bounded_fields(self):
        value = {f'private_{i:02d}': i if i % 2 else str(i) for i in range(40)}
        self.assertEqual(shape(value), shape(dict(reversed(list(value.items())))))
        self.assertEqual(len(shape(value)['properties']), 32)
        self.assertTrue(shape(value)['x-surface-map-truncated'])

    def test_basic_normalizes_only_fetch_standard_methods(self):
        cases = [('get', 'GET'), ('pOsT', 'POST'), ('put', 'PUT'), ('delete', 'DELETE'),
                 ('head', 'HEAD'), ('options', 'OPTIONS'), ('PATCH', 'PATCH'),
                 ('patch', None), ('Patch', None), ('poſt', None), ('CONNECT', None)]
        for wire, expected in cases:
            with self.subTest(wire=wire):
                report = analyze([{'kind': 'javascript', 'text': 'fetch("/api/orders",{method:' + json.dumps(wire, ensure_ascii=False) + '});'}], javascript_engine='basic')
                self.assertEqual([(r['method'], r['path']) for r in report['routes']],
                                 [] if expected is None else [(expected, '/api/orders')])
                self.assertEqual([r['path'] for r in report.get('unresolved_operations', [])],
                                 ['/api/orders'] if expected is None else [])

    @unittest.skipUnless(available()[0], 'optional JavaScript parser unavailable')
    def test_basic_and_enhanced_agree_on_literal_wire_methods(self):
        for method in ('post', 'PATCH', 'patch', 'poſt', 'CONNECT'):
            capture = [{'kind': 'javascript', 'text': 'fetch("/api/orders",{method:' + json.dumps(method, ensure_ascii=False) + '});'}]
            facts = []
            for engine in ('basic', 'enhanced'):
                report = analyze(capture, javascript_engine=engine)
                facts.append(([(r['method'], r['path']) for r in report['routes']],
                              [r['path'] for r in report.get('unresolved_operations', [])]))
            self.assertEqual(*facts)
