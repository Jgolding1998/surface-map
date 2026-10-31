from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from evals.holdout.run import EvaluationError, load_corpus
from evals.realworld.combine import (
    DEFAULT_BATCHES, OUTPUTS, check_outputs, combine, main, render, write_outputs,
)


class RealworldCombinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source_data = {}
        self.add_source("", "base-project")
        for batch in DEFAULT_BATCHES:
            self.add_source("batches/" + batch + "/", batch + "-project")

    def add_source(self, prefix, identity):
        folder = self.root / prefix
        (folder / "artifacts").mkdir(parents=True, exist_ok=True)
        # Deliberately identical normalized facts across independent toy projects
        # ensure the combiner does not turn output overlap into data exclusion.
        capture = b'<a href="/docs">Docs</a>\n'
        (folder / "artifacts/page.html").write_bytes(capture)
        project = {"id": identity, "group": "github:fixtures/" + identity,
                   "split": "development", "kind": "real", "exposure": "exposed",
                   "provenance": {"url": "https://example.invalid/" + identity,
                                  "revision": "fixture-revision", "license": "MIT", "retrieved_at": "2026-10-31"}}
        manifest = {"schema": "surface-map-holdout-corpus/1", "name": identity,
                    "projects": [project], "cases": [{"id": identity + "-task", "project": identity,
                    "family": "html-link", "artifacts": [{"kind": "html", "path": "artifacts/page.html",
                    "sha256": hashlib.sha256(capture).hexdigest()}]}]}
        labels = {"schema": "surface-map-holdout-labels/1", "corpus_sha256": "",
                  "labels": [{"id": identity + "-task", "review": {"status": "agent-reviewed",
                  "reviewer": "Synthetic test author", "reviewed_at": "2026-10-31",
                  "notes": "Synthetic test gold manually authored from the link; not real-world evidence or an analyzer prediction."},
                  "expected": {"routes": [{"method": "GET", "path": "/docs", "confidence": "declared",
                  "evidence_kinds": ["link"]}], "unresolved": []}}]}
        self.source_data[prefix] = (manifest, labels)
        self.write_source(prefix)

    def write_source(self, prefix):
        manifest, labels = self.source_data[prefix]
        raw = render(manifest)
        labels["corpus_sha256"] = hashlib.sha256(raw).hexdigest()
        folder = self.root / prefix
        (folder / "corpus.json").write_bytes(raw)
        (folder / "labels.json").write_bytes(render(labels))

    def test_explicit_batches_are_combined_with_safe_prefixed_paths(self):
        combined = combine(self.root)
        self.assertEqual(combined["sources"]["independent_project_groups"], 4)
        self.assertEqual(combined["sources"]["tasks"], 4)
        cases = {case["project"]: case for case in combined["corpus"]["cases"]}
        self.assertEqual(cases["base-project"]["artifacts"][0]["path"], "artifacts/page.html")
        self.assertEqual(cases["frameworks-project"]["artifacts"][0]["path"], "batches/frameworks/artifacts/page.html")
        self.assertTrue(all(".." not in item["path"].split("/") for case in cases.values() for item in case["artifacts"]))
        write_outputs(self.root, combined)
        validated = load_corpus(self.root / "combined-corpus.json", self.root / "combined-labels.json")
        self.assertEqual(len(validated["cases"]), 4)

    def test_source_order_does_not_change_deterministic_outputs(self):
        first = combine(self.root)
        second = combine(self.root, batches=tuple(reversed(DEFAULT_BATCHES)))
        self.assertEqual({key: render(value) for key, value in first.items()},
                         {key: render(value) for key, value in second.items()})
        self.assertEqual(first, combine(self.root))

    def test_expected_gold_and_review_notes_are_copied_without_analyzer(self):
        def blocked(*args, **kwargs):
            raise AssertionError("No analyzer, process or network operation is permitted")
        with patch("surface_map.analyze.analyze", blocked), patch("evals.holdout.run.evaluate", blocked), patch.object(socket, "getaddrinfo", blocked), patch("subprocess.Popen", blocked):
            combined = combine(self.root)
        expected = {label["id"]: label for _, labels in self.source_data.values() for label in labels["labels"]}
        self.assertEqual({label["id"]: label for label in combined["labels"]["labels"]}, expected)

    def test_combined_and_source_digest_binding(self):
        combined = combine(self.root)
        corpus_sha = hashlib.sha256(render(combined["corpus"])).hexdigest()
        labels_sha = hashlib.sha256(render(combined["labels"])).hexdigest()
        self.assertEqual(combined["labels"]["corpus_sha256"], corpus_sha)
        self.assertEqual(combined["sources"]["combined_corpus_sha256"], corpus_sha)
        self.assertEqual(combined["sources"]["combined_labels_sha256"], labels_sha)
        for source in combined["sources"]["sources"]:
            for field in ("corpus", "labels"):
                data = (self.root / source[field]).read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(source[field + "_sha256"], hashlib.sha256(data).hexdigest())

    def test_optional_provenance_sidecar_retains_original_context_and_hash(self):
        sidecar = self.root / "batches/frameworks/sources.json"
        sidecar.write_bytes(b'{"license_file":"licenses/frameworks.txt"}\n')
        combined = combine(self.root)
        receipt = next(row for row in combined["sources"]["sources"] if row["prefix"] == "batches/frameworks/")
        self.assertEqual(receipt["provenance_sidecar"], {"path": "batches/frameworks/sources.json", "sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest()})

    def test_duplicate_project_id_case_id_or_group_is_rejected(self):
        prefix = "batches/frameworks/"
        original = copy.deepcopy(self.source_data[prefix])
        for duplicate in ("id", "group", "case"):
            self.source_data[prefix] = copy.deepcopy(original)
            manifest, labels = self.source_data[prefix]
            if duplicate == "id":
                manifest["projects"][0]["id"] = "base-project"
                manifest["cases"][0]["project"] = "base-project"
            elif duplicate == "group":
                manifest["projects"][0]["group"] = "github:fixtures/base-project"
            else:
                manifest["cases"][0]["id"] = "base-project-task"
                labels["labels"][0]["id"] = "base-project-task"
            self.write_source(prefix)
            with self.subTest(duplicate=duplicate), self.assertRaises(EvaluationError):
                combine(self.root)

    def test_invalid_or_missing_labels_do_not_produce_partial_outputs(self):
        for name in OUTPUTS.values():
            (self.root / name).write_bytes(b"old-output-canary")
        label_path = self.root / "batches/request-patterns/labels.json"
        label_path.unlink()
        with redirect_stdout(io.StringIO()):
            result = main(["--root", str(self.root)])
        self.assertEqual(result, 2)
        self.assertTrue(all((self.root / name).read_bytes() == b"old-output-canary" for name in OUTPUTS.values()))
        self.write_source("batches/request-patterns/")
        labels = json.loads(label_path.read_text(encoding="utf-8"))
        labels["corpus_sha256"] = "0" * 64
        label_path.write_bytes(render(labels))
        with self.assertRaises(EvaluationError):
            combine(self.root)

    def test_same_upstream_provenance_cannot_be_counted_again_under_a_new_group(self):
        prefix = "batches/frameworks/"
        self.source_data[prefix][0]["projects"][0]["provenance"] = copy.deepcopy(
            self.source_data[""][0]["projects"][0]["provenance"])
        self.write_source(prefix)
        with self.assertRaisesRegex(EvaluationError, "Duplicate upstream provenance"):
            combine(self.root)

    def test_repository_revision_and_www_alias_duplicates_are_rejected_before_writing(self):
        self.source_data[""][0]["projects"][0]["provenance"]["url"] = "https://github.com/pallets/flask/tree/aaaaaaaa/"
        self.source_data["batches/frameworks/"][0]["projects"][0]["provenance"]["url"] = "https://www.github.com/Pallets/Flask/blob/bbbbbbbb/src/app.py"
        self.write_source("")
        self.write_source("batches/frameworks/")
        with self.assertRaisesRegex(EvaluationError, "Duplicate upstream provenance"):
            combine(self.root)
        with redirect_stdout(io.StringIO()):
            status = main(["--root", str(self.root)])
        self.assertEqual(status, 2)
        self.assertFalse(any((self.root / name).exists() for name in OUTPUTS.values()))

    def test_artifact_overrides_cannot_hide_same_repository_across_batches(self):
        for prefix, url in (
            ("", "https://github.com/pallets/flask/blob/aaaaaaaa/app.py"),
            ("batches/frameworks/", "https://raw.githubusercontent.com/pallets/flask/bbbbbbbb/app.py"),
        ):
            manifest, _ = self.source_data[prefix]
            manifest["cases"][0]["artifacts"][0]["provenance"] = {
                **manifest["projects"][0]["provenance"], "url": url}
            self.write_source(prefix)
        with self.assertRaisesRegex(EvaluationError, "Duplicate upstream provenance"):
            combine(self.root)

    def test_artifact_override_cannot_hide_another_batchs_project_repository(self):
        self.source_data[""][0]["projects"][0]["provenance"]["url"] = "https://github.com/pallets/flask/tree/aaaaaaaa"
        manifest, _ = self.source_data["batches/frameworks/"]
        manifest["cases"][0]["artifacts"][0]["provenance"] = {
            **manifest["projects"][0]["provenance"],
            "url": "https://www.github.com/pallets/flask/blob/bbbbbbbb/app.py"}
        self.write_source("")
        self.write_source("batches/frameworks/")
        with self.assertRaisesRegex(EvaluationError, "Duplicate upstream provenance"):
            combine(self.root)

    def test_unreviewed_synthetic_holdout_or_unseen_sources_cannot_enter_public_development_combination(self):
        prefix = "batches/frameworks/"
        original = copy.deepcopy(self.source_data[prefix])
        for field, value in (("kind", "synthetic"), ("split", "holdout"), ("exposure", "unseen")):
            self.source_data[prefix] = copy.deepcopy(original)
            self.source_data[prefix][0]["projects"][0][field] = value
            self.write_source(prefix)
            with self.subTest(field=field), self.assertRaises(EvaluationError):
                combine(self.root)
        self.source_data[prefix] = copy.deepcopy(original)
        review = self.source_data[prefix][1]["labels"][0]["review"]
        review.update(status="unreviewed", reviewer="", reviewed_at=None)
        self.write_source(prefix)
        with self.assertRaises(EvaluationError):
            combine(self.root)

    def test_nondefault_directories_are_not_discovered_and_batch_paths_cannot_traverse(self):
        unrelated = self.root / "batches/ignored"
        unrelated.mkdir()
        (unrelated / "corpus.json").write_bytes(b"not-json")
        self.assertEqual(combine(self.root)["sources"]["projects"], 4)
        self.assertEqual(combine(self.root, batches=())["sources"]["projects"], 1)
        for batches in (("../elsewhere",), ("frameworks", "frameworks"), ("/absolute",), "frameworks"):
            with self.subTest(batches=batches), self.assertRaises(EvaluationError):
                combine(self.root, batches=batches)

    def test_check_detects_output_and_source_drift_without_writing(self):
        combined = combine(self.root)
        self.assertFalse(check_outputs(self.root, combined))
        write_outputs(self.root, combined)
        self.assertTrue(check_outputs(self.root, combined))
        path = self.root / "combined-labels.json"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertTrue(check_outputs(self.root, combined))
        path.write_bytes(b"drift-canary")
        with redirect_stdout(io.StringIO()):
            result = main(["--root", str(self.root), "--check"])
        self.assertEqual(result, 1)
        self.assertEqual(path.read_bytes(), b"drift-canary")
        write_outputs(self.root, combined)
        self.source_data[""][1]["labels"][0]["review"]["notes"] += " Additional source review."
        self.write_source("")
        self.assertFalse(check_outputs(self.root, combine(self.root)))

    def test_changed_capture_is_detected_by_source_validation(self):
        artifact = self.root / "batches/frameworks/artifacts/page.html"
        artifact.write_bytes(b"changed capture")
        with self.assertRaisesRegex(EvaluationError, "Capture SHA-256 mismatch"):
            combine(self.root)

    def test_cli_expected_project_count_gate_precedes_writes(self):
        with redirect_stdout(io.StringIO()):
            result = main(["--root", str(self.root), "--expected-projects", "21"])
        self.assertEqual(result, 2)
        self.assertFalse(any((self.root / name).exists() for name in OUTPUTS.values()))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--root", str(self.root), "--expected-projects", "4"]), 0)
            self.assertEqual(main(["--root", str(self.root), "--expected-projects", "4", "--check"]), 0)


if __name__ == "__main__":
    unittest.main()
