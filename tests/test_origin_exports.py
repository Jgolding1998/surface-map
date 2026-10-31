from copy import deepcopy
import json
import unittest

from surface_map.analyze import analyze
from surface_map.export import compare, contract, openapi, openapi_routes


DOCUMENT = 'https://document-canary.invalid/page'


def capture(html, *scripts, document=DOCUMENT):
    return analyze([{'kind': 'html', 'text': html},
                    *({'kind': 'javascript', 'text': value} for value in scripts)],
                   javascript_engine='basic', document_url=document)


class OriginExportTests(unittest.TestCase):
    def test_canonical_merge_keeps_aggregate_after_cross_origin_evidence_was_capped(self):
        html = '<a href="/api/items/123">numeric</a>\n' + '\n'.join(
            ['<a href="/api/items/private-value">local</a>'] * 32 +
            ['<a href="https://external-canary.invalid/api/items/private-value">external</a>'])
        report = capture(html)
        before = deepcopy(report)
        routes = openapi_routes(report['routes'])
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]['path'], '/api/items/{segment_3}')
        self.assertEqual(routes[0]['origin_relations'], ['cross_origin', 'same_origin'])
        self.assertTrue(all(e['origin_relation'] == 'same_origin' for e in routes[0]['evidence']))
        exported = openapi(report)
        operation = exported['paths']['/api/items/{segment_3}']['get']
        self.assertEqual(operation['x-surface-map-origin-relations'], ['cross_origin', 'same_origin'])
        self.assertTrue(exported['x-surface-map-evidence-limits']['truncated'])
        self.assertEqual(report, before)
        for canary in ('document-canary', 'external-canary', 'private-value'):
            self.assertNotIn(canary, json.dumps(exported))

    def test_contract_preserves_origin_classes_and_unresolved_context(self):
        report = capture('<a href="https://external-canary.invalid/api/items">x</a>',
                         'fetch("/api/tasks", options);')
        result = contract(report)
        self.assertEqual(result['routes'][0]['origin_relations'], ['cross_origin'])
        self.assertEqual(result['unresolved_operations'][0]['origin_relations'], ['same_origin'])
        self.assertNotIn('external-canary', json.dumps(result))
        self.assertNotIn('document-canary', json.dumps(result))

    def test_origin_only_change_is_reported_with_both_bounded_classes(self):
        before = capture('<a href="/api/items">x</a>')
        after = capture('<a href="https://external-canary.invalid/api/items">x</a>')
        result = compare(before, after)
        self.assertFalse(result['added'])
        self.assertFalse(result['removed'])
        self.assertEqual(result['changed'][0]['changed_fields'], ['origin_relations'])
        self.assertEqual(result['changed'][0]['before_origin_relations'], ['same_origin'])
        self.assertEqual(result['changed'][0]['origin_relations'], ['cross_origin'])
        self.assertNotIn('external-canary', json.dumps(result))

    def test_context_presence_transitions_are_coverage_changes_only(self):
        html = '<a href="/api/items">x</a>'
        plain, contextual = capture(html, document=None), capture(html)
        self.assertNotIn('has_document_context', plain)
        self.assertIs(contextual['has_document_context'], True)
        for before, after, old_context, new_context in ((plain, contextual, False, True),
                                                      (contextual, plain, True, False)):
            with self.subTest(old_context=old_context):
                result = compare(before, after)
                self.assertEqual(result['changed'], [])
                self.assertEqual(result['added'], [])
                self.assertEqual(result['removed'], [])
                self.assertIs(result['before_coverage']['has_document_context'], old_context)
                self.assertIs(result['after_coverage']['has_document_context'], new_context)
        self.assertEqual(compare(plain, plain)['changed'], [])
        self.assertNotIn('origin_relations', contract(plain)['routes'][0])
        self.assertNotIn('x-surface-map-origin-relations', openapi(plain)['paths']['/api/items']['get'])

    def test_empty_capture_coverage_retains_document_context(self):
        plain, contextual = capture('', document=None), capture('')
        self.assertNotIn('has_document_context', plain)
        self.assertIs(contextual['has_document_context'], True)
        for before, after, old_context, new_context in ((plain, contextual, False, True),
                                                      (contextual, plain, True, False)):
            with self.subTest(old_context=old_context):
                result = compare(before, after)
                self.assertFalse(result['added'] or result['removed'] or result['changed'])
                self.assertIs(result['before_coverage']['has_document_context'], old_context)
                self.assertIs(result['after_coverage']['has_document_context'], new_context)

    def test_other_changes_retain_available_origin_labels(self):
        plain = capture('<a href="/api/items?q=1">x</a>', document=None)
        contextual = capture('<a href="/api/items?page=2">x</a>')
        for before, after, old_classes, new_classes in ((plain, contextual, None, ['same_origin']),
                                                      (contextual, plain, ['same_origin'], None)):
            with self.subTest(old=old_classes):
                change = compare(before, after)['changed'][0]
                self.assertEqual(change['changed_fields'], ['query_fields'])
                self.assertEqual(change['before_origin_relations'], old_classes)
                self.assertEqual(change['origin_relations'], new_classes)

    def test_added_and_removed_rows_keep_aggregate_when_examples_were_limited(self):
        empty = capture('')
        report = capture('\n'.join(['<a href="/api/items">x</a>'] * 32 +
                                   ['<a href="https://external-canary.invalid/api/items">x</a>']))
        for result, key in ((compare(empty, report), 'added'), (compare(report, empty), 'removed')):
            self.assertEqual(result[key][0]['origin_relations'], ['cross_origin', 'same_origin'])
            self.assertTrue(all(e['origin_relation'] == 'same_origin' for e in result[key][0]['evidence']))

    def test_unresolved_origin_change_requires_context_on_both_sides(self):
        script = 'fetch("/api/tasks", options);'
        before = capture('', script)
        after = capture('', 'fetch("https://external-canary.invalid/api/tasks", options);')
        change = compare(before, after)['unresolved_changed'][0]
        self.assertEqual(change['changed_fields'], ['origin_relations'])
        self.assertEqual(change['before_origin_relations'], ['same_origin'])
        self.assertEqual(change['origin_relations'], ['cross_origin'])
        plain = capture('', script, document=None)
        for old, new, old_context, new_context in ((before, plain, True, False),
                                                 (plain, before, False, True)):
            with self.subTest(old_context=old_context):
                result = compare(old, new)
                self.assertEqual(result['unresolved_changed'], [])
                self.assertEqual(result['unresolved_added'], [])
                self.assertEqual(result['unresolved_removed'], [])
                self.assertIs(result['before_coverage']['has_document_context'], old_context)
                self.assertIs(result['after_coverage']['has_document_context'], new_context)

    def test_other_unresolved_changes_retain_available_origin_labels(self):
        plain = capture('', 'fetch("/api/tasks?q=1", options);', document=None)
        contextual = capture('', 'fetch("/api/tasks?page=2", options);')
        for before, after, old_classes, new_classes in ((plain, contextual, None, ['same_origin']),
                                                      (contextual, plain, ['same_origin'], None)):
            with self.subTest(old=old_classes):
                change = compare(before, after)['unresolved_changed'][0]
                self.assertEqual(change['changed_fields'], ['query_fields'])
                self.assertEqual(change['before_origin_relations'], old_classes)
                self.assertEqual(change['origin_relations'], new_classes)

    def test_origin_class_order_is_not_a_capture_change(self):
        report = capture('<a href="/api/items">x</a><a href="https://external-canary.invalid/api/items">x</a>')
        other = deepcopy(report)
        other['routes'][0]['origin_relations'].reverse()
        self.assertEqual(compare(report, other)['changed'], [])
