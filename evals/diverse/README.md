# Capture task checks

This suite contains 64 synthetic tasks across 12 families. It checks HTML
navigation, forms, relative URLs, missing coverage, HAR observations, JavaScript
and TypeScript intent, unresolved operations, combined evidence, comparisons,
optional page signals and malformed-input rejection.

The corpus was recorded with a SHA-256 digest before the recorded agent runs.
This ordering is self-reported, not externally attested. These are
developer-authored regression cases, not an independent holdout or evidence that
Surface Map outperforms an AI agent. Several exercise features already covered
by narrower unit tests. The corpus and its digest are committed together.

## Run

From the repository root:

```sh
python -m evals.diverse --engine basic --repeats 3 --output basic-results.json
python -m evals.diverse --engine enhanced --repeats 3 --output enhanced-results.json --check
```

Enhanced mode needs the optional JavaScript parser described in the main README.
The evaluator refuses enhanced mode when that parser is unavailable. It never
silently falls back, installs packages, fetches a URL or executes captured code.
Each artifact is literal synthetic content, including URLs and instruction-like
text. Output paths are chosen by the person running the command.

## What a pass means

Every accepted analysis task checks the complete set of known operations, their confidence
labels and exact source locations. Unresolved targets are checked separately.
Unexpected routes remain false positives. Selected tasks also check query names,
body-field names, response statuses, omission warnings, page-signal categories and
absence of synthetic private-value canaries. Comparison tasks check added, absent
and changed normalized evidence, including loss of an evidence kind. They do not
prove that a deployed endpoint was added or removed.

Three malformed inputs must be rejected. Unexpected rejection is a failure on a
supported task. Repeating a task checks the stability of its scored projection; three repetitions
are still one case, not three independent examples. Timings include analysis and
projection overhead, and the first script call may start a parser worker. They
are diagnostic timings, not an isolated speed benchmark.

The JSON output includes predictions, failed rubric fields, per-family counts,
known-operation precision/recall and consistency. Raw page content and exception
details are not copied into result files.

The 64-case score covers the projections described above. It does not score the
new source-origin relations, script status and omission metadata, or all recent
HTML parsing fixes. Dedicated unit tests cover those changes. A selected check
of HAR response statuses or omission-warning wording should not be read as
coverage of every status or coverage field in a full report.

## Comparing an agent

A recorded text-only comparison on October 30, 2026 used `claude-opus-5-5`.
It tied Surface Map enhanced on all 45 route/evidence tasks in three trials.
Each trial used four fresh sessions with batches of 12, 12, 12 and 9 tasks.
The scored predictions were identical across trials. There were no tool-only or
agent-only wins. This result shows no route-extraction advantage over that model.

The 45-task subset excludes comparisons, page signals and malformed-input
rejection. It checks routes, confidence, source locations and unresolved targets,
with 30 expected known-operation observations across the tasks; 17 of the 45
tasks have empty known-route sets. It does not score warnings, field shapes or
explanations. The broader 64-task tool suite and this 45-task paired comparison
answer different questions.

Reproduce the score without an AI account:

```sh
python -m evals.diverse.compare_agent --output paired-results.json
```

To compare a new local analyzer run with the frozen agent predictions:

```sh
python -m evals.diverse --engine enhanced --repeats 3 --output current-results.json
python -m evals.diverse.compare_agent --tool-results current-results.json
```

The [baseline manifest](agent_baseline/manifest.json) records the selected case
IDs, batch boundaries, model, date and artifact hashes. Its per-batch receipts
include locally recorded start/finish times and the raw response-text SHA-256.
These are provenance records, not external attestations or speed measurements.
The same folder contains
the [exact common prompt](agent_baseline/prompt-policy.txt) and synthetic raw
predictions. Each raw `.txt` file preserves the exact UTF-8 encoding of the
Claude Code result text, including whitespace and any fences; no answers were
edited or reformatted. The scorer treats route and evidence order as irrelevant,
accepts a single enclosing JSON code fence, and rejects missing, duplicate or
unknown case IDs and malformed responses. Unexpected operations are not filtered.

These were batched, text-only model calls with no tools, expected answers or
analyzer source supplied. The prompt specified both analysis rules and
normalization, including what to omit and how to classify evidence. Semantic
case IDs such as `shadowed-fetch` also provide hints. This measures rule
following on small synthetic tasks, not spontaneous tool value, unseen accuracy
or a general ability to reverse engineer websites. No latency or cost comparison
is claimed.

Export only the inputs before an independent run:

```sh
python -m evals.diverse --export-inputs agent-inputs.json
```

Do not give the baseline agent the corpus, expected answers, existing predictions
or analyzer source. Give it the same artifacts, task instructions, normalization
rules and response format. Record the model, tool access, run date, budget and
number of fresh sessions. Preserve failed cases. An agent that reads these public
labels cannot subsequently be described as blind to them.

To reproduce the recorded subset, select the IDs from each manifest batch from
the exported inputs, omit the `family` key, append them as JSON after `TASKS:` to
the common prompt, and start a fresh text-only session for each batch. Preserve
the returned predictions before scoring them.

Keep task correctness separate from tool selection. This runner calls the local
analyzer directly; it does not measure whether Claude, Codex or another host will
choose an installed MCP tool. MCP transport and agent selection need their own
checks. Saved-page replay and live-page results should also be reported separately.

## Limits

All fixtures are small, fixed and synthetic. This suite does not measure browser
execution, authenticated pages, unseen sites, endpoint reachability, actual
service changes or a general prompt-injection defense. Page signals are source
markers, not a maliciousness classifier. Privacy canaries cover selected examples,
not all possible secrets. Passing this corpus is useful regression evidence, not
a security certification or a claim of universal reverse engineering.
