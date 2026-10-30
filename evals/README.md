# Static route regression check

For HTML/HAR evidence, comparisons and page signals, see the [64-task suite](diverse/README.md).
It also contains a recorded, reproducible comparison with Claude on 45 route/evidence tasks.

This is a curated, versioned set of 16 synthetic cases for known static client patterns and negative controls. It is a regression test, not an unseen benchmark, a peer comparison, or evidence that a request is valid or reachable. Fixture source is passed as text to the public analyzer and never executed. No HAR or observed-network evidence is mixed into this task.

From the repository root:

```sh
python -m evals.run --mode basic --trials 1
python -m evals.run --mode enhanced --trials 3 --check
```

Enhanced mode requires the documented Node/parser setup and never falls back to basic. The runner reads only its fixed local fixtures. JSON results go to stdout, with a concise Markdown result table on stderr. Exit 0 means the run completed; `--check` additionally requires every exact case and repeated prediction to pass. Analysis or fixture errors always exit 2; an accuracy or stability failure with `--check` exits 1.

| Projection | Meaning |
| --- | --- |
| Known operations | Exact `(method, normalized path)` from `routes` |
| Unresolved targets | Exact `(null, normalized path)` from `unresolved_operations` |
| Targets ignoring method | URI evidence only; a guessed method cannot improve operation accuracy |
| Exact cases | Both operation sets match, including empty negative controls; errors fail the case |

Every projection reports TP/FP/FN, precision, recall and F1. Undefined precision or recall is JSON `null`; F1 is 1 when both sets are empty. Unexpected paths remain false positives. Per-case errors retain only allowlisted parser error codes, or `analysis_failed`; exception text is never included. Expected paths use only the analyzer's generic allowlisted `/api/items`, `/api/users` and `/api/search` vocabulary. Hashes include the annotation file and all fixture sources, normalizing only CRLF to LF for checkout portability. Change the corpus version and reviewed hashes when changing its truth.

Accuracy uses the first 16-case pass only. Each trial closes parser workers, measures one complete cold pass, then immediately measures a second warm pass with every source analyzed again. The two 16-case passes stay below the current 128-request worker retirement limit. JSON gives milliseconds per complete pass, all measured values and sample counts. Python imports, fixture loading and worker closing are excluded, so cold means parser-worker startup rather than a fresh Python process. In a fresh enhanced CLI process, the first cold pass also includes the Node version probe. Worker closing preserves the version cache, so later cold passes reuse it; the evaluator does not reset that private cache. Basic mode has no worker startup; its labels mean initial and repeated passes only. Slowdowns or worker errors can affect timing; there is no memory or peer-speed claim.
