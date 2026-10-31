import unittest

from surface_map.analyze import analyze


def capture(text):
    return analyze([{'kind': 'html', 'text': text}])


class NestedFormTests(unittest.TestCase):
    def test_nested_action_is_ignored_and_inner_end_closes_outer_form(self):
        report = capture('<form action="/api/orders" method="post"><input name="id">'
                         '<form action="/api/users"><input name="email"></form>'
                         '<input name="token"></form>'
                         '<form action="/api/search"><input name="q"></form>')
        routes = {(row['method'], row['path']): row for row in report['routes']}
        self.assertEqual(set(routes), {('POST', '/api/orders'), ('GET', '/api/search')})
        self.assertEqual(set(routes['POST', '/api/orders']['request_shapes'][0]['properties']), {'id', 'email'})
        self.assertEqual(routes['GET', '/api/search']['query_fields'], ['q'])

    def test_unrepresented_outer_form_still_suppresses_nested_form(self):
        for outer in ('<form method="dialog" action="/api/items">', '<form>'):
            with self.subTest(outer=outer):
                report = capture(outer + '<form action="/api/orders"><input name="id"></form></form>')
                self.assertEqual(report['routes'], [])

    def test_ignored_nested_form_without_action_keeps_outer_fields(self):
        report = capture('<form action="/api/orders" method="post"><form><input name="id"></form></form>')
        self.assertEqual(len(report['routes']), 1)
        self.assertEqual(report['routes'][0]['path'], '/api/orders')
        self.assertEqual(set(report['routes'][0]['request_shapes'][0]['properties']), {'id'})

    def test_self_closing_syntax_does_not_create_a_nested_form(self):
        report = capture('<form action="/api/orders"><form action="/api/users"/><input name="id"></form>')
        self.assertEqual([(r['path'], r['query_fields']) for r in report['routes']], [('/api/orders', ['id'])])

    def test_separate_forms_keep_independent_fields(self):
        report = capture('<form action="/api/orders"><input name="id"></form>'
                         '<form action="/api/users"><input name="email"></form>')
        self.assertEqual([(r['path'], r['query_fields']) for r in report['routes']],
                         [('/api/orders', ['id']), ('/api/users', ['email'])])

    def test_template_controls_and_end_tags_do_not_close_or_modify_outer_form(self):
        report = capture('<form action="/api/orders" method="post"><input name="id">'
                         '<template><form action="/api/users"><input name="email"></form>'
                         '<template><input name="password"></template></template>'
                         '<input name="token"></form>')
        self.assertEqual([(r['method'], r['path']) for r in report['routes']], [('POST', '/api/orders')])
        self.assertEqual(set(report['routes'][0]['request_shapes'][0]['properties']), {'id', 'token'})
        self.assertTrue(any('Template form' in warning['reason'] for warning in report['warnings']))

    def test_standalone_template_form_is_omitted_but_declared_link_remains(self):
        report = capture('<template><form action="/api/orders"><input name="id"></form>'
                         '<a href="/api/users">users</a></template>')
        self.assertEqual([(r['method'], r['path']) for r in report['routes']], [('GET', '/api/users')])
        self.assertTrue(any('Template form' in warning['reason'] for warning in report['warnings']))

    def test_template_textarea_does_not_activate_markup_inside_it(self):
        report = capture('<form action="/api/orders"><template><textarea name="id">'
                         '</template><form action="/api/users"></form></textarea></template>'
                         '<input name="q"></form>')
        self.assertEqual([(r['path'], r['query_fields']) for r in report['routes']], [('/api/orders', ['q'])])
