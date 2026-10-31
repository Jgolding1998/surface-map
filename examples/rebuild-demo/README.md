# Rebuild demo (synthetic)

Invented files for the [rebuild guide](../../docs/REBUILD.md). The `.invalid`
host is reserved and never resolves. Nothing here comes from a real service.

| File | Kind | Purpose |
|---|---|---|
| `page.html` | `html` | Navigation links, a GET search form and a POST form, all root-relative |
| `app.js` | `javascript` | A literal GET, a literal POST, a dynamic template target and a call with an unknown method |
| `session.har` | `har` | A saved session: list (200), create (201) and one item (404) |

The links are root-relative, so all three files work in one capture without a
`document_url`. Without one, origin is not classified.

## Run it
1. Build `capture.json` as `{"artifacts": [...]}` with one entry per file:
   `{"kind": "html", "text": <page.html contents>}`, then `javascript` and `har`
   entries the same way. The HAR text is the file contents as a JSON string.
   In the browser app, choose the three files directly instead.
2. `python -m surface_map rebuild --capture capture.json --scaffold demo-mock`
3. In `demo-mock`, run `python -m unittest -v test_mock`.

## Recorded result
On 2026-10-30 the maintainers ran this demo through the browser app. The plan
had 4 operations, 2 pages and 2 candidate references. The downloaded scaffold had
5 files, and its 3 generated tests passed. Those tests check routing and the 501
and 404 responses, not application behavior.

## What to look for
- Operations: `GET /api/projects`, `POST /api/projects`,
  `GET /api/projects/{integer_3}` and `GET /search`, each a 501 stub.
- Pages: `/dashboard` and `/settings`, with no handlers.
- `/api/export` has an unknown method, so it should be in `unresolved`.
- The template-literal fetch is omitted with a warning.
- No file contains values from `session.har`.
