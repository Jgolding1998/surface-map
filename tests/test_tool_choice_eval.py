import unittest
from evals.tool_choice import score


class ToolChoiceEvaluationTests(unittest.TestCase):
    cases = [{"id": "one", "expected": "analyze_capture"}, {"id": "two", "expected": "none"}]

    def test_wrong_selection_is_a_failure(self):
        result = score(self.cases, [{"id": "two", "tool": "analyze_url"}, {"id": "one", "tool": "analyze_capture"}])
        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["failed_ids"], ["two"])

    def test_missing_duplicate_unknown_or_malformed_predictions_reject(self):
        for choices in ([], [{"id": "one", "tool": "none"}] * 2,
                        [{"id": "wrong", "tool": "none"}, {"id": "two", "tool": "none"}],
                        [{"id": "one", "tool": []}, {"id": "two", "tool": "none"}]):
            with self.subTest(choices=choices), self.assertRaises(ValueError):
                score(self.cases, choices)
