# Local evaluation viewer

This optional integration runs Surface Map's existing objective evaluator inside
[promptfoo](https://www.promptfoo.dev/docs/intro/) **0.125.1**. The exact npm
dependency and transitive lockfile are committed. It adds side-by-side results,
repeat records and downloadable JSON, HTML and JUnit reports. It does not change
the analyzer, make model calls, require an AI account or upload results.

The default suite uses the 46 non-JavaScript tasks from the checksum-verified
[64-task synthetic corpus](../diverse/README.md). The enhanced suite uses all 64.
These are the same developer-authored cases, not additional independent sites,
a held-out benchmark or evidence of improved accuracy. Repeating them measures
repeat consistency, not corpus diversity.

## Run locally

Use Python 3.11+ and Node.js >=22.22.0 (the repository's enhanced JavaScript
parser has its own stricter Node requirement). From the repository root:

```sh
cd evals/promptfoo
npm ci --ignore-scripts
npm run eval
npm run view
```

`npm ci` downloads the pinned optional UI dependencies. Evaluation
itself calls the local Python analyzer on saved synthetic data. The wrapper
disables telemetry, update checks, sharing and the response cache, and binds the
viewer to `127.0.0.1`. Results and UI history are local under ignored `results/`.
`npm run view` starts a small standard-library server for only the exported HTML
reports at `http://127.0.0.1:15500`; it stays running until you stop it. This report
viewer does not expose promptfoo's database or full dashboard. You can also open
`results/basic.html` directly without running a server.

`npm run eval` produces 46 tasks × 3 repetitions = 138 scored calls, with
`results/basic.json`, `results/basic.html` and `results/basic.junit.xml`. It exits
nonzero for failed assertions or provider errors. The interface reports
`exact_case`: the complete published case rubric, including evidence locations,
uncertainty, applicable warnings, shapes, comparison changes and privacy
canaries. It uses `evals.diverse.run.score`; an extra route or incorrect source
location fails the case. This metric is not a general website accuracy rate.

After a successful vendor CLI exit, the wrapper independently checks the JSON
export: all expected case/repetition identities must occur exactly once, every
row and assertion must pass, summary totals must agree, repeat groups must be
complete and stable, and cached counts must be zero. Missing/partial exports
fail; an old JSON report is removed before a new run. A genuine nonzero vendor
exit code is preserved. Expected totals are 138 basic, 192 enhanced and 12 MCP.

For full syntax analysis, perform the repository's documented `setup-js` step
from the repository root first, then run `npm run eval:enhanced` here. Missing
parser dependencies are an error; this suite never substitutes basic analysis.
All 64 tasks run three times and produce `results/enhanced.*`.

The wrapper selects its own Python interpreter for promptfoo's workers. Activate
the desired virtual environment before invoking it. Running through an explicit
interpreter also works:

```sh
python evals/promptfoo/run.py basic
python evals/promptfoo/prepare.py --check
python -m unittest discover -s tests -p test_promptfoo_eval.py -v
```

`prepare.py` regenerates the three committed test lists; `--check` detects drift.
Gold labels remain in the existing corpus and scorer, outside the provider's
prompt. Do not edit generated test lists to improve a displayed pass rate.

Reports can retain full inputs, outputs and assertion context. The bundled
inputs are synthetic; if extending this integration to private held-out data,
review reports before publishing or uploading them.

## Optional real MCP transport check

The default suite imports the analyzer directly; it does not test MCP transport.
For an additional four-task transport smoke check, install the exact existing
`requirements-mcp.txt` into a virtual environment as documented in
[MCP setup](../../docs/MCP.md), activate it, then run:

```sh
cd evals/promptfoo
npm run eval:mcp
```

`mcp.yaml` launches `../../run_mcp.py` through promptfoo's standard local stdio
MCP provider, calls `analyze_capture` and `compare_captures`, and scores the raw
responses with the same rubric. It covers an HTML form, HAR body shapes, a
before/after status change and page signals. Three repetitions produce 12 calls
and `results/mcp.*`. The npm command does not install Python dependencies or
silently skip a missing MCP SDK. This is a transport smoke check, not full MCP
protocol conformance testing. Existing `tests/test_mcp.py` remains relevant.

## Comparing agents fairly

These checked-in configurations test a direct tool. They contain no paid model
provider and make no claim that a model independently chooses Surface Map.
For a subsequent agent experiment, use separate, clearly labeled conditions:

| Condition | What it measures |
| --- | --- |
| Direct analyzer / direct MCP | Whether tool output matches the objective labels |
| Model without Surface Map | The model's answer from the same saved inputs |
| Same model, explicitly instructed to use Surface Map | Answer quality when tool usage is directed |
| Same model, Surface Map available without a reminder | Tool selection as well as answer quality |

Use promptfoo's [model-provider MCP integration](https://www.promptfoo.dev/docs/integrations/mcp/)
for the last two conditions. The standalone `mcp` provider used by `mcp.yaml`
issues tool calls directly; it cannot measure the model's decision to use them.
Capture executed `metadata.toolCalls` and score actual usage separately from the
final answer. Include no-tool-needed tasks and competing tools when evaluating
selection. Do not count a correct tool-name string in prose as an executed call.

Pin the selected model version, prompt and budget; use independently reviewed
labels and hold out entire sites/projects from tuning. Give each condition the
same inputs and output rules. Report failures, unsupported inputs, evidence
errors, unsupported claims, latency and cost alongside extraction scores. Repeat
fresh independent conversations; keep `cache: false` / `--no-cache` so saved
responses cannot masquerade as new trials. Additional model-provider credentials
and API costs are explicit prerequisites for that separate experiment.

## CI use

The same `python evals/promptfoo/run.py basic` command can run after
`npm ci --ignore-scripts` in this directory. Upload ignored `results/basic.*`
as workflow artifacts, including on failure. No token or model secret is needed.
Pin workflow actions and keep untrusted captures as data. A normal `pull_request`
job is enough for these deterministic checks; adding this viewer does not
require a privileged PR-commenting workflow. Model comparisons should be a
separate explicitly configured job with bounded cost and an appropriate secret
boundary. See [promptfoo CI documentation](https://www.promptfoo.dev/docs/integrations/ci-cd/).
