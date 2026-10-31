import copy
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from surface_map.analyze import ArtifactError, shape
from surface_map.rebuild import plan_rebuild
from surface_map.scaffold import FILENAMES, scaffold_files, scaffold_zip, write_scaffold


def fixture():
    return plan_rebuild([{'kind': 'html', 'text':
        '<a href="/dashboard">Open</a><form action="/api/orders" method="post"><input name="order_id"></form>'},
        {'kind': 'javascript', 'text': 'fetch("/api/users/42"); const hint="/api/admin";'}],
        javascript_engine='basic', document_url='https://capture-source.invalid/')


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScaffoldTests(unittest.TestCase):
    def test_fixed_files_and_candidate_exclusions(self):
        files = scaffold_files(fixture())
        self.assertEqual(tuple(files), FILENAMES)
        contract = json.loads(files['contract.json'])
        self.assertEqual({(row['method'], row['path']) for row in contract['operations']},
                         {('POST', '/api/orders'), ('GET', '/api/users/{integer_3}')})
        self.assertTrue(all(row['stub'] == 'not_implemented' for row in contract['operations']))
        self.assertNotIn('capture-source.invalid', ''.join(files.values()))
        empty = {'format': 'surface-map-rebuild-v1', 'operations': []}
        self.assertEqual(scaffold_files(empty)['mock_app.py'], files['mock_app.py'])

    def test_cross_origin_operations_have_no_stub(self):
        plan = plan_rebuild([{'kind': 'html', 'text': '<p>Example</p>'},
                            {'kind': 'javascript', 'text': 'fetch("https://other.invalid/api/users");'}],
                            javascript_engine='basic', document_url='https://example.invalid/')
        self.assertEqual(json.loads(scaffold_files(plan)['contract.json'])['operations'], [])
        plan['operations'][0]['stub'] = 'not_implemented'
        with self.assertRaises(ArtifactError):
            scaffold_files(plan)

    def test_har_values_and_source_code_are_not_copied(self):
        canary = 'PRIVATE_CAPTURE_VALUE_12345'
        request = {'method': 'POST', 'url': 'https://private.invalid/api/users/' + canary,
                   'headers': [{'name': 'Authorization', 'value': canary}],
                   'postData': {'mimeType': 'application/json', 'text': json.dumps({'name': canary})}}
        har = {'log': {'entries': [{'request': request, 'response': {'status': 201,
            'content': {'mimeType': 'application/json', 'text': json.dumps({'token': canary})}}}]}}
        plan = plan_rebuild([{'kind': 'har', 'text': json.dumps(har)}], javascript_engine='basic')
        files = scaffold_files(plan)
        self.assertNotIn(canary, ''.join(files.values()))
        self.assertNotIn('private.invalid', ''.join(files.values()))
        operation = json.loads(files['contract.json'])['operations'][0]
        self.assertEqual(operation['statuses'], [201])
        self.assertEqual(operation['stub'], 'not_implemented')

    def test_tampered_plans_cannot_supply_code_paths_or_values(self):
        plan = fixture()
        variants = []
        bad = copy.deepcopy(plan)
        bad['code'] = '__import__("os").system("bad")'
        variants.append(bad)
        bad = copy.deepcopy(plan)
        bad['scope'] = 'PRIVATE_CAPTURE_VALUE_12345'
        variants.append(bad)
        bad = copy.deepcopy(plan)
        bad['operations'][0]['path'] = '/api/PRIVATE_CAPTURE_VALUE_12345'
        variants.append(bad)
        bad = copy.deepcopy(plan)
        bad['operations'][0]['response_shapes'] = [{'type': 'string', 'example': 'PRIVATE_CAPTURE_VALUE_12345'}]
        variants.append(bad)
        bad = copy.deepcopy(plan)
        bad['operations'][0]['id'] = 'route_1\nimport os'
        variants.append(bad)
        bad = copy.deepcopy(plan)
        bad['operations'][0]['method'] = []
        variants.append(bad)
        for bad in variants:
            with self.subTest(bad=variants.index(bad)), self.assertRaises(ArtifactError):
                scaffold_files(bad)
        plan['coverage']['warnings'] = [{'source': 'source_1', 'reason': 'PRIVATE_CAPTURE_VALUE_12345'}]
        plan['coverage']['script_omissions'] = [{'source': 'source_1', 'code': 'PRIVATE_CAPTURE_VALUE_12345'}]
        files = scaffold_files(plan)
        self.assertNotIn('PRIVATE_CAPTURE_VALUE_12345', ''.join(files.values()))
        coverage = json.loads(files['rebuild-plan.json'])['coverage']
        self.assertEqual(coverage['warnings_count'], 1)
        self.assertEqual(coverage['script_omissions_count'], 1)

    def test_archive_has_fixed_names_timestamps_and_no_paths(self):
        first = scaffold_zip(fixture())
        self.assertEqual(first, scaffold_zip(fixture()))
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            self.assertEqual(tuple(archive.namelist()), FILENAMES)
            for row in archive.infolist():
                self.assertEqual(row.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertNotIn('/', row.filename)
                self.assertEqual(row.comment, b'')

    def test_supported_nested_shapes_and_field_aliases(self):
        plan = fixture()
        model = shape({'field_1': ['private', 1, {'items': [[{'name': 'private'}], None]}],
                       'arbitrary-secret-name': True, 'name': 'private', 'name_2': 'private'})
        plan['operations'][0]['request_shapes'] = [model]
        exported = json.loads(scaffold_files(plan)['contract.json'])
        self.assertEqual(exported['operations'][0]['request_shapes'], [model])
        self.assertNotIn('arbitrary-secret-name', json.dumps(exported))

    def test_operation_limit_and_duplicate_ids_rejected(self):
        plan = fixture()
        plan['operations'] = plan['operations'] * 26
        with self.assertRaises(ArtifactError):
            scaffold_files(plan)
        plan['operations'] = plan['operations'][:2]
        plan['operations'][1]['id'] = plan['operations'][0]['id']
        with self.assertRaises(ArtifactError):
            scaffold_files(plan)

    def test_destination_is_new_and_local(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'mock'
            self.assertEqual(write_scaffold(fixture(), destination), destination.resolve())
            self.assertEqual({item.name for item in destination.iterdir()}, set(FILENAMES))
            with self.assertRaises(ArtifactError):
                write_scaffold(fixture(), destination)
            for path in ('//server/share/mock', '\\\\server\\share\\mock', str(Path(directory) / 'mock:stream'),
                         str(Path(directory) / 'missing' / 'mock'), str(Path(directory) / '..' / 'mock')):
                with self.subTest(path=path), self.assertRaises(ArtifactError):
                    write_scaffold(fixture(), path)

    def test_linked_parent_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            actual = Path(directory) / 'actual'
            actual.mkdir()
            link = Path(directory) / 'link'
            try:
                link.symlink_to(actual, target_is_directory=True)
            except OSError:
                self.skipTest('Creating directory links is unavailable')
            with self.assertRaises(ArtifactError):
                write_scaffold(fixture(), link / 'mock')
            self.assertEqual(list(actual.iterdir()), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows mapped-drive check')
    def test_mapped_network_drive_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'mock'
            with patch('surface_map.source_io._windows_drive_type', return_value=4):
                with self.assertRaises(ArtifactError):
                    write_scaffold(fixture(), target)
            self.assertFalse(target.exists())

    def test_linked_ancestor_stops_before_descendant_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'mock'
            first = list(reversed(target.parent.parents))[0]
            with patch('surface_map.scaffold._ordinary', side_effect=ArtifactError('Linked ancestor')) as inspect:
                with self.assertRaises(ArtifactError):
                    write_scaffold(fixture(), target)
                inspect.assert_called_once_with(first)
            self.assertFalse(target.exists())

    def test_generated_tests_run_and_server_reports_only_unimplemented(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = write_scaffold(fixture(), Path(directory) / 'mock')
            module = load_module(destination / 'mock_app.py', 'mock_app')
            with patch.dict('sys.modules', {'mock_app': module}):
                tests = load_module(destination / 'test_mock.py', 'generated_mock_tests')
                result = unittest.TestResult()
                unittest.defaultTestLoader.loadTestsFromModule(tests).run(result)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
                self.assertEqual(result.testsRun, 3)
            operations = module.load_contract()['operations']
            self.assertIsNotNone(module.match_operation('GET', '/api/users/123?token=PRIVATE', operations))
            self.assertIsNone(module.match_operation('GET', '/api/users/' + '1' * 21, operations))
            self.assertIsNone(module.match_operation('GET', 'https://example.invalid/api/users/123', operations))
            server = module.make_server(contract=module.load_contract())
            self.assertEqual(server.server_address[0], '127.0.0.1')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for method in ('GET', 'POST', 'TRACE', 'UNKNOWN'):
                    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                    try:
                        client.request(method, '/__missing__', body='PRIVATE_BODY')
                        response = client.getresponse()
                        self.assertEqual(response.status, 404)
                        self.assertNotIn(b'PRIVATE_BODY', response.read())
                        self.assertEqual(response.version, 10)
                        self.assertEqual(response.getheader('Connection'), 'close')
                    finally:
                        client.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_overlapping_placeholders_choose_documented_first_match(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = write_scaffold(fixture(), Path(directory) / 'mock')
            module = load_module(destination / 'mock_app.py', 'mock_app_overlap')
            rows = [{'id': 'route_2', 'method': 'GET', 'path': '/api/users/{segment_3}'},
                    {'id': 'route_1', 'method': 'GET', 'path': '/api/users/{integer_3}'}]
            self.assertEqual(module.match_operation('GET', '/api/users/42', rows), 'route_1')
            self.assertEqual(module.match_operation('GET', '/api/users/example', rows), 'route_2')

    def test_generated_unknown_check_with_broad_root_parameter(self):
        plan = plan_rebuild([{'kind': 'javascript', 'text': 'fetch("/something");'}], javascript_engine='basic')
        with tempfile.TemporaryDirectory() as directory:
            destination = write_scaffold(plan, Path(directory) / 'mock')
            module = load_module(destination / 'mock_app.py', 'mock_app')
            with patch.dict('sys.modules', {'mock_app': module}):
                tests = load_module(destination / 'test_mock.py', 'generated_broad_tests')
                result = unittest.TestResult()
                unittest.defaultTestLoader.loadTestsFromModule(tests).run(result)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)

    def test_empty_generated_mock_has_two_passes_and_explicit_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = write_scaffold({'format': 'surface-map-rebuild-v1', 'operations': []},
                                         Path(directory) / 'mock')
            module = load_module(destination / 'mock_app.py', 'mock_app')
            with patch.dict('sys.modules', {'mock_app': module}):
                tests = load_module(destination / 'test_mock.py', 'generated_empty_tests')
                result = unittest.TestResult()
                unittest.defaultTestLoader.loadTestsFromModule(tests).run(result)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
                self.assertEqual(result.testsRun, 3)
                self.assertEqual(len(result.skipped), 1)
                self.assertIn('No operations were included', result.skipped[0][1])

    def test_encoded_separators_do_not_change_route_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = write_scaffold(fixture(), Path(directory) / 'mock')
            module = load_module(destination / 'mock_app.py', 'mock_app_encoding')
            rows = [{'id': 'route_1', 'method': 'GET', 'path': '/api/orders'},
                    {'id': 'route_2', 'method': 'GET', 'path': '/api/{segment_2}'}]
            for target in ('/api%2Forders', '/api%2forders', '/api%5Corders', '/api%5corders',
                           '/api/ord%ers', '/api/orders%', '/api/orders%2', '/api/ord%GGers'):
                with self.subTest(target=target):
                    self.assertIsNone(module.match_operation('GET', target, rows))
            self.assertEqual(module.match_operation('GET', '/api/orders?url=%2Forders', rows), 'route_1')
            self.assertEqual(module.match_operation('GET', '/api/%6Frders', rows), 'route_1')


if __name__ == '__main__':
    unittest.main()
