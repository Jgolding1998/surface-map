"use strict";
// Startup reliability: input limits must arrive within a bounded deadline, as a
// valid contract, before files or analysis are enabled. Every request is served
// by the local test server, a synthetic fulfilment, or aborted.
const assert = require("node:assert/strict");
const {chromium} = require("playwright");

const base = process.env.SURFACE_TEST_URL || "http://127.0.0.1:8768";
const PRIMARY = ["#analysis-mode", "#javascript-engine", "#files", "#demo", "#demo-plan", "#demo-diff"];

(async () => {
  const browser = await chromium.launch({headless:true, channel:process.env.SURFACE_BROWSER_CHANNEL || undefined});
  let checks = 0;
  const check = (value, message) => {assert.ok(value, message); checks++;};
  const errors = [], external = [];
  const counts = {limits:0, demo:0, otherPosts:0};

  // limitsHandler/demoHandler are replaced per case; anything else goes to the local server or is aborted.
  async function open({limitsHandler, demoHandler, clock = true, init} = {}) {
    const context = await browser.newContext({viewport:{width:1280,height:900}});
    const page = await context.newPage();
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => {if (!request.url().startsWith(base + "/")) external.push(request.url());});
    const state = {limitsHandler, demoHandler, held:[]};
    await page.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (url.origin !== base) return route.abort();
      if (url.pathname === "/api/limits") { counts.limits++; return state.limitsHandler ? state.limitsHandler(route, state) : route.continue(); }
      if (url.pathname === "/api/demo") { counts.demo++; return state.demoHandler ? state.demoHandler(route, state) : route.continue(); }
      if (route.request().method() === "POST") counts.otherPosts++;
      return route.continue();
    });
    if (clock) await page.clock.install();
    if (init) await page.addInitScript(init);
    await page.goto(base);
    return {page, context, state};
  }
  const hold = (route, state) => { state.held.push(route); };
  const blockedState = async page => page.evaluate(selectors => ({
    disabled:selectors.every(selector => document.querySelector(selector).disabled),
    analyze:document.querySelector("#analyze").disabled,
    busy:document.querySelector("#report").getAttribute("aria-busy"),
    error:document.querySelector("#notice").classList.contains("error"),
    notice:document.querySelector("#notice").textContent,
    retryVisible:!document.querySelector("#retry-bootstrap").hidden,
    retryEnabled:!document.querySelector("#retry-bootstrap").disabled,
    limitsText:document.querySelector("#upload-limits").textContent,
    routes:document.querySelector("#routes-count").textContent
  }), PRIMARY);
  const waitForError = page => page.waitForFunction(() => document.querySelector("#notice").classList.contains("error") && document.querySelector("#report").getAttribute("aria-busy") === "false");
  const waitForDemo = page => page.waitForFunction(() => document.querySelector("#routes-count").textContent === "7" && document.querySelector("#report").getAttribute("aria-busy") === "false");

  try {
    // 1. A held limits request reaches a bounded, recoverable error; Retry loads the existing synthetic demo.
    {
      const {page, context, state} = await open({limitsHandler:hold});
      await page.waitForFunction(() => document.querySelector("#report").getAttribute("aria-busy") === "true");
      await page.clock.runFor(14000);
      let view = await blockedState(page);
      check(view.busy === "true" && !view.error && view.disabled && !view.retryVisible, "Startup is still pending just before its deadline");
      await page.clock.runFor(1500);
      await waitForError(page);
      view = await blockedState(page);
      check(view.notice.includes("did not load within 15 seconds") && view.notice.includes("Retry"), "Held limits produce fixed timeout wording");
      check(view.disabled && view.analyze && view.retryVisible && view.retryEnabled, "Analysis stays disabled and Retry is offered after the deadline");
      check(view.limitsText === "Input limits unavailable." && view.routes === "0" && counts.demo === 0, "No fallback limits or example request without a contract");
      check(await page.getByRole("button", {name:"Retry loading the workspace"}).isVisible(), "Retry is an accessible named button");
      const late = state.held.shift();
      state.limitsHandler = null;
      await page.getByRole("button", {name:"Retry loading the workspace"}).click();
      await waitForDemo(page);
      view = await blockedState(page);
      check(!view.error && !view.retryVisible && !view.disabled, "Retry restores the workspace controls");
      check(counts.demo === 1 && view.notice.startsWith("Synthetic example loaded."), "Retry loads the exact existing synthetic example once");
      check(await page.evaluate(() => document.activeElement === document.querySelector("#report")), "Focus moves to the report after a successful retry");
      check((await page.locator("#upload-limits").textContent()).includes("HTML up to 4 MiB"), "Server limits are displayed after retry");
      await late.fulfill({json:{web:{max_artifacts:1,html_bytes:1,other_file_bytes:1,total_bytes:1}}}).catch(() => {});
      await page.clock.runFor(20000);
      check((await page.locator("#upload-limits").textContent()).includes("HTML up to 4 MiB") && await page.locator("#routes-count").textContent() === "7", "A late original reply cannot replace the loaded contract");
      await context.close();
    }

    // 2. The deadline also covers a held response body; a late body cannot apply afterwards.
    {
      const init = () => {
        const original = window.fetch;
        window.fetch = (input, options) => {
          if (String(input) === "/api/limits" && !window.__heldBody) {
            const stream = new ReadableStream({start(controller) { window.__heldBody = controller; }});
            return Promise.resolve(new Response(stream, {status:200, headers:{"Content-Type":"application/json"}}));
          }
          return original(input, options);
        };
      };
      const {page, context} = await open({init});
      await page.waitForFunction(() => Boolean(window.__heldBody));
      await page.clock.runFor(15001);
      await waitForError(page);
      let view = await blockedState(page);
      check(view.notice.includes("did not load within 15 seconds") && view.disabled && view.retryVisible, "A stalled response body is bounded by the same startup deadline");
      const limitsBody = await (await context.request.get(base + "/api/limits")).text();
      await page.evaluate(text => { try { window.__heldBody.enqueue(new TextEncoder().encode(text)); window.__heldBody.close(); } catch (_) {} }, limitsBody);
      await page.clock.runFor(1000);
      view = await blockedState(page);
      check(view.error && view.disabled && view.routes === "0" && view.limitsText === "Input limits unavailable.", "A late body after the deadline is not applied");
      await page.getByRole("button", {name:"Retry loading the workspace"}).click();
      await waitForDemo(page);
      check(!(await blockedState(page)).disabled, "Retry after a stalled body succeeds through the local server");
      await context.close();
    }

    // 3. Malformed HTTP, JSON and contracts fail closed and stay recoverable without page errors.
    {
      const probe = await browser.newContext();
      const valid = JSON.parse(await (await probe.request.get(base + "/api/limits")).text());
      await probe.close();
      const variants = [
        {status:500, body:JSON.stringify(valid), type:"application/json"},
        {status:200, body:"not json", type:"application/json"},
        {status:200, body:"[]", type:"application/json"},
        {status:200, body:"null", type:"application/json"},
        {status:200, body:"{}", type:"application/json"},
        {status:200, body:JSON.stringify({...valid, request_timeout_ms:"35000"}), type:"application/json"},
        {status:200, body:JSON.stringify({...valid, request_timeout_ms:0}), type:"application/json"},
        {status:200, body:JSON.stringify({...valid, web:{...valid.web, html_bytes:-1}}), type:"application/json"},
        {status:200, body:JSON.stringify({...valid, source:null}), type:"application/json"},
        {status:200, body:JSON.stringify({...valid, public_url:{}}), type:"application/json"},
        {status:200, body:JSON.stringify({...valid, padding:"x".repeat(70000)}), type:"application/json"}
      ];
      let index = 0;
      const demoBefore = counts.demo;
      const {page, context, state} = await open({clock:false, limitsHandler:route => {
        const variant = variants[index++];
        return route.fulfill({status:variant.status, contentType:variant.type, body:variant.body});
      }});
      for (let attempt = 0; attempt < variants.length; attempt++) {
        if (attempt) await page.getByRole("button", {name:"Retry loading the workspace"}).click();
        await waitForError(page);
        await page.waitForFunction(() => !document.querySelector("#retry-bootstrap").disabled);
        const view = await blockedState(page);
        check(view.notice.includes("not in the expected format") && view.disabled && view.analyze && view.retryVisible && view.routes === "0", `Malformed limits variant ${attempt + 1} fails closed and offers Retry`);
      }
      check(index === variants.length && counts.demo === demoBefore, "Each Retry makes one limits request and no example request without a valid contract");
      state.limitsHandler = null;
      await page.getByRole("button", {name:"Retry loading the workspace"}).click();
      await waitForDemo(page);
      check(counts.demo === demoBefore + 1, "Valid limits after malformed attempts load the example once");
      await context.close();
    }

    // 4. Repeated Retry cannot overlap attempts, and a superseded request cannot override later state.
    {
      const {page, context, state} = await open({limitsHandler:route => route.fulfill({status:503, json:{error:"busy"}})});
      await waitForError(page);
      const before = counts.limits;
      state.limitsHandler = hold;
      const retry = page.locator("#retry-bootstrap");
      await retry.click();
      await page.waitForFunction(() => document.querySelector("#retry-bootstrap").disabled);
      await retry.click({force:true}).catch(() => {});
      await retry.dispatchEvent("click");
      await page.evaluate(() => document.querySelector("#retry-bootstrap").click());
      check(counts.limits === before + 1 && state.held.length === 1, "Repeated Retry while an attempt is pending sends no overlapping request");
      check((await blockedState(page)).disabled, "Controls remain disabled during the retry attempt");
      await page.clock.runFor(15001);
      await waitForError(page);
      const stale = state.held.shift();
      state.limitsHandler = null;
      await retry.click();
      await waitForDemo(page);
      await stale.fulfill({status:500, body:"late"}).catch(() => {});
      await page.clock.runFor(20000);
      const view = await blockedState(page);
      check(!view.error && !view.retryVisible && !view.disabled && view.routes === "7", "A late superseded reply cannot override a successful later attempt");
      await context.close();
    }

    // 5. A failed initial example is reported truthfully and remains recoverable without automatic POST retries.
    {
      const demoBefore = counts.demo;
      const {page, context, state} = await open({clock:false, demoHandler:route => route.abort()});
      await waitForError(page);
      const view = await blockedState(page);
      check(view.notice.includes("Input limits loaded, but the synthetic example did not.") && view.notice.includes("could not be reached"), "Example failure is reported without claiming it loaded");
      check(!view.disabled && !view.retryVisible && view.routes === "0", "Valid limits keep file and example controls usable");
      check(await page.locator("#workspace-mode").textContent() === "WEB ARTIFACT INSPECTION" && (await page.locator("#empty-routes").textContent()).startsWith("No evidence yet"), "The workspace does not claim an example is loaded");
      check(counts.demo === demoBefore + 1, "The failed example POST is not retried automatically");
      state.demoHandler = null;
      await page.click("#demo");
      await waitForDemo(page);
      check(counts.demo === demoBefore + 2 && (await page.locator("#notice").textContent()).startsWith("Synthetic example loaded."), "Explore example workspace recovers on explicit request");
      await context.close();
    }

    check(counts.otherPosts === 0, "No unrelated POST requests were made");
    check(errors.length === 0, "No browser script errors");
    check(external.length === 0, "Browser makes no external requests");
    console.log(JSON.stringify({checks, pageErrors:errors, externalRequests:external.length}));
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
