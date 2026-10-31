# Real-world development batch: request patterns (v1)

Seven small cases from five permissively licensed public GitHub projects that are new to the real-world corpus. Every artifact is a whole upstream file pinned to an immutable commit. The cases cover literal Fetch calls with absolute URLs, lowercase methods, trailing slashes and template-literal siblings, plus XHR, client-wrapper, dynamic-origin and route-helper negative controls. The artifacts are real upstream bytes, not generated scenarios.

**This is development coverage.** It does not measure unseen performance and gives no independent evidence of accuracy. Every project uses `split: development`, `kind: real` and `exposure: exposed`. The files are public and were chosen with knowledge of the analyzer's documented rules. Labels are `agent-reviewed` by Claude Code and were not human-reviewed. Use fresh projects for held-out claims.

The schema matches `evals/realworld/` (`surface-map-holdout-corpus/1`, `surface-map-holdout-labels/1` and `surface-map-realworld-sources/1`). `corpus_sha256` is the SHA-256 of `corpus.json` with CRLF normalized to LF. Artifact hashes cover the exact saved bytes. The local `.gitattributes` marks artifacts and licenses `-text` so the bytes stay LF. The root `evals/realworld/verify.py` checks this batch when its `ROOT` points here. This batch does not ship its own copy of that script.

## Sources

All files were retrieved on 2026-10-31 from `raw.githubusercontent.com` at the commit SHA that the GitHub REST API returned for `HEAD`.

| Project id | Repository @ revision | License | Artifacts (whole files) |
| --- | --- | --- | --- |
| googlechrome-samples | GoogleChrome/samples @ 8c3a755f756fcd816f088684a3c697cbd891fc35 | Apache-2.0 | `fetch-api/fetch-post-demo.js`, `encoding-api/demo.js` |
| tanstack-query | TanStack/query @ 979695d0a2daa8662bdfcbf9f4ae9c9805d97dec | MIT | `examples/react/basic/src/services/posts/api.ts`, `examples/react/star-wars/src/services/films/api.ts` |
| redux-toolkit | reduxjs/redux-toolkit @ 0fd611cc79d44990ac9a040dec4d5cb446233440 | MIT | `examples/query/react/basic/src/services/pokemon.ts` |
| astro | withastro/astro @ 10dae2f31486cc0a6a04d1f3d7f5ab7fa8889b30 | MIT | `examples/ssr/src/api.ts` |
| remix | remix-run/remix @ a178e1f3a6e4ad94eebdf9b53e360c1cff7fe259 | MIT | `demos/bookstore/app/actions/fragments/public/cart-button.tsx` |

`licenses/<project>.txt` holds the verbatim upstream `LICENSE` at the pinned revision. GoogleChrome/samples has no `NOTICE` file at that revision; the request returned 404. GitHub reports Astro's license as `NOASSERTION` because its `LICENSE` appends third-party MIT notices for code vendored from SvelteKit and Vite in `packages/`. The primary license, and the license for `examples/`, is MIT. The full file is kept.

## Cases

| Case | Kind | Expected routes |
| --- | --- | --- |
| googlechrome-fetch-post-gists | javascript | `POST /{segment_1}` |
| googlechrome-encoding-xhr-parameter-negative | javascript | none |
| tanstack-basic-posts-api | typescript | `GET /posts` |
| tanstack-star-wars-films-api | typescript | `GET /api/{segment_2}` |
| rtk-query-pokemon-fetchbasequery-negative | typescript | none |
| astro-ssr-api-dynamic-origin-negative | typescript | none |
| remix-bookstore-cart-button-route-helper-negative | typescript (TSX) | none |

There are 3 positive cases with 3 routes, 4 negative controls and 0 unresolved targets.

## Label rules applied

Labels come from reading the artifacts against the root README and the `analyze.py` and `js_analysis.py` vocabulary: `SEGMENTS`, `safe_path`, `_wire_method` and the Fetch, XHR and Axios call handling. Neither the analyzer nor any artifact was executed. No Surface Map output was used.

- Hosts are dropped. Empty path chunks, including trailing slashes, are discarded. Segments in `SEGMENTS` are kept (`posts`, `api`). Other segments become `{segment_N}`, where N is the 1-based index among non-empty chunks.
- A Fetch call without options, or with options that omit `method`, is GET. A literal method is upper-cased for standard verbs, so `'post'` becomes POST.
- JS/TS routes are `inferred` with `javascript` evidence.
- Targets built from function parameters, imported helpers or ambient globals such as `location.origin` are dynamic. They produce neither a route nor an unresolved target. An unresolved target requires a known URL with an unknown method.
- Library wrappers that are not Fetch, XHR or Axios, such as RTK Query `fetchBaseQuery`, are not modeled.

## Ambiguous labels

- **`astro-ssr-api-dynamic-origin-negative`:** L56 posts to `` `${location.origin}/api/cart` `` with literal `method: 'POST'`. A tool that treats `location.origin` as the same-origin base would report `POST /api/cart`, which is arguably correct. The label is empty because the target is not a constant. The `getJson` call sites pass the literal paths `/api/products`, `/api/user` and `/api/cart`, but those are function arguments.
- **`tanstack-*`:** the sibling template calls (`` `.../posts/${id}` `` and `` `.../films/${filmId}/` ``) are excluded as parameter-derived. A tool that templatizes partially known URLs would add routes, such as `GET /posts/{segment_2}`.
- **`rtk-query-pokemon-fetchbasequery-negative`:** at runtime, this file issues `GET https://pokeapi.co/api/v2/pokemon/<name>`. A tool that models RTK Query would disagree with the empty label.
- **`remix-bookstore-cart-button-route-helper-negative`:** the endpoint is defined in `routes.ts`, which is excluded. A project-level tool given that file might resolve it.
- **`googlechrome-fetch-post-gists`:** the label depends on case-normalizing `'post'`. A tool that requires upper-case verbs would leave the method unresolved.

## Limitations

- There is no unresolved-target case, no HTML, no HAR and no Axios. TanStack's current examples use Fetch rather than Axios, and no imported Axios call was found in these five repositories within the time budget.
- Four of the seven cases are empty negatives. Positives cover only absolute-URL Fetch GET and POST requests.
- GoogleChrome/samples `service-worker/mock-responses/index.html` has a literal `xhr.open('POST', ...)` call. It was excluded because the URL embeds an API key.
- GitHub code search hit its rate limit partway through candidate discovery. Raw file downloads were unaffected.
- All artifacts are whole files, so `upstream_file_sha256` equals the artifact `sha256`. The upstream files may change after these revisions. The pinned SHAs and hashes preserve the retrieved bytes.
