"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {chromium} = require("playwright");

(async () => {
  const browser = await chromium.launch({headless:true, channel:process.env.SURFACE_BROWSER_CHANNEL || undefined});
  const errors = [], external = [];
  let checks = 0;
  const check = (value, message) => {assert.ok(value, message); checks++;};
  try {
    const base = process.env.SURFACE_TEST_URL || "http://127.0.0.1:8768";
    const page = await browser.newPage({viewport:{width:1440,height:1100}});
    await page.route("**/*", route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => {if (!request.url().startsWith(base + "/") && !request.url().startsWith("blob:")) external.push(request.url());});
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector("#routes-count").textContent === "7");
    check(await page.locator("#build-plan").isDisabled(), "Example report does not pretend to retain its original inputs");
    const canary = "BROWSER-PRIVATE-CANARY-5294";
    await page.locator("#files").setInputFiles([
      {name:canary + ".html", mimeType:"text/html", buffer:Buffer.from('<a href="/dashboard">dashboard</a><form method="post" action="/api/orders"><input name="order_id"></form>')},
      {name:canary + ".js", mimeType:"text/javascript", buffer:Buffer.from('fetch("/api/items"); const label="https://' + canary + '.invalid/api/tasks?token=' + canary + '";')}
    ]);
    await page.click("#build-plan");
    await page.waitForFunction(() => !document.querySelector("#export-plan").disabled);
    check(await page.locator("#plan-panel").isVisible(), "Plan visible after explicit build action");
    check(await page.locator("#workspace-mode").textContent() === "YOUR REBUILD PLAN", "Plan has its own workspace label");
    check(await page.locator(".stats-grid").isHidden() && await page.locator(".map-panel").isHidden(), "Plan hides unrelated route totals and empty map");
    check((await page.locator("#plan-summary").textContent()).includes("planned stubs"), "Plan distinguishes stubs from references");
    check(await page.locator("#plan-unknowns li").count() > 0, "Unknown behavior remains visible");
    check(await page.locator("#plan-capture-guide li").count() > 0, "Browser capture guidance is available");
    check(!(await page.locator("body").textContent()).includes(canary), "Input names and values are not rendered");
    const planDownload = page.waitForEvent("download");
    await page.click("#export-plan");
    const plan = JSON.parse(await fs.readFile(await (await planDownload).path(), "utf8"));
    check(plan.format === "surface-map-rebuild-v1", "Plan download uses the documented schema");
    check(!JSON.stringify(plan).includes(canary), "Plan export does not contain canaries");
    const mockDownload = page.waitForEvent("download");
    await page.click("#export-scaffold");
    const mock = JSON.parse(await fs.readFile(await (await mockDownload).path(), "utf8"));
    check(Object.values(mock).some(value => typeof value === "string" && value.includes("501")), "Mock file bundle contains explicit unimplemented handlers");
    check(!JSON.stringify(mock).includes(canary), "Generated file bundle does not copy canaries");
    await page.click("#analyze");
    await page.waitForFunction(() => document.querySelector("#routes-count").textContent === "3");
    check(await page.locator("#plan-panel").isHidden() && await page.locator(".stats-grid").isVisible() && await page.locator(".map-panel").isVisible(), "Analysis restores the web evidence view");
    await page.click("#build-plan");
    await page.waitForFunction(() => !document.querySelector("#export-plan").disabled);
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Desktop plan fits");
    await page.setViewportSize({width:390,height:844});
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Mobile plan fits");
    const screenshots = process.env.SURFACE_REBUILD_SCREENSHOT_DIR;
    if (screenshots) {
      await fs.mkdir(screenshots, {recursive:true});
      await page.screenshot({path:path.join(screenshots,"rebuild-mobile.png"),fullPage:true});
    }
    await page.selectOption("#analysis-mode", "project");
    check(await page.locator("#plan-panel").isHidden(), "Mode change clears stale plan");
    check(await page.locator(".baseline-box").isHidden(), "Project mode cannot compare local source IDs across selections");
    check(await page.locator("#demo").isHidden(), "Project mode does not reuse an unrelated source example");
    check((await page.locator("#upload-limits").textContent()).includes("24"), "Project mode shows its own file limit");
    await page.locator("#files").setInputFiles([
      {name:"ui-entry-canary.js", mimeType:"text/javascript", buffer:Buffer.from('import "./ui-target-canary.js"; import "./not-selected.js";')},
      {name:"ui-target-canary.js", mimeType:"text/javascript", buffer:Buffer.from('export const value = "' + canary + '";')}
    ]);
    await page.click("#analyze");
    await page.waitForFunction(() => !document.querySelector("#export-source").disabled);
    check(await page.locator("#project-graph").isVisible(), "Project graph appears with the source inventory");
    check((await page.locator("#project-graph-list").textContent()).includes("source 1 → source 2"), "Graph shows generic source relationships");
    check((await page.locator("#project-omission-list").textContent()).includes("Target file not selected: 1"), "Missing-file coverage is shown");
    check(!(await page.locator("body").textContent()).includes("ui-entry-canary") && !(await page.locator("body").textContent()).includes(canary), "Project UI does not expose names or contents");
    const projectDownload = page.waitForEvent("download");
    await page.click("#export-source");
    const project = JSON.parse(await fs.readFile(await (await projectDownload).path(), "utf8"));
    check(project.format === "surface-map-project-v1" && project.graph.edges.length === 1, "Project export includes the bounded graph");
    check(!JSON.stringify(project).includes("ui-entry-canary") && !JSON.stringify(project).includes(canary), "Project export is minimized");
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Mobile project graph fits");
    if (screenshots) await page.screenshot({path:path.join(screenshots,"project-mobile.png"),fullPage:true});
    await page.click("#clear");
    check(await page.locator("#project-graph").isHidden() && await page.locator("#export-source").isDisabled(), "Clear releases project evidence");
    await page.selectOption("#analysis-mode", "web");
    check(await page.locator("#build-plan").isVisible() && await page.locator("#build-plan").isDisabled(), "Web plan controls reset on return");
    check(await page.locator(".stats-grid").isVisible() && await page.locator(".map-panel").isVisible(), "Returning to web mode restores its regions");
    check(errors.length === 0, "No browser errors");
    check(external.length === 0, "No external requests");
    console.log(JSON.stringify({assertions:checks, scriptErrors:errors.length, externalRequests:external.length}));
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
