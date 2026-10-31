# Contributing to Surface Map

Thanks for trying it. A small reproducible failure is useful even if you do not
want to write code.

## Report a problem

Use the [trial feedback form](https://github.com/Jgolding1998/surface-map/issues/new?template=real-world-trial.yml).
Describe the job, expected behavior, actual behavior, tested commit, operating
system, Python version, and selected JavaScript mode.

Use synthetic HTML or JavaScript when possible. Do not attach credentials,
private URLs, client names, source captures, or raw HARs to public issues.
For a suspected vulnerability, follow [SECURITY.md](SECURITY.md).

## Make a change

1. Fork the repository and create a branch for one focused problem.
2. Follow [local setup](README.md#run). Syntax checks require the optional pinned
   parser; the basic local workspace has no third-party Python dependency.
3. Add a meaningful regression for a behavior change. Include a quiet or
   unsupported control when changing extraction, and a private-value canary when
   changing output. Keep existing saved-capture formats compatible.
4. Run the relevant tests, then the full suite when the change affects shared
   analysis. CI covers supported Python versions, Linux/Windows, MCP, browser
   workflows, evaluations, and secret scanning.
5. Open a PR explaining the problem, the resulting behavior, and what you checked.

Do not introduce paid services, automatic package installation during analysis,
source execution, or hidden network retrieval. Evidence labels, explicit
uncertainty, bounded work, and data minimization are part of the product.

For browser development, use the [pinned browser checks](docs/CI.md#browser-workflow-checks).
For evaluation changes, retain earlier failures and document which examples were
already examined. Passing fixtures do not establish unseen-site accuracy.

## Useful places to help

- Report a pattern the analyzer missed, with a small synthetic example.
- Try the [first-run guide](docs/TRY_IT.md) from a clean checkout and note friction.
- Improve explanations without turning inferred behavior into a fact.
- Review [alternative tools and open gaps](docs/RESEARCH.md).

There is no requirement to star the project, join a server, or share private data.
