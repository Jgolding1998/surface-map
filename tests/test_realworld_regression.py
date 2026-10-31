import copy
import unittest

from evals.realworld.regression import check, snapshot


def report():
    return {"schema": "surface-map-holdout-results/1", "engine": "enhanced", "split": "all",
            "analysis_calls": 6, "composition": {"tasks": 2}, "corpus_sha256": "a" * 64,
            "labels_sha256": "b" * 64, "passed": 1, "stable_cases": 2,
            "results": [{"id": "good", "passed": True, "stable": True, "failures": [], "prediction": {}},
                        {"id": "gap", "passed": False, "stable": True, "failures": ["routes"], "prediction": {}}]}


class RealworldRegressionTests(unittest.TestCase):
    def test_known_gap_stays_visible_without_failing_the_regression_gate(self):
        value = report()
        baseline = snapshot(value)
        self.assertTrue(check(value, baseline)["passed"])
        self.assertEqual(check(value, baseline)["known_failures"], 1)
        self.assertEqual(check(value, baseline)["total_cases"], 2)

    def test_new_regression_is_failure_and_improvement_is_allowed(self):
        value = report()
        baseline = snapshot(value)
        value["results"][0]["passed"] = False
        value["passed"] = 0
        self.assertEqual(check(value, baseline)["regressed"], ["good"])
        self.assertFalse(check(value, baseline)["passed"])
        value = report()
        value["results"][1]["passed"] = True
        value["passed"] = 2
        self.assertEqual(check(value, baseline)["improved"], ["gap"])
        self.assertTrue(check(value, baseline)["passed"])

    def test_changed_gold_and_removed_cases_cannot_silently_pass(self):
        baseline = snapshot(report())
        for key in ("corpus_sha256", "labels_sha256", "engine", "split"):
            value = report()
            value[key] = "changed"
            with self.subTest(key=key), self.assertRaises(ValueError):
                check(value, baseline)
        value = report()
        value["results"] = value["results"][:1]
        value["composition"]["tasks"] = 1
        value["stable_cases"] = 1
        with self.assertRaises(ValueError):
            check(value, baseline)

    def test_instability_or_execution_error_on_a_known_gap_fails(self):
        baseline = snapshot(report())
        value = report()
        value["results"][1]["stable"] = False
        value["stable_cases"] = 1
        self.assertFalse(check(value, baseline)["passed"])
        value = report()
        value["results"][1]["failures"] = ["analysis_error"]
        self.assertFalse(check(value, baseline)["passed"])

    def test_recorded_predictions_and_duplicate_or_inconsistent_counts_rejected(self):
        for mutation in (lambda r: r.update(analysis_calls=0),
                         lambda r: r.update(passed=2),
                         lambda r: r["results"].append(copy.deepcopy(r["results"][0]))):
            value = report()
            mutation(value)
            with self.assertRaises(ValueError):
                snapshot(value)


if __name__ == "__main__":
    unittest.main()
