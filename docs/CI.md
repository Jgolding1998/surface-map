# Evidence checks on pull requests

Surface Map can analyze saved web captures without starting a server, using MCP,
or calling an AI model. The same analyzer powers the browser, MCP and CI paths.
CI compares supplied, normalized evidence. It does not test endpoints or prove
that a deployed API is compatible.

## Automatic static source comparison

Use `compare-git` to collect changed website source directly from local Git
objects. No screenshots or manually prepared capture JSON are required. It reads
supported `.html`, `.htm`, `.js`, `.mjs`, `.cjs`, `.jsx`, `.ts` and `.tsx` files,
without checking out, importing, building or executing the proposed application.
JSX syntax support does not imply that JSX elements or framework routing
components are recognized as HTML forms and links.

```sh
python -m surface_map compare-git --repo /path/to/app --base FULL_BASE_COMMIT_SHA --head FULL_HEAD_COMMIT_SHA --format markdown
```

Both IDs must be full 40- or 64-character commit SHAs. The tool uses their unique
merge base, so unrelated changes on the base branch are not reversed into the PR
report. It reports both the requested base and effective merge base. Needed
objects must already be local. Use a recent Git that supports `--no-lazy-fetch`;
older Git fails with a fixed error rather than permitting an implicit fetch.

Enhanced JavaScript analysis uses the optional pinned parser described in the
[setup guide](../README.md#run). Add `--javascript-engine basic` for a Python-only
run with substantially fewer recognized JavaScript patterns. Basic mode still
requires Git. A selected-file or parser failure is an error, not an all-clear.

Automatic selection is bounded to six changed supported files, with 4 MiB per
HTML file, 512 KiB per script, and 4 MiB combined per side. Larger selections are
refused; narrow the task with `--files src/page.html src/client.js` using exact
repository-relative paths. Explicit selection can include unchanged files for
context. Glob patterns, absolute paths, symlinks, submodules, Git LFS pointers,
NUL-containing or non-UTF-8 source, config includes, alternates, shallow or partial
clones, grafts and worktree Gitfile redirects are refused. An added or absent file uses an empty
artifact on its missing side; this is stated in the report.

Only selected source is inspected. Imports, unchanged files, generated output,
server behavior and browser interactions are not automatically checked. Empty
or unsupported-only selections return `analysis_performed: false` and
`policy.status: not_applicable`, with no passing verdict. Exit 0 in that case
means no analysis applied, not that the app is unchanged. Explicit policy matches
exit 1, and rejected/incomplete work exits 2.

### Add it to PRs

The companion [`git-action`](../git-action/action.yml) handles collection and
reporting. Keep a trusted, reviewed Surface Map revision separate from the PR's
source. This minimal consumer workflow runs no application code and needs no
model API key or paid service:

```yaml
name: Static source changes to review
on: [pull_request]
permissions:
  contents: read
jobs:
  evidence:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          repository: Jgolding1998/surface-map
          ref: FULL_REVIEWED_SURFACE_MAP_SHA
          path: surface-map-tool
          persist-credentials: false
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          path: pr-source
          fetch-depth: 0
          persist-credentials: false
      - uses: ./surface-map-tool/git-action
        with:
          repository: pr-source
          base: ${{ github.event.pull_request.base.sha }}
          head: ${{ github.event.pull_request.head.sha }}
```

Replace the Surface Map SHA placeholder with a reviewed full commit. The action
sets up Python and the pinned optional Node parser in the trusted tool folder;
it never installs from the target project's manifests. Leave `fail-on` empty to
start with advisory reports. It writes a GitHub Actions job summary and new local
minimized JSON/Markdown files. It does not post PR comments, upload captures,
request write permission or build/run the app. Do not add head-code execution to
this job or move it to `pull_request_target`. Existing browser-test captures can
still use the original saved-capture Action below in a separate trusted job.

### What the first version checks

| Example from PR feedback | Supported result | Important limit |
| --- | --- | --- |
| Form destination or GET/POST changes | Added/absent normalized operations from literal HTML; inferred supported request patterns | No server acceptance check; private target renames can normalize to the same path |
| Added or absent form field, including a hidden input | Changes to declared field sets; a hidden input is reported only as an added field | Values and visibility/type are not exported; unknown field renames can be hidden by aliases |
| New or absent script/stylesheet reference | Added/absent retained normalized asset references | Domains are discarded; a domain-only swap can be invisible; dynamic loaders are uninspected |
| A static navigation link disappears | Prior link evidence is no longer found in the selected source | Framework routing and browser navigation are not proved; absent evidence is not a broken-link verdict |
| A tracking request disappears | A supported literal Fetch/XHR/Axios request may be absent | No general analytics SDK or event-coverage detection; wrappers and `gtag` calls alone are not supported |

Every asset summary states the domain and alias limits, including when no asset
changes are found. These checks identify evidence for review; they do not claim
to detect all third-party domains, tracking regressions or security flaws.
Real Git fixtures exercise the requested examples and unchanged controls in
[`test_pr_feedback_cases.py`](../tests/test_pr_feedback_cases.py). The integration
workflow also compares two selected files across Surface Map's actual source
history. Both are developer verification, not evidence of customer benefit or
unseen-site accuracy.

## Try the local example

From the checkout root, install the optional pinned parser once with
`python -m surface_map setup-js` using the documented supported Node version.
Then run:

```sh
python -m surface_map analyze --capture examples/ci/before.json
python -m surface_map compare --before examples/ci/before.json --after examples/ci/after.json
python -m surface_map compare --before examples/ci/before.json --after examples/ci/after.json --format markdown
```

The synthetic after capture adds a `/reports` link and a `quantity` form control.
The first comparison is advisory and exits 0. Add `--fail-on added,changed` to
exit 1 for those selected evidence changes. Use `--javascript-engine basic` for
a Python-only run; basic JavaScript analysis has substantially narrower support.
Enhanced is the default and does not silently fall back if JavaScript requires
an unavailable parser.

The capture file is UTF-8 JSON, with exactly an `artifacts` array and an optional
`document_url`. Each artifact has exactly `kind` and `text`; supported kinds are
`html`, `har`, `javascript`, and `typescript`. Existing web-artifact size and work
limits apply. `document_url` provides static document context and never causes
a fetch. A shared ten-second work deadline covers both sides of a comparison.
Use an absolute `run_ci.py` path to run the CLI from another working directory.

| Exit | Meaning |
| --- | --- |
| 0 | Analysis/comparison completed; no selected policy matched. Advisory reports may contain changes. |
| 1 | Comparison completed and an explicitly selected evidence policy matched. |
| 2 | Invalid/rejected input, unavailable required parser, incomparable coverage, work limit or output failure. No successful comparison is implied. |

Policies are comma-separated: `added`, `absent`, `changed`, `unresolved`, and
`warnings`. Added/absent/changed policies include changes to unknown-method
targets as well as known operations. The original saved-capture Action keeps its
v1 route-policy behavior. The Git companion opts into v2 reports, where `added`
and `absent` also cover retained normalized asset references. The saved-capture
CLI can opt in with `compare --include-assets`. Domain-only swaps can still be invisible.
`absent` refers to the comparator's `removed` evidence list, not
proven service removal. `unresolved` fails when either capture contains any
unknown-method target, including unchanged targets. `warnings` checks either
capture's retained warnings. No policy is enabled by default.

## Use the GitHub Action

The repository root contains a composite Action. Pin it to a reviewed full
commit SHA. The following step assumes your own capture/test job has already
produced `captures/base.json` and `captures/head.json`:

```yaml
permissions:
  contents: read

steps:
  - name: Compare corresponding captures
    id: evidence
    uses: Jgolding1998/surface-map@FULL_REVIEWED_COMMIT_SHA
    with:
      before: captures/base.json
      after: captures/head.json
      # Omit fail-on to begin with advisory reporting.
      fail-on: changed
      output-directory: surface-map-results
```

The Action sets up Python 3.11 and, except in explicit basic mode, Node 24.22.1
and the locked parser with lifecycle scripts disabled. The capture paths and
output directory are relative to `GITHUB_WORKSPACE`. They must stay inside it
and cannot traverse links. The output directory must be new; existing files
are never overwritten. The Action writes `comparison.json`, `comparison.md`
and the Actions job summary, and exposes `report-json`, `report-markdown`,
`policy-status` and `policy-passed` outputs. `policy-status` is `advisory`,
`passed` or `failed`; `policy-passed` is empty in advisory mode. Python/Node setup
changes those executables for subsequent steps in the job. A policy failure
still produces reports. Rejected input
does not produce a successful comparison. Reports are bounded and Markdown
escapes dynamic values. Nothing is posted as a PR comment automatically.

The [integration workflow](../.github/workflows/evidence.yml) exercises the
Action on Linux and Windows with synthetic fixtures. It tests the Action
itself. It is not a demonstration of capturing a production application.

## Produce comparable base and head inputs

Use the application's existing preview or browser-test workflow to capture the
same selected pages, scripts and user journey at both revisions. Playwright can
provide rendered HTML and HARs when runtime evidence is needed; capture remains
the host project's responsibility. Keep test data, authentication state,
browser settings and cache policy consistent. Differences in status codes or
sample response bodies can be caused by test data or caching.

Obtain the base capture from the actual base revision or its trusted capture
job, not a baseline file controlled solely by the PR head. Use a pinned Surface
Map version to judge an application PR. Run capture/head application code in an
unprivileged `pull_request` job without secrets. Run the pinned comparison in a
**separate fresh job** that downloads only captures and never runs head code.
Earlier head-code steps in the same job can tamper with runner files and the
environment even when the Action is pinned. Isolated Python startup disables
site packages; it does not isolate the Action from arbitrary earlier job code.
Keep any later privileged
publishing separate. Do not use `pull_request_target` to execute head code.
The supplied Action only reads captures; it does not build or run the PR.

By default comparison refuses different artifact-kind counts, different document
context availability, different effective JavaScript parser modes, omitted or
rejected sources, and evidence-retention truncation. Matching that metadata does
not prove equivalent page/resource coverage. Six selected web artifacts are
supported per capture; this is not a recursive whole-repository scanner.

## Interpret the report

An absent route may reflect incomplete capture coverage. Static declarations and
inferred client calls do not establish server acceptance or runtime reachability.
Privacy normalization discards origins and aliases unknown path and field names.
Different private routes can collide; a rename may be invisible. Field aliases
can renumber when a field set changes. Report changes to normalized field sets,
not claims that an aliased field identifies a particular original name.

The initial integration is therefore an evidence-review aid. Turn on failure
policies only after checking normal behavior on your own captures. A green
advisory check means the analysis completed, not that there were no differences.
Upload only the minimized reports you have reviewed. Raw HARs and HTML can
contain credentials or private data before Surface Map minimizes them.

## Evaluation checks

The separate [promptfoo integration](../evals/promptfoo/README.md) runs objective
local checks and exports a results viewer without model API calls. The
[project corpus runner](../evals/holdout/README.md) tracks provenance, label
review status and project-disjoint splits for future independent evaluations.
These support testing the analyzer; they do not automatically create a
representative dataset or establish that agents benefit from the tool.

The [real-project collection](../evals/realworld/README.md) includes 30 exposed
development tasks from 21 upstream projects. The initial enhanced result is
28/30; the unchanged collection now passes 30/30 after scoped global-Axios support.
Both the initial result and the updated result are retained. CI requires all 30
to pass. Added or lost `unverified_global_axios` assumptions count as evidence
changes even if the normalized route and method stay the same. The assumption
is preserved in JSON, contract/OpenAPI exports, compact review and rebuild plans.
The pinned Action is for comparing an application's captures. The project's own
test/evaluation workflows also run proposed analyzer changes, as normal test CI;
those jobs are not a trusted independent verdict on arbitrary hostile changes.

## Browser workflow checks

The project's [browser workflow](../.github/workflows/browser.yml) checks the
actual local interface on PRs, pushes to `main`, and manual dispatch. It uses a
standard Ubuntu runner, pinned Playwright 1.62.1 and Chromium. It does not upload
artifacts or create a dependency/browser cache. Running only on PRs and main
avoids a duplicate browser job for a feature-branch push with an open PR.

These development checks cover file-mode handoffs, clipboard fallback, downloads,
assumption metadata, mobile layout, public-URL mode, stale-result clearing and
private-target rejection. Public transport is simulated; browser checks make no
external requests. They are regressions, not evidence of general accuracy across
websites. The pinned browser driver is not a user-facing runtime dependency.

After the supported Node version and optional syntax parser are installed,
developers can run the same checks locally:

```sh
npm ci --ignore-scripts --bin-links=false --prefix tests/browser
node tests/browser/node_modules/playwright/cli.js install chromium
python tests/run_browser_checks.py
```

Use `npm.cmd` on Windows if PowerShell blocks `npm.ps1`. On Linux, the browser
may also need its system dependencies; the CI job installs those explicitly.
The runner starts a temporary loopback server on an available port, bounds each
test process, and shuts the server down on completion or failure. Regular users
can keep using the Python-only workspace without this development setup.
