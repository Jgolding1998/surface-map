const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {chromium} = require("playwright");

(async () => {
  const browser = await chromium.launch({headless:true,channel:process.env.SURFACE_BROWSER_CHANNEL || undefined});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1100}});
    const errors = [], external = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => {if (!request.url().startsWith("http://127.0.0.1:") && !request.url().startsWith("blob:")) external.push(request.url());});
    await page.goto(process.env.SURFACE_TEST_URL || "http://127.0.0.1:8768");
    await page.waitForFunction(() => document.querySelector("#routes-count").textContent === "7");
    assert.equal(await page.locator("#observed-count").textContent(), "3");
    await page.locator('[data-filter="inferred"]').click();
    assert.equal(await page.locator("#route-rows tr").count(), 2);
    await page.locator('[data-filter="all"]').click();
    const downloadPromise = page.waitForEvent("download");
    await page.locator("#export-openapi").click();
    const download = await downloadPromise;
    const document = JSON.parse(await fs.readFile(await download.path(), "utf8"));
    assert.equal(document.openapi,"3.1.0");
    assert.ok(document.paths["/api/orders/{integer_3}"]);
    await page.locator("#demo-diff").click();
    await page.waitForFunction(() => document.querySelector("#diff-summary").textContent.includes("1 added"));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path:path.join(__dirname,"../docs/dashboard.png"),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path:path.join(__dirname,"../docs/mobile.png"),fullPage:true});
    const marker = "PRIVATE" + "-BROWSER-CANARY-38475";
    const email = "person" + String.fromCharCode(64) + "example.invalid";
    const payload = {log:{entries:[{request:{method:"GET",url:`https://${marker}.invalid/api/customers/${email}?token=${marker}`,headers:[{name:"Authorization",value:marker}]},response:{status:200,content:{mimeType:"application/json",text:JSON.stringify({token:marker,email})}}}]}};
    await page.locator("#files").setInputFiles({name:`${marker}.har`,mimeType:"application/json",buffer:Buffer.from(JSON.stringify(payload))});
    await page.locator("#analyze").click();
    await page.waitForFunction(() => document.querySelector("#routes-count").textContent === "1");
    const bodyText = await page.locator("body").textContent();
    assert.ok(!bodyText.includes(marker) && !bodyText.includes(email));
    for(const kind of ["report","openapi","contract"]) {
      const pending = page.waitForEvent("download");
      await page.locator("#export-"+kind).click();
      const data = await fs.readFile(await (await pending).path(),"utf8");
      assert.ok(!data.includes(marker) && !data.includes(email));
    }
    await page.locator("#baseline").click();
    payload.log.entries.push({request:{method:"GET",url:"/api/reports"},response:{status:201}});
    await page.locator("#files").setInputFiles({name:"second.har",mimeType:"application/json",buffer:Buffer.from(JSON.stringify(payload))});
    await page.locator("#analyze").click();
    await page.waitForFunction(() => document.querySelector("#routes-count").textContent === "2");
    await page.locator("#compare").click();
    await page.waitForFunction(() => document.querySelector("#diff-summary").textContent.includes("1 added"));
    await page.locator("#clear").click();
    assert.equal(await page.locator("#route-rows tr").count(),0);
    assert.equal(await page.locator("#export-report").isDisabled(),true);
    assert.deepEqual(errors,[]);
    assert.deepEqual(external,[]);
    console.log("Browser checks passed: desktop/mobile, filters, exports, planted-value redaction, baseline comparison, clear session, no JS errors or external requests.");
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
