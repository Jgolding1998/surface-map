# Framework development batch

Seven cases from five distinct upstream projects: Angular, Vue documentation,
Express, FastAPI and Lit. The seven saved artifacts total 8,092 bytes. All are
`development`, `real` and `exposed`; their labels are **agent-reviewed by Codex**,
not human-reviewed. These teaching examples, templates and starter pages add
real-source regression coverage, not unseen-project or production accuracy
evidence. Multiple cases from Vue or Lit remain one project group each.

The manifest and labels use the existing `surface-map-holdout-corpus/1` and
`surface-map-holdout-labels/1` contracts. Labels were derived by reading the
actual bytes and the documented extraction/minimization rules before any
Surface Map predictions. No analyzer, upstream program or captured source was
executed to create the labels.

| Project | Frozen revision | License | Cases |
| --- | --- | --- | --- |
| [Angular](https://github.com/angular/angular/tree/74961c662420ea02a5872bfa1a0ee3284526b887) | `74961c662420ea02a5872bfa1a0ee3284526b887` | MIT | Reactive-form template without a document URL |
| [Vue documentation](https://github.com/vuejs/docs/tree/40aa88af0094f7bab4aaf786e55c748a6a251d88) | `40aa88af0094f7bab4aaf786e55c748a6a251d88` | CC BY 4.0 | Dynamic tutorial Fetch target; literal Fetch inside a watcher callback |
| [Express](https://github.com/expressjs/express/tree/1c6f1b0fe4000977b9a7b4f34097e4d527dd9824) | `1c6f1b0fe4000977b9a7b4f34097e4d527dd9824` | MIT | EJS login form and navigation |
| [FastAPI](https://github.com/fastapi/fastapi/tree/45cb2252c737906f37544f5ba6bee0295fb81468) | `45cb2252c737906f37544f5ba6bee0295fb81468` | MIT | Unrendered Jinja navigation |
| [Lit](https://github.com/lit/lit/tree/16507ef656417c9c53a55a02a44a563bbbf2c484) | `16507ef656417c9c53a55a02a44a563bbbf2c484` | BSD 3-Clause | Starter navigation; relative documentation navigation with explicit resolution context |

All files were retrieved on 2026-10-31. `sources.json` records each upstream
path, exact revision, full-file SHA-256, saved-artifact SHA-256, byte size and
selection range. Whole files preserve upstream bytes. The Vue watcher artifact
is exactly `src/guide/essentials/watchers.md` lines 76–96, joined with LF and one
trailing LF. It retains the import and complete callback; only the surrounding
Markdown fences and `<script setup>` tags are excluded. No wrapper was invented.

Verbatim upstream copyright/license notices are in `licenses/`, including the
complete Vue documentation CC BY 4.0 license. Vue selections are credited to
Yuxi (Evan) You and Vue documentation contributors; original source URLs appear
in every artifact's provenance. Changes are limited to selecting the specified
JavaScript lines and renaming local copies. Other files retain their bytes.

Three intentional empty expected sets cover an actionless Angular form without
page context, a Vue target depending on an imported `ref()` result, and an
unrendered FastAPI Jinja URL. They do not establish an absence of runtime
requests. The literal Vue watcher call is inferred intent even though execution
depends on the callback and condition. EJS/Jinja includes are never rendered.

The Lit documentation case supplies `https://example.com/` as **hypothetical
resolution metadata**, not an observed deployment. Two distinct navigation
paths (`examples/`, `install/`) both minimize to `/{segment_1}`. The label keeps
that documented collision; it does not claim destination identity. The empty
Home `href` contributes no route. Assets and escaped code examples are excluded.
All unresolved-target sets are empty: this batch does not cover known URL with
unknown method, HAR observations, authenticated pages or complete applications.

Validate the batch without executing any artifact:

```sh
python -m evals.holdout --corpus evals/realworld/batches/frameworks/corpus.json --labels evals/realworld/batches/frameworks/labels.json --validate
```

Manifest SHA-256 (LF-normalized):
`81e6749716d45c99ab8c704a5d29fe9d64f7836d89c1e3b84e916c27cf7f4ed5`.
Schema/hash validation passed for five groups, seven cases and seven unique
captures. Validation checks integrity and declared metadata, not label quality
or independent-project provenance.
