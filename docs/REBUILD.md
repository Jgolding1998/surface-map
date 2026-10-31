# Evidence-backed rebuild guide

Turn saved evidence into a plan and an unimplemented local mock that you can
extend. The mock is not the original application. It returns no recorded
responses, and passing its checks does not show functional parity.

## What the evidence supports

| Evidence | Label | Supports | Does not support |
|---|---|---|---|
| HAR entry you saved | `observed` | A request was recorded with this method, normalized path, query names, status and JSON field names | Availability, authorization, side effects, unrecorded statuses or fields |
| HTML link or form | `declared` | The page declares a destination or submission | That the server accepts it, or what it returns |
| Script call hint | `inferred` | Client code appears to call this target | That the call runs, or the response |
| Unresolved target | `unresolved` | A target string with an unknown method | Any method or handler |
| String or module reference | `candidate` | A literal that looks like a path or module | That it is requested, reachable or used |

Origins, values and unknown field names are minimized. Nothing is fetched or
replayed, and candidates never become operations.

## 1. Capture evidence
Use only flows you are authorized to exercise, in a test account and a fresh
browser profile.

- **Chrome DevTools:** export a sanitized HAR from the Network panel. It filters
  sensitive headers but can keep URLs, query values and bodies. See the
  [Network reference](https://developer.chrome.com/docs/devtools/network/reference).
- **Playwright:** review traces locally in the
  [Trace Viewer](https://playwright.dev/docs/trace-viewer). Traces hold DOM
  snapshots and screenshots and are not imported. Export a HAR instead.

Remove unrelated and third-party entries before supplying a HAR. Your agent or
client sees file contents before Surface Map minimizes them.

## 2. Supply the capture
- **Browser app:** choose files in "Build a plan".
- **MCP `plan_rebuild`:** `artifacts`, optional `javascript_engine`,
  `document_url` and `include_scaffold`.
- **CLI:** a UTF-8 JSON file containing only an `artifacts` array:

```json
{"artifacts": [
  {"kind": "html", "text": "<!doctype html>..."},
  {"kind": "javascript", "text": "fetch(\"/api/items\");"},
  {"kind": "har", "text": "{\"log\": {\"entries\": []}}"}
]}
```

`kind` is `html`, `har`, `javascript` or `typescript`. A HAR is its JSON as a
string. Limits: 6 artifacts, 512 KiB per HAR or script, 4 MiB per HTML,
4 MiB in total, 300 HAR entries.

`document_url` applies to one HTML artifact plus scripts and cannot be combined
with a HAR. The CLI has no `document_url` option.

## 3. Read the plan

```sh
python -m surface_map rebuild --capture capture.json
```

The plan (`surface-map-rebuild-v1`) contains:
- `operations`: forms, script hints and HAR entries, with evidence, statuses,
  query names, shapes and per-operation unknowns (at most 50);
- `pages`: link-only groups, never handlers (at most 50);
- `unresolved`: targets with unknown methods (at most 50);
- `candidate_references`: string and module candidates, never handlers;
- `coverage`: sources, warnings, omissions and counts of anything cut;
- `unknowns`, `capture_guide`, `privacy` and `scope`.

Operations beyond the cap are cut in sorted method and path order, and counted
in `coverage.omitted_operations`.

**Origin scope.** Script hints and form targets on other origins are analyzed
like any other evidence, but nothing is requested. With a `document_url`, an
operation whose evidence is only `cross_origin` gets `stub: "none"` and no
handler. Without a `document_url`, which includes every HAR capture and every
CLI run, origin is unknown, so **third-party HAR entries become stubs unless you
remove them first**.
The plan flags unclassified origins and mixed-origin evidence as checks to resolve.

## 4. Generate the scaffold

```sh
python -m surface_map rebuild --capture capture.json --scaffold demo-mock
```

The parent directory must exist and contain no links; `demo-mock` must not exist.
MCP with `include_scaffold: true` returns the same files as text and writes
nothing. The browser app offers a download.

| File | Contents |
|---|---|
| `mock_app.py` | Standard-library server. Each included operation returns `501` with `implemented: false`; anything else returns `404` |
| `contract.json` | Minimized operations used for routing |
| `rebuild-plan.json` | A further-minimized plan: warnings appear as counts, so it is not identical to the original plan |
| `test_mock.py` | Checks routing, the 501/404 responses and local-request guards |
| `README.md` | Usage and limits |

Run it from the scaffold directory:

```sh
python -m unittest -v test_mock
python mock_app.py --port 8000
```

An empty contract explicitly skips the known-operation test. A passing mock test
only checks generated routing and guards, not the behavior of the captured app.

The server binds only to `127.0.0.1`. Use `http://127.0.0.1:PORT`; a
`localhost` Host header is rejected, as are foreign `Origin` and
`Sec-Fetch-Site` headers. Pages, unresolved targets, candidates and
cross-origin-only operations get no handler. No authentication is invented.

## 5. Map project files (optional)

```sh
python -m surface_map project --root ./my-app --files src/main.js src/api.js package.json
```

MCP `inspect_project` takes `files` as `{kind, content, logical_path}`.
- Up to 24 named files, 512 KiB each, 4 MiB total. File names are relative to
  `--root`; directories are never walked.
- Links, dot-paths, dependency and build folders, and credential-like names are
  refused. The name check is best-effort, so review what you select.
- Logical paths are used only during matching and are not returned. Output uses
  generic `source_N` IDs.
- Only exact relative literal imports between supplied files are linked. There
  is no extension guessing, so `./api.js` does not match `api.ts`. No package
  resolution, import maps, `require`, template specifiers or source maps.

## 6. Implement
Replace a 501 only with behavior you can justify, and add a behavior test for it.
Keep fixtures invented, never copied from a capture. Keep every unknown listed
until you resolve it.

## Limits
- This does not reconstruct a visual design. Use screenshots and traces as
  local references; the plan lists layout and interaction as unknowns.
- Inline scripts, WebSockets and source maps are not analyzed.
- Normalized paths are lossy. Unrelated originals can share one placeholder, and
  overlapping mock routes match in sorted order.
- Do not use this to copy a third-party product or to probe services you are not
  authorized to test.

See [`examples/rebuild-demo/`](../examples/rebuild-demo/README.md).
