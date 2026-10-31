# Try Surface Map on one real task

Surface Map turns selected saved web artifacts into a route map and an agent
handoff with source evidence and unknowns. Start with one form or request flow.
The local trial needs no signup, AI account, API key, Node.js or paid service.
Python 3.11 or later is required. Installation time is separate from the trial.

You can [download the latest version (ZIP)](https://github.com/Jgolding1998/surface-map/archive/refs/heads/main.zip)
and extract it, or clone the repository with Git. If you already have a checkout,
switch to `main` and pull the latest changes. Open a terminal in the folder
containing `surface_map` and this README.

## Start locally

```sh
python -m surface_map --open
```

On Windows, `py -3 -m surface_map --open` is another option. Use
`python3 -m surface_map --open` if that is your Python 3 command on macOS/Linux.
If the browser does not open, visit the printed local URL. If the port is busy,
run `python -m surface_map --port 9000 --open`. Keep the terminal running.

1. Select **Saved web artifacts**, then **Basic lexical hints** under JavaScript
   analysis. This makes the small trial independent of the optional Node parser.
2. Select **Try an example agent brief**. This uses synthetic inputs and does not
   contact the illustrated service.
3. Choose simple, developer or Gen Z style. Only the introduction changes; all
   factual content and uncertainty remain the same.
4. Review the unknowns and expand **Review the agent brief**. Copy the brief or
   download **Agent brief (.md)**. Download **Plan JSON** for full retained shapes.
5. Press **Ctrl+C** in the terminal when finished.

If you want to practice importing files, use the three artifacts and expected
results in [the synthetic quickstart](../examples/quickstart/README.md).

## Try a public URL without preparing files

Select **Public HTTPS page**, enter a public HTTPS URL, and select **Inspect
page + create brief**. Choose whether to include up to five same-origin external
scripts. No request is made just by typing. This fetches from the computer running
Surface Map; it uses no browser login or cookies and does not render the page.

The report and brief come from one capture. Change the explanation style, copy
the brief, or select **Show route inventory and exports** without another fetch.
Capture warnings remain available in the evidence notes and JSON downloads.
Private/internal targets are rejected. Some public sites will reject automated
retrieval or fall outside the supported media, size, encoding or time limits.
Use reviewed saved files for those flows.

This mode intentionally omits a separate string/module reference inventory and
does not generate mock files or compare versions. Those use saved artifacts.
Editing the URL or script-selection option clears the previous results so the
next brief cannot be mistaken for the old capture. Clearing the session or
changing mode clears the URL field too.

## A five-minute task after setup

Choose a web app you own or are authorized to inspect. Use a test account and
save one narrow HTML/script selection or a reviewed HAR of one flow. A sanitized
HAR can still contain sensitive URLs and body values, so review it before import.
The tool keeps this selection in local browser/server memory, without telemetry.
Minimization is lossy and is not a guarantee that every sensitive fact is absent.

Add the files and select **Build a plan**. Then:

- Check three retained claims against their source locations. Does the method,
  target and observed/declared/inferred label match the supplied evidence?
- Identify one unknown the next developer would need resolved before coding.
- Give the brief and reviewed Plan JSON to a teammate or your usual coding agent.
  Can they produce a useful implementation plan without inventing behavior?
- Record one mistake, one important omission, and anything it made easier.

You do not need to share your captures. A correction or aggregate feedback is
enough. If you choose to use an external agent, review what you send; its normal
account terms and usage costs apply. Surface Map itself makes no model calls.

Basic JavaScript hints support fewer patterns. For stronger syntax/scope
analysis, follow [the optional parser setup](../README.md#run), then select
**Syntax and scope required** and reanalyze. Fallbacks and parser failures remain
visible; neither should be treated as a complete app inventory.

## Report useful feedback

Keep these answers privately or use the **Real-world trial feedback** form under
**Issues → New issue** in the repository:

1. What job were you trying to do, and what was your normal workflow?
2. How long did setup take? Could you finish the task without help?
3. What did the report get wrong or miss? Which claim was affected?
4. How much editing did the receiving developer or agent need?
5. Would you use it again for another project? Why?

Share the tested commit, operating system, Python version and selected parser
mode when possible. Avoid client names, private URLs, source, credentials or raw
HARs in public feedback. A small synthetic reproduction is more useful.

This trial does not clone a working application, establish complete site
coverage, or perform a security audit. Mock handlers return 501 until implemented;
passing mock checks verifies the scaffold, not application equivalence.
