# Security

Surface Map analyzes saved artifacts for one trusted local operating-system account. It does not capture live sessions, access installed apps, recover credentials, bypass authentication, execute imported code, replay traffic, or retrieve remote URLs.

## Data minimization

Web parsers project into a restricted model before returning anything to the browser. Source text, original filenames, hostnames, headers, cookies, query values and scalar body values are excluded. Common route and field vocabulary is retained; unknown names are aliased. Evidence locations contain only generic source IDs and numeric positions. Errors and logs contain no source excerpts.

Source inspection returns only curated language, dependency, library-capability and installer-hint labels with generic file IDs and bounded locations. Unknown dependency names are counted, never returned. Versions, project metadata, command arguments, source text and imported module paths are omitted. Python uses bounded tokenization and AST parsing, not imports or execution. Rust, JavaScript/TypeScript and POSIX shell use bounded lexical scans. TOML and JSON manifests have byte and structure limits. These are static hints, not a runtime behavior or security assessment.

JSON is bounded and rejects duplicate keys and nonfinite values. Reserved JavaScript prototype keys are not retained as schema property names. The UI uses `textContent`, never imported HTML or source markup. Imported HTML is parsed as text and is never mounted into the page. JavaScript is scanned without an execution engine or subprocess.

Minimization trades fidelity for disclosure reduction. A normalized path, field name, count, type or status can still reveal information about an application. Review exports before sharing. The tool does not certify that an artifact or export is public or free of sensitive facts.

## Local server

- IPv4 loopback bind with exact Host and same-origin checks; no CORS permissions. Windows listeners reserve the port with `SO_EXCLUSIVEADDRUSE` so another reuse-enabled listener cannot share it.
- Per-process random request token, checked before processing POST bodies.
- Duplicate security/framing headers and transfer encoding rejected. Content-Length and JSON content type required.
- 3 MiB HTTP request/response caps, bounded incremental export serialization, a ten-second total request-line/header deadline, an eight-second body deadline, two concurrent analysis slots and at most sixteen connection workers.
- Fixed static assets, no path-based file access, no upload storage, no request-body logging and no source data in error responses.
- Content Security Policy disallows inline scripts, remote scripts, framing and form submission. Browser camera, microphone and location permissions are disabled.
- HTTP responses omit the Python and base-server version banner.

The server retains no files or reports. Selected files and a chosen baseline remain in browser memory until cleared or the page closes. Operating-system swap, a compromised browser, extensions, a debugger, or another process under the same account are outside this boundary. This is not an operating-system sandbox. Do not expose the port through a public tunnel or change its bind address.

## Supported input boundary

Use artifacts you own or are authorized to inspect. Capture authorization and the right to share derived information are installation responsibilities. Archives and application binaries are unsupported; no extraction, decompilation or native process attachment occurs.

The source CLI reads only explicitly selected ordinary UTF-8 files. It checks ancestors before descendants to reject symbolic links and Windows reparse points. On Windows it rejects UNC/device paths and alternate streams before filesystem access, checks the resulting absolute path again, and rejects remote or unknown drive types with the system drive-type API. It never walks a directory or follows requirements includes or package URLs. Other mounted filesystems and concurrent path changes by another local process are outside this boundary. HTTP source inspection accepts content, not filesystem paths or remote URLs. A rejected source contributes no partial evidence, and rejected selections cannot be compared as though their dependencies were absent. Cooperative work limits reduce accidental resource use; this is not a hard process-isolation boundary.

Use a private repository security advisory for a vulnerability report. Do not include real credentials or private captures in a public issue.
