# Project-separated capture evaluation

This is an offline harness for evaluating saved web artifacts from independent
projects. **The four included fixtures are synthetic development self-tests.
They provide zero independent-project or unseen-holdout evidence.** The harness
does not download websites, call a model, install packages, or execute captures.
Enhanced JavaScript analysis uses the optional, explicitly installed parser.

The [machine-readable schema](schema.json) describes manifests, labels and saved
predictions. Runtime validation also enforces hashes, safe local file paths,
project separation, complete labels and unique operation identities.

## Run the self-tests

From the repository root:

```sh
python -m evals.holdout --validate
python -m evals.holdout --engine basic --repeats 3 --check
python -m unittest discover -s tests -p test_holdout_eval.py -v
```

`--output result.json` saves a report. Its parent directory must already exist.
`--check` exits 1 if a declared case or repeat-consistency check fails; invalid
inputs, unavailable enhanced parsing and output errors exit 2. A successful
`--validate` only validates the dataset. A passing `--check` is a correctness
gate, not certification of label quality, independence or unseen exposure.
Error messages do not include capture contents, exception details or paths.

## Import independent captures

1. Select distinct projects before examining analyzer results. Record the exact
   upstream revision, source URL, applicable license and acquisition date. Save
   authorized UTF-8 HTML, HAR and JavaScript/TypeScript bytes locally. Confirm
   redistribution rights and remove personal data before making a corpus public.
2. Assign a stable lowercase `group` to each independent upstream project.
   Multiple versions, routes, tasks and forks of the same project belong to the
   same group. Put the entire group in either `development` or `holdout`.
   Different IDs do not create independent projects. An offline validator cannot
   discover undisclosed fork relationships or verify provenance declarations.
3. Write `corpus.json` with artifact paths and SHA-256 digests of the exact bytes.
   Do not normalize capture newlines. Keep Git from changing their bytes, for
   example with a local `.gitattributes` rule `artifacts/** -text`. Provenance on
   an individual artifact may override its project's provenance when the source
   URL differs. File paths are relative to the manifest directory; directory
   walking, traversal, links, reparse points and network paths are rejected.
4. Independently label every task in a separate `labels.json`. Derive labels
   from the source and the documented extraction/minimization contract, **never
   from Surface Map predictions**. Review the complete expected operation and
   unresolved-target sets, including intentional empty sets. Preserve difficult
   cases and unsupported inputs. Mark actual review status honestly.
5. Bind the label file to the manifest's SHA-256 after converting only manifest
   CRLF to LF. Record a new manifest/label digest for every change. For example:

   ```sh
   python -c "from pathlib import Path; import hashlib; print(hashlib.sha256(Path('corpus.json').read_bytes().replace(b'\r\n', b'\n')).hexdigest())"
   ```

6. Run the import validation before scoring:

   ```sh
   python -m evals.holdout --corpus path/to/corpus.json --labels path/to/labels.json --validate
   python -m evals.holdout --corpus path/to/corpus.json --split development --engine enhanced --check
   python -m evals.holdout --corpus path/to/corpus.json --split holdout --engine enhanced --check
   ```

Projects sharing a group cannot cross splits or change between real and
synthetic. Exact artifact hashes cannot cross splits, even under different
project IDs. Identical normalized output from different projects is legitimate
and is not rejected. Published or previously inspected labels and cases must
be marked `exposed`; an `unknown` exposure is not counted as unseen.

For confidential data, store the manifest, captures, labels, exported inputs,
model predictions and results under `evals/holdout/private/`, or use
`evals/holdout/private-results/` for confidential results. Both directories are
ignored locally. Verify they are untracked before sharing. Files outside these
directories are not automatically private. Publishing a corpus changes its
exposure; a digest does not make public answers a hidden holdout.

## Manifest and label contract

`corpus.json` has `schema: "surface-map-holdout-corpus/1"`, a `name`, `projects`
and `cases`. Each project declares:

| Field | Meaning |
| --- | --- |
| `id` | Local project identifier |
| `group` | Canonical lowercase independent upstream group |
| `split` | `development` or `holdout` |
| `kind` | `real` or `synthetic`; synthetic holdout projects are rejected |
| `exposure` | Optional `unseen`, `exposed` or `unknown`; defaults to unknown |
| `provenance` | `url`, `revision`, `license`, `retrieved_at` |

The acquisition date accepts `YYYY-MM-DD` or an ISO timestamp with a timezone.
`revision` is an immutable upstream commit/release or capture identifier where
one exists. A URL alone is not a reproducible snapshot. The synthetic examples
use a synthetic fixture version and do not claim an upstream acquisition.

Each case declares `id`, `project`, `family` and one to six `artifacts`.
Each artifact contains `kind` (`html`, `har`, `javascript`, `typescript`), `path`
and `sha256`, with an optional `provenance` override. Cases may provide a
`document_url` for one HTML document and its scripts; that metadata never causes
a fetch. Existing analyzer limits still apply. The harness accepts at most
10,000 tasks, 4 MiB per HTML artifact, 512 KiB per other artifact, and 4 MiB of
combined capture bytes per task. A byte limit does not guarantee parser support.

`labels.json` has `schema: "surface-map-holdout-labels/1"`, `corpus_sha256`
and `labels`. Every label has `id`, `review`, and `expected`.

```json
{
  "id": "one-task",
  "review": {
    "status": "agent-reviewed",
    "reviewer": "reviewing agent and version",
    "reviewed_at": "2026-10-31",
    "notes": "Reviewed against the source; no human review claimed."
  },
  "expected": {
    "routes": [{
      "method": "POST",
      "path": "/api/orders",
      "confidence": "declared",
      "evidence_kinds": ["form"],
      "evidence": [{"source": "source_1", "location": "line 2", "kind": "form"}]
    }],
    "unresolved": []
  }
}
```

`review.status` is `unreviewed`, `agent-reviewed` or `human-reviewed`. A real
human must perform the review before the last status is used. Agent review is
not human certification. `reviewer`, `reviewed_at`, and `notes` are required;
unreviewed labels may have an empty reviewer and a null review date.

Known operations require method, normalized path, confidence and complete
evidence-kind sets. `evidence` is optional: include it when exact source IDs,
locations and kinds have also been independently labeled. Evidence locations
are scored as unique location identities, without depending on order.
Unresolved targets require path, `confidence: "inferred"` and
`evidence_kinds: ["javascript"]`, with optional evidence locations.
`{"rejected": true}` is the entire expectation for a deliberately rejected input.
An internal analyzer error never counts as a correct rejection.

Unknown fields, duplicate keys, duplicate IDs/operations, missing labels,
unmatched corpus digests, non-finite JSON numbers and invalid UTF-8 are errors.
Artifact bytes and label files are validated before any analysis is attempted.

## Compare an agent or another extraction adapter

Export the selected inputs separately:

```sh
python -m evals.holdout --corpus path/to/corpus.json --split holdout --export-inputs inputs.json
```

The export contains only the input schema, digest and hash-ordered opaque case
IDs with artifact contents and optional document URL. It excludes case names,
families, project IDs, source filenames, provenance, review status and gold
labels. Original content can itself reveal its project; the export cannot make
recognizable public source anonymous. Keep the private manifest and labels
away from a baseline agent. Give both evaluation arms the same artifacts,
normalization rules, response format and budget, then retain all raw answers.

An agent/adapter must produce a strict JSON object in this format:

```json
{
  "schema": "surface-map-holdout-predictions/1",
  "input_sha256": "copy the exported input digest",
  "results": [{"id": "copy an opaque case ID", "routes": [], "unresolved": []}]
}
```

Use the same route format as labels. Every selected input must appear exactly
once. Return `id` plus `rejected: true` for a rejected input. Unknown IDs,
duplicate/missing predictions, explanatory text, fences and malformed JSON are
not silently repaired. A malformed batch is an evaluation error, never a pass.

```sh
python -m evals.holdout --corpus path/to/corpus.json --split holdout --predictions trial-1.json --check
python -m evals.holdout --corpus path/to/corpus.json --split holdout --predictions trial-1.json --predictions trial-2.json --predictions trial-3.json --output comparison.json
```

This scores saved predictions and makes no model/API call. The report sets
`analysis_calls` to zero and `analyzer.executed` to false; source hashes identify
the local scoring environment and do not attest the origin of saved answers.
Record model,
prompt, tool access, date, trial boundaries and budget outside the gold labels.
The harness does not conduct or substantiate an agent-benefit experiment by
itself. Tool selection, real review outcomes, unsupported claims, time and cost
need separate measurements. A model that has read the labels is not blind.

## Reading the report

Results identify the manifest and label digests, analyzer version, Python
version, analyzer/harness source hashes and parser-lock/integrity hashes.
The report lists tasks, unique capture hashes, real/synthetic project groups,
split, review status and exposure declarations separately from repetition count.
Unknown/exposed groups do not contribute to declared unseen-holdout counts.

For a publication copy that omits individual predictions and failure details,
add `--aggregate-only --output public-summary.json`. This preserves the usual
`--check` failure exit status. Aggregate project/family identifiers, counts and
digests still require privacy review; the switch does not make confidential
data automatically safe to share. Keep full reports, labels and captures private.
Known GitHub/raw-host aliases and repository revisions are canonicalized to one
repository identity, including per-artifact provenance overrides. Declare
related forks and other shared upstreams with the same project group as well.
The `declared_unseen_human_reviewed_holdout_groups` count additionally requires
all selected labels in the group to declare human review. These metadata are
self-reported; even that count is not an external attestation of independence.

Strict case scores preserve all extra/missing known routes and unresolved
targets, confidence/evidence differences, unexpected rejections and internal
errors. Precision/recall/F1 count operation identities from the first trial;
confidence and evidence correctness are additional strict case requirements.
The report includes per-project and per-family metrics. Zero denominators
produce `null`, not a perfect score. Repeating a task checks identical scored
projections and is still one task. No timing or population confidence interval
is inferred from synthetic examples or correlated captures.

Ordinary Python network connections and name resolution are blocked while the
analyzer runs. This defensive check is not an operating-system sandbox; it does
not claim to constrain an independently configured Node executable. Captures
are always passed to the analyzer as data, and the harness contains no download,
installation or model-client code.
