"""Integrity check for the real-world development seed. Reads files only; never runs artifacts or the analyzer.

Usage: python -I evals/realworld/verify.py
"""
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PROJECT_KEYS = {"id", "group", "split", "kind", "exposure", "provenance"}
PROVENANCE_KEYS = {"url", "revision", "license", "retrieved_at"}
KINDS = {"html", "javascript", "typescript", "har"}
CONFIDENCE = {"declared", "inferred", "observed"}


def fail(message):
    print(f"FAIL: {message}")
    sys.exit(1)


def main():
    corpus_bytes = (ROOT / "corpus.json").read_bytes().replace(b"\r\n", b"\n")
    corpus = json.loads(corpus_bytes)
    labels = json.loads((ROOT / "labels.json").read_bytes())
    if corpus.get("schema") != "surface-map-holdout-corpus/1":
        fail("corpus schema")
    if labels.get("schema") != "surface-map-holdout-labels/1":
        fail("labels schema")
    if labels.get("corpus_sha256") != hashlib.sha256(corpus_bytes).hexdigest():
        fail("labels.corpus_sha256 does not match corpus.json (CRLF normalized to LF)")
    projects = {}
    for project in corpus["projects"]:
        if set(project) != PROJECT_KEYS or set(project["provenance"]) != PROVENANCE_KEYS:
            fail(f"project keys {project.get('id')}")
        if (project["split"], project["kind"], project["exposure"]) != ("development", "real", "exposed"):
            fail(f"project split/kind/exposure {project['id']}")
        if not (ROOT / "licenses" / f"{project['id']}.txt").is_file():
            fail(f"missing license file for {project['id']}")
        projects[project["id"]] = project
    case_ids = []
    for case in corpus["cases"]:
        if case["project"] not in projects:
            fail(f"unknown project in {case['id']}")
        revision = projects[case["project"]]["provenance"]["revision"]
        for artifact in case["artifacts"]:
            path = (ROOT / artifact["path"]).resolve()
            if ROOT not in path.parents or artifact["kind"] not in KINDS:
                fail(f"artifact path/kind {artifact['path']}")
            data = path.read_bytes().replace(b"\r\n", b"\n")
            if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
                fail(f"sha256 mismatch {artifact['path']}")
            provenance = artifact.get("provenance", {})
            if provenance.get("revision") != revision or revision not in provenance.get("url", ""):
                fail(f"artifact revision {artifact['path']}")
        case_ids.append(case["id"])
    if len(set(case_ids)) != len(case_ids):
        fail("duplicate case id")
    label_ids = [label["id"] for label in labels["labels"]]
    if sorted(label_ids) != sorted(case_ids):
        fail("labels and cases differ")
    for label in labels["labels"]:
        review = label["review"]
        if review["status"] != "agent-reviewed" or review["reviewer"] != "Claude Code" or not review["notes"]:
            fail(f"review metadata {label['id']}")
        for route in label["expected"]["routes"]:
            if route["confidence"] not in CONFIDENCE or not route["path"].startswith("/") or not route["evidence_kinds"]:
                fail(f"route shape {label['id']}")
        for target in label["expected"]["unresolved"]:
            if target["confidence"] != "inferred" or target["evidence_kinds"] != ["javascript"]:
                fail(f"unresolved shape {label['id']}")
    print(f"OK: {len(projects)} projects, {len(case_ids)} cases, "
          f"{sum(len(c['artifacts']) for c in corpus['cases'])} artifacts")


if __name__ == "__main__":
    main()
