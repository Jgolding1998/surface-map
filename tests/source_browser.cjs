"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {chromium} = require("playwright");
let checks = 0;
const check = (value, message) => {assert.ok(value, message); checks++;};

(async () => {
  const browser = await chromium.launch({headless:true, channel:process.env.SURFACE_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1100}});
    const errors = [], external = [];
    const base = process.env.SURFACE_TEST_URL || "http://127.0.0.1:8768";
    let sourcePosts = 0;
    await page.route("**/*", route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => {
      if (!request.url().startsWith(base + "/") && !request.url().startsWith("blob:")) external.push(request.url());
      if (request.url() === base + "/api/source") sourcePosts++;
    });
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector("#routes-count").textContent === "7");
    await page.selectOption("#analysis-mode", "source");
    check(await page.locator("#source-panel").isVisible(), "Source mode visible");
    check(await page.locator(".map-panel").isHidden(), "Web routes are kept separate");
    check(await page.locator("#export-source").isDisabled(), "No empty inventory export");
    await page.click("#demo");
    await page.waitForFunction(() => Number(document.querySelector("#dependency-count").textContent) > 0);
    check(await page.locator("#source-list .source-row").count() === 3, "Source example has generic IDs");
    const marker = "PRIVATE" + "_SOURCE_BROWSER_5367";
    const email = "person" + String.fromCharCode(64) + "example.invalid";
    const manifest = (extra=false) => ({name:marker, author:email, dependencies:{react:"1.2.3", ...(extra ? {zustand:"4.5.6"} : {}), [marker]:"file:/private/" + marker}, scripts:{start:"echo " + marker}});
    const selection = (extra=false) => [
      {name:"package.json", mimeType:"application/json", buffer:Buffer.from(JSON.stringify(manifest(extra)))},
      {name:"pyproject.toml", mimeType:"text/plain", buffer:Buffer.from('[project]\nname="' + marker + '"\ndependencies=["fastapi", "uvicorn"]\n')},
      {name:marker + ".py", mimeType:"text/plain", buffer:Buffer.from('import sounddevice\napi_key="' + marker + '"\n')}
    ];
    await page.locator("#files").setInputFiles(selection());
    await page.click("#analyze");
    await page.waitForFunction(() => document.querySelector("#dependency-count").textContent === "4");
    let text = await page.locator("body").textContent();
    check(!text.includes(marker) && !text.includes(email) && !text.includes("1.2.3"), "Values and original names do not enter the page");
    check(await page.locator("#source-list .source-row").count() === 3, "Selected sources get generic IDs");
    check(await page.locator("#export-openapi").isDisabled(), "Source declarations do not fabricate an OpenAPI service");
    const pending = page.waitForEvent("download");
    await page.click("#export-source");
    const downloaded = JSON.parse(await fs.readFile(await (await pending).path(), "utf8"));
    check(downloaded.format === "surface-map-source-v1", "Inventory export schema");
    check(downloaded.sources.every((entry, index) => entry.id === "source_" + (index + 1) && entry.status === "parsed"), "Export preserves only generic source IDs");
    check(!JSON.stringify(downloaded).includes(marker) && !JSON.stringify(downloaded).includes(email), "Export canaries removed");
    const screenshots = process.env.SURFACE_SOURCE_SCREENSHOT_DIR || path.join(__dirname, "../docs");
    await fs.mkdir(screenshots, {recursive:true});
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Desktop source inventory fits");
    await page.screenshot({path:path.join(screenshots, "source-dashboard.png"), fullPage:true});
    await page.setViewportSize({width:390,height:844});
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Mobile source inventory fits");
    await page.screenshot({path:path.join(screenshots, "source-mobile.png"), fullPage:true});
    await page.click("#baseline");
    await page.locator("#files").setInputFiles(selection(true));
    await page.click("#analyze");
    await page.waitForFunction(() => document.querySelector("#dependency-count").textContent === "5");
    await page.click("#compare");
    await page.waitForFunction(() => document.querySelector("#source-diff-list").textContent.includes("zustand"));
    check((await page.locator("#source-diff-list").textContent()).includes("Added dependencies: zustand"), "Static evidence comparison works");
    const difference = page.waitForEvent("download");
    await page.click("#export-source-diff");
    const diffText = await fs.readFile(await (await difference).path(), "utf8");
    check(JSON.parse(diffText).format === "surface-map-source-diff-v1" && !diffText.includes(marker), "Static comparison export is minimized");
    await page.click("#demo");
    await page.waitForFunction(() => document.querySelector("#source-list").textContent.includes("PYPROJECT") && !document.querySelector("#demo").disabled);
    check(await page.locator("#source-diff").isHidden(), "Loading the source example clears an unrelated prior comparison");
    check((await page.locator("#baseline-status").textContent()).startsWith("No baseline kept"), "Loading the example clears baseline selection");
    await page.locator("#files").setInputFiles({name:"broken.py", mimeType:"text/plain", buffer:Buffer.from("def : " + marker)});
    await page.click("#analyze");
    await page.waitForFunction(() => document.querySelector("#notice").textContent.includes("rejected"));
    check(await page.locator("#baseline").isDisabled() && await page.locator("#compare").isDisabled(), "Rejected sources cannot create misleading comparison");
    for (const name of ["__proto__", "constructor", "py", "rs", "sh", ".py", ".js", ".ts", ".rs", ".sh"]) {
      const before = sourcePosts;
      await page.locator("#files").setInputFiles({name, mimeType:"text/plain", buffer:Buffer.from(marker)});
      await page.click("#analyze");
      await page.waitForFunction(() => document.querySelector("#notice").classList.contains("error"));
      check(sourcePosts === before, "Unsupported filename rejected before upload");
    }
    await page.locator("#files").setInputFiles({name:marker + ".js", mimeType:"text/javascript", buffer:Buffer.from("const ui = require('react');")});
    await page.click("#analyze");
    await page.waitForFunction(() => document.querySelector("#dependency-count").textContent === "1");
    const javascriptExport = page.waitForEvent("download");
    await page.click("#export-source");
    const javascript = JSON.parse(await fs.readFile(await (await javascriptExport).path(), "utf8"));
    check(javascript.sources[0].kind === "javascript", "JavaScript extension selects JavaScript source format");
    check(javascript.languages.length === 1 && javascript.languages[0].name === "javascript", "Plain JavaScript is not mislabeled TypeScript");
    await page.click("#clear");
    check(await page.locator("#dependency-count").textContent() === "0" && await page.locator("#export-source").isDisabled(), "Clear releases source report and export");
    await page.selectOption("#analysis-mode", "web");
    await page.click("#demo");
    await page.waitForFunction(() => document.querySelector("#routes-count").textContent === "7");
    check(await page.locator(".map-panel").isVisible() && await page.locator("#source-panel").isHidden(), "Original web mode remains available");
    check(errors.length === 0, "No script errors");
    check(external.length === 0, "No external requests");
    console.log(JSON.stringify({assertions:checks, scriptErrors:errors.length, externalRequests:external.length}));
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode=1;});
