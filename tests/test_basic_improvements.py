import unittest

from surface_map.analyze import analyze


def capture(text):
    return analyze([{'kind': 'javascript', 'text': text}], javascript_engine='basic')


class BasicImprovementTests(unittest.TestCase):
    def test_known_target_survives_dynamic_options_without_inventing_method(self):
        for options in ('options', 'getOptions()', '{method:chooseMethod()}', '{method:"POST",...options}',
                        '{get method(){return chosen}}', '{method:"POST",method:"GET"}'):
            with self.subTest(options=options):
                report = capture('fetch("/api/tasks",' + options + ');')
                self.assertEqual(report['routes'], [])
                self.assertEqual([r['path'] for r in report['unresolved_operations']], ['/api/tasks'])
                self.assertTrue(report['warnings'])

    def test_dynamic_target_does_not_become_a_fabricated_operation(self):
        for text in ('fetch(target, options);', 'fetch("/api/" + id, options);',
                     'fetch(`/api/${id}`, options);', 'fetch("/api/tasks" + suffix, options);'):
            with self.subTest(text=text):
                report = capture(text)
                self.assertEqual(report['routes'], [])
                self.assertFalse(report.get('unresolved_operations'))

    def test_truncated_unbalanced_or_over_budget_call_is_not_retained(self):
        cases = ['fetch("/api/tasks", options', 'fetch("/api/tasks", {method:unknown)',
                 'fetch("/api/tasks", options]', 'fetch("/api/tasks", {method: unknown}',
                 'fetch("/api/tasks", [' + ','.join(['x'] * 90) + ']);']
        for text in cases:
            with self.subTest(text=text):
                report = capture(text)
                self.assertFalse(report['routes'])
                self.assertFalse(report.get('unresolved_operations'))

    def test_global_receivers_use_literal_options_and_source_location(self):
        for receiver in ('window', 'globalThis'):
            report = capture(receiver + '.\nfetch("/api/orders", {method:"POST"});')
            self.assertEqual([(r['method'], r['path']) for r in report['routes']], [('POST', '/api/orders')])
            self.assertEqual(report['routes'][0]['evidence'][0]['location'], 'line 1')

    def test_receiver_binding_mutation_and_escape_suppress_qualified_hints(self):
        for receiver in ('window', 'globalThis'):
            prefixes = [f'const {receiver} = mock;', f'function f({receiver}){{}}',
                        f'{receiver}.fetch = mock;', f'{receiver}["fetch"] = mock;',
                        f'const alias = {receiver}; alias.fetch = mock;',
                        f'Object.defineProperty({receiver}, "fetch", {{value:mock}});',
                        'eval(code);', 'with(config) {}', 'self.fetch = mock;', 'this.fetch = mock;',
                        f'host.{receiver}.fetch("/api/users");']
            for prefix in prefixes:
                with self.subTest(receiver=receiver, prefix=prefix):
                    report = capture(prefix + receiver + '.fetch("/api/orders", options);')
                    self.assertFalse(report['routes'])
                    self.assertFalse(report.get('unresolved_operations'))

    def test_member_names_and_constructed_fetch_are_not_global_calls(self):
        for text in ('service.fetch("/api/orders");', 'host.window.fetch("/api/orders");',
                     'new window.fetch("/api/orders");', 'new globalThis.fetch("/api/orders");'):
            with self.subTest(text=text):
                report = capture(text)
                self.assertFalse(report['routes'])
                self.assertFalse(report.get('unresolved_operations'))

    def test_strings_comments_and_shadowed_fetch_are_not_new_unresolved_targets(self):
        cases = ['const text = \'fetch("/api/orders", options)\';',
                 '// globalThis.fetch("/api/orders", options);',
                 'function run(fetch){ fetch("/api/orders", options); }',
                 'const view = <pre>fetch("/api/orders", options)</pre>;']
        for text in cases:
            with self.subTest(text=text):
                report = capture(text)
                self.assertFalse(report['routes'])
                self.assertFalse(report.get('unresolved_operations'))

    def test_constant_target_remains_limited_to_proven_module_binding(self):
        report = capture('const target="/api/orders"; globalThis.fetch(target, options);')
        self.assertEqual([r['path'] for r in report['unresolved_operations']], ['/api/orders'])
        report = capture('const target="/api/orders"; function fn(target){ globalThis.fetch(target, options); }')
        self.assertFalse(report.get('unresolved_operations'))

    def test_unsupported_balanced_expressions_are_not_mistaken_for_simple_options(self):
        for options in ('options +', 'options ++ +', '[x, y]', 'getOptions(user)', 'new Options()'):
            with self.subTest(options=options):
                report = capture('fetch("/api/orders",' + options + ');')
                self.assertFalse(report['routes'])
                self.assertFalse(report.get('unresolved_operations'))
