"use strict";
// HAR body coverage, observed form fields and static page review in the local UI.
// Synthetic fixtures only. Requests outside the local test server are aborted.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const {chromium} = require("playwright");

const CANARY = "BODYCANARY7f3a";
const HOST = "canaryhost7f3a";
const PRIVATE = ["private_old_name", "private_new_name", "oldwidget7f3a", "newwidget7f3a"];
const b64 = text => Buffer.from(text, "utf8").toString("base64");

function harFixture() {
  return JSON.stringify({log:{entries:[
    {request:{method:"POST", url:`https://${HOST}.invalid/api/orders?token=${CANARY}`,
      headers:[{name:"Cookie", value:`sid=${CANARY}`}],
      postData:{mimeType:"application/x-www-form-urlencoded",
        text:`quantity=1&quantity=2&private_state=${CANARY}&%3Cimg%20src%3Dx%20onerror%3Dwindow.__pwn%3D1%3E=${CANARY}`}},
     response:{status:200, content:{mimeType:"application/json", encoding:"base64", text:b64(`{"success":true,"note":"${CANARY}"}`)}}},
    {request:{method:"GET", url:`https://${HOST}.invalid/api/items`},
     response:{status:200, content:{mimeType:"application/json", text:"not JSON"}}},
    {request:{method:"POST", url:`https://${HOST}.invalid/api/upload`,
      postData:{mimeType:"multipart/form-data; boundary=x",
        params:[{name:"title", value:CANARY}, {name:"attachment", fileName:`${CANARY}-private.txt`, value:CANARY}],
        text:`--x\r\nContent-Disposition: form-data; name="title"\r\n\r\n${CANARY}\r\n--x--`}},
     response:{status:201, content:{mimeType:"image/png", encoding:"base64", text:"iVBORw0KGgo="}}},
    {request:{method:"GET", url:`https://${HOST}.invalid/api/broken`},
     response:{status:200, content:{mimeType:"application/json", encoding:"base64", text:"%%%not-base64%%%"}}}
  ]}});
}
const oldHtml = `<form action="/api/orders" method="post"><input name="private_old_name" type="hidden"></form><script src="https://oldwidget7f3a.invalid/widget.js"></script><a href="/checkout">Checkout</a>`;
const newHtml = `<form action="/api/orders" method="post"><input name="private_new_name" type="hidden"></form><script src="https://newwidget7f3a.invalid/widget.js"></script>`;
const file = (name, text) => ({name, mimeType:"text/plain", buffer:Buffer.from(text)});

(async () => {
  const browser = await chromium.launch({headless:true, channel:process.env.SURFACE_BROWSER_CHANNEL || undefined});
  let checks = 0;
  const check = (value, message) => { assert.ok(value, message); checks++; };
  try {
    const base = process.env.SURFACE_TEST_URL || "http://127.0.0.1:8768";
    const context = await browser.newContext({viewport:{width:1280, height:900}, acceptDownloads:true});
    await context.route("**/*", route => route.request().url().startsWith(base + "/") || route.request().url().startsWith("blob:") ? route.continue() : route.abort());
    const page = await context.newPage();
    const errors = [], external = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => { if (!request.url().startsWith(base + "/") && !request.url().startsWith("blob:")) external.push(request.url()); });
    const home = await page.goto(base);
    const csp = home.headers()["content-security-policy"] || "";
    check(csp.includes("script-src 'self'") && !csp.includes("unsafe-inline"), "Content Security Policy still blocks inline script");
    await page.waitForFunction(() => Number(document.querySelector("#routes-count").textContent) > 0);

    const run = async (selection, button = "#analyze") => {
      await page.locator("#files").setInputFiles(selection);
      await page.click(button);
      await page.waitForFunction(() => !document.body.classList.contains("is-busy") && !document.querySelector("#notice").textContent.startsWith("Selection ready"));
    };
    const download = async id => {
      const pending = page.waitForEvent("download");
      await page.click(id);
      return fs.readFile(await (await pending).path(), "utf8");
    };

    // 1. Real backend: HAR body coverage counts match the response and reconcile.
    const analyzed = page.waitForResponse(response => response.url().endsWith("/api/analyze"));
    await run([file("capture.har", harFixture())]);
    const result = await (await analyzed).json();
    const rows = result.report.har_body_analysis;
    check(Array.isArray(rows) && rows.length === 1, "Backend returned HAR body analysis for the synthetic capture");
    const counts = rows[0].counts, notRecorded = counts.reasons.not_present || 0;
    check(counts.inspected + counts.omitted === counts.bodies, "Backend body counts reconcile");
    check(await page.locator("#body-coverage").isVisible(), "Body coverage is shown for HAR results");
    const figures = await page.locator("#body-coverage-summary .coverage-figure strong").allTextContents();
    check(figures.join(",") === [counts.inspected, counts.omitted - notRecorded, notRecorded].join(","), "Inspected, not inspected and not recorded figures match the backend counts");
    check((await page.locator("#body-coverage-total").textContent()).includes(`${counts.bodies} request or response body slots across ${rows[0].entries} HAR entries`), "Total slots and entries are stated");
    const reasonsText = await page.locator("#body-coverage-reasons").textContent();
    check(reasonsText.includes("Labelled JSON but does not parse") && reasonsText.includes("Marked base64 but not valid base64") && reasonsText.includes("No body record in the HAR"), "Skip reasons use plain language");
    check(!/not_present|invalid_json|invalid_base64|unsupported_mime/.test(reasonsText), "Raw reason codes are not shown");
    check((await page.locator("#body-coverage-notes").textContent()).includes("does not mean the request or response had no body"), "A missing body record is not presented as an empty body");
    const why = page.locator("#body-coverage-why summary");
    await why.focus(); await page.keyboard.press("Enter");
    check(await page.locator("#body-coverage-why").evaluate(element => element.open), "Reasons disclosure opens from the keyboard");
    check((await page.locator("#notice").textContent()).includes(`HAR bodies: ${counts.inspected} inspected`), "Status notice summarizes body coverage");

    // 2. Observed form fields with provenance and repeat counts; hostile names stay text.
    const ordersButton = page.locator('#route-rows button[aria-label="Inspect POST /api/orders"]');
    await ordersButton.focus(); await page.keyboard.press("Enter");
    const detail = await page.locator("#detail").textContent();
    check(detail.includes("Observed form fields") && detail.includes("URL-encoded form · source 1 · entry 1"), "Form fields show format and provenance");
    check(await page.locator("#detail .field-list li", {hasText:"quantity"}).locator(".field-count").textContent() === "2 times", "Repeated field names show their count");
    check(detail.includes("Values, types and file data are never kept"), "Detail states that values are not kept");
    check(detail.includes("Body coverage") && detail.includes("Form field names read") && detail.includes("JSON shape read"), "Route detail lists body coverage for its entries");
    check(await page.locator("#detail img, #route-rows img").count() === 0 && await page.evaluate(() => window.__pwn === undefined), "Hostile field names are not parsed as HTML");
    check(await page.evaluate(() => document.activeElement?.getAttribute("aria-label")) === "Inspect POST /api/orders", "Focus stays on the inspected route");
    await page.locator('#route-rows button[aria-label="Inspect POST /api/upload"]').click();
    const upload = await page.locator("#detail").textContent();
    check(upload.includes("Multipart form") || upload.includes("Multipart body without saved field names") || upload.includes("Body coverage"), "Multipart route shows fields or a stated reason");
    check(!/json|integer|string/i.test(await page.locator("#detail .field-list").allTextContents().then(items => items.join(" "))), "Form fields do not claim data types");
    await page.fill("#search", "quantity");
    check(await page.locator("#route-rows tr").count() === 1 && (await page.locator("#route-rows").textContent()).includes("/api/orders"), "Search matches observed field names");
    await page.fill("#search", "");

    // 3. Canary privacy across the DOM and every export.
    const html = await page.content();
    check(!html.includes(CANARY) && !html.includes(HOST) && !html.includes("private_state"), "No body value, host or unknown field name reaches the page");
    for (const id of ["#export-report", "#export-openapi", "#export-contract"]) {
      const text = await download(id);
      check(!text.includes(CANARY) && !text.includes(HOST), `${id} contains no body value or host`);
    }

    // 4. Layout at mobile and desktop widths.
    for (const width of [390, 1280]) {
      await page.setViewportSize({width, height:900});
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `No horizontal overflow at ${width}px`);
    }

    // 5. Future reason codes, hostile names and unreconciled counts from a tampered response.
    const tampered = structuredClone(result);
    tampered.report.har_body_analysis[0].counts.reasons = {...counts.reasons, future_reason_code:2, "<script>":1};
    tampered.report.har_body_analysis[0].counts.text_omissions = {future_text_code:1};
    tampered.report.har_body_analysis[0].bodies[0].reason = "future_inspected_code";
    tampered.report.routes.find(route => route.path === "/api/orders").request_fields[0].fields.push({name:"<img src=x onerror=window.__pwn=2>", occurrences:3});
    await page.route("**/api/analyze", route => route.fulfill({status:200, contentType:"application/json", body:JSON.stringify(tampered)}));
    await run([file("capture.har", harFixture())]);
    await page.unroute("**/api/analyze");
    const tamperedText = await page.locator("#body-coverage").textContent();
    check(tamperedText.includes("Not inspected for another reason") && !tamperedText.includes("future_reason_code") && !tamperedText.includes("<script>"), "Unknown reason codes use a generic label");
    check(tamperedText.includes("did not add up"), "Counts that do not reconcile are flagged");
    await page.locator('#route-rows button[aria-label="Inspect POST /api/orders"]').click();
    check(await page.locator("#detail img").count() === 0 && await page.evaluate(() => window.__pwn === undefined) && (await page.locator("#detail").textContent()).includes("<img src=x"), "Hostile names render as inert text");
    const overflow = structuredClone(result);
    overflow.report.har_body_analysis[0].counts.reasons = {not_present:counts.omitted + 1};
    await page.route("**/api/analyze", route => route.fulfill({status:200, contentType:"application/json", body:JSON.stringify(overflow)}));
    await run([file("capture.har", harFixture())]);
    await page.unroute("**/api/analyze");
    check((await page.locator("#body-coverage-notes").textContent()).includes("did not add up"), "Not-recorded counts cannot exceed all omitted bodies without a warning");

    // 6. Comparison with static page review on the real backend.
    await page.selectOption("#javascript-engine", "basic");
    await run([file("old.html", oldHtml)]);
    await page.click("#baseline");
    const compared = page.waitForResponse(response => response.url().endsWith("/api/compare"));
    await run([file("new.html", newHtml)]);
    await page.click("#compare");
    const comparison = await (await compared).json();
    await page.waitForFunction(() => !document.querySelector("#review-signals").hidden);
    check(typeof comparison.diff?.added === "object" && comparison.review_signals, "Backend returned the legacy diff and review signals");
    const status = comparison.review_signals.status;
    check(["available", "partial"].includes(status), "HTML comparison produced a static review");
    const review = await page.locator("#review-signals").textContent();
    check(review.includes(status === "partial" ? "Partial static page review" : "Static page review complete"), "Review status banner matches the backend status");
    check(review.includes("Form fields") && /1 added · 1 absent/.test(review), "Hidden field changes are grouped with counts");
    check(review.includes("hidden input") && review.includes("Baseline: source 1 · line 1"), "Rows show plain attributes and source evidence");
    check(review.includes("private to this comparison"), "Opaque IDs are explained");
    const comparisonHTML = await page.content();
    check(!PRIVATE.some(value => review.includes(value)) && !PRIVATE.some(value => comparisonHTML.includes(value)), "Original field names and domains are not shown");
    check((await page.locator("#notice").textContent()).includes("Comparison ready"), "Comparison result is announced in the status region");
    const group = page.locator(".signal-group summary").first();
    await group.focus(); await page.keyboard.press("Enter");
    check(!(await page.locator(".signal-group").first().evaluate(element => element.open)), "Category disclosure toggles from the keyboard");
    check((await page.locator("#diff-summary").textContent()).includes("absent"), "Legacy route diff stays visible");
    await page.setViewportSize({width:390, height:900});
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Comparison fits at 390px");
    await page.setViewportSize({width:1280, height:900});

    // 7. Real partial backend: independent declared changes remain visible even
    // when JavaScript-only endpoint/navigation scopes could not be compared.
    const oldPartial = '<form action="/api/orders"><input name="email"></form><a href="/checkout">Old</a><script>fetch("/api/items")</script>';
    const newPartial = '<form action="/api/payments"><input name="email"></form><a href="/dashboard">New</a><script>fetch("/api/users")</script>';
    await run([file("old.html", oldPartial)]);
    await page.click("#baseline");
    await run([file("new.html", newPartial)]);
    const partialResponse = page.waitForResponse(response => response.url().endsWith("/api/compare"));
    await page.click("#compare");
    const realPartial = await (await partialResponse).json();
    await page.waitForFunction(() => document.querySelector("#review-status").textContent.includes("Partial"));
    check(realPartial.review_signals.status === "partial" && realPartial.review_signals.comparison.endpoint.added.length === 1, "Real basic-mode comparison retains declared form changes alongside incomplete JavaScript");
    const retainedTitles = await page.locator(".signal-group .signal-title").allTextContents();
    check(retainedTitles.includes("Request targets") && retainedTitles.includes("Navigation"), "Independent supported request and navigation changes are not hidden by JavaScript-only exclusions");
    check((await page.locator("#review-status").textContent()).includes("not an unchanged or passing result"), "Partial comparison cannot be mistaken for passing");
    check((await page.locator("#review-quiet").textContent()) === "", "Partial categories are not described as unchanged");
    const exported = JSON.parse(await download("#export-diff"));
    check(Array.isArray(exported.added) && exported.review_signals.status === "partial", "Diff download retains legacy keys and supplemental partial review");
    check(exported.before && exported.after && exported.review_signals.coverage.incomplete_sources.length > 0, "Diff download preserves both normalized and supplemental coverage");
    check(!JSON.stringify(exported).includes(CANARY) && !JSON.stringify(exported).includes(HOST), "Comparison export contains no fixture values or hostnames");

    // 8. Body-shape changes carry capture-wide inspection gaps in both versions.
    const changedHar = JSON.parse(harFixture());
    changedHar.log.entries[0].response.content = {mimeType:"application/json", text:"not JSON"};
    await run([file("before.har", harFixture())]);
    await page.click("#baseline");
    await run([file("after.har", JSON.stringify(changedHar))]);
    const bodyComparisonResponse = page.waitForResponse(response => response.url().endsWith("/api/compare"));
    await page.click("#compare");
    const bodyComparison = await (await bodyComparisonResponse).json();
    await page.waitForFunction(() => document.querySelector("#diff-list").textContent.includes("baseline capture contains bodies"));
    const changedBodyRoute = bodyComparison.diff.changed.find(route => route.path === "/api/orders");
    check(changedBodyRoute.context.includes("capture_bodies_uninspected_before") && changedBodyRoute.context.includes("capture_bodies_uninspected_after"), "Actual body-shape comparison reports both capture-level inspection gaps");
    const bodyDiffText = await page.locator("#diff-list").textContent();
    check(bodyDiffText.includes("baseline capture contains bodies that were not inspected") && bodyDiffText.includes("new capture contains bodies that were not inspected") && !bodyDiffText.includes("capture_bodies_uninspected"), "Body inspection gaps are visible in plain language without internal codes");
    const bodyDiffExport = JSON.parse(await download("#export-diff"));
    check(bodyDiffExport.before.har_body_analysis.length === 1 && bodyDiffExport.after.har_body_analysis.length === 1, "HAR comparison download retains both versions' body coverage");

    // 9. Not applicable: HAR-only and mismatched kinds.
    await run([file("a.har", harFixture())]);
    await page.click("#baseline");
    await run([file("b.har", harFixture())]);
    await page.click("#compare");
    await page.waitForFunction(() => document.querySelector("#review-status").textContent.includes("only HAR files"));
    check((await page.locator("#review-status").textContent()).includes("not an unchanged or passing result"), "HAR-only review is not presented as passing");
    check(await page.locator(".signal-group").count() === 0, "No category groups when the review does not apply");
    check((await page.locator("#review-withheld").textContent()) === "", "A later inapplicable review clears prior incomplete-category notes");
    await run([file("old.html", oldHtml)]);
    await page.click("#baseline");
    await run([file("b.har", harFixture())]);
    await page.click("#compare");
    await page.waitForFunction(() => document.querySelector("#review-status").textContent.includes("same kinds of files"));
    checks++;

    // 10. Another source's incomplete categories do not hide retained supported
    // changes; unknown values still use generic, inert presentation labels.
    const partial = structuredClone(comparison);
    partial.review_signals.status = "partial";
    partial.review_signals.coverage.incomplete_sources = [{source:"source_2", categories:["navigation", "origin", "tracking"], reasons:["javascript_syntax_analysis_unavailable"]}];
    partial.review_signals.coverage.warnings = [{side:"after", source:"source_1", reason:"future_warning_code"}];
    partial.review_signals.comparison.origin = {added:[], removed:[], changed:[], unchanged_count:0};
    partial.review_signals.comparison.future_category = {added:[{id:"<b>bad</b>", count:1, attributes:[{input_type:"sensitiveinput", api:"sensitive_api_name"}], evidence:[{source:"../etc", location:"line 1"}]}], removed:[], changed:[]};
    await page.route("**/api/compare", route => route.fulfill({status:200, contentType:"application/json", body:JSON.stringify(partial)}));
    await run([file("new.html", newHtml)]);
    await page.click("#compare");
    await page.waitForFunction(() => document.querySelector("#review-status").textContent.includes("Partial"));
    await page.unroute("**/api/compare");
    const partialText = await page.locator("#review-signals").textContent();
    check(partialText.includes("Coverage is incomplete for: Navigation, Origins"), "Incomplete categories are named without claiming all their evidence was withheld");
    check((await page.locator(".signal-group .signal-title").allTextContents()).includes("Navigation"), "Supported navigation from another source remains visible");
    check(!partialText.includes("No differences in the inspected static source for: Navigation"), "Incomplete categories are never called unchanged");
    check(partialText.includes("A source could not be fully compared") && !partialText.includes("future_warning_code"), "Unknown warnings use a generic label");
    check(partialText.includes("Other static signals") && partialText.includes("reference withheld") && partialText.includes("a source") && !partialText.includes("<b>bad</b>") && await page.locator("#review-signals img, #review-signals b").count() === 0, "Unknown categories and invalid IDs fall back safely");
    check(!partialText.includes("sensitiveinput") && !partialText.includes("sensitive_api_name"), "Unknown plain-text attribute codes also use generic labels");

    const unavailable = {...comparison, review_signals:{status:"unavailable", reason:"unsupported_or_limit"}};
    await page.route("**/api/compare", route => route.fulfill({status:200, contentType:"application/json", body:JSON.stringify(unavailable)}));
    await page.click("#compare");
    await page.waitForFunction(() => document.querySelector("#review-status").textContent.includes("unavailable"));
    await page.unroute("**/api/compare");
    check((await page.locator("#review-withheld").textContent()) === "" && (await page.locator("#review-notes").textContent()) === "", "Repeated comparison clears stale incomplete notes when its review is unavailable");

    // 11. Clearing removes body coverage and review state, including hidden DOM.
    await page.click("#clear");
    check(await page.locator("#body-coverage").isHidden() && await page.locator("#diff-panel").isHidden(), "Clearing hides body coverage and comparison");
    check((await page.locator("#body-coverage-total").textContent()) === "" && (await page.locator("#diff-list").textContent()) === "" && (await page.locator("#review-status").textContent()) === "", "Clearing also removes prior evidence from hidden report nodes");

    check(errors.length === 0, `No page errors: ${errors.join("; ")}`);
    check(external.length === 0, "No request left the local test server");
    console.log(`HAR evidence browser checks passed (${checks} checks).`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
