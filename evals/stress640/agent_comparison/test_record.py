import tempfile
import unittest
from pathlib import Path
from shutil import copytree

from .score import FOLDER, compare_record, load_record, parse_batch, score_case
from evals.diverse.run import EvaluationError


class RecordedComparisonTests(unittest.TestCase):
    def test_recorded_scores_and_failures(self):
        result = compare_record(*load_record())
        self.assertEqual((result['cases'], result['tool_passed'], result['agent_passed']), (20, 20, 17))
        self.assertEqual([row['id'] for row in result['results'] if not row['agent_passed']],
                         ['case_0003', 'case_0004', 'case_0017'])

    def test_changed_raw_answer_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'record'
            copytree(FOLDER, folder, ignore=lambda _path, names: [name for name in names if name == '__pycache__'])
            with (folder / 'response-1.txt').open('a', encoding='utf-8') as stream:
                stream.write(' ')
            with self.assertRaises(EvaluationError):
                load_record(folder)

    def test_invalid_or_duplicate_batch_fails(self):
        ids = ['case_0001', 'case_0002']
        for raw in ('not json', '{"results":[]}', '{"results":[{"id":"case_0001"},{"id":"case_0001"}]}'):
            predictions = parse_batch(raw, ids)
            self.assertEqual(set(predictions), set(ids))
            self.assertTrue(all(value.get('error') for value in predictions.values()))

    def test_unexpected_route_is_a_failure(self):
        case = {'task': 'analyze', 'expected': {'routes': [], 'unresolved': []}}
        prediction = {'routes': [['GET', '/admin', 'declared', [['source_1', 'line 1', 'link']]]],
                      'unresolved': [], 'details': []}
        self.assertEqual(score_case(case, prediction), ['routes'])


if __name__ == '__main__':
    unittest.main()
