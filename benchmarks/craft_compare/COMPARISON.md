# Actual offline HAR comparison

Source-informed capability and boundary comparison of a pinned offline Craft HAR CLI against Surface 1.6.3 and the upgraded checkout. Not an independent broad accuracy benchmark.

Craft is the verified released Windows x64 CLI v0.1.0 (`web-recording-show`), commit `c316051c9e01e1bf9863f3c802a805179afa4663`. Surface baseline is v1.6.3, commit `2a516820cf1836d7025df90b81df1b1cdfbaea23`. Surface was invoked through its analyzer library (also used by its CLI/MCP), not through MCP transport. Every tool received the same frozen fixture bytes. `manifest.json`, its SHA-256, source hashes, binary provenance, source audit, raw outputs and per-process metadata are retained alongside this report.

The upgraded version 1.7.0 HAR analyzer is frozen by SHA-256 `3e9edd02e0e41061ac66b978e4497c53d69b10d05d58608aaf234dd456464860`. Its complete Python module map is recorded in `source-pins.json` and enforced by the portable runner before analyzer execution. Fixture expectations were not changed after implementation or execution. The projection reads each tool's actual schema: Craft outlines and entry-level form-name lists; Surface JSON shapes, aliased request-field occurrence records, and per-source body-coverage records.

| Predeclared fixture | Surface 1.6.3 | Upgraded Surface | Craft released CLI |
|---|---|---|---|
| 01_json_control | 2 routes; request 4, response 2 JSON fields | 2 routes; request 4, response 2 JSON fields | 2 routes; request 4, response 2 JSON fields |
| 02_base64_json | 0 response fields | 2 response fields | 2 response fields |
| 03_invalid_base64 | 0 response fields | 0 response fields; invalid_base64 | 0 response fields |
| 04_non_utf8_base64 | 0 response fields | 0 response fields; invalid_utf8 | 0 response fields |
| 05_decoded_body_over_64k | 0 response fields | 0 response fields; body_too_large | 1 response fields |
| 06_urlencoded_text | 0 field occurrences / 0 names | 5 field occurrences / 4 names | 5 field occurrences / 4 names |
| 07_urlencoded_params | 0 field occurrences / 0 names | 3 field occurrences / 2 names | 3 field occurrences / 2 names |
| 08_multipart_params | 0 field occurrences / 0 names | 4 field occurrences / 3 names | 4 field occurrences / 3 names |
| 09_problem_json_mime | 2 response fields | 2 response fields | 2 response fields |
| 10_unknown_encoding | 0 response fields | 0 response fields; unsupported_encoding | 1 response fields |
| 11_plaintext_json_policy | 0 response fields | 0 response fields; unsupported_mime | 1 response fields |
| 12_malformed_json | 0 response fields | 0 response fields; invalid_json | 0 response fields |
| 13_privacy_defaults | 0 credential/body canaries; 0 policy-difference values retained | 0 credential/body canaries; 0 policy-difference values retained | 0 credential/body canaries; 3 policy-difference values retained |
| 14_path_templates | 4 routes | 4 routes | 4 routes |
| 15_malformed_entry | Rejected (exit 1) | Rejected (exit 1) | Accepted 1 operation; statuses [0] |
| 16_entry_cap_301 | Rejected (exit 1) | Rejected (exit 1) | Accepted 301 entries |
| 17_timings_and_headers | 1 route; timings outside scope | 1 route; timings outside scope | 1 route; 2 timing records |
| 18_empty_capture | 0 routes | 0 routes | 0 routes |

JSON-field counts are top-level retained property counts. Form counts show occurrences and unique names; serialization, aliases and grouped paths differ by design. These counts do not measure exact application reconstruction or general website accuracy.

The malformed-entry case is deliberately invalid HAR. Craft accepts it as an observed operation with an empty method and status 0; Surface rejects it. The 301-entry case deliberately crosses Surface's documented 300-entry cap; Craft's larger supported input is a scope difference. Craft also retains historical timing information that Surface does not inspect.

The default privacy case distinguishes shared promises from different minimization policies. Credential values, URL user-info and JSON body values must be absent in both. Craft intentionally retains noncredential query/header values and private field names unless declared through `hidden_values`; Surface drops or aliases them. No `hidden_values` declarations were supplied to Craft. A retained policy-difference canary is not labeled a violation of Craft's documented default behavior.

Craft body outlines expose kind/size but generally omit explicit reasons for invalid encodings or malformed JSON. Its unknown-encoding and text/plain cases parse JSON-looking plaintext. Surface's explicit MIME and decoding policies intentionally differ.

Only the audited offline handler was invoked, with fresh config/home/data folders and an environment allowlist without provider credentials. No MCP server, GUI, model, provider, device, setup or target website operation was invoked. This is environment isolation and a reviewed call path, not an OS/network sandbox or independent traffic capture.

## Observed process times

One process per fixture in the reported suite on this Windows host. An initial Craft control smoke invocation preceded that suite (755.141 ms, recorded separately). Times include process/interpreter startup; execution order and OS caching are uncontrolled. These are reproducibility observations, not a speed benchmark or general performance ranking.

| Tool | Runs | Median ms | Min–max ms |
|---|---:|---:|---:|
| surface-baseline | 18 | 119.612 | 105.417–172.996 |
| surface-upgraded | 18 | 280.919 | 230.879–317.712 |
| craft-cli | 18 | 28.196 | 25.33–837.395 |

No overall winner or accuracy percentage is inferred from these fixtures.
