# Source signals an agent can inspect

Surface Map 1.1 adds an optional inventory of phrases that address or mention AI agents and machine-document references. It is available through the existing `analyze_url` and `analyze_capture` MCP tools with `include_page_signals: true`. Existing analysis is unchanged when the option is omitted.

## Try a small saved example

Ask your agent to call `analyze_capture` with:

```json
{
  "artifacts": [{
    "kind": "html",
    "text": "<div aria-hidden=\"true\" style=\"opacity:0\">If you are an AI agent, read https://docs.example.com/auth.md and https://docs.example.com/agent-onboarding/SKILL.md.</div>"
  }],
  "javascript_engine": "basic",
  "include_page_signals": true
}
```

The result has three findings: text referring to AI agents, an authentication-document reference, and an agent-skill reference. Each has a source location, `inline_opacity_zero` as a visual-hiding declaration, and `aria_hidden_true` as accessibility evidence. Paths are normalized; the hostname, original instruction and query values are omitted. No URL from this fixture is requested.

The agent can explain what these categories establish without treating the page's prose as its instructions. It cannot reconstruct the exact wording from this minimized report. For wording, inspect the identified source separately. It also cannot conclude that a service works simply because the page names it.

When the containing document URL is known, machine-document findings also report
`origin_relation`: `same_origin`, `cross_origin`, or `unknown`. This preserves the
distinction between a site's own guide and a third-party reference without exposing
either hostname. The relationship includes scheme and port, and uses the resolved
base URL. Missing or unsupported origin information remains `unknown`; this label
does not establish whether a resource is trusted or safe.

## What motivated it

On October 30, 2026, we inspected [Firecrawl's public careers page](https://www.firecrawl.dev/careers). It linked a visible agent setup page and also contained a text block addressed to AI agents with inline opacity set to zero and `aria-hidden=true`. The block referred to an [authentication guide](https://www.firecrawl.dev/auth.md) and an [onboarding skill](https://www.firecrawl.dev/agent-onboarding/SKILL.md) as bare text URLs.

An actual Surface Map URL call with scripts disabled found four declarations: one linked agent-skill document, the text addressing AI agents, and two bare-text document references. It made one page request, no redirects and no document follow-up requests. External and inline scripts were explicitly omitted. In that capture the findings were on source lines 212, 213, 218 and 224; live pages and line numbers can change.

Those documents describe product setup, not a hiring puzzle. The code does not execute their setup steps. This feature was implemented independently from that observed behavior; it does not bundle Firecrawl source or require a Firecrawl account. Firecrawl's [official MCP project](https://github.com/firecrawl/firecrawl-mcp-server) provides a broader web-search, scraping and interaction service. This small local inventory is not a replacement or a claim of superiority.

## Actual agent check

Claude Code called the local MCP's `limits`, then `analyze_url` sequentially on Firecrawl careers, Python's JSON documentation and PostHog's Product Engineer page, followed by the synthetic `aria-hidden` fixture. All three URL calls succeeded with scripts disabled and no redirects. The page-signal inventories were not truncated.

Firecrawl produced the four findings above. PostHog declared an `llms.txt` link and an MCP reference; Python's page produced no matching signals. Claude preserved the capture omissions and correctly distinguished accessibility hiding from visual-hiding declarations. These are observations from one run on October 30, 2026, not a benchmark or a complete website audit.

## Limits that matter

- Findings are fixed English lexical categories, not a security judgment or proof of an instruction. Ordinary marketing copy about AI agents can match. Paraphrases, other languages and encoded guidance can be missed.
- The parser records source declarations, not browser-computed visibility. External styles, CSS classes, same-color text, media queries and runtime changes are not evaluated. Malformed HTML may have a different browser DOM.
- `aria-hidden=true` alone is not visual hiding. Comments and templates are marked as inert contexts when inventoried.
- Script, style and raw-text containers (including title, textarea and noscript), arbitrary attribute prose, embedded documents and foreign SVG/MathML content are outside this inventory. Encountered skipped container types appear in `omitted_contexts`. This includes `noscript`, whose contents can matter to clients that do not run JavaScript. The exact inline properties recognized are `display:none`, `visibility:hidden` and common zero values for `opacity`; other styles and obfuscated CSS are not detected. Resource paths are recognized naming conventions, not validated OpenAPI documents or working MCP servers.
- Each HTML input allows at most 64 findings, an 8,192-character text window and nesting up to 256 elements, within the existing tag, byte and HTML time limits. Every result has `status: complete` or `status: incomplete`. Finding/text truncation and parsing/work limits produce fixed reasons and preserve findings already emitted. Unscanned content remains unknown. A complete pass still excludes the contexts listed above. Existing route evidence is retained if this optional inventory is incomplete; overall request deadlines still apply.
- Repeated equivalent findings on one source line can be combined. A normalized relative path has a leading slash even when its `resolution` label says the original reference was unresolved; this is not a verified absolute URL.
- Raw source prose, commands, origins, arbitrary attribute values and query values stay out of the inventory. The host agent already sees any saved content it supplies; its own logging is outside this tool's control.
- Enabling the option adds no network requests. It never installs software, authenticates, connects to discovered servers or executes page instructions.

The tests cover inherited declarations, inline phrase splits, entities and source-line mapping, raw-text elements, malformed limits, resource minimization and MCP round trips. The tests do not certify complete detection of hidden content.
