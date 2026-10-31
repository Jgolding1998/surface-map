# Twenty-case text-only comparison

Surface Map's enhanced parser passed **20/20** selected cases. A fresh Claude
text-only run passed **17/20** against the same scored projection. This was
**one trial**, split across two fresh sessions of ten cases each. The CLI
reported the model identifier `claude-opus-5-5`; that metadata is recorded
rather than independently verified.

| Outcome | Cases |
| --- | ---: |
| Both passed | 17 |
| Surface Map passed, Claude missed | 3 |
| Claude passed, Surface Map missed | 0 |
| Both missed | 0 |

Two of the three differences are **policy-sensitive**, so the raw score needs
context. In `case_0004` and `case_0017`, Claude retained a query field from a
GET form's action URL. The frozen labels expect the submitted controls to
replace the action query. The prompt did not explicitly state that replacement
rule. These two cases are not strong evidence of a model capability gap.

The clearer difference is `case_0003`: Claude omitted the deterministic target
of `fetch("".concat("/users/42"))`. Surface Map returned the normalized
`GET /users/{integer_2}` route with its source evidence. The raw answers and
labels were not changed after reviewing these differences.

## Reproduce the score

From the repository root, using Python 3.11 or later:

```sh
python -m evals.stress640.agent_comparison
python -m unittest evals.stress640.agent_comparison.test_record -v
```

The first command verifies artifact hashes, recreates the case selection,
checks that each prompt contains the exact selected inputs, and applies the
existing [scorer](../../diverse/run.py) to both saved outputs. It checks the
result against [recorded-results.json](recorded-results.json). A zero exit code
means the recorded comparison was reproduced, not that every answer passed.
`--output result.json` saves the reproduced scores. No model account, network,
Node.js, captured-code execution, or paid API call is needed.

This rescores saved predictions. It does not rerun Claude or prove that a later
version of Surface Map produces the same output. Run the complete
[640-case suite](../README.md) separately for current parser behavior.

## Selection and scoring

The [manifest](manifest.json) records the frozen corpus digest, seed, fixed
family quotas, selected IDs, batch membership, and artifact hashes. Within
each family, selection takes the lowest `SHA256(seed + ":" + case_id)` values.
The selected cases are then ordered by
`SHA256(seed + ":mix:" + case_id)` and assigned opaque input IDs.

| Family | Cases |
| --- | ---: |
| HTML form controls | 3 |
| HTML text containers | 3 |
| HAR body shapes | 4 |
| JavaScript uncertainty | 4 |
| JavaScript resolution | 3 |
| Capture comparisons | 3 |

The exact [first prompt](prompt-1.txt) and [second prompt](prompt-2.txt) include
input artifacts and the normalization policy. They exclude expected answers,
family/axis labels, source case IDs, analyzer source, and prior predictions.
Claude received no tools or prior session context. Selection and prompts were
saved before the calls; submitted prompt hashes were checked against those
files. That sequence is self-reported, not independently attested.

The score requires complete known-route and unresolved-target sets, methods,
confidence, and source evidence. Selected cases also check detail fields or
all six comparison lists. Unexpected routes fail. It does not score warning
prose, page signals, privacy canaries, latency, cost, automatic tool selection,
or complete browser behavior. Labels follow the project's documented support
and minimization rules. Only complete Markdown code fences and list ordering
are normalized; missing facts, extra facts, and duplicate predictions are not
repaired. Invalid or incomplete response batches fail all their cases.

[response-1.txt](response-1.txt) and [response-2.txt](response-2.txt) preserve
the raw response text. [surface-map-predictions.json](surface-map-predictions.json)
contains the scored tool projections. Public artifacts contain only synthetic
inputs and predictions, without local paths, session IDs, or account metadata.

## Interpretation

These are developer-known synthetic cases, not unseen websites or independent
samples of the Web. The model received a specialized extraction contract and
could not use its usual tools. A single model trial does not establish
reliability or a general ranking. The earlier
[45-case comparison](../../diverse/README.md) tied across three model trials
on a narrower route/evidence rubric. The two experiments measure different
tasks and should not be combined into an overall accuracy claim.

The useful question is whether deterministic extraction and source evidence
help an agent complete a real task. That still needs independent examples and
an evaluation of agents working with and without the tool.
