# HTML and client-framework development batch

Seven small cases from five additional public upstream projects: Bootstrap, htmx, jQuery, Django and SvelteKit. The seven artifacts total 7,658 bytes. Four cases have positive static route labels; three deliberately exercise scope boundaries. They are source fixtures and fragments, not seven independent live websites.

Every project is `split: development`, `kind: real`, `exposure: exposed`. Labels are `agent-reviewed` by Codex, not independently human-reviewed. The sources were selected with knowledge of Surface Map's documented scope. This batch adds reproducible development coverage; it is not held-out evidence or proof of general accuracy.

The corpus, labels and source records use the same schemas as the real-world seed. Every upstream revision is an immutable Git commit. Whole artifacts and license files preserve exact upstream bytes; the two fragments preserve the selected upstream line bytes without rewriting or adding code. Source retrieval occurred on 2026-10-31. Complete upstream-file hashes, retained-artifact hashes, line ranges, byte counts and license hashes are in `sources.json`.

| Project | Immutable revision | License | Selection |
| --- | --- | --- | --- |
| `twbs/bootstrap` | `c2ab624f40c4dcfdbcae9747dce265834ffb4f5d` | MIT | Complete visual alert fixture; fragment-only anchors are excluded |
| `bigskysoftware/htmx` | `fa978b24e75fb03c137bf2cdae4fef0e711cf8a1` | 0BSD | Complete manual index and abort-request fixture; navigation versus unsupported `hx-get` |
| `jquery/jquery` | `c2f27864e4ffc2d1409c289fcb9a42b6165dfeaf` | MIT | `test/data/qunit-fixture.html` lines 1-14: complete navigation paragraphs and div |
| `django/django` | `a05e16a7230fe33d3f43f4a3aaed103cebb42efc` | BSD-3-Clause | Complete unrendered article form fixture |
| `sveltejs/kit` | `3da859a9f161d7e57d74a109149b83076664da7d` | MIT | Complete form at `actions/enhance/+page.svelte` lines 21-31 and complete injected-fetch load module |

Gold labels were written from reading the saved sources and documented rules, without running Surface Map or using its predictions. The initial freeze was:

- `corpus.json` SHA-256: `697ecd8b8306937844b91e7cae5b3cf2f6d8aff17a4c544df7189159bf7f67b6`
- `labels.json` SHA-256: `bade9fdb818802a4029ab4918b2736bd753f6e6ac08793576a03c1fbc2b4e560`

Each label includes its derivation and exclusions. No snippet was executed, imported as application code, rendered, used to install dependencies or used to contact an inferred endpoint.

## Context and interpretation limits

Three cases supply reserved-host `document_url` metadata. The htmx index uses `https://example.com/test/manual/index.html` to resolve its relative navigation against the source directory. The Django form uses hypothetical `https://example.com/articles/create` to test an actionless form. The SvelteKit form uses `https://example.com/actions/enhance`, corresponding to its source route directory. These URLs are evaluation inputs, not fetched URLs or claims about any deployed service. Without that context, those relative or absent actions would not support the same positive labels.

The Django template remains unrendered. `{{ form.as_p }}` and `{% csrf_token %}` do not establish expanded fields, generated routes or runtime behavior. The selected Svelte fragment is only a complete native HTML form within a `.svelte` file; the other forms, script imports and component runtime are excluded. The literal query-only `?/login` action resolves to the supplied page path. SvelteKit named-action semantics and submit-button `formAction` overrides are outside the declared extractor scope.

The negative htmx case still contains a real `hx-get` request declaration. Its empty supported-extraction label records the missing framework adapter, not proof that the application makes no request. Likewise, SvelteKit's `load({ fetch })` receives an injected client: a literal call through that parameter is outside the supported global Fetch identity rules. Runtime SvelteKit knowledge would justify a different answer. These are explicit framework-awareness boundaries, not universal ground truth about application behavior.

The navigation cases also expose lossy normalization. Different external origins in the jQuery fragment merge to `/`; distinct htmx manual links merge to the same three-segment alias path. A correct normalized result therefore cannot demonstrate destination identity or complete route recall. All positive labels are `declared` evidence, not observed successful operations. There are no HAR observations, deployed capture comparisons, known-target/unknown-method cases, browser-rendered states or independent reliability measurements in this batch.

## Verify integrity

Run `python -I evals/realworld/batches/html-clients/verify.py`. This checks local hashes, license presence, project flags and label/case parity. It reads files only and never runs the analyzer or artifacts. Corpus hashing follows the parent convention of normalizing CRLF to LF; artifact and license hashes use exact saved bytes.
