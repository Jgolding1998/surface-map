# Try an agent handoff in five minutes

This is a tiny, entirely synthetic example. It contains a saved HTML page, a
separate client script, and a made-up HAR request. No real service is contacted.

1. Install Python 3.11 or later if it is not already available. Download the
   repository with GitHub's **Code → Download ZIP**, extract it, and open a
   terminal in the extracted folder that contains `surface_map`.
2. Run `python -m surface_map --open`. On Windows, `py -3 -m surface_map --open`
   is an alternative. Keep the terminal running. If a browser does not open,
   use the local URL printed in the terminal.
3. In **Saved web artifacts**, choose **Basic lexical hints** for JavaScript
   analysis. This trial needs no Node.js, packages, AI account, or API key.
4. Add exactly `page.html`, `client.js`, and `orders.har` from this folder.
   Select **Build a plan** to prepare the explanation and agent handoff.
5. Read the unknowns, try the explanation styles, and review **Copy agent brief**
   or download **Agent brief (.md)**. The style changes the wording, while the
   evidence and uncertainty stay the same. Review the brief before sharing it
   with an agent; the agent provider's own usage terms and costs still apply.

The expected evidence is:

| Item | Evidence | What it establishes |
| --- | --- | --- |
| `GET /api/orders` | Observed HAR request, plus a script hint | This made-up capture records a GET and a 200 response |
| `POST /api/orders` | Declared HTML form | The page declares a submission; server acceptance is unknown |
| `GET /api/items` | Inferred client call | Selected code suggests a request; it was not observed |
| `/dashboard` | Declared navigation | A page link, separate from API operations |

Body values, the example hostname, and original filenames are discarded from
the minimized output. The report does not establish which inputs are required,
who may make requests, how orders are stored, or how the interface behaves.
The generated mock returns 501 until implemented. Its checks do not prove a
recreated application works like the original.

## Try your own work next

Choose one flow in a web app you own or are authorized to inspect. Save its HTML,
selected external scripts, or a narrow HAR from a test account. Review the
capture before importing it: sanitized HAR exports can still contain sensitive
URLs and body values. Import the files and prepare a new brief. Ask the app
owner or another developer what is wrong, missing, or unclear.

For a stronger JavaScript analysis, install the optional pinned parser with
`python -m surface_map setup-js` and select **Syntax and scope required**.
That setup needs a supported Node.js version and internet access; it is not
required for this basic trial. See the repository README for current runtime
requirements.

Stop the local server with **Ctrl+C** when finished.
