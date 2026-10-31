"""Passive, minimized JavaScript string and module-reference inventory."""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlsplit

from .analyze import (ArtifactError, Builder, MAX_ARTIFACTS, _http_url, _js_tokens,
                      _read_artifacts, document_base, safe_path, validate_document_url)

MAX_REFERENCES = 100
MAX_EVIDENCE = 16
MAX_FIELDS = 64
WORK_SECONDS = 5.0
_UNSET = object()
_PACKAGE = re.compile(r"(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_][A-Za-z0-9_./-]*\Z")
_MIME = re.compile(r"(?:application|audio|font|example|image|message|model|multipart|text|video)/[a-z0-9!#$&^_.+-]+\Z", re.I)
_SCOPE = ("String references are candidates, not verified endpoints or HTTP operations. "
          "Literals may be fragments, examples, or unused code. Only supplied JavaScript and TypeScript "
          "are scanned; comments, escaped strings, templates, and ambiguous lexical regions are omitted. "
          "Module references describe supported literal syntax, not executed imports or a resolved dependency graph. "
          "Origins, query values, and module names are discarded; normalized paths can merge unrelated references.")


def _budget(deadline):
    end = time.monotonic() + WORK_SECONDS
    if deadline is not None:
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not (-float('inf') < deadline < float('inf')):
            raise ArtifactError("Reference deadline must be a finite monotonic time")
        end = min(end, deadline)

    def tick():
        if time.monotonic() >= end:
            raise ArtifactError("Reference inventory exceeded its work deadline")

    tick()
    return tick, end


def _module_specifiers(tokens, tick):
    """Yield transient raw (specifier, line, relation, token-index) tuples."""
    count, depth = len(tokens), 0

    def is_token(position, kind, value=None):
        return (0 <= position < count and tokens[position][0] == kind
                and (value is None or tokens[position][1] == value) and tokens[position][3])

    def ident(position, value=None):
        return is_token(position, 'identifier', value)

    def punct(position, value):
        return is_token(position, 'punctuation', value)

    def named(position):
        if not punct(position, '{'):
            return None
        position += 1
        for _ in range(64):
            if punct(position, '}'):
                return position + 1
            if not ident(position):
                return None
            position += 1
            if ident(position, 'as'):
                if not ident(position + 1):
                    return None
                position += 2
            if punct(position, '}'):
                return position + 1
            if not punct(position, ','):
                return None
            position += 1
        return None

    def clause(position, exporting):
        if ident(position, 'type'):
            position += 1
        if not exporting and ident(position):
            position += 1
            if ident(position, 'from'):
                return position
            if not punct(position, ','):
                return None
            position += 1
        if punct(position, '*'):
            position += 1
            if ident(position, 'as') and ident(position + 1):
                position += 2
            elif not exporting:
                return None
            return position
        return named(position)

    for index, token in enumerate(tokens):
        if index % 128 == 0:
            tick()
        kind, value, line, claimable = token
        if kind == 'punctuation':
            if value in '([{':
                depth += 1
            elif value in ')]}':
                depth -= 1
            continue
        if not claimable or kind != 'identifier' or value not in ('import', 'export'):
            continue
        if punct(index - 1, '.') or ident(index - 1, 'new'):
            continue
        if value == 'import' and punct(index + 1, '('):
            if is_token(index + 2, 'string') and punct(index + 3, ')'):
                yield tokens[index + 2][1], line, 'dynamic_import_literal', index + 2
            continue
        if depth != 0 or (index and not punct(index - 1, ';') and
                          (tokens[index - 1][2] >= line or tokens[index - 1][1] in ('=', '.', ',', ':', '?', '+', '-', '*', '/'))):
            continue
        end = index + 1
        if value == 'import' and is_token(end, 'string'):
            pass
        else:
            end = clause(end, value == 'export')
            if end is None or not ident(end, 'from'):
                continue
            end += 1
        if not is_token(end, 'string') or not all(row[3] for row in tokens[index:end + 1]):
            continue
        if end + 1 < count and not punct(end + 1, ';') and tokens[end + 1][2] <= tokens[end][2]:
            continue
        yield tokens[end][1], line, 'static_reexport' if value == 'export' else 'static_import', end


def _iter_module_specifiers(text, *, deadline=None):
    """Private matching helper. Raw values must never be serialized or treated as paths to read."""
    tick, _ = _budget(deadline)
    builder = Builder('basic')
    items = list(_read_artifacts([{'kind': 'javascript', 'text': text}], builder, tick))
    tokens = _js_tokens(items[0][2])
    tick()
    for value, line, relation, _ in _module_specifiers(tokens, tick):
        yield value, line, relation


def _url_kind(raw):
    if not raw or len(raw) > 4096 or '\\' in raw or any(ord(c) <= 32 or ord(c) == 127 for c in raw):
        return None
    if raw.startswith('///'):
        return None
    if _MIME.fullmatch(raw):
        return None
    try:
        parts = urlsplit(raw)
        if parts.scheme:
            return 'absolute' if _http_url(raw) is not None else None
        if parts.netloc:
            return 'network_relative' if _http_url('https:' + raw) is not None else None
        if raw.startswith('/'):
            return 'root_relative'
        if raw.startswith(('./', '../')) or '/' in parts.path:
            return 'relative'
    except (ValueError, UnicodeError):
        pass
    return None


def _module_kind(raw):
    if raw.startswith(('/', './', '../')) or re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', raw):
        return _url_kind(raw)
    return 'package' if len(raw) <= 4096 and _PACKAGE.fullmatch(raw) and not raw.startswith('.') else None


def inventory_references(artifacts, *, document_url=None, base_url=_UNSET, deadline=None):
    """Inventory candidates offline, preserving input order as generic source IDs.

    A provided base_url is validated and must equal the first HTML base resolved
    against document_url. It cannot override the document's context.
    """
    tick, end = _budget(deadline)
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= MAX_ARTIFACTS:
        raise ArtifactError("Choose 1 to 6 HTML, HAR or JavaScript artifacts")
    builder = Builder('basic')
    items = list(_read_artifacts(artifacts, builder, tick))
    if document_url is not None:
        document_url = validate_document_url(document_url)
        kinds = [kind for _, kind, _ in items]
        if kinds.count('html') != 1 or 'har' in kinds:
            raise ArtifactError("A document URL applies to exactly one HTML artifact and its JavaScript")
        html = next(text for _, kind, text in items if kind == 'html')
        resolved_base = document_base(html, document_url, end)
    else:
        resolved_base = None
    if base_url is not _UNSET:
        supplied_base = None if base_url is None else validate_document_url(base_url)
        if supplied_base != resolved_base:
            raise ArtifactError("Reference base URL must match the supplied HTML document context")
    builder.document_url, builder.base_url = document_url, resolved_base
    candidates, modules = {}, {}
    omitted = dict.fromkeys(('candidate_rows', 'module_rows', 'candidate_evidence', 'module_evidence',
                             'query_fields', 'relative_strings_without_base'), 0)

    def evidence(row, source, line, category):
        value = {'source': source, 'location': f'line {line}', 'kind': 'javascript'}
        if value not in row['evidence']:
            if len(row['evidence']) < MAX_EVIDENCE:
                row['evidence'].append(value)
            else:
                omitted[category + '_evidence'] += 1

    for source, kind, text in items:
        if kind not in ('javascript', 'typescript'):
            continue
        tick()
        tokens = _js_tokens(text)
        tick()
        module_tokens = set()
        for raw, line, relation, position in _module_specifiers(tokens, tick):
            specifier_kind = _module_kind(raw)
            if specifier_kind is None:
                continue
            module_tokens.add(position)
            key = (raw, relation)
            if key not in modules:
                if len(modules) >= MAX_REFERENCES:
                    omitted['module_rows'] += 1
                    continue
                modules[key] = {'id': f'module_{len(modules) + 1}', 'relation': relation,
                                'specifier_kind': specifier_kind, 'confidence': 'candidate', 'evidence': []}
            evidence(modules[key], source, line, 'module')
        for position, (token_kind, raw, line, claimable) in enumerate(tokens):
            if position % 128 == 0:
                tick()
            if not claimable or token_kind != 'string' or position in module_tokens:
                continue
            url_kind = _url_kind(raw)
            if url_kind is None:
                continue
            if url_kind == 'relative' and resolved_base is None:
                omitted['relative_strings_without_base'] += 1
                continue
            resolved = urljoin(resolved_base, raw) if resolved_base is not None else raw
            minimized = safe_path(resolved)
            if minimized is None:
                continue
            path, fields = minimized
            if path not in candidates:
                if len(candidates) >= MAX_REFERENCES:
                    omitted['candidate_rows'] += 1
                    continue
                candidates[path] = {'id': f'reference_{len(candidates) + 1}', 'path': path,
                                    'method': None, 'confidence': 'candidate', 'reason': 'string_reference',
                                    'query_fields': [], 'evidence': []}
            row = candidates[path]
            for field in fields:
                if field not in row['query_fields']:
                    if len(row['query_fields']) < MAX_FIELDS:
                        row['query_fields'].append(field)
                    else:
                        omitted['query_fields'] += 1
            if document_url is not None:
                relations = row.setdefault('origin_relations', [])
                relation = builder.origin_relation(raw)
                if relation not in relations:
                    relations.append(relation)
                    relations.sort()
            evidence(row, source, line, 'candidate')
    tick()
    for row in candidates.values():
        row['query_fields'].sort()
    return {'format': 'surface-map-references-v1',
            'sources': [{'id': source, 'kind': kind} for source, kind, _ in items if kind in ('javascript', 'typescript')],
            'sources_not_scanned': sum(kind not in ('javascript', 'typescript') for _, kind, _ in items),
            'candidates': list(candidates.values()), 'module_references': list(modules.values()),
            'limits': {'max_candidates': MAX_REFERENCES, 'max_module_references': MAX_REFERENCES,
                       'max_evidence_per_row': MAX_EVIDENCE, 'max_query_fields_per_row': MAX_FIELDS,
                       'omitted_occurrences': omitted, 'truncated': any(omitted.values()),
                       'count_scope': 'Counts omitted occurrences, not distinct values; lexical exclusions are not counted.'},
            'scope': _SCOPE}
