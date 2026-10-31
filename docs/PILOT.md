# Free real-user pilot

The first product hypothesis is a useful developer handoff from unfamiliar web
evidence. Test this on actual work before adding integrations or a hosted service.
No participants have been recruited or contacted by this change. Audience fit
below is a hypothesis, not demonstrated demand.

## People to approach first

| Priority | Audience | Real job to test | Possible venue |
| --- | --- | --- | --- |
| 1 | Developers and small agencies inheriting a client web app | Document one unfamiliar form/request flow for a teammate | Personal professional contacts and a practical project post on [DEV #showdev](https://dev.to/t/showdev/) |
| 2 | Existing PostHog commenters building with coding agents | Check three claims, identify one unknown, and try the agent handoff | Reply personally in the existing Surface Map thread on [PostHog Questions](https://posthog.com/questions) |
| 3 | QA engineers using saved browser captures | Compare two corresponding captures and explain a change plus its coverage limits | [Playwright community](https://playwright.dev/community/welcome); check the Discord channel's current rules before recruiting |
| 4 | Open-source maintainers and technical founders | Produce a reviewable brief for one owned app flow | [Show HN](https://news.ycombinator.com/showhn.html), after the working trial is easy to run |

DEV's tag guidelines allow projects and product launches and ask for
community-focused posts. Show HN asks for something people can actually try,
ideally without signup; a landing page alone is unsuitable. Do not ask for votes
or comments. Playwright links its Discord for community participation; this is
not blanket permission to promote in any channel. Do not use another project's
bug tracker for recruitment.

PostHog's [answering guide](https://posthog.com/handbook/community/questions)
asks people to research with AI and write replies themselves. Use the factual
outline below to write your own response; it is not a paste-ready forum reply.
Check venue rules again when posting. Sources were checked on 2026-10-31.

## A factual outline for the existing discussion

- Brittany asked for broader, more diverse evidence. The existing 30/30 result is
  a development/regression result on examined examples, not an unseen accuracy
  estimate or evidence that the tool improves real work.
- Rik asked about PR CI. The draft PR supplies an offline capture comparison
  Action; users must provide comparable captures. It does not establish complete
  runtime API compatibility.
- The free local trial now includes a file-free synthetic agent-brief example,
  simple/developer/Gen Z introductions, and Markdown handoffs. It uses no model
  API calls.
- Ask for one owned flow, one incorrect claim, one missed interaction, and whether
  it improved a handoff. Captures can stay private.
- Link [PR #1](https://github.com/Jgolding1998/surface-map/pull/1) and
  [the tested branch's trial guide](https://github.com/Jgolding1998/surface-map/blob/feat/pr-evidence-evaluation/docs/TRY_IT.md).

## Developer/agency invitation draft

> I'm building Surface Map, a free local tool that turns saved HTML, HAR and
> JavaScript into an evidence-backed route map and developer brief. I'm looking
> for people documenting or taking over a web app they own or may inspect. After
> setup, could you try one flow and tell me what it got wrong, missed, or made
> easier? Captures can stay local; corrections or aggregate feedback are enough.
> It doesn't reproduce the app or verify its security. The trial instructions are
> here: https://github.com/Jgolding1998/surface-map/blob/feat/pr-evidence-evaluation/docs/TRY_IT.md

This draft has not been sent. Adapt it to your own experience and the venue.

## Run the pilot without buying analytics

Start with 8-12 participants as a discovery pilot, not a statistically sufficient
sample for broad claims. Record setup friction separately from task performance.
Use private notes or the public issue form with reviewed, non-sensitive feedback.

Give each person a concrete task, such as preparing a handoff for one order form.
Compare their normal developer/agent workflow with a Surface Map assisted
workflow, supplying equivalent captured material. Use matched tasks, alternate
the order across participants, and record the same model/settings where relevant.
Do not rerun an expensive agent just for this pilot unless the participant chooses
to use their own existing account. Manual teammate review is sufficient to start.

Have the app owner review the results without the workflow label when practical.
Check against actual source/observed behavior rather than how convincing the
explanation sounds. Track:

- Time to start, setup failures and help needed.
- Task completion and time to a usable handoff.
- Unsupported claims, wrong methods, critical missed interactions and corrections.
- Whether source evidence is correct and uncertainty is preserved.
- Receiving developer/agent rework and acceptance of the implementation plan.
- Voluntary reuse on a different project and the reason for reuse or rejection.

Keep participant, unique project and flow counts separate. Three runs of one
input are a stability check, not three independent samples. Report inconclusive
or incomplete captures separately; do not erase them from the denominator.

## Improve from failures without contaminating the final check

Include static sites, server-rendered forms, JavaScript-heavy apps, logged-in
flows, unfamiliar HTTP clients, third-party calls and incomplete captures.
Freeze expected evidence with the owner or an independent reviewer before scoring.
Keep whole projects aside from development; perform final evaluation on those
untouched projects only after the implementation and scoring rules are fixed.

Only add reviewed, shareable captures to the public corpus with permission.
Otherwise keep a private fixture or create a synthetic reproduction. The existing
[corpus runner](../evals/holdout/README.md) and
[promptfoo checks](../evals/promptfoo/README.md) can automate regressions. They do
not replace owner review or demonstrate that people benefit from the product.

Do not call the pilot a success from a few positive ratings. The useful next
signal is that people finish their task with fewer corrections and choose to use
the tool again. Predefine expansion criteria before viewing results, and publish
the failures along with the successes.

## Integration order

Keep the first trial local and dependency-light. The existing JSON/OpenAPI,
MCP, CLI, promptfoo and GitHub Action interfaces already support practical
workflows. Add a tool when a pilot task demonstrates a gap:

- [Lighthouse](https://developer.chrome.com/docs/lighthouse/overview/) for measured
  performance/accessibility audits; it is separate from route extraction.
- [ZAP](https://www.zaproxy.org/docs/desktop/start/features/scanpolicy/) for scoped,
  authorized security testing with independently reviewed findings. A route alone
  is not a vulnerability, and scanners cannot establish that an app is secure.
- [PostHog](https://posthog.com/docs/surveys) if a hosted trial later needs behavior
  analytics or surveys. No account, SDK, telemetry or hosted service was added here.

Do not spend on hosting or model APIs to validate this initial handoff workflow.
