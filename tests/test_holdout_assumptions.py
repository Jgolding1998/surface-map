"""Evaluation records preserve uncertainty without rewriting frozen expectations."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

from evals.holdout.run import EvaluationError, canonical_prediction, project_report, score


ASSUMPTION = "unverified_global_axios"
EVIDENCE = {"source": "source_1", "location": "line 3", "kind": "javascript"}


def route(*, unresolved=False):
    value = {"path": "/api/orders", "confidence": "inferred", "evidence_kinds": ["javascript"],
             "evidence": [dict(EVIDENCE)]}
    if not unresolved:
        value["method"] = "POST"
    return value


def prediction(value, *, unresolved=False):
    return {"routes": [] if unresolved else [value], "unresolved": [value] if unresolved else []}


class HoldoutAssumptionTests(unittest.TestCase):
    def test_projected_known_route_retains_aggregate_assumption_and_compatible_locations(self):
        production = {"routes": [{"method": "POST", "path": "/api/orders", "confidence": "inferred",
                                   "kinds": ["javascript"], "assumptions": [ASSUMPTION],
                                   "evidence": [{**EVIDENCE, "assumptions": [ASSUMPTION]}]}]}
        before = copy.deepcopy(production)
        result = project_report(production)
        expected = route()
        expected["assumptions"] = [ASSUMPTION]
        self.assertEqual(result, prediction(expected))
        self.assertEqual(result["routes"][0]["evidence"], [EVIDENCE])
        self.assertEqual(production, before)

    def test_projected_unknown_method_keeps_assumption_without_fabricating_operation(self):
        production = {"routes": [], "unresolved_operations": [
            {"method": None, "path": "/api/orders", "confidence": "inferred",
             "assumptions": [ASSUMPTION], "evidence": [{**EVIDENCE, "assumptions": [ASSUMPTION]}]}]}
        result = project_report(production)
        self.assertEqual(result["routes"], [])
        self.assertNotIn("method", result["unresolved"][0])
        self.assertEqual(result["unresolved"][0]["assumptions"], [ASSUMPTION])
        self.assertEqual(result["unresolved"][0]["evidence"], [EVIDENCE])

    def test_absent_assumption_is_not_manufactured_for_ordinary_evidence(self):
        result = project_report({"routes": [{"method": "POST", "path": "/api/orders", "confidence": "inferred",
                                              "evidence": [EVIDENCE]}]})
        self.assertNotIn("assumptions", result["routes"][0])
        self.assertEqual(result, prediction(route()))

    def test_unknown_duplicate_or_malformed_assumptions_are_rejected_in_both_route_sets(self):
        for unresolved in (False, True):
            for malformed in (None, ASSUMPTION, {}, ["unrecognized_client"],
                              [ASSUMPTION, ASSUMPTION], [1], [[ASSUMPTION]]):
                value = route(unresolved=unresolved)
                value["assumptions"] = malformed
                with self.subTest(unresolved=unresolved, value=malformed), self.assertRaises(EvaluationError):
                    canonical_prediction(prediction(value, unresolved=unresolved))

    def test_frozen_gold_without_assumption_remains_compatible_but_explicit_gold_is_scored(self):
        for unresolved in (False, True):
            key = "unresolved" if unresolved else "routes"
            expected = prediction(route(unresolved=unresolved), unresolved=unresolved)
            actual = copy.deepcopy(expected)
            actual[key][0]["assumptions"] = [ASSUMPTION]
            self.assertEqual(score(expected, actual), [])
            # An explicit empty set is a meaningful claim, unlike an omitted rubric field.
            expected[key][0]["assumptions"] = []
            self.assertEqual(score(expected, actual), [key + "_evidence"])
            expected[key][0]["assumptions"] = [ASSUMPTION]
            self.assertEqual(score(expected, actual), [])
            del actual[key][0]["assumptions"]
            self.assertEqual(score(expected, actual), [key + "_evidence"])

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "Optional JSON Schema validation library required")
    def test_machine_schema_agrees_with_runtime_for_known_unknown_and_empty_assumptions(self):
        from jsonschema import Draft202012Validator

        schema = json.loads((Path(__file__).resolve().parents[1] / "evals/holdout/schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        for unresolved in (False, True):
            name = "unresolved" if unresolved else "route"
            validator = Draft202012Validator({"$ref": "#/$defs/" + name, "$defs": schema["$defs"]})
            for assumptions, valid in (([], True), ([ASSUMPTION], True),
                                       ([ASSUMPTION, ASSUMPTION], False), (["unknown"], False)):
                value = route(unresolved=unresolved)
                value["assumptions"] = assumptions
                self.assertEqual(validator.is_valid(value), valid)


if __name__ == "__main__":
    unittest.main()
