# Surface Map

Turn saved web artifacts into a reviewable route inventory, field shapes and an API skeleton. Useful when documenting an app you own, planning an integration, or comparing two authorized captures.

![Surface Map workspace](docs/dashboard.png)

## Run

Python 3.11 or later. No third-party Python packages or AI account are required.

```sh
python -m surface_map
```

Open **http://127.0.0.1:8768**. A synthetic example loads automatically. Choose `--port 9000` if needed.

## Use

1. Add saved UTF-8 HTML, HAR or JavaScript files you own or have permission to inspect.
2. Analyze the selection. Inspect each route's generic source ID and line or HAR-entry location.
3. Export the report, an OpenAPI 3.1 skeleton, or the compact contract as JSON.
4. To compare captures, keep the first analyzed selection as a baseline, import and analyze the second, then compare. Clear the session to release both selections and reports from the page.

| Evidence | What is extracted | Label |
| --- | --- | --- |
| HAR | Request method, normalized path, status, query names and supported JSON body shapes | Observed |
| HTML | Links, form actions and control names, script and stylesheet asset paths | Declared |
| JavaScript | Literal `fetch` URLs and simple literal methods | Inferred |

No source is executed, no traffic is replayed, and no URL is fetched. Inline scripts inside HTML are ignored. Import a separate JavaScript file to inspect its literal fetch hints.

## Privacy choices

Input stays in request memory; the server keeps no uploads or reports. The browser keeps selected File objects while the workspace is open. Exports contain generic source IDs, never original filenames or source snippets.

Headers, cookies, hostnames, query values and body values are discarded. A small fixed vocabulary preserves common route and field names. Other names become aliases, and numeric path segments become inferred parameters. For example, a customer-specific path can become `/api/customers/{integer_3}/{segment_4}`. JSON bodies contribute types and field shapes, without examples, constants or inferred required fields.

This is deliberately lossy. Different origins or private route names can normalize to the same path. Aliases are not the original API's parameter names. Check a report before sharing it; minimization does not establish that every possible sensitive fact is absent.

## Practical limits

- Up to 6 artifacts per capture, 512 KiB each and 1 MiB combined; HAR supports up to 300 entries and 20,000 JSON nodes. Header-heavy captures can reach the node limit before the entry limit.
- Byte caps are not acceptance guarantees. JSON nesting, collections, routes and HTML tags are bounded. JavaScript supports up to 300,000 lexical tokens with a cooperative three-second tokenizer budget; unusually dense or complex files can be rejected below 512 KiB.
- Export serialization stops at 3 MiB, including amplification from combined response shapes. Smaller captures may be needed. Errors include only predefined reasons and generic source IDs, without source text.
- Base64 HAR bodies, non-JSON bodies, dynamic JavaScript calls, archives, APKs, native binaries and .NET assemblies are unsupported. JavaScript hints require unescaped string URLs; computed, shorthand, inherited or accessor method definitions are omitted.
- The JavaScript detector finds lexical hints, not complete program behavior. It may miss calls or find calls in code that never runs.
- A HAR observation describes that capture. It does not prove a route still works, is safe to call, or represents a complete service contract.
- OpenAPI exports omit servers. Equivalent redacted path templates merge with combined evidence; a parameter observed as both numeric and text uses a string schema. Body shapes are combined observations, form encoding is unverified, and redaction placeholders are inferred. Supply your own server and validate the contract before using it.
- HTML forms without an explicit action are omitted because the saved page URL is unknown. Duplicate HTML attributes use their first value, matching browser handling.
- Comparison reports added, absent and changed normalized evidence. Absence from a capture does not prove removal from a service.

## Check it

```sh
python -m unittest discover -s tests -v
```

The 39 tests cover extraction, evidence labels, body shapes, capture differences, planted-secret redaction across every export, malformed Unicode and JSON, 128/256/500 KiB JavaScript samples, work limits, hostile origins, CSRF, framing, slow header drips, export amplification and processing admission.

The optional real-browser check uses Node.js and Playwright:

```sh
npm install --no-save playwright
npx playwright install chromium
# Keep the app running in another terminal:
node tests/browser_smoke.cjs
```

Set `SURFACE_BROWSER_CHANNEL=chrome` to use an installed Chrome. The browser check verifies imports, downloads, filters, capture comparison, clearing, redaction in the page and exported files, and desktop/mobile overflow. It fails on script errors or external requests and refreshes the synthetic screenshots in `docs/`.

The local server binds IPv4 loopback, rejects unexpected hosts and origins, requires a request token, limits concurrent work and uses fixed static routes. See [SECURITY.md](SECURITY.md) for boundaries.
