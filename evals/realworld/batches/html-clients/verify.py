"""Read-only integrity verification; never import the analyzer or run artifacts."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    corpus_bytes = (ROOT / "corpus.json").read_bytes().replace(b"\r\n", b"\n")
    corpus = json.loads(corpus_bytes)
    labels = json.loads((ROOT / "labels.json").read_bytes())
    sources = json.loads((ROOT / "sources.json").read_bytes())
    assert corpus["schema"] == "surface-map-holdout-corpus/1"
    assert labels["schema"] == "surface-map-holdout-labels/1"
    assert sources["schema"] == "surface-map-realworld-sources/1"
    assert labels["corpus_sha256"] == digest(corpus_bytes)
    projects = {item["id"]: item for item in corpus["projects"]}
    assert len(projects) == len(corpus["projects"]) == 5
    records = {}
    for source in sources["projects"]:
        license_bytes = (ROOT / source["license_file"]).read_bytes()
        assert license_bytes and digest(license_bytes) == source["license_sha256"]
        revision = projects[source["project"]]["provenance"]["revision"]
        assert len(revision) == 40 and revision in source["license_url"]
        for artifact in source["artifacts"]:
            records[artifact["path"]] = artifact
            data = (ROOT / artifact["path"]).read_bytes()
            assert digest(data) == artifact["sha256"]
            assert len(data) == artifact["bytes"]
    case_ids = []
    for project in projects.values():
        assert (project["split"], project["kind"], project["exposure"]) == ("development", "real", "exposed")
    for case in corpus["cases"]:
        case_ids.append(case["id"])
        revision = projects[case["project"]]["provenance"]["revision"]
        for artifact in case["artifacts"]:
            assert artifact["sha256"] == records[artifact["path"]]["sha256"]
            assert artifact["provenance"]["revision"] == revision
            assert revision in artifact["provenance"]["url"]
    assert len(case_ids) == len(set(case_ids)) == 7
    assert sorted(case_ids) == sorted(item["id"] for item in labels["labels"])
    for label in labels["labels"]:
        assert label["review"]["status"] == "agent-reviewed"
        assert label["review"]["reviewer"] == "Codex"
        assert label["review"]["notes"]
        assert set(label["expected"]) == {"routes", "unresolved"}
    print("OK: 5 projects, 7 cases, 7 artifacts; exact-byte artifact/license hashes verified")


if __name__ == "__main__":
    main()
