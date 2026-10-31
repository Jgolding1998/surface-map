from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import patch

from evals.holdout.run import (
    CORPUS_SCHEMA, LABEL_SCHEMA, PREDICTION_SCHEMA, EvaluationError,
    aggregate_report, canonical_prediction, describe, evaluate, export_inputs, load_corpus,
    main, provenance_identity, score, score_prediction_files, select_cases,
)


FOLDER = Path(__file__).resolve().parents[1] / "evals/holdout"


class RejectedInput(ValueError):
    pass


class HoldoutEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        shutil.copytree(FOLDER / "fixtures", self.folder / "fixtures")
        self.manifest = json.loads((FOLDER / "corpus.json").read_text(encoding="utf-8"))
        self.labels = json.loads((FOLDER / "labels.json").read_text(encoding="utf-8"))
        self.write()

    def write(self):
        raw = (json.dumps(self.manifest, indent=2) + "\n").encode("utf-8")
        self.path = self.folder / "corpus.json"
        self.path.write_bytes(raw)
        self.labels["corpus_sha256"] = hashlib.sha256(raw).hexdigest()
        (self.folder / "labels.json").write_bytes((json.dumps(self.labels) + "\n").encode("utf-8"))

    def load(self):
        return load_corpus(self.path)

    def extra_project(self, *, split="holdout", group="github:example/other"):
        project = copy.deepcopy(self.manifest["projects"][0])
        project.update(id="other-project", group=group, split=split, kind="real", exposure="unknown")
        project["provenance"]["url"] = "https://github.com/example/other"
        self.manifest["projects"].append(project)
        case = copy.deepcopy(self.manifest["cases"][0])
        case.update(id="other-case", project=project["id"])
        self.manifest["cases"].append(case)
        label = copy.deepcopy(self.labels["labels"][0])
        label["id"] = case["id"]
        self.labels["labels"].append(label)
        return project, case, label

    def predictions(self, corpus, *, changes=None, name="predictions.json"):
        cases = select_cases(corpus)
        exported = export_inputs(corpus)
        from evals.holdout.run import _opaque_id
        rows = [{"id": _opaque_id(corpus, case), **copy.deepcopy(case["expected"])} for case in cases]
        if changes:
            changes(rows)
        value = {"schema": PREDICTION_SCHEMA, "input_sha256": exported["input_sha256"], "results": rows}
        path = self.folder / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_included_cases_are_only_synthetic_development_self_tests(self):
        corpus = load_corpus()
        report = evaluate(corpus, repeats=3)
        self.assertEqual((report["cases"], report["passed"], report["stable_cases"]), (4, 4, 4))
        self.assertEqual(report["analysis_calls"], 12)
        composition = report["composition"]
        self.assertEqual(composition["independent_project_groups"], 0)
        self.assertEqual(composition["synthetic_project_groups"], 1)
        self.assertEqual(composition["declared_unseen_holdout_project_groups"], 0)
        self.assertEqual(composition["label_review"], {"agent-reviewed": 4})
        self.assertIn("analyzers/javascript/integrity.json", report["analyzer"]["source_sha256"])

    def test_changed_capture_bytes_are_rejected_before_analysis(self):
        artifact = self.manifest["cases"][0]["artifacts"][0]
        (self.folder / artifact["path"]).write_bytes(b"PRIVATE-CAPTURE-CANARY")
        with self.assertRaisesRegex(EvaluationError, "SHA-256 mismatch") as caught:
            self.load()
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_label_digest_binds_the_manifest(self):
        self.manifest["name"] = "Changed name"
        self.path.write_text(json.dumps(self.manifest), encoding="utf-8")
        with self.assertRaisesRegex(EvaluationError, "digest mismatch"):
            self.load()

    def test_only_manifest_and_label_newlines_are_normalized(self):
        original = self.load()["sha256"]
        self.path.write_bytes(self.path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.load()["sha256"], original)
        capture = self.folder / self.manifest["cases"][0]["artifacts"][0]["path"]
        capture.write_bytes(capture.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaises(EvaluationError):
            self.load()

    def test_project_group_cannot_cross_splits_under_another_id(self):
        self.manifest["projects"][0]["kind"] = "real"
        self.extra_project(group=self.manifest["projects"][0]["group"])
        self.write()
        with self.assertRaisesRegex(EvaluationError, "group leaks"):
            self.load()

    def test_duplicate_capture_cannot_cross_split_under_another_group(self):
        self.extra_project()
        self.write()
        with self.assertRaisesRegex(EvaluationError, "capture bytes leak"):
            self.load()

    def test_same_provenance_cannot_be_disguised_as_another_group(self):
        project, _, _ = self.extra_project(split="development")
        project["provenance"] = copy.deepcopy(self.manifest["projects"][0]["provenance"])
        project["provenance"]["url"] += "/"
        self.write()
        with self.assertRaisesRegex(EvaluationError, "multiple group IDs"):
            self.load()

    def test_github_repository_revision_and_host_aliases_cannot_hide_split_leakage(self):
        base = self.manifest["projects"][0]
        base.update(kind="real", group="github:pallets/flask")
        base["provenance"]["url"] = "https://github.com/pallets/flask/tree/aaaaaaaa/"
        other, _, _ = self.extra_project(group="github:pallets/flask-disguised")
        for url in (
            "https://github.com/pallets/flask/tree/bbbbbbbb/src/flask/app.py",
            "https://www.github.com/Pallets/Flask/blob/bbbbbbbb/src/flask/app.py",
            "https://raw.githubusercontent.com/pallets/flask/bbbbbbbb/src/flask/app.py",
            "https://github.com/pallets/flask.git",
            "https://github.com/%70allets/%66lask/blob/bbbbbbbb/app.py",
        ):
            other["provenance"]["url"] = url
            other["provenance"]["revision"] = "bbbbbbbb"
            self.write()
            with self.subTest(url=url), self.assertRaisesRegex(EvaluationError, "multiple group IDs"):
                self.load()

    def test_same_forge_repository_different_revisions_can_share_one_development_group(self):
        base = self.manifest["projects"][0]
        base.update(kind="real", group="github:pallets/flask")
        base["provenance"]["url"] = "https://github.com/pallets/flask/tree/aaaaaaaa/"
        other, _, _ = self.extra_project(split="development", group=base["group"])
        other["exposure"] = base["exposure"]
        other["provenance"]["url"] = "https://raw.githubusercontent.com/pallets/flask/bbbbbbbb/app.py"
        self.write()
        corpus = self.load()
        self.assertEqual(describe(corpus, select_cases(corpus))["independent_project_groups"], 1)

    def test_artifact_provenance_overrides_cannot_bypass_repository_group_consistency(self):
        self.manifest["projects"][0]["kind"] = "real"
        other, other_case, _ = self.extra_project(split="development")
        first_artifact = self.manifest["cases"][0]["artifacts"][0]
        first_artifact["provenance"] = {
            **self.manifest["projects"][0]["provenance"],
            "url": "https://github.com/pallets/flask/blob/aaaaaaaa/src/app.py",
        }
        other_case["artifacts"][0]["provenance"] = {
            **other["provenance"],
            "url": "https://raw.githubusercontent.com/pallets/flask/bbbbbbbb/src/app.py",
        }
        self.write()
        with self.assertRaisesRegex(EvaluationError, "multiple group IDs"):
            self.load()

    def test_artifact_override_cannot_relabel_another_projects_repository(self):
        self.manifest["projects"][0]["kind"] = "real"
        self.manifest["projects"][0]["provenance"]["url"] = "https://github.com/pallets/flask/tree/aaaaaaaa"
        other, other_case, _ = self.extra_project(split="development")
        other_case["artifacts"][0]["provenance"] = {
            **other["provenance"], "url": "https://www.github.com/pallets/flask/blob/bbbbbbbb/app.py"}
        self.write()
        with self.assertRaisesRegex(EvaluationError, "multiple group IDs"):
            self.load()

    def test_legitimate_distinct_forge_repositories_remain_independent(self):
        self.manifest["projects"][0]["kind"] = "real"
        self.manifest["projects"][0]["provenance"]["url"] = "https://github.com/pallets/flask/tree/aaaaaaaa"
        other, _, _ = self.extra_project(split="development")
        other["provenance"]["url"] = "https://raw.githubusercontent.com/pallets/werkzeug/bbbbbbbb/app.py"
        self.write()
        corpus = self.load()
        self.assertEqual(describe(corpus, select_cases(corpus))["independent_project_groups"], 2)

    def test_gitlab_nested_namespace_and_bitbucket_revision_urls_have_stable_identities(self):
        equivalents = (
            ("https://gitlab.com/group/subgroup/project.git", "https://www.gitlab.com/group/subgroup/project/-/raw/abcdef/src/main.js"),
            ("https://gitlab.com/group/subgroup/project/-/tree/aaaa", "https://gitlab.com/group/subgroup/project/-/blob/bbbb/file.html"),
            ("https://bitbucket.org/owner/project.git", "https://www.bitbucket.org/owner/project/raw/bbbb/file.html"),
            ("https://bitbucket.org/owner/project/src/aaaa/main.js", "https://bitbucket.org/owner/project/raw/bbbb/main.js"),
        )
        for first, second in equivalents:
            with self.subTest(first=first, second=second):
                self.assertEqual(provenance_identity(first), provenance_identity(second))
        self.assertNotEqual(provenance_identity("https://gitlab.com/group/subgroup/project"),
                            provenance_identity("https://gitlab.com/group/another/project"))

    def test_multiple_project_records_in_one_group_count_once(self):
        self.manifest["projects"][0]["kind"] = "real"
        project, _, _ = self.extra_project(split="development", group=self.manifest["projects"][0]["group"])
        project["exposure"] = self.manifest["projects"][0]["exposure"]
        self.write()
        corpus = self.load()
        composition = describe(corpus, select_cases(corpus))
        self.assertEqual(composition["project_records"], 2)
        self.assertEqual(composition["independent_project_groups"], 1)
        self.assertEqual(composition["unique_capture_hashes"], 4)

    def test_synthetic_project_cannot_claim_holdout(self):
        self.manifest["projects"][0]["split"] = "holdout"
        self.write()
        with self.assertRaisesRegex(EvaluationError, "Synthetic projects"):
            self.load()

    def test_exposed_group_cannot_be_reintroduced_as_unseen_under_another_id(self):
        self.manifest["projects"][0]["kind"] = "real"
        project, _, _ = self.extra_project(split="development", group=self.manifest["projects"][0]["group"])
        project["exposure"] = "unseen"
        self.write()
        with self.assertRaisesRegex(EvaluationError, "exposure declarations"):
            self.load()

    def test_unknown_or_exposed_real_holdout_is_not_counted_unseen(self):
        project = self.manifest["projects"][0]
        project.update(kind="real", split="holdout")
        for exposure in ("unknown", "exposed"):
            project["exposure"] = exposure
            self.write()
            corpus = self.load()
            composition = describe(corpus, select_cases(corpus))
            self.assertEqual(composition["declared_unseen_holdout_project_groups"], 0)
            self.assertEqual(composition["declared_unseen_human_reviewed_holdout_groups"], 0)
        project["exposure"] = "unseen"
        self.write()
        corpus = self.load()
        composition = describe(corpus, select_cases(corpus))
        self.assertEqual(composition["declared_unseen_holdout_project_groups"], 1)
        self.assertEqual(composition["declared_unseen_human_reviewed_holdout_groups"], 0)

    def test_review_status_requires_explicit_date_and_reviewer(self):
        review = self.labels["labels"][0]["review"]
        review.update(status="human-reviewed", reviewed_at=None)
        self.write()
        with self.assertRaisesRegex(EvaluationError, "review date"):
            self.load()
        review.update(status="unreviewed", reviewer="", reviewed_at=None)
        self.write()
        self.assertEqual(self.load()["cases"][0]["review"]["status"], "unreviewed")

    def test_schema_errors_are_controlled_for_unknown_and_wrong_typed_fields(self):
        mutations = [
            lambda: self.manifest.update(extra="unknown"),
            lambda: self.manifest["projects"][0].update(split=[]),
            lambda: self.manifest["cases"][0].update(project=[]),
            lambda: self.manifest["cases"][0]["artifacts"][0].update(kind=[]),
            lambda: self.labels["labels"][0]["review"].update(status=[]),
            lambda: self.labels["labels"][0]["expected"].update(extra=True),
        ]
        manifest, labels = copy.deepcopy(self.manifest), copy.deepcopy(self.labels)
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.manifest, self.labels = copy.deepcopy(manifest), copy.deepcopy(labels)
                mutate()
                self.write()
                with self.assertRaises(EvaluationError):
                    self.load()

    def test_duplicate_json_keys_and_nonfinite_numbers_are_errors(self):
        for raw in (b'{"schema":"x","schema":"x"}', b'{"schema":NaN}'):
            self.path.write_bytes(raw)
            with self.assertRaises(EvaluationError):
                self.load()

    def test_missing_duplicate_and_unknown_labels_fail(self):
        labels = copy.deepcopy(self.labels["labels"])
        for rows in (labels[:-1], labels + [labels[0]], [{**labels[0], "id": "unknown"}, *labels[1:]]):
            self.labels["labels"] = rows
            self.write()
            with self.assertRaises(EvaluationError):
                self.load()

    def test_missing_unresolved_set_is_not_an_empty_success(self):
        del self.labels["labels"][0]["expected"]["unresolved"]
        self.write()
        with self.assertRaises(EvaluationError):
            self.load()

    def test_traversal_absolute_and_windows_special_paths_are_errors(self):
        artifact = self.manifest["cases"][0]["artifacts"][0]
        for name in ("../secret.html", "/secret.html", "C:/secret.html", "fixtures\\secret.html", "fixtures//secret.html", "fixtures/./secret.html", "fixtures/file:stream"):
            artifact["path"] = name
            self.write()
            with self.subTest(path=name), self.assertRaises(EvaluationError):
                self.load()

    def test_linked_capture_is_rejected_where_symlinks_are_available(self):
        source = self.folder / "fixtures/navigation-and-form.html"
        link = self.folder / "fixtures/linked.html"
        try:
            link.symlink_to(source)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is unavailable")
        self.manifest["cases"][0]["artifacts"][0]["path"] = "fixtures/linked.html"
        self.write()
        with self.assertRaisesRegex(EvaluationError, "Linked corpus"):
            self.load()

    def test_capture_must_be_utf8_and_within_its_kind_limit(self):
        artifact = self.manifest["cases"][1]["artifacts"][0]
        path = self.folder / artifact["path"]
        for data in (b"\xff", b"x" * (512 * 1024 + 1)):
            path.write_bytes(data)
            artifact["sha256"] = hashlib.sha256(data).hexdigest()
            self.write()
            with self.assertRaises(EvaluationError):
                self.load()

    def test_export_contains_opaque_hash_ordered_inputs_and_no_gold_metadata(self):
        corpus = self.load()
        exported = export_inputs(corpus)
        self.assertEqual(set(exported), {"schema", "input_sha256", "cases"})
        self.assertEqual(len(exported["cases"]), 4)
        ids = [case["id"] for case in exported["cases"]]
        self.assertEqual(ids, sorted(ids))
        self.assertTrue(all(identity.startswith("case_") and len(identity) == 25 for identity in ids))
        for case in exported["cases"]:
            self.assertEqual(set(case), {"id", "artifacts"})
            self.assertTrue(all(set(artifact) == {"kind", "text"} for artifact in case["artifacts"]))
        text = json.dumps(exported)
        for private in ("synthetic-harness", "navigation-form", "agent-reviewed", "synthetic-harness-v1", "navigation-and-form.html"):
            self.assertNotIn(private, text)
        self.assertEqual(exported, export_inputs(corpus))

    def test_empty_split_is_an_error_not_a_vacuous_pass(self):
        with self.assertRaisesRegex(EvaluationError, "no cases"):
            export_inputs(self.load(), "holdout")

    def test_duplicate_operations_and_unknown_prediction_fields_fail(self):
        good = copy.deepcopy(self.labels["labels"][0]["expected"])
        for bad in ({**good, "unknown": "ignored?"}, {**good, "routes": good["routes"] * 2}, {"routes": [], "unresolved": [], "rejected": True}, {"rejected": 1}):
            with self.assertRaises(EvaluationError):
                canonical_prediction(bad)

    def test_right_route_wrong_evidence_is_a_strict_failure(self):
        expected = canonical_prediction(self.labels["labels"][0]["expected"])
        kind = expected["routes"][0]["evidence"][0]["kind"]
        for field, value in (("confidence", "inferred"), ("evidence_kinds", ["javascript"]),
                             ("evidence", [{"source": "source_2", "location": "line 9", "kind": kind}])):
            observed = copy.deepcopy(expected)
            observed["routes"][0][field] = value
            if field == "evidence_kinds":
                observed["routes"][0].pop("evidence")
            self.assertIn("routes_evidence", score(expected, observed))

    def test_direct_scorer_does_not_ignore_unknown_prediction_fields(self):
        expected = {"routes": [], "unresolved": []}
        self.assertEqual(score(expected, {**expected, "unknown": True}), ["invalid_prediction"])

    def test_internal_error_is_never_a_correct_rejection(self):
        self.assertEqual(score({"rejected": True}, {"error": "analysis_failed"}), ["evaluation_error"])
        self.assertEqual(score({"rejected": True}, {"routes": [], "unresolved": []}), ["expected_rejection"])
        self.assertEqual(score({"routes": [], "unresolved": []}, {"rejected": True}), ["unexpected_rejection"])

    def test_network_regression_is_an_error_including_on_rejection_cases(self):
        def analyzer(*args, **kwargs):
            socket.getaddrinfo("PRIVATE-HOST-CANARY", 443)
        report = evaluate(self.load(), repeats=1, analyze=analyzer, artifact_error=RejectedInput)
        self.assertEqual(report["passed"], 0)
        self.assertTrue(all(row["failures"] == ["evaluation_error"] for row in report["results"]))
        self.assertNotIn("PRIVATE-HOST-CANARY", json.dumps(report))

    def test_basic_evaluation_never_executes_captures_or_installs_dependencies(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Unexpected process")), patch("subprocess.run", side_effect=AssertionError("Unexpected process")):
            report = evaluate(self.load(), repeats=1)
        self.assertEqual(report["passed"], 4)

    def test_repeat_instability_fails_and_is_not_an_extra_independent_sample(self):
        corpus = self.load()
        corpus["cases"] = [corpus["cases"][2]]
        calls = []
        def analyzer(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return {"routes": []}
            raise RejectedInput
        report = evaluate(corpus, repeats=3, analyze=analyzer, artifact_error=RejectedInput)
        self.assertEqual(report["cases"], 1)
        self.assertEqual(report["composition"]["tasks"], 1)
        self.assertEqual(report["analysis_calls"], 3)
        self.assertEqual(report["passed"], 0)
        self.assertEqual(report["results"][0]["failures"], ["repeat_inconsistency"])
        self.assertIsNone(report["known_operations"]["precision"])
        self.assertIsNone(report["known_operations"]["recall"])
        self.assertIsNone(report["known_operations"]["f1"])

    def test_saved_predictions_score_without_analyzer_or_model_calls(self):
        corpus = self.load()
        path = self.predictions(corpus)
        with patch("surface_map.analyze.analyze", side_effect=AssertionError("No analyzer")):
            report = score_prediction_files(corpus, [path, path, path])
        self.assertEqual((report["cases"], report["passed"], report["stable_cases"]), (4, 4, 4))
        self.assertEqual(report["engine"], "recorded-predictions")
        self.assertEqual(report["analysis_calls"], 0)
        self.assertEqual(report["scored_observations"], 12)
        self.assertIs(report["analyzer"]["executed"], False)

    def test_extra_model_routes_are_retained_as_false_positives(self):
        corpus = self.load()
        def extra(rows):
            row = next(row for row in rows if row.get("routes") == [] and "rejected" not in row)
            row["routes"].append({"method": "GET", "path": "/unexpected", "confidence": "inferred", "evidence_kinds": ["javascript"]})
        report = score_prediction_files(corpus, [self.predictions(corpus, changes=extra)])
        self.assertEqual(report["passed"], 3)
        self.assertEqual(report["known_operations"]["fp"], 1)
        self.assertTrue(any("routes" in row["failures"] for row in report["results"]))

    def test_saved_prediction_ids_digest_and_unknown_fields_are_strict(self):
        corpus = self.load()
        mutations = [lambda rows: rows.pop(), lambda rows: rows.append(copy.deepcopy(rows[0])),
                     lambda rows: rows[0].update(id="unknown"), lambda rows: rows[0].update(extra=True)]
        for mutate in mutations:
            path = self.predictions(corpus, changes=mutate)
            with self.assertRaises(EvaluationError):
                score_prediction_files(corpus, [path])
        path = self.predictions(corpus)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["input_sha256"] = "0" * 64
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(EvaluationError):
            score_prediction_files(corpus, [path])

    def test_cli_errors_are_source_free_and_output_files_are_explicit(self):
        with redirect_stdout(io.StringIO()) as stdout:
            status = main(["--corpus", str(self.path), "--validate"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["schema"], CORPUS_SCHEMA)
        self.path.write_bytes(b"PRIVATE-INVALID-JSON-CANARY")
        with redirect_stdout(io.StringIO()) as stdout:
            status = main(["--corpus", str(self.path)])
        self.assertEqual(status, 2)
        self.assertNotIn("PRIVATE", stdout.getvalue())
        self.assertNotIn(str(self.path), stdout.getvalue())

    def test_aggregate_publication_omits_case_results_predictions_and_failures(self):
        full = evaluate(self.load(), repeats=1)
        aggregate = aggregate_report(full)
        self.assertIn("results", full)
        self.assertNotIn("results", aggregate)
        self.assertTrue(aggregate["aggregate_only"])
        self.assertIn("privacy review", aggregate["publication_scope"])
        self.assertEqual(aggregate["known_operations"], full["known_operations"])
        self.assertEqual(aggregate["projects"], full["projects"])
        text = json.dumps(aggregate)
        self.assertNotIn("navigation-form", text)
        self.assertNotIn("/api/orders", text)
        def object_keys(value):
            if isinstance(value, dict):
                return set(value) | set().union(*(object_keys(child) for child in value.values()), set())
            if isinstance(value, list):
                return set().union(*(object_keys(child) for child in value), set())
            return set()
        self.assertFalse({"prediction", "failures", "results"} & object_keys(aggregate))

    def test_aggregate_cli_preserves_failure_exit_and_accepts_validation(self):
        corpus = self.load()
        def extra(rows):
            row = next(row for row in rows if row.get("routes") == [] and "rejected" not in row)
            row["routes"].append({"method": "GET", "path": "/unexpected", "confidence": "inferred", "evidence_kinds": ["javascript"]})
        predictions = self.predictions(corpus, changes=extra)
        output = self.folder / "aggregate.json"
        with redirect_stdout(io.StringIO()):
            status = main(["--corpus", str(self.path), "--predictions", str(predictions),
                           "--aggregate-only", "--check", "--output", str(output)])
        self.assertEqual(status, 1)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["passed"], 3)
        self.assertEqual(report["known_operations"]["fp"], 1)
        self.assertNotIn("results", report)
        with redirect_stdout(io.StringIO()) as stdout:
            status = main(["--corpus", str(self.path), "--validate", "--aggregate-only"])
        self.assertEqual(status, 0)
        self.assertTrue(json.loads(stdout.getvalue())["aggregate_only"])

    def test_aggregate_only_cannot_be_used_to_export_source_inputs(self):
        inputs = self.folder / "inputs.json"
        with redirect_stdout(io.StringIO()):
            status = main(["--corpus", str(self.path), "--aggregate-only", "--export-inputs", str(inputs)])
        self.assertEqual(status, 2)
        self.assertFalse(inputs.exists())
        with self.assertRaises(EvaluationError):
            aggregate_report(export_inputs(self.load()))

    def test_schema_files_and_package_data_are_valid_json(self):
        schema = json.loads((FOLDER / "schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$defs"]["corpus"]["properties"]["schema"]["const"], CORPUS_SCHEMA)
        self.assertEqual(schema["$defs"]["labels"]["properties"]["schema"]["const"], LABEL_SCHEMA)


if __name__ == "__main__":
    unittest.main()
