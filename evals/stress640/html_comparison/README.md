# Offline HTML comparison

**Accuracy tie: both matched the declared extraction policy on 200/200 cases.**

This compares a small extraction adapter we wrote using Beautiful Soup 4.15.0 and
html5lib 1.1 with Surface Map's HTML parser. It is a library-based baseline,
not an independent product benchmark or a comparison with an AI model.

| Shared task | Surface Map | Library + adapter |
|---|---:|---:|
| Exact operation and field sets | 200/200 | 200/200 |
| Operation precision / recall | 100% / 100% | 100% / 100% |
| Field precision / recall | 100% / 100% | 100% / 100% |
| Stable over three repeats | 200/200 | 200/200 |

Each tool found the 196 expected operation facts and 78 expected field facts,
with no extra or missing facts. Always returning nothing would match 12/200
cases, which intentionally contain no supported operations.

The cases are all 40 combinations from each of five frozen stress640 families:
HTML navigation, document bases, form controls, text containers, and foreign
content. They are synthetic developer-authored fixtures, not 200 independent
websites or a hidden holdout. The original labels were not changed.

## Reproduce

From the repository root, activate a disposable Python 3.12 virtual environment.
Install the optional dependencies explicitly, then run the offline comparison:

```sh
python -m pip install --require-hashes -r evals/stress640/html_comparison/requirements.txt
python -m evals.stress640.html_comparison.run --check --output html-comparison-results.json
```

The requirements pin official PyPI wheels and their hashes. The runner contains
no download or installation code. It reads the saved synthetic corpus, parses
strings, and never requests their URLs or executes their scripts. Network,
browser, and child-process calls are guarded during extraction. These guards
are defensive checks, not an operating-system sandbox.

[results.json](results.json) records aggregate results, per-family counts, any
failures, package versions, source hashes, and local elapsed times. It contains
no captured website content or machine paths. [spec.json](spec.json) is the
unchanged specification saved before the first scoring run; the runner checks
its digest and the frozen corpus digest.

## What is shared

Both sides extract declared ordinary HTML anchor destinations and form actions,
methods, and field names. The adapter uses html5lib's scripting-enabled parsing
state for `noscript`, without running JavaScript. It excludes templates, raw
text, and foreign-namespace elements under the stated policy. This is source
structure, not computed CSS visibility or proof of a successful submission.

Both sides use Surface Map's `safe_path` and `safe_field` normalization. That
normalization is therefore not independently tested here, and it can merge
distinct original URLs. The library adapter receives only HTML and the document
URL; it does not receive expected labels.

Evidence locations, confidence, warnings, JavaScript analysis, HAR, server
behavior, and security findings are outside the shared task. Library behavior
and our adapter policy must be distinguished when interpreting a mismatch.

## Timing limits

The recorded local medians were about 0.069 ms for Surface Map and 0.283 ms for
the library adapter. Those are tiny fixture timings, not representative website
latency or a general speed ranking. Imports are excluded; tool order alternates;
Surface Map still constructs its fuller report. Reruns will vary.

This comparison supports an accuracy tie on the declared task. Broader claims
need independent captures, reviewed labels, and a separate model-only baseline.

References: [Beautiful Soup documentation](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)
and [html5lib documentation](https://html5lib.readthedocs.io/en/latest/).
