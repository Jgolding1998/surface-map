# 640 capture checks

This suite crosses authored scenarios into **640 unique synthetic inputs**,
with 40 cases in each of 16 families. The combinations vary behavior such as
HTTP method, response status, URL resolution, evidence source, hidden context,
and JavaScript binding. They are not 640 independent websites or a hidden
holdout. The developers know the implementation.

The [generator](build.py) uses the Python standard library to construct inputs
and expected facts. It never imports or invokes Surface Map to create labels.
The JSON corpus and its SHA-256 digest were frozen before the first analyzer
run. This ordering is self-reported. The original [64-case suite](../diverse/)
and its recorded Claude comparison remain separate and unchanged.

## Run

From the repository root:

```sh
python -m evals.stress640 --engine enhanced --repeats 3 --output stress-results.json --check
python -m evals.stress640 --engine basic --repeats 3 --output basic-results.json
python -m unittest discover -s tests -p test_stress640.py -v
```

Enhanced mode needs the optional JavaScript parser described in the main
README. An unavailable parser is an error, never a silent fallback. The suite
does not fetch URLs, execute captured JavaScript, submit forms, or change an
agent's configuration. `--export-inputs inputs.json` exports synthetic inputs
without their expected answers, family names, axes, or descriptive case IDs.
Exported IDs are `case_0001` through `case_0640` in the original order. This
removes explicit label hints; the public corpus remains developer-known.

The result includes each case's prediction and failing rubric fields, counts
by family, known-operation precision/recall, separately labeled unresolved-target
precision/recall, and stability across repetitions. The unresolved metric checks
normalized paths in analysis cases, including missing targets on an unexpected
rejection; full case scores also require correct evidence. Empty denominators
are reported as `null`, not perfect scores.
Each family also reports `distinct_expected_projections`, excluding warning
text and privacy canaries, so many inputs sharing one expected outcome remain
visible. Counts distinguish accepted cases with known routes, unresolved
targets, no targets, and expected rejections. The known/unresolved counts can
overlap when a case contains both. Distinct projections are not a measure of
independent scenarios or complete semantic diversity.
Basic mode deliberately supports fewer JavaScript constructs. Its failures
against the enhanced rubric are retained rather than filtered out.

## Coverage

| Family | Axes |
| --- | --- |
| HTML navigation | Eight URL forms × five evidence contexts |
| Document bases | Five base-selection cases × eight references/actions |
| Form controls | Five method rules × eight control combinations |
| Text containers | Ten raw/RCDATA containers × four misleading payloads |
| Foreign content | Two namespaces × ten children × two recovery paths |
| HAR statuses | Five methods × eight valid/invalid response statuses |
| HAR body shapes | Five methods × eight body structures |
| Agent page signals | Five visibility/accessibility contexts × eight phrases |
| JavaScript clients | Five methods × eight request expressions/clients |
| JavaScript resolution | Eight URL forms × five binding expressions |
| JavaScript uncertainty | Five target forms × eight dynamic/shadowed contexts |
| TypeScript syntax | Five methods × eight annotation/assertion forms |
| Mixed evidence | Five target forms × eight source combinations |
| Capture comparison | Five methods × eight changes or stable transformations |
| Privacy channels | Five synthetic canary forms × eight data channels |
| Invalid inputs | Five value types × eight invalid input locations |

The invalid string-method case uses duplicate JSON keys in the request envelope.
An unsupported method string alone is omitted with a warning, so it would not
be a valid rejection expectation. The frozen case's `axes` retain the original
method/string grouping; the input itself records this exception explicitly.

Every accepted analysis case requires the complete normalized known-route
and unresolved-target sets, confidence labels, source IDs, locations, and
evidence kinds. Unexpected routes are errors. Selected cases also require
query names, top-level request/response field names, response statuses,
warnings, page-signal projections, and absence of synthetic private values.
The HAR body checks compare selected field names, not every nested schema
type. Comparisons require complete added, absent, and changed operation sets.
Rejected cases distinguish intentional input rejection from an internal error.

Labels follow Surface Map's declared support and minimization policies, not
complete browser behavior. For example, SVG/MathML contents are deliberately
omitted with a coverage warning; those cases test that boundary and recovery
into HTML. A HAR request with status `0` still counts as an observed request,
while contributing no supported response status. That does not establish
that a server responded or the operation succeeded.

Source-origin relations, capture deadlines, network protections, resource
caps, and new report fields need their dedicated unit/integration tests. This
suite does not replace them. It does not score runtime endpoint behavior,
model tool selection, prose quality, false-negative prompt-injection detection,
or performance on real public/private sites.

## Recorded result

At the initial run, enhanced mode passed **640/640** and basic mode passed
**498/640**. Both produced stable scored projections across three repetitions.
No expected labels changed after that run. This matrix found no new enhanced
parser failures; it expands regression coverage of established behavior.
The difference is between two Surface Map parser modes, not a comparison
against Claude, Codex, or a competing tool.

Version 1.4 retains the original corpus and improves basic mode to **538/640**,
with no previously passing case lost. Enhanced mode remains **640/640**. Basic
mode retains more targets with uncertain methods; it labels ten targets as
unresolved that enhanced mode resolves, so this is not an improvement on every
precision metric. The [separate 20-case model record](agent_comparison/README.md)
and [HTML library comparison](html_comparison/README.md) document their own
rules, results and limitations.

Repeated calls are not independent examples. These correlated, generated
cases do not justify population confidence intervals or a general claim of
100% accuracy. Recorded timings include projection overhead and possible
worker startup, so they are diagnostics rather than a speed benchmark.

To verify the corpus can be rebuilt exactly, run its unit test. Rebuilding
with `python -m evals.stress640.build` overwrites the local corpus and digest;
review any changes before committing them. Changes to labels or cases must
receive a new digest and be described, rather than silently rewriting a
comparison's history.
