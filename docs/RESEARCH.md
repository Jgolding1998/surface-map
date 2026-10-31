# Surface Map: alternatives, improvements, and adoption

Research date: **2026-10-31**. Baseline: Surface Map `fcc81b6`.
This is a review of public source, documentation, and repository metadata,
plus direct checks of Surface Map. It is **not a head-to-head accuracy or speed
benchmark**, and stars are not a count of active users.

## Which tool is better for which job?

| Tool | Documented strengths | When to choose it | Surface Map's different focus |
| --- | --- | --- | --- |
| [jsluice](https://github.com/BishopFox/jsluice) | Syntax-tree extraction of JavaScript URLs, request details, and secrets; custom matchers | Raw JavaScript discovery and extensible extraction | Minimized outputs, mixed HTML/HAR/JS evidence, agent handoffs, and before/after review |
| [Katana](https://github.com/projectdiscovery/katana/tree/v1.8.0) | Standard/headless web crawling, JavaScript discovery, form and runtime exploration | Discovering a larger live application surface | Bounded selected-source review without running PR code or submitting discovered forms |
| [mitmproxy2swagger](https://github.com/alufers/mitmproxy2swagger) | HAR/mitmproxy captures to OpenAPI, with manually curated path templates | Producing an API specification from observed traffic | Automatic conservative normalization, unresolved-method handling, and observed/declared/inferred labels |
| [LinkFinder](https://github.com/GerbenJavado/LinkFinder) | Regex-based JavaScript endpoint discovery with CLI/HTML output | A simple focused endpoint-extraction workflow | Scope-aware supported calls, privacy projection, and comparable evidence reports |
| [lychee](https://github.com/lycheeverse/lychee/tree/lychee-v0.24.2) | Actual link checking across documents/websites, with CI integration | Checking whether links respond or break | Detecting source navigation changes without claiming that a live destination works |

Katana covers substantially more live crawling and browser behavior than Surface
Map. lychee answers a live link-health question that Surface Map does not.
jsluice is a closer static JavaScript alternative, and mitmproxy2swagger is a
closer HAR-to-OpenAPI alternative. Calling Surface Map the best general scanner,
crawler, link checker, or API generator would not be supported by this research.

Surface Map's useful combination is **local evidence review + minimized exports +
explicit unknowns + agent handoffs + static PR comparisons**. That combination
can suit a developer handing over an unfamiliar flow or reviewing a frontend PR.
Whether it saves them work still needs real-user testing.

Raw URLs, source, credentials, and capture values can exist in other tools'
outputs or inputs. Check each tool's own options and policies before sharing
artifacts. mitmproxy2swagger specifically warns that opt-in examples/headers
can include sensitive information. Surface Map's minimization is also lossy and
is not a guarantee that every sensitive fact is absent.

## What the comparison exposed

These are concrete improvements or gaps, not a promise to reproduce every feature
of the other projects:

| Finding | Decision |
| --- | --- |
| README's first command assumed a downloaded checkout and the right working directory | Put clone/download, folder selection, and startup before the technical reference |
| Busy-port errors hid the recovery command | Return a fixed actionable error without exposing exception details |
| Startup described all modes as offline | State that saved files are offline and public HTTPS retrieval is explicitly requested |
| More than six changed supported files produced a vague CI error | Keep the bound and failure exit; explain how to select up to six exact paths using counts only |
| JavaScript location changes and window.open were not PR navigation hints | Add bounded literal navigation hints with scope/mutation guards, private IDs, and independent Git regressions |
| Pilot text still described capture preparation as necessary for Rik's CI request | Point to the merged automatic Git workflow and its current scope |
| Browser development instructions used an unpinned install | Use the existing locked development driver and browser-check runner |

Implementation and validation are recorded in the corresponding PR. Source
support is deliberately narrower than complete browser behavior: dynamic
destinations, framework routing, aliases and runtime effects remain possible gaps.

### Open work worth considering

- **Native JSX form/control evidence.** Accepting JSX syntax does not mean its
  elements are interpreted as HTML. Static JSX and framework components need
  ownership, identity, expression and spread handling before they can safely feed
  the existing form comparison.
- **Installation packaging.** The current supported setup is a source checkout.
  A pip/pipx/uvx install would need packaging of the optional parser and its pinned
  integrity files, not just a console-script entry.
- **PR report readability.** A compact change-count overview can help reviewers
  before they inspect detailed evidence and opaque identities.
- **Shared evaluation.** Run reviewed, pinned tools against a fixed corpus with
  independently checked labels before claiming an extraction or speed advantage.
  Keep privacy, crawling, runtime evidence, and static extraction as separate axes.

Adding every adjacent feature would make setup and trust harder. Prioritize
problems observed in completed user trials rather than a growing feature list.

## What to learn from Handy

The Handy project is [cjpais/Handy](https://github.com/cjpais/Handy), an offline
speech-to-text desktop app. Live GitHub metadata showed **35,861 stars** and
**3,408 forks** on the research date. Those figures change.

Its [README](https://github.com/cjpais/Handy/blob/main/README.md) leads with a clear
benefit, a short reason to use it, a simple workflow, and installation.
Its [v0.9.8 release](https://github.com/cjpais/Handy/releases/tag/v0.9.8)
provides desktop downloads, and [BUILD.md](https://github.com/cjpais/Handy/blob/main/BUILD.md)
separates developer setup. Its [website](https://handy.computer) presents the demo
and download path more visually. The README also has explicit known limitations.

The redesign applies that hierarchy to Surface Map: one primary local trial,
secondary MCP/PR paths, original artwork, genuine workflow badges, a real
workspace screenshot, and a separate technical reference. It does not imply
desktop installers, completed customer trials, sponsors, or benchmark wins.

Handy's code is MIT licensed, but its README explicitly reserves the name, logo,
icon, and brand assets. Surface Map uses original artwork and its own palette.

## How repositories become popular

The observations above support a practical hypothesis, not a proven explanation
of Handy's star history:

1. **Solve a recurring problem people can describe.** Handy's hotkey-to-dictation
   workflow serves a broad daily task. Surface Map currently serves a narrower
   developer workflow; clarity about that audience matters.
2. **Make the first useful result easy to reach.** Downloads, a short trial,
   accurate prerequisites, and useful errors reduce friction.
3. **Show the result.** A working demo and honest examples explain the payoff
   better than a long feature list.
4. **Earn repeat use.** Fix real failures, make limitations visible, and let people
   report small reproductions without uploading private data.
5. **Help the right people find it.** GitHub supports discovery through search and
   topics; useful documentation and community files help people navigate and
   contribute. [GitHub guidance](https://docs.github.com/en/repositories/creating-and-managing-repositories/best-practices-for-repositories),
   [project discovery](https://docs.github.com/en/get-started/exploring-projects-on-github/discovering-projects-on-github).

No README design guarantees popularity. Stars can reflect curiosity, bookmarking,
a launch, or existing distribution rather than successful repeat use. Buying
stars or asking unrelated maintainers for promotion would not validate the tool.

A useful next test is five developers or maintainers with an actual handoff or PR:
can each get a first result without help, identify one useful finding, report one
mistake or omission, and choose to use it again? Record setup time separately.
The [pilot guide](PILOT.md) provides the task and privacy-preserving feedback path.
No outreach or community posts were made as part of this research.

## Source and license notes

Documentation inspected on the research date; stable references used where
available. No competitor code or brand assets were copied, installed, or run.

- jsluice: [README](https://github.com/BishopFox/jsluice/blob/0ddfab153e060a9eeaded4d8669233f7c071e7e4/README.mkd), [CLI](https://github.com/BishopFox/jsluice/blob/0ddfab153e060a9eeaded4d8669233f7c071e7e4/cmd/jsluice/README.mkd), MIT.
- Katana: [v1.8.0 README](https://github.com/projectdiscovery/katana/blob/v1.8.0/README.md), MIT.
- mitmproxy2swagger: [README](https://github.com/alufers/mitmproxy2swagger), MIT.
- LinkFinder: [README](https://github.com/GerbenJavado/LinkFinder/blob/1debac5dace4724fd6187c06f133578dae51c86f/README.md), MIT.
- lychee: [v0.24.2 README/license](https://github.com/lycheeverse/lychee/blob/lychee-v0.24.2/README.md#license), MIT or Apache-2.0 at the user's option.
- Handy: [README and brand notice](https://github.com/cjpais/Handy/blob/main/README.md), [contributing](https://github.com/cjpais/Handy/blob/main/CONTRIBUTING.md), [live repository metadata](https://api.github.com/repos/cjpais/Handy).
