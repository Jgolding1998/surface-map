# Real-world development seed (v1)

The combined collection contains **30 tasks from 21 upstream project groups**,
with 31 saved artifacts. Enhanced analysis now scores **30/30**, stable across
three repetitions, after adding scoped browser-global Axios inference. The
original **28/30** result and baseline are preserved in
[initial-results.json](initial-results.json) and
[initial-baseline.json](initial-baseline.json). No inputs or gold labels changed.
The fix was developed after inspecting those failures; this is known-case
development tuning, not a new unseen accuracy measurement. The Axios predictions
retain explicit `unverified_global_axios` assumption metadata: library identity
and prior configuration are not verified.
These are small public source selections and teaching examples, not 21 complete
websites, private production captures, human-reviewed labels or an unseen
holdout. All 30 labels are honestly marked agent-reviewed and all projects
development/exposed.

Run the combined collection from the repository root after optional parser setup:

```sh
python -m evals.realworld.combine --expected-projects 21 --check
python -m evals.holdout --corpus evals/realworld/combined-corpus.json --labels evals/realworld/combined-labels.json --engine enhanced --repeats 3 --check --output realworld-results.json
python -m evals.realworld.regression --results realworld-results.json
```

The full correctness gate is `--check` on the holdout runner; all 30 cases must
now pass. The separate regression command fails CI on a regressed passing case, an
unexpected execution/rejection error, or repeat instability. It allows a known
failure to improve. Its baseline is a recorded regression snapshot, not a gold
label generator. Changed corpus or label hashes require an intentional reviewed
baseline update. `--record` refuses an existing baseline destination.

`combined-corpus.json` and `combined-labels.json` deterministically combine the
initial seed below and three explicitly named batches:

| Batch | Projects | Tasks |
| --- | ---: | ---: |
| Initial seed | 6 | 9 |
| [HTML and clients](batches/html-clients/README.md) | 5 | 7 |
| [Frameworks](batches/frameworks/README.md) | 5 | 7 |
| [Request patterns](batches/request-patterns/README.md) | 5 | 7 |

`combined-sources.json` binds each source manifest, label file and provenance
sidecar by digest. Labels and review notes are copied unchanged from the source
batches. Run `python -m evals.realworld.combine --expected-projects 21` to
regenerate after an intentional source update; `--check` detects drift without
writing. Duplicate projects, groups and task IDs cannot inflate the combined
count. The [project-corpus workflow](../../.github/workflows/corpus.yml) runs the
harness self-tests and real collection on Windows/Linux, then checks regressions.

The initial case choices and gold labels were written without using analyzer
predictions. Codex independently fetched all selected artifacts and licenses
from their immutable upstream URLs and verified the saved bytes. That verifies
provenance and preservation, not correctness of the agent-authored labels.
The published [recorded results](recorded-results.json) identify code/data hashes.
No agent-benefit, latency/cost or independent accuracy claim follows from this
small exposed collection. There are no HAR or unresolved-method examples yet.

Nine small cases from six permissively licensed public GitHub projects, pinned to immutable commits. They exercise HTML forms, Axios and XMLHttpRequest/Fetch hints, including negative and uncertain cases. The artifacts are real upstream bytes, not generated scenarios.

**This is development seed coverage.** It does not measure unseen performance and gives no independent evidence of accuracy. Every project uses `split: development`, `kind: real` and `exposure: exposed`. The selection is public, small and was chosen with knowledge of the analyzer's rules. Labels are `agent-reviewed` by Claude Code and were not human-reviewed. Expect to tune against these cases. Use fresh projects for held-out claims.

## Files

| File | Purpose |
| --- | --- |
| `corpus.json` | Agreed `surface-map-holdout-corpus/1` schema with projects, cases, artifact paths, `sha256` and per-artifact provenance |
| `labels.json` | `surface-map-holdout-labels/1` schema with `corpus_sha256`, expected routes and unresolved targets, plus derivation notes |
| `sources.json` | Upstream path, line range, upstream file sha256 and license file for every artifact |
| `artifacts/<project>/` | Saved HTML files and JS/TS fragments, 10.7 KB total |
| `licenses/<project>.txt` | Verbatim upstream license at the pinned revision |
| `verify.py` | Stdlib integrity check of schema, hashes, license presence and label/case parity. It does not run the analyzer or any artifact. |

`corpus_sha256` is the SHA-256 of `corpus.json` with CRLF normalized to LF. Artifact hashes use the exact saved bytes. `.gitattributes` keeps LF. Run `python -I evals/realworld/verify.py`.

## Sources

All files were retrieved on 2026-10-31 from `raw.githubusercontent.com` at the commit SHA that the GitHub REST API returned for `HEAD`.

| Project | Repository @ revision | License | Artifacts |
| --- | --- | --- | --- |
| httpbin | postmanlabs/httpbin @ f8ec666b4d1b654e4ff6aedd356f510dcac09f83 | ISC | `httpbin/templates/forms-post.html` (whole file) |
| flask | pallets/flask @ 49943b5a94413a530761350f04e896f76e8d620e | BSD-3-Clause | `examples/tutorial/flaskr/templates/auth/register.html`, `.../login.html` (whole files) |
| axios | axios/axios @ 9d11464a75ee5c644c0308d742f58755c0482594 | MIT | `examples/get/index.html` (whole file + L15-L32 inline script), `examples/post/index.html` (L24-L39 inline script) |
| github-fetch | JakeChampion/fetch (formerly github/fetch) @ ba5cf1ed2e02ebb96fa1e60b4fd2eb04071b60e4 | MIT | `fetch.js` L526-L633 |
| mdn-learning-area | mdn/learning-area @ 21973263ba859ff3477919087b488157f9d618a6 | CC0-1.0 | `html/forms/sending-form-data/get-method.html`, `post-method.html` (whole files) |
| nextjs | vercel/next.js @ fac5fc87df2b60169189ac6dcba3142854124371 | MIT | `examples/api-routes-rest/pages/index.tsx` (whole file) |

Copyright and permission notices are kept in `licenses/`. Each artifact's `provenance.url` is a commit-pinned `blob` URL, and fragments include a `#Lx-Ly` anchor.

## What fragments exclude

Fragments are deliberately incomplete selections. Line ranges are 1-based and inclusive. A fragment is those lines joined with LF plus one trailing LF.

- `axios/get-inline-L15-L32.js` and `post-inline-L24-L39.js` contain only the inline `<script>` body. The `<script>` tags, the separately loaded `/axios.min.js` and, for POST, the surrounding page and form are excluded. In the GET case, the whole page is also supplied as a separate HTML artifact. This follows the README workflow of importing inline scripts separately.
- `github-fetch/fetch-L526-L633.js` contains only the exported `fetch(input, init)` function. Module helpers such as `Request`, `Response`, `Headers`, `DOMException`, `support`, `g`, `parseHeaders`, `normalizeName` and `normalizeValue`, plus the trailing `g.fetch = fetch` installation, are excluded.
- Flask templates are unrendered Jinja. `base.html`, with its navigation links, is not included. The `auth.py` route decorators used to choose `document_url` were read but are not artifacts.
- The Next.js page is supplied without its API handlers, `interfaces` or `user/[id].tsx`.

## Label rules applied

Labels were derived by reading the artifacts against the root README and the `analyze.py` vocabulary (`SEGMENTS`, `safe_path`, the form/link handling and confidence assignment). The analyzer was not run, and no Surface Map output was used as a label.

- Known path segments in `SEGMENTS` are kept (`auth`, `register`, `login` and `post`). Other segments become `{segment_N}`, and decimal segments would become `{integer_N}`. Hosts are dropped, so an empty path is `/`.
- Form method: `post` maps to POST, `dialog` is ignored and everything else maps to GET. A missing action uses `document_url`. A page-relative action without `document_url` is omitted.
- HTML routes are `declared` with `form` or `link` evidence. JS/TS routes are `inferred` with `javascript` evidence. Inline scripts in HTML are ignored.
- Targets that flow through function parameters are dynamic. They produce neither a route nor an unresolved target.

The two Flask `document_url` values use the reserved `example.com` host. Their paths come from upstream `flaskr/auth.py` (`url_prefix="/auth"` and `@bp.route("/register")` / `"/login"`). They are deployment metadata, not fetched URLs.

## Ambiguous labels and limitations

- **Global Axios (`axios-example-*`):** the initial analyzer recognized only imported or required clients and missed both literal requests (`GET /{segment_1}/{segment_2}` and `POST /post/{segment_2}`). Enhanced mode now supports the conventional global with explicit assumption metadata. It checks local bindings, mutations and selected peer scripts, while acknowledging uninspected initialization. The original label expectations and review notes remain unchanged.
- **Next.js SWR (`nextjs-swr-fetcher-indirect`):** the page requests `GET /api/users` at runtime through `useSwr` → `fetcher(url)`. Under the documented rules, functions are not executed, so the expected set is empty. A tool that models SWR keys would disagree.
- **httpbin template action:** the form posts to `POST /post` in the rendered service. The saved artifact is an unrendered template, so the expected set is empty.
- No case has an unresolved known-URL/unknown-method target. A suitably licensed real example was not found within the time budget. The `unresolved` lists are empty, and this gap remains open.
- The set has 9 cases and no HAR, link-heavy navigation, `<base href>` or cross-origin `document_url` evidence. Several artifacts are teaching examples rather than production applications. Upstream files may change after these revisions; the pinned SHAs and hashes preserve the retrieved bytes.
