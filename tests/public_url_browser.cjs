"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {execFileSync} = require("node:child_process");
const {chromium} = require("playwright");

// Generate the fixture through the actual HTTP/backend pipeline with fake transport.
// Neither fixture generation nor the browser test needs an external request.
const fixtureSource = `
import json
from unittest.mock import patch
from tests.test_public_url import captured
from tests.test_public_url_boundary import PublicURLBoundaryTests
case = PublicURLBoundaryTests('test_token_origin_and_method_gate_network_access')
case.setUp()
try:
    with patch('surface_map.public_url.capture_public_url', return_value=captured()):
        status, _, body = case.request(value={'url':'https://example.org/', 'javascript_engine':'basic'})
    assert status == 200, status
    print(body.decode('utf-8'))
finally:
    case.tearDown()
`;
const fixture = JSON.parse(execFileSync(process.env.SURFACE_MAP_TEST_PYTHON || "python", ["-c", fixtureSource],
  {cwd:path.resolve(__dirname,".."),encoding:"utf8",timeout:15000}));

(async () => {
  const browser = await chromium.launch({headless:true,channel:process.env.SURFACE_BROWSER_CHANNEL || undefined});
  const errors = [], external = [];
  let checks = 0;
  const check = (value, message) => {assert.ok(value,message); checks++;};
  try {
    const base = process.env.SURFACE_TEST_URL || "http://127.0.0.1:8768";
    const page = await browser.newPage({viewport:{width:1440,height:1100}});
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => {if (!request.url().startsWith(base + "/") && !request.url().startsWith("blob:")) external.push(request.url());});
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector("#routes-count").textContent === "7");
    await page.selectOption("#analysis-mode", "url");
    await page.selectOption("#javascript-engine", "basic");
    check(await page.locator("#public-url-form").isVisible() && await page.locator("#file-upload").isHidden(), "Public URL mode has its own explicit input");
    check(await page.locator("#build-plan").isHidden() && await page.locator(".baseline-box").isHidden(), "File-only actions are hidden in URL mode");
    check((await page.locator("#network-label").textContent()).includes("PUBLIC HTTPS"), "Network behavior is visible before inspection");
    const requests = [];
    await page.route(base + "/api/public-url", async route => {
      requests.push(route.request().postDataJSON());
      await route.fulfill({json:fixture});
    });
    await page.fill("#public-url", "https://example.org/");
    check(requests.length === 0, "Typing a URL does not fetch anything");
    await page.click("#inspect-url");
    await page.waitForFunction(() => !document.querySelector("#export-brief").disabled);
    check(requests.length === 1 && requests[0].include_scripts === true && requests[0].javascript_engine === "basic", "Only the explicit inspect action sends the selected options");
    check(await page.locator("#workspace-mode").textContent() === "PUBLIC PAGE EVIDENCE", "Public evidence has an honest workspace label");
    check(await page.locator("#plan-heading").evaluate(element => document.activeElement === element), "Successful URL inspection moves keyboard focus to its result");
    check(await page.locator("#export-scaffold").isHidden() && await page.locator("#mock-plan-note").isHidden(), "URL mode cannot accidentally regenerate mock files or refetch");
    const brief = await page.locator("#agent-brief").inputValue();
    check(brief.includes("Public HTTPS capture") && brief.includes("Inline JavaScript was not extracted") && brief.includes("were not inventoried"), "Human brief preserves capture and reference omissions");
    check((await page.locator("#explanation-status").textContent()).includes("basic lexical hints"), "Basic analysis limits remain visible without expanding notes");
    check(!brief.includes("PRIVATE_PUBLIC_CAPTURE_83651") && !brief.includes("public.example"), "Public brief contains no capture hostname or planted values");
    await page.selectOption("#brief-style", "developer");
    await page.click("#view-public-routes");
    check(await page.locator(".map-panel").isVisible() && await page.locator("#view-public-routes").getAttribute("aria-expanded") === "true", "Inventory and exports are available without another fetch");
    check(await page.locator("#route-rows tr").count() === fixture.report.routes.length, "Inventory agrees with the same captured report");
    const pending = page.waitForEvent("download");
    await page.click("#export-openapi");
    const downloaded = JSON.parse(await fs.readFile(await (await pending).path(),"utf8"));
    check(downloaded["x-surface-map-capture"].mode === "public_https_static" && downloaded["x-surface-map-capture-warnings"].length > 0, "OpenAPI download retains capture coverage");
    check(requests.length === 1, "Styles, inspection and downloads reuse the original capture");
    await page.setViewportSize({width:390,height:844});
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Public URL input and evidence fit mobile");
    if (process.env.SURFACE_PUBLIC_SCREENSHOT_DIR) {
      await fs.mkdir(process.env.SURFACE_PUBLIC_SCREENSHOT_DIR,{recursive:true});
      await page.screenshot({path:path.join(process.env.SURFACE_PUBLIC_SCREENSHOT_DIR,"public-url-mobile.png"),fullPage:true});
    }
    await page.fill("#public-url", "https://example.org/changed");
    check(await page.locator("#plan-panel").isHidden() && await page.locator("#agent-brief").inputValue() === "" && await page.locator("#export-report").isDisabled(), "Editing the URL removes stale briefs and exports");
    await page.unroute(base + "/api/public-url");
    await page.route(base + "/api/public-url", route => route.fulfill({status:422,json:{error:"Public capture rejected within supported limits."}}));
    await page.click("#inspect-url");
    await page.waitForFunction(() => document.querySelector("#notice").classList.contains("error"));
    check(await page.locator("#plan-panel").isHidden() && await page.locator("#copy-brief").isDisabled(), "Failed capture cannot revive an earlier brief");
    await page.unroute(base + "/api/public-url");
    await page.fill("#public-url", "https://127.0.0.1/");
    await page.click("#inspect-url");
    await page.waitForFunction(() => document.querySelector("#notice").textContent.includes("not an allowed public"));
    check(!(await page.locator("#notice").textContent()).includes("127.0.0.1"), "Actual backend rejects a private target without echoing it");
    await page.click("#clear");
    check(await page.locator("#public-url").inputValue() === "" && await page.locator("#inspect-url").isDisabled(), "Clear session clears the URL and its results");
    await page.selectOption("#analysis-mode", "web");
    check(await page.locator("#file-upload").isVisible() && await page.locator("#public-url-form").isHidden(), "Returning to file mode restores the offline flow");
    check((await page.locator("#network-label").textContent()).includes("OFFLINE"), "File mode accurately labels its network behavior");
    check(errors.length === 0,"No browser script errors");
    check(external.length === 0,"Browser makes no external requests");
    console.log(JSON.stringify({checks,pageErrors:errors,externalRequests:external.length}));
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
