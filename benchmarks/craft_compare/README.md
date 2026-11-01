# Reproduce the offline HAR comparison

Read [COMPARISON.md](COMPARISON.md) for the results and limits. All 18 cases use
synthetic data, including explicitly labeled privacy canaries. They were selected
from source-inspection findings before execution; this is a focused capability
and boundary comparison, not an independent blind accuracy benchmark.

The original fixture/expectation manifest is byte-for-byte unchanged. Its SHA-256
is in `manifest.sha256`. Raw synthetic stdout/stderr, exit codes, output hashes
and observed process times are under `outputs/`. Public source metadata omits
local absolute paths; raw analyzer outputs have not been altered.

The portable `run.py` was itself executed on all 18 fixtures with all three
tools in a fresh output directory. All **54 raw stdout results were byte-for-byte
identical** to the retained original results, with no timeouts. See
`portable-validation.json`. This verifies the reproducer on this Windows host;
it does not establish accuracy beyond these synthetic inputs.

The actual product invocation is Craft Anything v0.1.0's released **Windows x64
offline HAR CLI** (`web-recording-show`). Surface runs through the same `analyze`
library used by its CLI/MCP. This does not test MCP transport, full-product
reconstruction, Ghidra, browsers, devices, provider APIs or generated code.

## Inspect recorded results anywhere

Python 3.11+ and its standard library can re-project the retained outputs:

```sh
python summarize_comparison.py
```

This reads retained JSON and writes `COMPARISON.md` / `comparison-results.json`.
No executable, tool installation or network is involved.

## Run the tools again

A full run needs Windows x64, Python 3.11+, local Surface baseline/upgraded source
folders and the explicitly selected vendor CLI. Nothing is downloaded or
installed by `run.py`; it does not execute repository setup/build scripts.

1. Obtain the [Craft v0.1.0 portable Windows x64 release](https://github.com/storytold/craft-anything/releases/tag/v0.1.0).
   Verify its ZIP against the published SHA-256
   `2544a2c457e5bc22ab0b57dd757ece6bb35e622de35cffed2a3e0f216d832e9c`.
   The CLI executable is further pinned by the runner to
   `52a4249d3d9b6f321883b5048e774026856f5fe8e8afaca89984d02ffe8f0fd5`.
   The artifact is not included in this bundle. Published checksums confirm
   artifact bytes; this comparison did not reproduce the vendor's build.
2. Supply baseline Surface source from commit
   `2a516820cf1836d7025df90b81df1b1cdfbaea23` and the upgraded source whose HAR
   analyzer hashes appear in `execution-provenance.json`. The upgraded comparison
   used the final version 1.7.0 source freeze before its final commit existed.
3. Run from this bundle, selecting your local relative paths and a **new** output
   directory, for example:

```sh
python run.py --baseline-source ../../surface-v1.6.3 --upgraded-source ../.. --craft-cli ../../vendor/craft-anything-cli.exe --output rerun
python summarize_comparison.py --root rerun
```

The runner checks fixture hashes, executable bytes and the **complete Python
module map** in `source-pins.json` by default: all relative module paths and every
canonical file hash must match before any analyzer worker starts. Hashes normalize
CRLF to LF for cross-checkout portability; raw module hashes are recorded too.
`--allow-new-source` explicitly permits a different module map and records the
departure; that is a new comparison rather than exact reproduction. A changed-helper
negative control was rejected during preflight without executing altered source;
see `helper-pin-rejection.json`. Environment differences can still affect results.

Each process receives a fresh home/config/data/temp directory and an environment
allowlist without provider credentials. Plugin lists are explicitly empty, no
project is selected, stdin is empty and process time is bounded to 20 seconds.
The reviewed vendor handler reads only the synthetic HAR and performs in-memory
analysis. No model, provider, target website, MCP server, GUI or device operation
is selected. This is not an OS/network sandbox or independent traffic capture.

The output folder also contains local source snapshots and isolation folders;
these are execution inputs, not intended for publication. Review a rerun's raw
output before sharing it. The original authored harness/fixture-generation code
under `provenance/` records the historical local layout and is not the portable
entry point; use `run.py` above. No full source checkout or binary is bundled.

## Projection and fairness

The projection adapts the actual formats without changing fixture expectations:

- Craft exposes JSON outlines under `api_surface`, and form-name occurrences in
  entry-level `request_body.fields` arrays.
- Surface exposes JSON schemas under routes, aliased request-field records with
  `occurrences`, and per-source `har_body_analysis` body-omission records.
- Field/occurrence counts are compared without demanding raw private names.
  Surface intentionally aliases or discards more identifiers than Craft's default
  policy. Craft's noncredential query/header values are not mislabeled as a breach
  of its documented default behavior.
- Malformed-input rejection, the 300-vs-500,000-entry limits, MIME policy, body
  decoding policy and historical timing support are reported separately.
- Process timings include startup and uncontrolled cache/security-scanning effects.
  No overall winner, speed ranking or accuracy percentage is inferred.

## License and sources

The authored runner, fixture/projection code and synthetic fixtures use the
Surface repository's [MIT license](LICENSE). No Craft application source, Rust
module, executable, logo, font or UI asset is vendored. Upstream offers MIT or
Apache-2.0; its MIT text and original NOTICE are retained under `notices/` for
source/attribution provenance. The NOTICE mentions vendor-bundled assets that are
not included here. Exact reviewed upstream source paths/hashes, reference URLs,
published-checksum proof and the startup/handler audit are retained in the JSON
records. Any later substantial source copying must preserve the selected upstream
copyright and permission notice.
