"""Rescore saved synthetic answers without model calls, networking or Node.js."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from evals.diverse.compare_agent import parse_response
from evals.diverse.run import EvaluationError, _read, score


FOLDER = Path(__file__).parent
SEED = 'surface-map-blind-broader-v1'
QUOTAS = {'form-controls': 3, 'text-containers': 3, 'har-body-shapes': 4,
          'javascript-uncertainty': 4, 'javascript-resolution': 3, 'capture-comparison': 3}
FILES = {'prompt-1.txt', 'prompt-2.txt', 'response-1.txt', 'response-2.txt',
         'surface-map-predictions.json'}
RUBRIC = {'analyze': ('routes', 'unresolved', 'details'),
          'compare': ('added', 'removed', 'changed', 'unresolved_added',
                      'unresolved_removed', 'unresolved_changed')}
INPUT_KEYS = ('task', 'artifacts', 'before', 'after', 'document_url',
              'before_document_url', 'after_document_url', 'include_page_signals')


def select_cases(cases):
    chosen = []
    for family, count in QUOTAS.items():
        rows = [case for case in cases if case['family'] == family]
        rows.sort(key=lambda case: hashlib.sha256(f"{SEED}:{case['id']}".encode()).hexdigest())
        chosen.extend(rows[:count])
    chosen.sort(key=lambda case: hashlib.sha256(f"{SEED}:mix:{case['id']}".encode()).hexdigest())
    return chosen


def canonical(value):
    """Use the original ordering normalization; never add or remove predictions."""
    value = json.loads(json.dumps(value))
    for route in value.get('routes', []):
        route[3].sort(key=lambda entry: json.dumps(entry, sort_keys=True))
    for target in value.get('unresolved', []):
        target[1].sort(key=lambda entry: json.dumps(entry, sort_keys=True))
    for row in value.get('changed', []):
        row[2].sort()
    for row in value.get('unresolved_changed', []):
        row[1].sort()
    for row in value.get('details', []):
        for key in ('statuses', 'query_fields', 'request_fields', 'response_fields'):
            if key in row:
                row[key].sort()
    return value


def parse_batch(raw, ids):
    """A malformed or incomplete batch fails every case in that batch."""
    try:
        rows = parse_response(raw)
        row_ids = [row['id'] for row in rows]
        if len(row_ids) != len(set(row_ids)) or set(row_ids) != set(ids):
            raise EvaluationError('Batch identities differ')
        return {row['id']: row for row in rows}
    except (EvaluationError, KeyError, TypeError, ValueError, RecursionError):
        return {case_id: {'error': 'invalid_batch_response'} for case_id in ids}


def score_case(case, prediction):
    expected = {key: value for key, value in case['expected'].items() if key in RUBRIC[case['task']]}
    try:
        observed = canonical({key: value for key, value in prediction.items()
                              if key in RUBRIC[case['task']] or key in {'error', 'rejected'}})
        return score({**case, 'expected': expected}, observed)
    except (KeyError, IndexError, TypeError, AttributeError, ValueError, RecursionError):
        return ['invalid_case_response']


def load_record(folder=FOLDER):
    manifest = json.loads(_read(folder / 'manifest.json', 65536))
    if (manifest.get('schema') != 'surface-map-stress-agent-comparison/1'
            or manifest.get('seed') != SEED or manifest.get('family_quotas') != QUOTAS
            or set(manifest.get('files', {})) != FILES):
        raise EvaluationError('Unsupported comparison manifest')
    corpus_bytes = _read(FOLDER.parent / 'corpus.json', 4 * 1024 * 1024)
    if hashlib.sha256(corpus_bytes).hexdigest() != manifest['corpus_sha256']:
        raise EvaluationError('Frozen corpus checksum differs')
    cases = select_cases(json.loads(corpus_bytes)['cases'])
    identities = [{'id': f'case_{i:04d}', 'source_id': case['id'], 'family': case['family']}
                  for i, case in enumerate(cases, 1)]
    if len(cases) != 20 or identities != manifest['cases']:
        raise EvaluationError('Deterministic case selection differs')
    artifacts = {}
    for name in sorted(FILES):
        raw = _read(folder / name, 256 * 1024, normalize_newlines=False)
        if hashlib.sha256(raw).hexdigest() != manifest['files'][name]:
            raise EvaluationError('Recorded artifact checksum differs')
        artifacts[name] = raw.decode('utf-8')
    if len(manifest['batches']) != 2:
        raise EvaluationError('Expected two batches')
    policies = []
    for number, batch in enumerate(manifest['batches'], 1):
        start = (number - 1) * 10
        ids = [row['id'] for row in identities[start:start + 10]]
        prompt = artifacts[f'prompt-{number}.txt']
        if (batch['batch'] != number or batch['case_ids'] != ids
                or hashlib.sha256(prompt.encode()).hexdigest() != batch['prompt_sha256']):
            raise EvaluationError('Frozen prompt identity differs')
        policy, inputs = prompt.split('\nINPUT CASES:\n')
        expected_inputs = {'cases': [{'id': case_id, **{key: case[key] for key in INPUT_KEYS if key in case}}
                                    for case_id, case in zip(ids, cases[start:start + 10])]}
        if json.loads(inputs) != expected_inputs:
            raise EvaluationError('Prompt does not contain exact synthetic inputs')
        policies.append(policy)
    if policies[0] != policies[1]:
        raise EvaluationError('Batch policies differ')
    return manifest, cases, artifacts


def compare_record(manifest, cases, artifacts):
    tool = parse_batch(artifacts['surface-map-predictions.json'], [row['id'] for row in manifest['cases']])
    agent = {}
    for number, batch in enumerate(manifest['batches'], 1):
        agent.update(parse_batch(artifacts[f'response-{number}.txt'], batch['case_ids']))
    results, paired = [], Counter(tool_only=0, agent_only=0, both_passed=0, both_failed=0)
    for identity, case in zip(manifest['cases'], cases):
        tool_errors = score_case(case, tool[identity['id']])
        agent_errors = score_case(case, agent[identity['id']])
        key = 'both_passed' if not tool_errors and not agent_errors else (
            'tool_only' if not tool_errors else 'agent_only' if not agent_errors else 'both_failed')
        paired[key] += 1
        results.append({**identity, 'tool_passed': not tool_errors, 'agent_passed': not agent_errors,
                        'tool_failures': tool_errors, 'agent_failures': agent_errors})
    return {'schema': 'surface-map-stress-agent-result/1', 'corpus_sha256': manifest['corpus_sha256'],
            'model_reported_by_cli': manifest['conditions']['model_reported_by_cli'],
            'cases': len(cases), 'agent_trials': manifest['conditions']['trials'],
            'tool_passed': sum(row['tool_passed'] for row in results),
            'agent_passed': sum(row['agent_passed'] for row in results), 'paired': dict(paired),
            'policy_sensitive_case_ids': manifest['policy_sensitive_case_ids'], 'results': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Write reproduced scores to this path')
    args = parser.parse_args(argv)
    try:
        result = compare_record(*load_record())
        recorded = json.loads(_read(FOLDER / 'recorded-results.json', 65536))
        if result != recorded:
            raise EvaluationError('Reproduced scores differ from the record')
        text = json.dumps(result, indent=2) + '\n'
        if args.output:
            args.output.write_text(text, encoding='utf-8')
        else:
            print(text, end='')
        return 0
    except (EvaluationError, OSError, ValueError, KeyError, TypeError, RecursionError):
        print('Comparison rejected: invalid, incomplete or changed recorded artifacts.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
