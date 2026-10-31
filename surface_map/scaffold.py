"""Generate a fixed, local-only mock whose handlers explicitly remain unimplemented."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import stat
import zipfile

from .analyze import ArtifactError, FIELDS, METHODS, SEGMENTS

FILENAMES = ('mock_app.py', 'contract.json', 'rebuild-plan.json', 'test_mock.py', 'README.md')
ROOT_KEYS = {'format', 'operations', 'pages', 'unresolved', 'candidate_references', 'coverage',
             'unknowns', 'capture_guide', 'privacy', 'scope', 'limits'}
COMMON_KEYS = {'id', 'path', 'confidence', 'evidence', 'additional_evidence_count', 'origin_relations'}
OPERATION_KEYS = COMMON_KEYS | {'method', 'statuses', 'query_fields', 'request_shapes', 'response_shapes', 'stub', 'unknowns'}
RELATIONS = {'same_origin', 'cross_origin', 'unknown'}
SUMMARY_NOTE = 'Read the original plan for detailed warnings; this export contains bounded summaries, not complete coverage.'


def _fail():
    raise ArtifactError('Scaffold requires a bounded, minimized rebuild plan')


def _number(value, maximum=100000000):
    if type(value) is not int or not 0 <= value <= maximum:
        _fail()
    return value


def _rows(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        _fail()
    return value


def _path(value):
    if not isinstance(value, str) or not value.startswith('/') or len(value) > 2048:
        _fail()
    if value == '/':
        return value
    parts = value[1:].split('/')
    if len(parts) > 24:
        _fail()
    for index, part in enumerate(parts, 1):
        if part not in SEGMENTS and part not in (f'{{integer_{index}}}', f'{{segment_{index}}}'):
            _fail()
    return value


def _field(value):
    if not isinstance(value, str) or len(value) > 128 or not (
        value in FIELDS or re.fullmatch(r'field_[0-9]+(?:_2)*', value)
        or any(value.startswith(name + '_2') and re.fullmatch(re.escape(name) + r'(?:_2)+', value) for name in FIELDS)
    ):
        _fail()
    return value


def _shape(value, depth=0):
    if not isinstance(value, dict) or depth > 16 or set(value) - {
        'type', 'properties', 'items', 'anyOf', 'x-surface-map-truncated', 'x-surface-map-sampled'
    }:
        _fail()
    result = {}
    for key, item in value.items():
        if key == 'type':
            if item not in {'null', 'boolean', 'integer', 'number', 'string', 'object', 'array'}:
                _fail()
            result[key] = item
        elif key == 'properties':
            if not isinstance(item, dict) or len(item) > 32:
                _fail()
            result[key] = {_field(name): _shape(model, depth + 1) for name, model in item.items()}
        elif key == 'items':
            result[key] = _shape(item, depth + 1)
        elif key == 'anyOf':
            result[key] = [_shape(model, depth + 1) for model in _rows(item, 8)]
        elif item is True:
            result[key] = True
        else:
            _fail()
    return result


def _relations(value):
    rows = _rows(value, 3)
    if any(not isinstance(item, str) or item not in RELATIONS for item in rows):
        _fail()
    return sorted(set(rows))


def _evidence(value):
    result = []
    for row in _rows(value, 32):
        if not isinstance(row, dict) or set(row) - {'source', 'location', 'kind', 'origin_relation'}:
            _fail()
        if (not re.fullmatch(r'source_[1-9][0-9]{0,3}', str(row.get('source', '')))
                or not re.fullmatch(r'(?:line|entry) [1-9][0-9]{0,8}', str(row.get('location', '')))
                or row.get('kind') not in {'link', 'form', 'javascript', 'har'}):
            _fail()
        clean = {key: row[key] for key in ('source', 'location', 'kind')}
        if 'origin_relation' in row:
            clean['origin_relation'] = _relations([row['origin_relation']])[0]
        result.append(clean)
    return result


def _common(row, *, unresolved=False):
    pattern = r'target_[1-9][0-9]{0,3}' if unresolved else r'route_[1-9][0-9]{0,3}'
    if not re.fullmatch(pattern, str(row.get('id', ''))) or row.get('confidence') not in {'declared', 'inferred', 'observed'}:
        _fail()
    clean = {'id': row['id'], 'path': _path(row.get('path')), 'confidence': row['confidence'],
             'evidence': _evidence(row.get('evidence', []))}
    if 'additional_evidence_count' in row:
        clean['additional_evidence_count'] = _number(row['additional_evidence_count'])
    if 'origin_relations' in row:
        clean['origin_relations'] = _relations(row['origin_relations'])
    return clean


def _minimize_plan(plan):
    from .rebuild import CAPTURE_GUIDE, SCOPE, UNKNOWNS, operation_unknowns
    if not isinstance(plan, dict) or set(plan) - ROOT_KEYS or plan.get('format') != 'surface-map-rebuild-v1':
        _fail()
    try:
        encoded = json.dumps(plan, ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        _fail()
    if len(encoded) > 1024 * 1024:
        _fail()
    for key, expected in (('scope', SCOPE), ('unknowns', list(UNKNOWNS)), ('capture_guide', list(CAPTURE_GUIDE))):
        if key in plan and plan[key] != expected:
            _fail()
    operations, ids, targets = [], set(), set()
    for row in _rows(plan.get('operations'), 50):
        if not isinstance(row, dict) or set(row) - OPERATION_KEYS:
            _fail()
        clean = _common(row)
        if row.get('method') not in METHODS or row.get('stub') not in {'none', 'not_implemented'}:
            _fail()
        expected_stub = 'none' if clean.get('origin_relations') == ['cross_origin'] else 'not_implemented'
        if row['stub'] != expected_stub:
            _fail()
        if clean['id'] in ids or (row['method'], clean['path']) in targets:
            _fail()
        ids.add(clean['id'])
        targets.add((row['method'], clean['path']))
        statuses = [_number(status, 599) for status in _rows(row.get('statuses', []), 64)]
        if any(status < 100 for status in statuses):
            _fail()
        unknowns = operation_unknowns(clean.get('origin_relations'))
        if 'unknowns' in row and row['unknowns'] != unknowns:
            _fail()
        clean.update(method=row['method'], stub=row['stub'], statuses=sorted(set(statuses)),
                     query_fields=sorted({_field(name) for name in _rows(row.get('query_fields', []), 64)}),
                     unknowns=unknowns)
        for prefix in ('request', 'response'):
            clean[prefix + '_shapes'] = [_shape(model) for model in _rows(row.get(prefix + '_shapes', []), 64)]
        operations.append(clean)
    pages, unresolved = [], []
    for row in _rows(plan.get('pages', []), 50):
        if not isinstance(row, dict) or set(row) - COMMON_KEYS:
            _fail()
        pages.append(_common(row))
    for row in _rows(plan.get('unresolved', []), 50):
        if (not isinstance(row, dict) or set(row) - (COMMON_KEYS | {'method', 'query_fields'})
                or row.get('method') is not None):
            _fail()
        clean = _common(row, unresolved=True)
        clean.update(method=None, query_fields=sorted({_field(name) for name in _rows(row.get('query_fields', []), 64)}))
        unresolved.append(clean)
    coverage = plan.get('coverage', {})
    references = plan.get('candidate_references', {})
    if not isinstance(coverage, dict) or not isinstance(references, dict):
        _fail()
    evidence_limits = coverage.get('evidence_limits', {})
    reference_limits = references.get('limits', {})
    if not isinstance(evidence_limits, dict) or not isinstance(reference_limits, dict):
        _fail()
    omissions = _rows(evidence_limits.get('omissions', []), 512)
    if any(not isinstance(row, dict) for row in omissions):
        _fail()
    summary = {key: _number(coverage[key]) for key in (
        'retained_operation_groups', 'planned_operations', 'omitted_operations', 'omitted_pages',
        'omitted_unresolved_targets', 'cross_origin_groups_without_stubs') if key in coverage}
    summary.update(warnings_count=len(_rows(coverage.get('warnings', []), 512)),
                   script_omissions_count=len(_rows(coverage.get('script_omissions', []), 64)),
                   evidence_omissions_count=sum(_number(row.get('omitted_occurrences', 0)) for row in omissions),
                   evidence_limit_reached=bool(evidence_limits.get('truncated')),
                   source_count=len(_rows(coverage.get('sources', []), 64)), note=SUMMARY_NOTE)
    reference_summary = {'candidate_count': len(_rows(references.get('candidates', []), 100)),
                         'module_reference_count': len(_rows(references.get('module_references', []), 100)),
                         'truncated': bool(reference_limits.get('truncated')), 'stubbed': False}
    return {'format': 'surface-map-rebuild-v1', 'operations': operations, 'pages': pages, 'unresolved': unresolved,
            'candidate_references': reference_summary, 'coverage': summary, 'unknowns': list(UNKNOWNS),
            'capture_guide': list(CAPTURE_GUIDE), 'scope': SCOPE,
            'privacy': {'mode': 'conservative', 'export': 'Bounded minimized schema and coverage summaries only.'}}


MOCK_APP = r'''"""Local mock only. No captured application behavior is implemented."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
from urllib.parse import unquote

METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}
SEGMENTS = set('api v1 v2 v3 public private users user accounts account auth login logout register session sessions profile settings dashboard health healthz status orders order customers customer invoices invoice products product inventory items item cart checkout payments payment reports report search files file upload download events event notifications messages message contact submit forms form assets static js css images index.html app.js main.js bundle.js app.css main.css index.js favicon.ico favicon.svg robots.txt admin list details create update delete feed tags categories comments posts post projects project tasks task export import docs openapi.json graphql'.split())


def compile_operations(operations):
    if not isinstance(operations, list) or len(operations) > 50:
        raise ValueError('Invalid mock contract')
    compiled = []
    for row in operations:
        if (not isinstance(row, dict) or row.get('method') not in METHODS
                or not re.fullmatch(r'route_[1-9][0-9]{0,3}', str(row.get('id', '')))):
            raise ValueError('Invalid mock operation')
        path = row.get('path')
        if not isinstance(path, str) or not path.startswith('/') or len(path) > 2048:
            raise ValueError('Invalid mock path')
        parts = path[1:].split('/') if path != '/' else []
        if len(parts) > 24:
            raise ValueError('Invalid mock path')
        pattern = []
        for index, part in enumerate(parts, 1):
            if part == '{integer_%d}' % index:
                pattern.append(r'[0-9]{1,20}')
            elif part == '{segment_%d}' % index:
                pattern.append(r'[^/\\?#\x00-\x20\x7f]{1,128}')
            elif part in SEGMENTS:
                pattern.append(re.escape(part))
            else:
                raise ValueError('Invalid mock path')
        compiled.append((row['method'], path, row['id'], re.compile('/' + '/'.join(pattern) + r'\Z')))
    return sorted(compiled, key=lambda row: row[:3])


def _match(method, target, compiled):
    if method not in METHODS or not isinstance(target, str) or len(target) > 4096 or not target.startswith('/'):
        return None
    raw_path = target.partition('?')[0]
    if re.search(r'%(?:2f|5c)', raw_path, re.I) or re.search(r'%(?![0-9a-fA-F]{2})', raw_path):
        return None
    path = unquote(raw_path, errors='replace')
    if len(path) > 2048 or path.startswith('//') or any(ord(char) < 32 or ord(char) == 127 for char in path):
        return None
    for known_method, _path, identity, pattern in compiled:
        if method == known_method and pattern.fullmatch(path):
            return identity
    return None


def match_operation(method, target, operations):
    return _match(method, target, compile_operations(operations))


def load_contract():
    path = Path(__file__).with_name('contract.json')
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError('Invalid mock contract file')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('format') != 'surface-map-mock-contract-v1':
        raise ValueError('Invalid mock contract')
    return value


def make_server(port=0, contract=None):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Invalid local port')
    compiled = compile_operations((load_contract() if contract is None else contract)['operations'])

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'
        server_version = 'SurfaceMock'
        sys_version = ''

        def setup(self):
            self.request.settimeout(3.0)
            super().setup()

        def log_message(self, _format, *args):
            pass

        def _send(self, status, value):
            payload = json.dumps(value, separators=(',', ':')).encode('utf-8')
            self.close_connection = True
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Connection', 'close')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'")
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)

        def send_error(self, code, message=None, explain=None):
            self._send(code, {'implemented': False, 'error': 'invalid_request'})

        def _handle(self):
            host = '127.0.0.1:%d' % self.server.server_port
            if self.headers.get_all('Host', []) != [host]:
                self._send(403, {'implemented': False, 'error': 'request_rejected'})
                return
            origins = self.headers.get_all('Origin', [])
            sites = self.headers.get_all('Sec-Fetch-Site', [])
            if ((origins and origins != ['http://' + host]) or
                    (sites and sites not in (['same-origin'], ['none']))):
                self._send(403, {'implemented': False, 'error': 'request_rejected'})
                return
            identity = _match(self.command, self.path, compiled)
            if identity is None:
                self._send(404, {'implemented': False, 'error': 'unknown_operation'})
            else:
                self._send(501, {'implemented': False, 'operation_id': identity})

        def __getattr__(self, name):
            if name.startswith('do_'):
                return self._handle
            raise AttributeError(name)

    return HTTPServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    server = make_server(args.port)
    print('Unimplemented local mock: http://127.0.0.1:%d' % server.server_port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
'''

MOCK_TEST = r'''import http.client
import json
import re
import threading
import unittest

import mock_app


class MockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = mock_app.load_contract()
        cls.server = mock_app.make_server(contract=cls.contract)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, method, path, headers=None):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            client.request(method, path, headers=headers or {})
            response = client.getresponse()
            return response.status, response.read()
        finally:
            client.close()

    def test_known_operations_are_unimplemented(self):
        if not self.contract['operations']:
            self.skipTest('No operations were included in this mock; known-operation behavior was not tested.')
        for row in self.contract['operations']:
            path = re.sub(r'\{integer_[0-9]+\}', '1', row['path'])
            path = re.sub(r'\{segment_[0-9]+\}', 'example', path)
            status, body = self.request(row['method'], path)
            self.assertEqual(status, 501)
            if row['method'] != 'HEAD':
                self.assertEqual(json.loads(body), {'implemented': False, 'operation_id':
                    mock_app.match_operation(row['method'], path, self.contract['operations'])})

    def test_unknown_operation(self):
        self.assertEqual(self.request('GET', '/' + 'x' * 129)[0], 404)

    def test_host_and_browser_origin_checks(self):
        for headers in ({'Host': 'example.invalid'}, {'Origin': 'https://example.invalid'},
                        {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('GET', '/', headers)[0], 403)


if __name__ == '__main__':
    unittest.main()
'''

MOCK_README = '''# Local implementation scaffold

This is an unimplemented mock, not a reconstructed application. Every included
operation returns HTTP 501 with `implemented: false` and an operation ID.
Unknown method/path combinations return 404. Observed HAR statuses and schemas
are evidence in JSON, not successful responses generated by this server.

With Python 3.11 or later, run `python -m unittest -v test_mock` and then
`python mock_app.py`. The server chooses an available port and prints its local
address. `--port 8000` selects a port. It only binds to `127.0.0.1` and accepts
that exact Host header with its actual port. Foreign Origin and Sec-Fetch-Site
headers are rejected. There is no CORS allowance, upstream proxy, file-serving
route, dynamic code execution, request logging, or external dependency.

Only planned API operations with `stub: not_implemented` are included. Links,
unresolved methods, candidate strings and excluded cross-origin operations
do not become handlers. Numeric placeholders match up to 20 digits; segment
placeholders match one segment of up to 128 characters. Matching rejects
encoded slash/backslash separators and malformed percent escapes.
The known-operation test is explicitly skipped when no operations were included.
Path normalization loses information. When normalized patterns overlap, the first match sorted
by method, normalized path and operation ID wins. This is routing convenience,
not proof that either original endpoint accepts that input.

`contract.json` contains minimized evidence and shapes. `rebuild-plan.json`
retains bounded coverage summaries; read the original plan for detailed
warnings. Missing warnings do not establish complete coverage. Mock tests
only check generated routing, explicit failure responses and local-request
guards. They do not verify the captured application, security, authorization,
validation, storage, side effects or business rules. Implement those separately
and add behavior tests before using the code beyond local development.
'''


def scaffold_files(plan):
    try:
        minimized = _minimize_plan(plan)
    except ArtifactError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        _fail()
    operations = sorted((row for row in minimized['operations'] if row['stub'] == 'not_implemented'),
                        key=lambda row: (row['method'], row['path'], row['id']))
    contract = {'format': 'surface-map-mock-contract-v1', 'operations': operations}
    return {'mock_app.py': MOCK_APP, 'contract.json': json.dumps(contract, indent=2, ensure_ascii=True) + '\n',
            'rebuild-plan.json': json.dumps(minimized, indent=2, ensure_ascii=True) + '\n',
            'test_mock.py': MOCK_TEST, 'README.md': MOCK_README}


def scaffold_zip(plan):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, text in scaffold_files(plan).items():
            info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            archive.writestr(info, text.encode('utf-8'))
    return output.getvalue()


def _ordinary(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ArtifactError('Scaffold destination must not contain links or reparse points')
    return info


def write_scaffold(plan, destination):
    files = scaffold_files(plan)
    try:
        raw = os.fspath(destination)
        if not isinstance(raw, str) or not raw or '\x00' in raw or raw.startswith(('\\\\', '//')):
            raise ArtifactError('Scaffold destination must be an ordinary local path')
        rest = raw[2:] if os.name == 'nt' and re.match(r'^[A-Za-z]:[\\/]', raw) else raw
        if ':' in rest or '..' in re.split(r'[\\/]', raw):
            raise ArtifactError('Scaffold destination must be an ordinary local path')
        target = Path(raw).absolute()
        if os.name == 'nt':
            from .source_io import _windows_drive_type, _windows_local_path
            if not _windows_local_path(str(target)) or _windows_drive_type(target.anchor) not in {2, 3, 5, 6}:
                raise ArtifactError('Scaffold destination must use a local drive')
        parent = target.parent
        for ancestor in (*reversed(parent.parents), parent):
            if not stat.S_ISDIR(_ordinary(ancestor).st_mode):
                raise ArtifactError('Scaffold parent must be an existing local directory')
        if os.path.lexists(target):
            raise ArtifactError('Scaffold destination already exists')
        target = parent.resolve(strict=True) / target.name
        target.mkdir(mode=0o700)
        for filename, text in files.items():
            _ordinary(target)
            with (target / filename).open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(text)
        return target
    except ArtifactError:
        raise
    except (OSError, TypeError, ValueError):
        raise ArtifactError('Could not create scaffold in the local destination') from None
