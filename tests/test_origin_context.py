import json
import unittest

from surface_map.analyze import analyze


DOCUMENT = 'https://document-canary.invalid/page'


def capture(html, *scripts, document=DOCUMENT):
    return analyze([{'kind': 'html', 'text': html},
                    *({'kind': 'javascript', 'text': value} for value in scripts)],
                   javascript_engine='basic', document_url=document)


class OriginContextTests(unittest.TestCase):
    def test_same_path_keeps_both_origin_relations_without_hosts(self):
        report = capture('<a href="/api/items">Local</a><a href="https://external-canary.invalid/api/items">External</a>')
        self.assertEqual(len(report['routes']), 1)
        route = report['routes'][0]
        self.assertEqual(route['origin_relations'], ['cross_origin', 'same_origin'])
        self.assertEqual({row['origin_relation'] for row in route['evidence']}, {'same_origin', 'cross_origin'})
        self.assertEqual(len(route['evidence']), 2)
        self.assertNotIn('document-canary', json.dumps(report))
        self.assertNotIn('external-canary', json.dumps(report))

    def test_cross_origin_base_applies_to_root_and_relative_refs_but_not_empty_form(self):
        report = capture('<base href="https://base-canary.invalid/v1/">'
                         '<a href="/api/items">root</a><a href="orders">relative</a>'
                         '<form action="/api/users" method="post"></form><form method="post"></form>')
        routes = {(row['method'], row['path']): row for row in report['routes']}
        for key in [('GET', '/api/items'), ('GET', '/v1/orders'), ('POST', '/api/users')]:
            self.assertEqual(routes[key]['origin_relations'], ['cross_origin'])
        self.assertEqual(routes['POST', '/{segment_1}']['origin_relations'], ['same_origin'])
        self.assertNotIn('base-canary', json.dumps(report))

    def test_network_relative_scheme_ports_and_host_case_are_classified(self):
        for target, document, expected in [
            ('//DOCUMENT-CANARY.invalid:443/api/items', DOCUMENT, 'same_origin'),
            ('https://DOCUMENT-CANARY.invalid/api/items', DOCUMENT, 'same_origin'),
            ('https://document-canary.invalid:444/api/items', DOCUMENT, 'cross_origin'),
            ('http://document-canary.invalid/api/items', DOCUMENT, 'cross_origin'),
            ('//document-canary.invalid:80/api/items', 'http://document-canary.invalid/page', 'same_origin'),
        ]:
            with self.subTest(target=target):
                route = capture('<a href="' + target + '">target</a>', document=document)['routes'][0]
                self.assertEqual(route['origin_relations'], [expected])

    def test_unknown_origin_does_not_drop_supported_minimized_path(self):
        for target in ('https://éxample.invalid/api/items', 'https:/api/items', '///external-canary.invalid/api/items'):
            with self.subTest(target=target):
                report = capture('<a href="' + target + '">target</a>')
                self.assertEqual(len(report['routes']), 1)
                self.assertEqual(report['routes'][0]['origin_relations'], ['unknown'])
                self.assertNotIn('external-canary', json.dumps(report))
                self.assertNotIn('éxample', json.dumps(report))

    def test_without_context_report_keeps_prior_optional_field_shape(self):
        report = capture('<a href="/api/items">x</a>', 'fetch("/api/tasks", options);', document=None)
        for row in [*report['routes'], *report['unresolved_operations']]:
            self.assertNotIn('origin_relations', row)
            self.assertTrue(all('origin_relation' not in evidence for evidence in row['evidence']))

    def test_origin_aggregates_survive_evidence_retention_caps(self):
        report = capture('\n'.join(['<a href="/api/items">x</a>'] * 32 +
                                   ['<a href="https://external-canary.invalid/api/items">x</a>']))
        route = report['routes'][0]
        self.assertEqual(len(route['evidence']), 32)
        self.assertEqual(route['origin_relations'], ['cross_origin', 'same_origin'])
        self.assertTrue(all(e['origin_relation'] == 'same_origin' for e in route['evidence']))
        self.assertTrue(report['evidence_limits']['truncated'])

    def test_unresolved_script_targets_keep_relative_and_cross_origin_context(self):
        report = capture('', 'fetch("/api/tasks", options);\nfetch("https://external-canary.invalid/api/tasks", options);')
        target = report['unresolved_operations'][0]
        self.assertEqual(target['origin_relations'], ['cross_origin', 'same_origin'])
        self.assertEqual({row['origin_relation'] for row in target['evidence']}, {'same_origin', 'cross_origin'})
        self.assertNotIn('external-canary', json.dumps(report))

    def test_path_case_does_not_change_origin_comparison(self):
        report = capture('<a href="https://DOCUMENT-CANARY.invalid/API/items">x</a>')
        self.assertEqual(report['routes'][0]['path'], '/{segment_1}/items')
        self.assertEqual(report['routes'][0]['origin_relations'], ['same_origin'])
