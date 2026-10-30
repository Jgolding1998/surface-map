# An agent using Surface Map

These runs record the initial MCP integration. The later [64-task evaluation and
45-task Claude comparison](../evals/diverse/README.md) provide broader synthetic
checks, with saved inputs, predictions and scoring code.

This is a small reproducible example of the MCP interface. On October 30, 2026,
Claude Code started the local server and called three tools: `limits`, `demo`,
and `analyze_capture`. All three returned successfully. The session used only
Surface Map's MCP tools, with no browser, shell, or other agent tools enabled.

## Try it

Configure the server using [the setup instructions](MCP.md), then ask:

> Use Surface Map to analyze this saved HTML. Explain what the page declares
> and what you would need to verify separately.
>
> `<html><body><a href="/dashboard">Dashboard</a><form action="/api/orders" method="post"><input name="order_id"><button>Save order</button></form></body></html>`

The tested tool call was:

```json
{
  "artifacts": [
    {
      "kind": "html",
      "text": "<html><body><a href=\"/dashboard\">Dashboard</a><form action=\"/api/orders\" method=\"post\"><input name=\"order_id\"><button>Save order</button></form></body></html>"
    }
  ],
  "javascript_engine": "basic"
}
```

Claude identified two declared routes from `source_1`:

| Route | Evidence |
| --- | --- |
| `POST /api/orders` | HTML form with an `order_id` field |
| `GET /dashboard` | HTML link |

It correctly explained that neither route had been requested or verified.
HTML alone does not establish authentication, accepted payloads, or server
responses. In a separate call to the bundled `demo`, it also distinguished
observed HAR traffic from declared HTML and inferred JavaScript routes.

This example uses synthetic content. It demonstrates an actual agent-to-tool
round trip, not production adoption or a measured accuracy score. A different
agent may phrase its explanation differently.

## A saved public page

A second Claude session called `analyze_capture` with two unchanged selections
from a saved public PostHog Product Engineer application: the HTML form and
four lines from its public `Apply.tsx` component. Enhanced analysis used
Babel 8.0.4 through Node 24.22.1.

The tool returned one inferred `POST /api/{segment_2}` route from the TypeScript.
The actual source path is `/api/apply`; Surface Map aliases unknown path names
in its minimized output. The form had no `action`, and the capture supplied no
base URL, so the tool omitted it and returned a warning. Claude preserved that
warning and correctly explained that the result did not establish a request
schema, connect the form fields to the inferred route, or verify the endpoint.

No application was submitted and no endpoint was called. This checks a real
saved-capture workflow and its limitations, not the current live application.
Source locations refer to the selected fragments; line 1 of the TypeScript
fragment was line 271 in that saved full component.

Sources: [public role page](https://posthog.com/careers/product-engineer) and
[public website repository](https://github.com/PostHog/posthog.com).

## Verification

The Windows run with public URL support executed 309 tests: 307 passed and two
POSIX-only checks were skipped. All 16 MCP integration tests ran, including enhanced JavaScript analysis,
bounded messages, minimized errors, launcher startup, and EOF cleanup. The
Windows process-cleanup regression also passed 20 consecutive focused runs.
Claude reviewed the implementation; its launcher finding was fixed before the
final run. CI includes Windows and Linux MCP jobs, but those remote runs are
separate from these local results.

## Use it on your own work

For a public HTTPS page, ask your agent to call `analyze_url` with the URL and
explain the evidence and omissions. For browser runtime behavior or authorized
logged-in pages, supply selected saved HTML, HAR, JavaScript, or TypeScript.
The agent explains the evidence it gets back. Discovered API operations are
never called by Surface Map.

For a before-and-after review, use `compare_captures`. For selected source files
or dependency manifests, use `inspect_sources`. Keep captures small and review
warnings. Raw captures reach the agent before Surface Map minimizes its output,
so do not provide data that the agent is not authorized to receive.

## Live public URL checks

Codex exercised the real MCP protocol on five public sites. Claude Code also
called `analyze_url` on the first three sites using the installed stdio server.
Only this server's tools were enabled in Claude's test session.

| Site | Result on the test machine |
| --- | --- |
| Example.com | Verified TLS connection failed; no analysis returned |
| Python JSON documentation | HTML and five scripts analyzed; six other external scripts and two inline scripts omitted |
| PostHog Product Engineer | HTML and four scripts analyzed; one selected script failed capture, with further scripts and inline code omitted |
| RFC 9110 specification | Exceeded the HTML structural limit; explicit error |
| Wikipedia landing page | Server returned an unsupported HTTP status; explicit error |

These checks ran on October 30, 2026. Live pages and script counts change.
Claude initially sent concurrent requests and received busy responses for two;
sequential retries succeeded. Neither client submitted forms or replayed API
operations. The successful pages yielded declared HTML routes; their selected
scripts added no inferred routes. This is limited coverage, not a full API map.

For comparison, Windows curl 8.21 with Schannel retrieved all five documents.
Curl did not analyze them or retrieve their scripts. Python's ordinary urllib
also failed certificate verification for Example.com on this machine. The
comparison does not establish a universal fetching or speed advantage.

A separate six-case synthetic extraction check scored a simple literal matcher
2/6, Surface Map basic 3/6, Surface Map enhanced 6/6, and one blind Claude run
without Surface Map 6/6. This small fixture set is illustrative, not a general
accuracy benchmark. It shows no accuracy advantage over Claude on those cases.
Surface Map's useful distinction is consistent, locally computed evidence and
comparison output that an agent can cite, with limits made explicit.
