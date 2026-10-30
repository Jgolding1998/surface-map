# Surface Map

Inspect saved web artifacts and selected source files without running them. Build a route inventory and API skeleton from web captures, or a minimized language, dependency and library-capability inventory from source. Useful when documenting an app you own or comparing authorized captures.

For Claude, Codex and other local MCP clients, use the [MCP installation guide](docs/MCP.md). The optional interface can also inspect a public HTTPS URL and selected scripts, with explicit coverage limits. A [recorded agent demo](docs/AGENT_DEMO.md) shows what was tested.

![Surface Map workspace](docs/dashboard.png)

## Run

Python 3.11 or later. No third-party Python packages or AI account are required.

```sh
python -m surface_map
```

Open **http://127.0.0.1:8768**. A synthetic example loads automatically. Choose `--port 9000` if needed.

For syntax and scope analysis of JavaScript, JSX and TypeScript, install Node.js 24.22.1 or later in the 24.x line, then explicitly install the pinned parser:

```sh
python -m surface_map setup-js
```

This command installs the exact locked Babel dependencies with npm lifecycle scripts disabled. Analysis never installs packages. Keep the `analyzers/javascript` directory with the Python module. Use an absolute `SURFACE_MAP_NODE` path to select a particular Node executable when needed.

The **JavaScript analysis** selector offers automatic, required syntax analysis, and basic lexical hints. Automatic mode uses the parser when available and reports a missing-parser fallback in the export and warnings. Required mode also rejects unavailable parser dependencies. Once syntax analysis starts, syntax, work-limit and worker failures reject the input in both modes; they never trigger weaker lexical parsing. Basic mode needs only Python and retains the narrower lexical scanner.

## Use

1. Choose **Saved web artifacts** and add UTF-8 HTML, HAR or JavaScript files you own or have permission to inspect.
2. Analyze the selection. Inspect each route's generic source ID and line or HAR-entry location.
3. Export the report, an OpenAPI 3.1 skeleton, or the compact contract as JSON.
4. To compare captures, keep the first analyzed selection as a baseline, import and analyze the second, then compare. Clear the session to release both selections and reports from the page.

| Evidence | What is extracted | Label |
| --- | --- | --- |
| HAR | Request method, normalized path, status, query names and supported JSON body shapes | Observed |
| HTML | Links, form actions and control names, script and stylesheet asset paths | Declared |
| JavaScript/TypeScript | Syntax and scope analysis of Fetch, XMLHttpRequest and supported Axios calls, aliases, constant expressions and options | Inferred |

Saved-artifact analysis executes no source, replays no traffic, and fetches no URL. Inline scripts inside HTML are ignored. Import a separate script to inspect its client hints. A known URL with an unknown method is listed separately; exports never invent a GET operation for it. OpenAPI retains these targets in an `x-surface-map-unresolved-operations` extension. The optional MCP URL tool below can fetch a public page and selected external scripts before analysis.

## Inspect selected source files

Choose **Selected source files** in the workspace. Add saved `requirements.txt`, `pyproject.toml`, `package.json`, `Cargo.toml`, or supported Python, JavaScript/TypeScript, Rust and POSIX shell files. Analyze, inspect each generic source location, and export **Inventory JSON**. A synthetic source example is available in this mode. Keep a source selection as a baseline to compare added, absent, and changed static evidence in another selection.

The source view separates manifest declarations from inferred import hints. It recognizes a curated vocabulary of public libraries and their usual roles, such as HTTP frameworks, audio I/O or desktop frameworks. A dependency declaration, import, or installer hint does not prove that the application uses that library at runtime or implements its associated feature. Unknown dependency names are omitted and counted. Versions, project names, maintainers, source paths, command text and snippets are never exported.

![Static source inventory](docs/source-dashboard.png)

The CLI prints the same minimized inventory without starting a server:

```sh
python -m surface_map inspect --files requirements.txt example.py
python -m surface_map inspect --files package.json Cargo.toml > inventory.json
python -m surface_map inspect --capture selection.source.json > inventory.json
python -m surface_map inspect --javascript-engine enhanced --files example.ts
```

The selected-file CLI reads only the explicit ordinary files supplied. It does not walk a checkout, follow symbolic links or Windows reparse points, access Windows UNC/device paths, mapped network drives or alternate streams, open requirements includes, install dependencies, fetch GitHub URLs, or run an installer. Save the authorized files first. A source capture JSON can supply logical files without retaining their original names:

```json
{"artifacts":[{"kind":"requirements","content":"fastapi\nuvicorn\n"},{"kind":"python","content":"import sounddevice\n"}]}
```

Capture kinds correspond to the supported formats: `requirements`, `pyproject`, `package`, `cargo`, `python`, `javascript`, `typescript`, `rust`, and `shell`. The browser accepts individual manifests and source files; capture JSON is a CLI input. Keep a private mapping from source IDs to your selected files if you need to trace an exported line back to the original.

Each source reports `parsed` or `rejected`. Unsupported syntax or structural limits can reject one file while other files still contribute evidence; inspect warnings before using the report. Source comparisons require fully parsed selections, so a parser failure cannot masquerade as a removed dependency. Malformed envelopes, unreadable files and byte-limit failures are controlled errors; CLI failure exits with status 2. No source text or path is included in these errors.

## Use from an agent with MCP

The optional [MCP stdio interface](docs/MCP.md) exposes `analyze_url`, `analyze_capture`, `compare_captures`, `inspect_sources`, `demo`, and `limits` through the official Python MCP SDK. Install `requirements-mcp.txt` in a virtual environment, then configure Claude or Codex using the linked guide. The default CLI and browser app still need no third-party Python packages.

See the [verified Claude agent demonstration](docs/AGENT_DEMO.md) for real tool calls and the resulting explanation.

Ask an agent: **“Use Surface Map to analyze this public URL and explain the routes and coverage limits.”** `analyze_url` fetches one public HTTPS page and up to five same-origin external scripts. It does not run JavaScript, log in, submit forms, test discovered APIs, or crawl linked pages. Captures are analyzed locally; the tool returns minimized evidence and explicit omissions. Use `demo` for an offline example.

For logged-in or rendered pages, use a separate browser to obtain authorized saved artifacts, then call `analyze_capture`. Prefer small relevant selections; the client handles those supplied contents before Surface Map minimizes its response, and its context limit may be lower than the analyzer's byte caps.

To inspect source-level agent guidance, ask for `include_page_signals: true` with `analyze_url` or `analyze_capture`. It reports phrases that address or mention AI agents, explicit hiding declarations and machine-document references with source locations, without returning the page's instructions or following them. [Example and limitations](docs/PAGE_SIGNALS.md).

## Privacy choices

Input stays in process memory; the server keeps no upload files or reports. Optional parser workers are reused briefly and release explicit request references after responding; idle workers expire after five seconds. This does not securely erase process memory or operating-system swap. The browser keeps selected File objects while the workspace is open. Exports contain generic source IDs, never original filenames or source snippets.

Headers, cookies, hostnames, query values and body values are discarded. A small fixed vocabulary preserves common route and field names. Other names become aliases, and numeric path segments become inferred parameters. For example, a customer-specific path can become `/api/customers/{integer_3}/{segment_4}`. JSON bodies contribute types and field shapes, without examples, constants or inferred required fields.

This is deliberately lossy. Different origins or private route names can normalize to the same path. Aliases are not the original API's parameter names. Check a report before sharing it; minimization does not establish that every possible sensitive fact is absent.

## Practical limits

- Up to 6 web artifacts per capture: HTML can be up to 4 MiB, HAR/JavaScript/TypeScript up to 512 KiB each, and the capture can total 4 MiB of decoded UTF-8. Selected source files retain their separate 512 KiB per-file and 1 MiB combined limits. The interface reads these limits from the local server and reports the measured size when rejecting an input.
- The browser sends web artifacts as strict base64 UTF-8 to avoid JSON quote expansion. API callers can send either `text` or `text_b64` for each artifact, never both. The HTTP envelope is capped at 16 MiB plus 64 KiB for web analysis and comparison, 3 MiB for source requests. Heavily escaped legacy `text` requests can hit the envelope cap before the decoded cap; use `text_b64` for predictable transfer size.
- HAR supports up to 300 entries and 20,000 JSON nodes. Header-heavy captures can reach the node limit before the entry limit. HTML supports up to 10,000 start tags with cooperative three-second work checks. Inline scripts and embedded JSON are not interpreted. Embedded documents are recorded as references, without fetching their content; reports warn about these missing inputs.
- Byte caps are not acceptance guarantees. JSON nesting, collections, routes and HTML tags are bounded. The basic JavaScript scanner supports up to 300,000 lexical tokens with a cooperative three-second tokenizer budget. The optional parser allows 100,000 flat AST nodes, 200,000 edges, an 8 MiB protocol response, a 256 MiB Node heap and a three-second request deadline. Python interpretation has separate nesting and work limits. Dense files can be rejected below 512 KiB.
- Source manifests have structural limits, source code has parser and work limits, and evidence lists are bounded. The source inventory uses a cooperative five-second work budget. Registry dependencies are recognized only from a curated list; local paths, URLs, editable installs and Cargo workspace inheritance are not resolved. Shell hints are POSIX only. Simple here-documents are skipped with exact delimiter handling; unsupported or unterminated here-documents and unsupported shell quoting reject that source. Ambiguous parameter, command or ANSI-C expansions on a line containing `<<` also reject the source. Compound guards, command substitution and wrapper options can hide installer commands from the lexical hints. The scanner does not build a call graph, resolve every module or distinguish reachable code from dead code.
- Export serialization stops at 3 MiB, including amplification from combined response shapes. Smaller captures may be needed. Errors include only predefined reasons and generic source IDs, without source text.
- Base64 HAR bodies, non-JSON bodies, fully dynamic targets, archives, APKs, native binaries and .NET assemblies are unsupported. Syntax analysis recognizes supported Fetch, XMLHttpRequest and Axios patterns, constant aliases and expressions, lexical scope, options spreads and conservative mutation effects. Unknown options can leave a known target with an unresolved method. It does not execute functions, resolve an arbitrary module graph, prove reachability, or reconstruct application behavior.
- The basic JavaScript fallback is narrower: it recognizes literal fetch calls and selected module-level string constants, without full syntax or scope analysis. Recognized client shadowing can suppress hints for the whole file. Uncertain syntax, JSX text or hidden bindings can conceal or fabricate lexical hints. Required syntax mode is preferable when reviewing JavaScript evidence. Both modes can miss calls or find calls in code that never runs; static hints need verification.
- Inferred calls describe intended targets, not valid executable requests. A call can throw before sending, for example a Node client given a relative URL or a GET request with a body. Runtime environment, request validity, proxies, external scripts, arbitrary function returns and module side effects are not reconstructed. Mutation and aliasing analysis is approximate; unsupported behavior can hide calls or produce incorrect hints. Review the source evidence before using a generated contract.
- Parser workers are reused, with at most two active workers and two waiting callers. Saturation returns a fixed `busy` reason. The three-second parser deadline excludes bounded process cleanup, which can add about 3.6 seconds per worker on exceptional termination. Python interpretation has its own cooperative three-second deadline; these are work safeguards, not a total HTTP latency guarantee.
- A HAR observation describes that capture. It does not prove a route still works, is safe to call, or represents a complete service contract.
- OpenAPI exports omit servers. Equivalent redacted path templates merge with combined evidence; a parameter observed as both numeric and text uses a string schema. Body shapes are combined observations, form encoding is unverified, and redaction placeholders are inferred. Supply your own server and validate the contract before using it.
- Without a known page URL, saved captures omit page-relative references and forms without an action. MCP callers can supply optional `document_url` metadata for one saved HTML page and its scripts; this never fetches the URL. URL mode supplies the final document URL automatically. Both resolve references using the first supported `<base href>`. This remains static HTML interpretation; script-driven navigation, submit-button overrides and CSP are not simulated. In-page `#` links are ignored.
- Form methods follow browser submission: `post` is POST, `dialog` is ignored and anything else is GET. GET form controls become query fields. Duplicate HTML attributes use their first value, matching browser handling.
- Text containers such as `textarea`, `title` and `script` do not contribute nested routes. A slash in `<script/>` or `<form/>` does not close that HTML element. Conditional `noscript` contents are omitted because the capture does not establish the browser's scripting state.
- On Python parsers that change ambiguous attribute references, link and form URLs such as `?page=1&currency=USD` produce a controlled error. Update Python or write `&amp;` in HTML attributes. The guard checks used URL attributes; an unfamiliar attribute tokenizer conservatively rejects ambiguous tags. Asset queries are discarded.
- Comparison reports added, absent and changed normalized evidence, including gained or lost evidence kinds and query-field changes for unknown-method targets. Changed entries retain before and after source evidence. Line moves and source-order changes alone do not count as changes. Absence from a capture does not prove removal from a service.

## Check it

```sh
python -m unittest discover -s tests -v
```

For a reproducible static-route regression check, run `python -m evals.run --mode enhanced --trials 3 --check` after parser setup, or use `--mode basic` to inspect lexical hints. The [16-case synthetic corpus](evals/README.md) reports exact cases, TP/FP/FN, precision/recall/F1 and separate unknown-method targets, with explicit cold/warm timing samples. It tests known patterns and negative controls; it is not an unseen benchmark or a claim about live endpoints. Enhanced mode is required when selected and never falls back.

For broader evidence checks, run `python -m evals.diverse --engine enhanced --repeats 3 --check`. The [diverse task corpus](evals/diverse/README.md) covers HTML, HAR, JavaScript/TypeScript, mixed evidence, comparisons, page signals and rejected inputs. Its fixed labels and scorer are public so failures can be reproduced. These are developer-authored regressions, not proof of general superiority over an AI agent.

The October 30, 2026 run passed 64/64 tasks in enhanced mode and 57/64 in basic mode, with stable scored results over three repetitions. In a separate 45-task route/evidence comparison, enhanced analysis and blind text-only Claude each scored 45/45 in all three trials. The agent received the full task rules. This shows no extraction advantage on that synthetic sample. A separate [tool-description check](evals/TOOL_CHOICE.md) covers choosing appropriate tools, without claiming automatic use in normal sessions.

The tests cover extraction, browser form and relative URL semantics, evidence labels, body shapes, capture differences, planted-secret redaction across every export, malformed Unicode and JSON, 128/256/500 KiB JavaScript samples, work limits, hostile origins, CSRF, framing, slow header drips, export amplification and processing admission. Source tests add manifest and import inspection, ignored installer arguments and includes, no execution or network, bounded malformed inputs, explicit file selection, minimized CLI errors, and source evidence comparison.

The optional real-browser check uses Node.js and Playwright:

```sh
npm install --no-save playwright
npx playwright install chromium
# Keep the app running in another terminal:
node tests/browser_smoke.cjs
node tests/source_browser.cjs
node tests/enhanced_browser.cjs
```

Set `SURFACE_BROWSER_CHANNEL=chrome` to use an installed Chrome. The browser check verifies imports, downloads, filters, capture comparison, clearing, redaction in the page and exported files, and desktop/mobile overflow. It fails on script errors or external requests and refreshes the synthetic screenshots in `docs/`.

The local server binds IPv4 loopback, rejects unexpected hosts and origins, requires a request token, limits concurrent work and uses fixed static routes. See [SECURITY.md](SECURITY.md) for boundaries.
