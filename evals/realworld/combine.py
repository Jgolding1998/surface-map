"""Combine explicit, reviewed development batches without running an analyzer.

Run ``python -m evals.realworld.combine --expected-projects 21`` after every
batch is present. ``--check`` verifies tracked combined files without writing.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from evals.holdout.run import EvaluationError, load_corpus, provenance_identity

DEFAULT_BATCHES = ("frameworks", "html-clients", "request-patterns")
SOURCE_SCHEMA = "surface-map-realworld-combined-sources/1"
OUTPUTS = {"corpus": "combined-corpus.json", "labels": "combined-labels.json",
           "sources": "combined-sources.json"}
MAX_SOURCE_BYTES = 16 * 1024 * 1024


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def render(value):
    """Canonical on-disk encoding, also used to bind combined labels."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _read_validated(path, digest):
    """Do not combine a different manifest than the one the loader validated."""
    try:
        data = path.read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        raise EvaluationError("A validated source file cannot be read") from None
    if len(data) > MAX_SOURCE_BYTES or _digest(data) != digest:
        raise EvaluationError("A source changed after validation")
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise EvaluationError("A validated source document is invalid") from None


def _batch_names(batches):
    if not isinstance(batches, (tuple, list)):
        raise EvaluationError("Batch names must be an explicit list")
    if any(not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name) for name in batches):
        raise EvaluationError("Invalid explicit batch name")
    if len(batches) != len(set(batches)):
        raise EvaluationError("Duplicate explicit batch name")
    return sorted(batches)


def combine(root, batches=DEFAULT_BATCHES):
    """Return combined corpus, labels and source receipts; never write or analyze.

    ``root`` contains the initial ``corpus.json``/``labels.json``. Every named
    batch is read from ``root/batches/<name>/``; no directory discovery occurs.
    Expected labels and review notes are copied from those validated files,
    without deriving facts from Surface Map predictions.
    """
    root = Path(root)
    prefixes = [""] + ["batches/" + name + "/" for name in _batch_names(batches)]
    projects, cases, labels, sources = [], [], [], []
    project_ids, case_ids, groups, provenance_groups = set(), set(), set(), {}
    def register_provenance(url, group):
        identity = provenance_identity(url)
        if identity in provenance_groups and provenance_groups[identity] != group:
            raise EvaluationError("Duplicate upstream provenance across combined sources")
        provenance_groups[identity] = group
    for prefix in prefixes:
        folder = root / prefix
        manifest_path, labels_path = folder / "corpus.json", folder / "labels.json"
        validated = load_corpus(manifest_path, labels_path)
        manifest = _read_validated(manifest_path, validated["sha256"])
        label_file = _read_validated(labels_path, validated["labels_sha256"])
        for project in manifest["projects"]:
            if (project["split"], project["kind"], project.get("exposure")) != ("development", "real", "exposed"):
                raise EvaluationError("Combined sources must be real, exposed development projects")
            if project["id"] in project_ids:
                raise EvaluationError("Duplicate project ID across combined sources")
            if project["group"] in groups:
                raise EvaluationError("Duplicate independent project group across combined sources")
            register_provenance(project["provenance"]["url"], project["group"])
            project_ids.add(project["id"])
            groups.add(project["group"])
            projects.append(copy.deepcopy(project))
        source_groups = {project["id"]: project["group"] for project in manifest["projects"]}
        for case in manifest["cases"]:
            if case["id"] in case_ids:
                raise EvaluationError("Duplicate task ID across combined sources")
            case_ids.add(case["id"])
            copied = copy.deepcopy(case)
            for artifact in copied["artifacts"]:
                if "provenance" in artifact:
                    register_provenance(artifact["provenance"]["url"], source_groups[case["project"]])
                artifact["path"] = prefix + artifact["path"]
            cases.append(copied)
        for label in label_file["labels"]:
            review = label["review"]
            if review["status"] not in {"agent-reviewed", "human-reviewed"} or not review["notes"].strip():
                raise EvaluationError("Combined labels require explicit source review notes")
            labels.append(copy.deepcopy(label))
        receipt = {"prefix": prefix, "corpus": prefix + "corpus.json", "corpus_sha256": validated["sha256"],
                   "labels": prefix + "labels.json", "labels_sha256": validated["labels_sha256"],
                   "project_ids": sorted(project["id"] for project in manifest["projects"]),
                   "project_groups": sorted(project["group"] for project in manifest["projects"]),
                   "case_ids": sorted(case["id"] for case in manifest["cases"])}
        # The optional sidecar retains license paths and excerpt provenance in
        # its own original directory. Its original relative context is preserved.
        sidecar = folder / "sources.json"
        if sidecar.exists():
            from evals.holdout.run import _read
            receipt["provenance_sidecar"] = {"path": prefix + "sources.json",
                                             "sha256": _digest(_read(sidecar, MAX_SOURCE_BYTES).replace(b"\r\n", b"\n"))}
        sources.append(receipt)
    corpus = {"schema": "surface-map-holdout-corpus/1", "name": "surface-map-realworld-development-combined-v1",
              "projects": sorted(projects, key=lambda item: item["id"]),
              "cases": sorted(cases, key=lambda item: item["id"])}
    label_file = {"schema": "surface-map-holdout-labels/1", "corpus_sha256": _digest(render(corpus)),
                  "labels": sorted(labels, key=lambda item: item["id"])}
    source_file = {"schema": SOURCE_SCHEMA, "combined_corpus_sha256": _digest(render(corpus)),
                   "combined_labels_sha256": _digest(render(label_file)),
                   "projects": len(projects), "independent_project_groups": len(groups), "tasks": len(cases),
                   "artifacts": sum(len(case["artifacts"]) for case in cases),
                   "sources": sources,
                   "scope": "Source manifests, capture hashes and separate labels were validated before combination. Labels and review declarations are copied, never generated from analyzer predictions. These are exposed, agent- or human-reviewed development projects; combination creates no unseen holdout, human certification, model comparison or additional independent projects."}
    return {"corpus": corpus, "labels": label_file, "sources": source_file}


def write_outputs(root, combined):
    """Stage every deterministic output before replacing any tracked output."""
    root = Path(root)
    staged = []
    try:
        for key, name in OUTPUTS.items():
            with tempfile.NamedTemporaryFile(mode="wb", dir=root, prefix=".combined-", suffix=".tmp", delete=False) as stream:
                path = Path(stream.name)
                staged.append((path, root / name))
                stream.write(render(combined[key]))
        for temporary, target in staged:
            os.replace(temporary, target)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def check_outputs(root, combined):
    """Check exact deterministic content, permitting checkout CRLF conversion."""
    root = Path(root)
    for key, name in OUTPUTS.items():
        try:
            current = (root / name).read_bytes().replace(b"\r\n", b"\n")
        except OSError:
            return False
        if current != render(combined[key]):
            return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    parser.add_argument("--check", action="store_true", help="Fail on combined-file drift without writing")
    parser.add_argument("--expected-projects", type=int, help="Require this many distinct independent project groups")
    args = parser.parse_args(argv)
    try:
        combined = combine(args.root)
        count = combined["sources"]["independent_project_groups"]
        if args.expected_projects is not None and (args.expected_projects < 1 or count != args.expected_projects):
            raise EvaluationError("Unexpected independent project count")
        if args.check:
            if not check_outputs(args.root, combined):
                print("Combined files differ or are missing; regenerate after reviewing validated source changes.")
                return 1
        else:
            write_outputs(args.root, combined)
        print(f"Combined development corpus {'verified' if args.check else 'written'}: {count} projects, {combined['sources']['tasks']} tasks.")
        return 0
    except (EvaluationError, OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        print("Combination could not run: invalid or incomplete explicit source batches, labels, hashes or output files.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
