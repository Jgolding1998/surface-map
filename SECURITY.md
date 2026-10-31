# Security

Surface Map analyzes artifacts for one trusted local operating-system account. Saved-input tools do not access the network. The optional MCP `analyze_url` tool retrieves a public HTTPS page and selected scripts under the limits below. Neither mode captures authenticated browser sessions, accesses installed apps, recovers credentials, bypasses authentication, executes imported code, or replays discovered API traffic.

## Data minimization

Web parsers project into a restricted model before returning anything to the browser. Source text, original filenames, hostnames, headers, cookies, query values and scalar body values are excluded. Common route and field vocabulary is retained; unknown names are aliased. Evidence locations contain only generic source IDs and numeric positions. Errors and logs contain no source excerpts.

Source inspection returns only curated language, dependency, library-capability and installer-hint labels with generic file IDs and bounded locations. Unknown dependency names are counted, never returned. Versions, project metadata, command arguments, source text and imported module paths are omitted. Python uses bounded tokenization and AST parsing, not imports or execution. Optional JavaScript/TypeScript syntax analysis uses a pinned Babel parser and a bounded Python abstract interpreter; a visible basic fallback, Rust and POSIX shell use lexical scans. TOML and JSON manifests have byte and structure limits. These are static hints, not a runtime behavior or security assessment.

JSON is bounded and rejects duplicate keys and nonfinite values. HTTP envelopes have an early token and depth check before object decoding, followed by a 100-node structural check; large quoted captures do not bypass limits on wide containers. Reserved JavaScript prototype keys are not retained as schema property names. The UI uses `textContent`, never imported HTML or source markup. Imported HTML is parsed as text and is never mounted into the page. Optional Node workers run only the reviewed parser, never the imported JavaScript. Target strings are not evaluated, imported, rendered, or treated as instructions.

Parser and worker files are checked against a pinned integrity manifest. Node receives a minimal environment without API keys or Node injection options, bounded input/output, a per-request deadline and a heap cap. Its filesystem permissions and module allowlist are defense in depth, not an operating-system sandbox: Node 24 permissions do not block network access and do not fully constrain every native or process-internal interface. Trust in the pinned parser remains necessary. Local executable replacement, same-account races, compromised dependencies and arbitrary native exploits are outside this boundary. Package setup is an explicit operation; request handling never runs npm, installs dependencies, or executes downloaded source. URL mode downloads selected scripts only as untrusted text for analysis.

Minimization trades fidelity for disclosure reduction. A normalized path, field name, count, type or status can still reveal information about an application. Review exports before sharing. The tool does not certify that an artifact or export is public or free of sensitive facts.

Opt-in page-signal analysis returns fixed content categories, source positions, source hiding declarations and minimized machine-resource paths. It does not return page prose, instruction bodies, commands, arbitrary attributes, URLs or digests of private text. These additional labels can still reveal facts about supplied content. Source declarations are not computed visibility or a prompt-injection verdict. Detected guidance is never followed and discovered resources are never fetched by this option. The bounded inventory shares the HTML analysis deadline; malformed nesting, unsupported styles and text/finding truncation limit coverage.

## MCP and public URL capture

The optional MCP interface uses stdio, with no network listener. Five tools analyze supplied or bundled contents. Only `analyze_url` makes outbound requests: public HTTPS on port 443, without cookies, credentials or environment proxies. DNS answers and redirects are checked, private and special-purpose addresses are rejected, and connections use a vetted numeric IP while verifying TLS for the requested hostname. TLS verification is never disabled.

Capture allows one HTML document and up to five same-origin external scripts, at most nine requests and three redirects, within a shared 20-second network deadline and per-file/combined byte caps. Inline scripts, cross-origin scripts, dynamic imports, browser rendering, API replay and form submission are unsupported. A pending operating-system DNS lookup can outlive its timeout; a single retained slot bounds outstanding lookups. The standard library bounds HTTP header parsing before the module applies its smaller accepted-header cap.

The final URL is used internally to resolve relative references. Raw fetched content is not returned to the agent; minimized evidence, counts and fixed warnings are. Websites still receive normal requests from the tool's host. Supplied-content tools receive data already handled by the agent/client, whose retention and logging are outside this tool's control. Captures and results are untrusted data, not instructions. A successful fetch may contain a consent or challenge page rather than the intended application.

Analysis is serialized, with bounded MCP input and output and fixed busy/error messages. If script analysis fails within supported limits, valid HTML evidence is retained with an explicit partial-analysis warning. Missing evidence never proves an API is absent. See [MCP setup and limits](docs/MCP.md) for exact schemas and operational boundaries.

## Local HTTP server

- IPv4 loopback bind with exact Host and same-origin checks; no CORS permissions. Windows listeners reserve the port with `SO_EXCLUSIVEADDRUSE` so another reuse-enabled listener cannot share it.
- Per-process random request token, checked before processing POST bodies.
- Duplicate security/framing headers and transfer encoding rejected. Content-Length and JSON content type required.
- Web analysis and comparison have a 16 MiB plus 64 KiB encoded request cap; source requests remain capped at 3 MiB. Decoded HTML is capped at 4 MiB, other web artifacts at 512 KiB, and each web capture at 4 MiB combined. Source-file budgets are unchanged. Strict base64 transport has a bounded expansion and is decoded only after a per-kind encoded-length check.
- Responses remain capped at 3 MiB with bounded incremental serialization. A ten-second total request-line/header deadline, an eight-second body deadline, two concurrent analysis slots and at most sixteen connection workers bound admission and transfer. Web analysis shares a cooperative ten-second budget across both sides of a comparison. HTML also has a 10,000-tag cap and cooperative three-second work checks. The browser allows 35 seconds for transfer, analysis and bounded worker cleanup; these checks are not a hard process sandbox or a total HTTP latency guarantee.
- Fixed static assets, no path-based file access, no upload storage, no request-body logging and no source data in error responses.
- Content Security Policy disallows inline scripts, remote scripts, framing and form submission. Browser camera, microphone and location permissions are disabled.
- HTTP responses omit the Python and base-server version banner.

The server retains no upload files or reports. At most two optional parser workers are reused, with five-second idle expiry, a thirty-second maximum lifetime and a 128-request maximum. Explicit source and AST references are dropped after each response; memory is not securely erased. Selected files and a chosen baseline remain in browser memory until cleared or the page closes. Operating-system swap, a compromised browser, extensions, a debugger, or another process under the same account are outside this boundary. This is not an operating-system sandbox. Do not expose the port through a public tunnel or change its bind address.

## Supported input boundary

### Static Git comparison

The optional `compare-git` CLI and companion GitHub Action read immutable commit
objects from an explicitly selected local repository. They never check out or
execute its source, run its build, invoke its package manager, replay requests,
or fetch missing objects. The Git executable is resolved outside the data
repository and started by absolute path from the trusted tool directory with a
minimal environment, fixed arguments, subprocess time/output bounds, disabled
replacement objects, optional locks, lazy fetching, filesystem monitors and
external helpers. Git config includes, alternates, partial/promisor repositories,
metadata symlinks/reparse points, worktree redirects and selected symlink/gitlink
entries are rejected. These restrictions intentionally exclude some normal Git
setups. An ordinary inactive `config.worktree` file left by checkout setup is
permitted only while the primary configuration does not enable per-worktree
settings; enabled per-worktree configuration is refused before object reads.
An installed Git supporting `--no-lazy-fetch` remains trusted software;
this is not a sandbox against a malicious replacement Git binary or native Git
vulnerability.

Inputs require full commit SHAs and a unique merge base. Selected paths remain
local and are represented by generic source IDs. Raw source, filenames, origins,
field values and exception text are not copied into reports or logs. A six-file
limit and per-file/combined byte limits fail closed. No supported selection is
reported as not applicable, not a passing app check. Privacy normalization can
hide target, field and asset renames in the legacy normalized comparison. The
supplemental Git review uses jointly assigned in-memory identities and exports
only opaque IDs, fixed labels, counts and generic source/line references. It
retains no hashes or originals in reports. Domain-only changes can be detected;
classifying third-party origins requires caller-supplied HTTP(S) site-origin
metadata, which is never fetched. The Action reads it from a bounded ordinary
workspace file rather than echoing an origin-valued input. Local HTTP origins
are accepted as metadata; this does not change the public-URL HTTPS fetch policy.

Executable inline script bodies are analyzed as text with the same pinned AST
parser. Selected literal SDK imports and the classic gtag queue shim are hints,
not proof of package identity, successful loading or event delivery. Unknown
loader/global mutation can make tracking comparison incomplete. Supplemental
source/category failures suppress that category on both versions, withhold the
shared origin comparison, retain the compatibility report and force a non-pass
policy result. They never silently fall back to weaker tracking inspection.
Collection, record, inline-source, evidence and output limits bound this work;
see [CI coverage](docs/CI.md#checks-requested-in-the-pr-feedback). Source data
exists in process memory and is not securely erased. No script, SDK, import,
form or discovered endpoint is executed. Minimized reports still need review
before sharing.

The consumer workflow pins the reviewed tool separately from target source,
requires only repository read permission, keeps checkout credentials out of Git
config, and executes no target application code. Keep it unprivileged and do not
add build/test execution before the comparison in that job. The repository's own
integration test runs proposed tool code as ordinary test CI; it is not an
independent trusted assessment of hostile changes to Surface Map itself.

Use artifacts you own or are authorized to inspect. Capture authorization and the right to share derived information are installation responsibilities. Archives and application binaries are unsupported; no extraction, decompilation or native process attachment occurs.

The source CLI reads only explicitly selected ordinary UTF-8 files. It checks ancestors before descendants to reject symbolic links and Windows reparse points. On Windows it rejects UNC/device paths and alternate streams before filesystem access, checks the resulting absolute path again, and rejects remote or unknown drive types with the system drive-type API. It never walks a directory or follows requirements includes or package URLs. Other mounted filesystems and concurrent path changes by another local process are outside this boundary. HTTP source inspection accepts content, not filesystem paths or remote URLs. A rejected source contributes no partial evidence, and rejected selections cannot be compared as though their dependencies were absent. Cooperative work limits reduce accidental resource use; this is not a hard process-isolation boundary.

## Reporting a vulnerability

Use [Security > Report a vulnerability](https://github.com/Jgolding1998/surface-map/security/advisories/new) to send a private report. Do not submit vulnerability details through the public feedback form.

Include the affected version, platform, expected behavior and a synthetic reproduction. Leave out credentials, personal information and private files.
