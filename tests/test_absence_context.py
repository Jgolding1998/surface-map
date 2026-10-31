import unittest

from surface_map.analyze import analyze
from surface_map.export import compare
from surface_map.js_parser import available, close


def html(text):
    return analyze([{'kind': 'html', 'text': text}], javascript_engine='basic')


class AbsenceContextTests(unittest.TestCase):
    def test_missing_source_kind_is_different_from_matching_capture_without_route(self):
        before = html('<a href="/api/items">x</a>')
        after = analyze([{'kind': 'javascript', 'text': 'const x = 1;'}], javascript_engine='basic')
        missing = compare(before, after)['removed'][0]
        self.assertIn('prior_source_kind_not_supplied', missing['absence_context'])
        matching = compare(before, html(''))['removed'][0]
        self.assertEqual(matching['absence_context'], ['not_seen_in_supplied_capture'])
        self.assertIn('does not prove', compare(before, after)['note'])

    def test_method_uncertainty_is_not_silently_treated_as_service_removal(self):
        before = html('<a href="/api/items">x</a>')
        after = html('')
        after['unresolved_operations'] = [{'path': '/api/items', 'method': None, 'query_fields': [], 'evidence': []}]
        diff = compare(before, after)
        self.assertIn('same_path_unresolved_after', diff['removed'][0]['absence_context'])
        self.assertEqual(len(diff['unresolved_added']), 1)

    def test_changed_engine_and_omitted_scripts_remain_visible(self):
        before = analyze([{'kind': 'javascript', 'text': 'fetch("/api/items");'}], javascript_engine='basic')
        after = html('')
        after['javascript_analysis'] = [{'source': 'source_2', 'engine': 'enhanced'}]
        after['script_omissions'] = [{'source': 'source_2', 'code': 'unsupported_or_limit'}]
        after['warnings'] = [{'source': 'source_2', 'reason': 'An input was omitted'}]
        context = compare(before, after)['removed'][0]['absence_context']
        self.assertIn('javascript_engine_changed', context)
        self.assertIn('scripts_omitted_from_analysis', context)
        self.assertEqual(compare(before, after)['after_coverage']['retained_warning_count'], 1)

    def test_retention_limit_is_context_not_confirmed_cause(self):
        before = html('<a href="/api/users">x</a>')
        after = html('\n'.join(['<a href="/api/items">x</a>'] * 40))
        diff = compare(before, after)
        self.assertNotIn('evidence_retention_limited', diff['removed'][0]['absence_context'])
        self.assertTrue(diff['after_coverage']['evidence_retention_limited'])
        self.assertTrue(diff['evidence_limits']['after']['truncated'])

    def test_added_and_changed_rows_distinguish_new_evidence_kind(self):
        script = analyze([{'kind': 'javascript', 'text': 'fetch("/api/items");'}], javascript_engine='basic')
        before = html('')
        self.assertIn('source_kind_not_supplied_before', compare(before, script)['added'][0]['presence_context'])
        declared = html('<a href="/api/items">x</a>')
        context = compare(script, declared)['changed'][0]['context']
        self.assertIn('prior_source_kind_not_supplied', context)
        self.assertIn('source_kind_not_supplied_before', context)

    def test_script_omissions_do_not_explain_missing_html_routes(self):
        before = html('<a href="/api/items">x</a>')
        after = html('')
        after['script_omissions'] = [{'source': 'source_2', 'code': 'unsupported_or_limit'}]
        result = compare(before, after)
        self.assertEqual(result['removed'][0]['absence_context'], ['not_seen_in_supplied_capture'])
        self.assertEqual(result['after_coverage']['omitted_script_count'], 1)

    @unittest.skipUnless(available()[0], 'optional JavaScript parser unavailable')
    def test_real_engine_and_omission_reports_explain_added_and_changed_evidence(self):
        try:
            capture = [{'kind': 'javascript', 'text': 'window.fetch("/api/items");'}]
            before = analyze(capture, javascript_engine='basic')
            after = analyze(capture, javascript_engine='enhanced')
            self.assertIn('javascript_engine_changed', compare(before, after)['added'][0]['presence_context'])
            before = analyze([{'kind': 'html', 'text': ''}, {'kind': 'javascript', 'text': 'function {'}],
                             javascript_engine='enhanced', tolerate_script_errors=True)
            self.assertTrue(before['script_omissions'])
            self.assertIn('scripts_omitted_before', compare(before, after)['added'][0]['presence_context'])
            before = analyze([{'kind': 'javascript', 'text': 'fetch("/api/items?q=1");'}], javascript_engine='basic')
            after = analyze([{'kind': 'javascript', 'text': 'fetch("/api/items?page=2");'}], javascript_engine='enhanced')
            self.assertIn('javascript_engine_changed', compare(before, after)['changed'][0]['context'])
        finally:
            close()
