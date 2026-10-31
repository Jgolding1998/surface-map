from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from surface_map import js_parser
from surface_map.analyze import ArtifactError, GLOBAL_AXIOS_ASSUMPTION
from surface_map.rebuild import operation_unknowns, plan_rebuild
from surface_map.scaffold import FILENAMES, scaffold_files, scaffold_zip, write_scaffold


ASSUMPTION = GLOBAL_AXIOS_ASSUMPTION
CANARY = "PRIVATE_SCAFFOLD_ASSUMPTION_83475"


def annotated_plan():
    plan = plan_rebuild([{"kind": "javascript", "text": 'fetch("/api/orders");'}], javascript_engine="basic")
    row = plan["operations"][0]
    row["assumptions"] = [ASSUMPTION]
    row["evidence"][0]["assumptions"] = [ASSUMPTION]
    row["unknowns"] = operation_unknowns(row.get("origin_relations"), row["assumptions"])
    return plan


class ScaffoldAssumptionValidationTests(unittest.TestCase):
    def test_supported_metadata_survives_and_input_is_not_mutated(self):
        plan = annotated_plan()
        original = deepcopy(plan)
        files = scaffold_files(plan)
        for name in ("contract.json", "rebuild-plan.json"):
            row = json.loads(files[name])["operations"][0]
            self.assertEqual(row["assumptions"], [ASSUMPTION])
            self.assertEqual(row["evidence"][0]["assumptions"], [ASSUMPTION])
            self.assertEqual(row["unknowns"], operation_unknowns(row.get("origin_relations"), [ASSUMPTION]))
        self.assertEqual(plan, original)

    def test_aggregate_assumption_survives_evidence_preview_without_assumption(self):
        plan = annotated_plan()
        del plan["operations"][0]["evidence"][0]["assumptions"]
        row = json.loads(scaffold_files(plan)["contract.json"])["operations"][0]
        self.assertEqual(row["assumptions"], [ASSUMPTION])
        self.assertTrue(any("Axios identity and prior configuration" in value for value in row["unknowns"]))

    def test_evidence_only_assumption_is_promoted_before_canonical_unknowns(self):
        plan = annotated_plan()
        del plan["operations"][0]["assumptions"]
        row = json.loads(scaffold_files(plan)["rebuild-plan.json"])["operations"][0]
        self.assertEqual(row["assumptions"], [ASSUMPTION])
        self.assertEqual(row["evidence"][0]["assumptions"], [ASSUMPTION])
        self.assertTrue(any("Axios identity and prior configuration" in value for value in row["unknowns"]))

    def test_unrecognized_duplicate_and_malformed_assumptions_are_rejected(self):
        invalid = (None, True, 1, ASSUMPTION, {}, [None], [True], [1], [[]], [{}],
                   [CANARY], [ASSUMPTION, ASSUMPTION], [ASSUMPTION, CANARY])
        for location in ("operation", "evidence"):
            for value in invalid:
                plan = annotated_plan()
                row = plan["operations"][0]
                target = row if location == "operation" else row["evidence"][0]
                target["assumptions"] = value
                with self.subTest(location=location, value=value), self.assertRaises(ArtifactError) as caught:
                    scaffold_files(plan)
                self.assertNotIn(CANARY, str(caught.exception))

    def test_unknown_fields_and_arbitrary_unknowns_remain_rejected(self):
        for mutate in (
            lambda row: row.update(assumption=CANARY),
            lambda row: row["evidence"][0].update(assumption=CANARY),
            lambda row: row.update(unknowns=[CANARY]),
            lambda row: row.update(path="/api/" + CANARY),
        ):
            plan = annotated_plan()
            mutate(plan["operations"][0])
            with self.subTest(mutate=mutate), self.assertRaises(ArtifactError) as caught:
                scaffold_files(plan)
            self.assertNotIn(CANARY, str(caught.exception))

    def test_omitting_the_assumption_warning_cannot_weaken_canonical_unknowns(self):
        plan = annotated_plan()
        row = plan["operations"][0]
        row["unknowns"] = operation_unknowns(row.get("origin_relations"))
        with self.assertRaises(ArtifactError):
            scaffold_files(plan)

    def test_missing_unknowns_are_regenerated_with_the_assumption_warning(self):
        plan = annotated_plan()
        del plan["operations"][0]["unknowns"]
        row = json.loads(scaffold_files(plan)["contract.json"])["operations"][0]
        self.assertEqual(row["unknowns"], operation_unknowns(row.get("origin_relations"), [ASSUMPTION]))


@unittest.skipUnless(js_parser.available()[0], "Optional parser and supported Node required")
class RealAxiosScaffoldTests(unittest.TestCase):
    def plan(self, script, *, document_url=None):
        artifacts = [{"kind": "javascript", "text": script}]
        if document_url:
            artifacts.insert(0, {"kind": "html", "text": "<p>Saved page</p>"})
        with patch("socket.create_connection", side_effect=AssertionError("Offline analysis only")):
            return plan_rebuild(artifacts, javascript_engine="enhanced", document_url=document_url)

    def test_actual_global_axios_supports_files_zip_and_new_directory(self):
        plan = self.plan("axios.post('/api/a',{x:1})")
        self.assertEqual(len(plan["operations"]), 1)
        row = plan["operations"][0]
        self.assertEqual(row["method"], "POST")
        self.assertEqual(row["assumptions"], [ASSUMPTION])
        self.assertEqual(row["evidence"][0]["assumptions"], [ASSUMPTION])
        files = scaffold_files(plan)
        self.assertEqual(tuple(files), FILENAMES)
        for name in ("contract.json", "rebuild-plan.json"):
            exported = json.loads(files[name])["operations"][0]
            self.assertEqual(exported["assumptions"], [ASSUMPTION])
            self.assertEqual(exported["evidence"][0]["assumptions"], [ASSUMPTION])
            self.assertTrue(any("Axios identity and prior configuration" in value for value in exported["unknowns"]))
        with zipfile.ZipFile(io.BytesIO(scaffold_zip(plan))) as archive:
            self.assertEqual(tuple(archive.namelist()), FILENAMES)
            for name in FILENAMES:
                self.assertEqual(archive.read(name).decode("utf-8"), files[name])
        with tempfile.TemporaryDirectory() as directory:
            destination = write_scaffold(plan, Path(directory) / "mock")
            for name in FILENAMES:
                self.assertEqual((destination / name).read_text(encoding="utf-8"), files[name])

    def test_imported_axios_remains_unmarked(self):
        plan = self.plan('import axios from "axios"; axios.post("/api/orders", {name:"example"});')
        self.assertEqual(len(plan["operations"]), 1)
        files = scaffold_files(plan)
        for name in ("contract.json", "rebuild-plan.json"):
            self.assertNotIn("assumptions", files[name])
            self.assertFalse(any("Axios identity" in value for value in json.loads(files[name])["operations"][0]["unknowns"]))

    def test_unresolved_global_axios_stays_out_of_handlers(self):
        plan = self.plan('axios.request({url:"/api/orders", method:chosen});')
        self.assertEqual(plan["operations"], [])
        self.assertEqual(len(plan["unresolved"]), 1)
        files = scaffold_files(plan)
        self.assertEqual(json.loads(files["contract.json"])["operations"], [])
        target = json.loads(files["rebuild-plan.json"])["unresolved"][0]
        self.assertIsNone(target["method"])
        self.assertEqual(target["assumptions"], [ASSUMPTION])
        self.assertEqual(target["evidence"][0]["assumptions"], [ASSUMPTION])

    def test_cross_origin_assumed_client_is_retained_without_handler(self):
        plan = self.plan('axios.get("https://other.invalid/api/orders");', document_url="https://example.invalid/")
        self.assertEqual(plan["operations"][0]["stub"], "none")
        files = scaffold_files(plan)
        self.assertEqual(json.loads(files["contract.json"])["operations"], [])
        row = json.loads(files["rebuild-plan.json"])["operations"][0]
        self.assertEqual(row["assumptions"], [ASSUMPTION])
        self.assertEqual(row["origin_relations"], ["cross_origin"])
        self.assertEqual(row["stub"], "none")

    def test_private_values_and_original_source_do_not_leak_into_files(self):
        script = 'axios.post("https://private.invalid/api/orders?token=' + CANARY + '", {name:"' + CANARY + '"});'
        plan = self.plan(script)
        files = scaffold_files(plan)
        output = "".join(files.values())
        self.assertNotIn(CANARY, output)
        self.assertNotIn("private.invalid", output)
        self.assertNotIn(script, output)
        self.assertIn(ASSUMPTION, output)
        model = json.loads(files["contract.json"])["operations"][0]["request_shapes"][0]
        self.assertEqual(model, {"type": "object", "properties": {"name": {"type": "string"}}})


if __name__ == "__main__":
    unittest.main()
