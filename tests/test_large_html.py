import base64
import json
import unittest
from unittest.mock import patch

from surface_map.analyze import ArtifactError, MAX_FILE_BYTES, MAX_HTML_BYTES, MAX_TOTAL_BYTES, analyze
from surface_map.server import MAX_REQUEST, MAX_SOURCE_REQUEST


def marketing_page():
    padding = 'const text="do not execute"; ' * 50000
    return ('<html><body><a href="/api/users">People</a><svg><path d="' + 'M0 0 ' * 5600
            + '"/></svg><script>' + padding + 'fetch("/api/admin");</script>'
            + '<script type="application/json">{"example":"embedded data"}</script>'
            + '<iframe src="/forms/register"></iframe><a href="/api/orders">Orders</a></body></html>')


class LargeHTMLTests(unittest.TestCase):
    def test_full_marketing_page_keeps_tail_evidence_and_ignores_script_code(self):
        page = marketing_page()
        self.assertGreater(len(page.encode()), 1024 * 1024)
        report = analyze([{'kind': 'html', 'text': page}], javascript_engine='basic')
        self.assertEqual({row['path'] for row in report['routes']}, {'/api/users', '/api/orders'})
        self.assertEqual(report['sources'][0]['bytes'], len(page.encode()))
        self.assertEqual(report['assets'][0]['kind'], 'embedded-document')
        self.assertTrue(any('Inline scripts' in str(item) for item in report['warnings']))
        self.assertTrue(any('Embedded document' in str(item) for item in report['warnings']))

    def test_base64_has_the_same_evidence_without_json_quote_expansion(self):
        page = marketing_page()
        plain = [{'kind': 'html', 'text': page}]
        encoded = [{'kind': 'html', 'text_b64': base64.b64encode(page.encode()).decode()}]
        self.assertEqual(analyze(plain, javascript_engine='basic'), analyze(encoded, javascript_engine='basic'))
        self.assertLess(len(json.dumps({'baseline': encoded, 'candidate': encoded}).encode()), MAX_REQUEST)

    def test_utf8_byte_boundary_and_non_html_cap_remain_enforced(self):
        page = 'é' * (MAX_HTML_BYTES // 2)
        self.assertEqual(analyze([{'kind': 'html', 'text': page}])['sources'][0]['bytes'], MAX_HTML_BYTES)
        with self.assertRaises(ArtifactError) as error:
            analyze([{'kind': 'html', 'text': page + 'é'}])
        self.assertIn(f'{MAX_HTML_BYTES:,}', str(error.exception))
        with self.assertRaises(ArtifactError):
            analyze([{'kind': 'javascript', 'text': ' ' * (MAX_FILE_BYTES + 1)}])
        with self.assertRaises(ArtifactError) as error:
            analyze([{'kind': 'html', 'text': page}, {'kind': 'html', 'text': 'x'}])
        self.assertIn('combined limit', str(error.exception))

    def test_encoded_input_is_exclusive_strict_bounded_and_utf8(self):
        invalid = [
            {'kind': 'html', 'text': '', 'text_b64': ''},
            {'kind': 'html', 'text_b64': None},
            {'kind': 'html', 'text_b64': '@not-valid'},
            {'kind': 'html', 'text_b64': base64.b64encode(b'\xff').decode()},
            {'kind': 'html', 'text_b64': 'x' * (4 * ((MAX_HTML_BYTES + 2) // 3) + 1)},
        ]
        for artifact in invalid:
            with self.subTest(case=type(artifact.get('text_b64')).__name__), self.assertRaises(ArtifactError):
                analyze([artifact])

    def test_html_work_deadline_still_rejects_the_whole_artifact(self):
        with patch('surface_map.analyze.time.monotonic', side_effect=[0, 4]):
            with self.assertRaises(ArtifactError) as error:
                analyze([{'kind': 'html', 'text': '<a href="/api/users">People</a>'}])
        self.assertIn('deadline', str(error.exception))

    def test_transport_covers_base64_comparison_without_raising_source_budget(self):
        max_encoded_capture = 4 * ((MAX_TOTAL_BYTES + 2) // 3) + 6 * 128
        self.assertLess(2 * max_encoded_capture, MAX_REQUEST)
        self.assertEqual(MAX_SOURCE_REQUEST, 3 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
