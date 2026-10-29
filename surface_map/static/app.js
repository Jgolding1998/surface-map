"use strict";

const $ = selector => document.querySelector(selector);
const token = $('meta[name="surface-token"]').content;
let files = [], baselineFiles = [], current = null, diff = null, filter = "all", selected = null, busy = false;
const node = (tag, className, value) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined) element.textContent = value;
  return element;
};

function notice(message, error = false) {
  $("#notice").textContent = message;
  $("#notice").classList.toggle("error", error);
}

function refresh() {
  $("#files").disabled = busy;
  $("#analyze").disabled = busy || files.length === 0;
  $("#demo").disabled = busy;
  $("#demo-diff").disabled = busy;
  $("#baseline").disabled = busy || !current || files.length === 0;
  $("#compare").disabled = busy || !current || files.length === 0 || baselineFiles.length === 0;
  $("#clear").disabled = busy;
  for (const kind of ["report", "openapi", "contract"]) $("#export-" + kind).disabled = !current || busy;
}

function sourceList(sources) {
  const list = $("#source-list"); list.replaceChildren();
  for (const source of sources) {
    const row = node("div", "source-row");
    row.append(node("span", "source-type", source.kind === "javascript" ? "JS" : source.kind.toUpperCase()), node("span", "", source.id.replace("_", " ")), node("span", "source-size", `${(source.bytes / 1024).toFixed(1)} KiB`));
    list.append(row);
  }
}

function details(route) {
  const panel = $("#detail"); panel.replaceChildren();
  panel.append(node("span", "detail-label", "ROUTE EVIDENCE"), node("h3", "", `${route.method} ${route.path}`), node("p", "", `${route.confidence.toUpperCase()} · Path parameters and unknown names are conservative aliases.`));
  panel.append(node("h4", "", "Source locations"));
  const evidence = node("ul");
  for (const entry of route.evidence) evidence.append(node("li", "", `${entry.source.replace("_", " ")} · ${entry.location} · ${entry.kind}`));
  panel.append(evidence);
  panel.append(node("h4", "", "Query fields"), node("p", "", route.query_fields.join(", ") || "None observed"));
  for (const [key, title] of [["request_shapes", "Request field shapes"], ["response_shapes", "Response field shapes"]]) {
    panel.append(node("h4", "", title));
    panel.append(route[key].length ? node("pre", "", JSON.stringify(route[key], null, 2)) : node("p", "", "No supported body shape observed."));
  }
}

function table() {
  const rows = $("#route-rows"); rows.replaceChildren();
  const query = $("#search").value.toLowerCase();
  const routes = current ? current.report.routes.filter(route => (filter === "all" || route.confidence === filter) && route.path.toLowerCase().includes(query)) : [];
  $("#empty-routes").hidden = routes.length !== 0;
  for (const route of routes) {
    const row = node("tr", selected === route.id ? "selected" : "");
    const method = node("td"); method.append(node("span", "method " + route.method.toLowerCase(), route.method));
    const path = node("td", "", route.path);
    const confidence = node("td"); confidence.append(node("span", "confidence " + route.confidence, route.confidence));
    const status = node("td", "", route.statuses.join(", ") || "Unobserved");
    const action = node("td"); const button = node("button", "row-detail", "↗");
    button.setAttribute("aria-label", `Inspect ${route.method} ${route.path}`);
    button.addEventListener("click", () => { selected = route.id; details(route); table(); });
    action.append(button); row.append(method, path, confidence, status, action); rows.append(row);
  }
}

function show(result, example = false) {
  current = result; selected = null;
  const report = result.report;
  $("#routes-count").textContent = report.routes.length;
  const observed = report.routes.filter(route => route.confidence === "observed").length;
  $("#observed-count").textContent = observed;
  $("#inferred-count").textContent = report.routes.length - observed;
  $("#sources-count").textContent = report.sources.length;
  $("#workspace-mode").textContent = example ? "EXAMPLE WORKSPACE" : "YOUR SAVED ARTIFACTS";
  sourceList(report.sources); table();
  if (report.routes.length) { selected = report.routes[0].id; details(report.routes[0]); table(); }
  notice(`${example ? "Synthetic example loaded." : "Analysis complete."} ${report.routes.length} normalized routes, ${report.assets.length} declared assets. ${report.warnings.length ? `${report.warnings.length} unsupported or dynamic patterns were omitted. ` : ""}No service was contacted. Exports retain structure, types and aliases; body and query values are discarded.`);
  refresh();
}

async function request(path, payload) {
  const response = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json", "X-Surface-Token":token}, body:JSON.stringify(payload), signal:AbortSignal.timeout(20000)});
  const result = await response.json();
  if (!response.ok) {
    const error = new Error("Request rejected");
    error.safeMessage = typeof result.error === "string" ? result.error : "Request rejected";
    throw error;
  }
  return result;
}

function kind(file) {
  const extension = file.name.split(".").pop().toLowerCase();
  return ["har", "json"].includes(extension) ? "har" : ["html", "htm"].includes(extension) ? "html" : ["js", "mjs"].includes(extension) ? "javascript" : null;
}

async function read(selection) {
  if (!selection.length || selection.length > 6 || selection.some(file => file.size > 512 * 1024 || !kind(file)) || selection.reduce((sum,file) => sum + file.size, 0) > 1024 * 1024) throw new Error("Unsupported selection");
  const result = [];
  for (const file of selection) {
    const bytes = await file.arrayBuffer();
    result.push({kind:kind(file), text:new TextDecoder("utf-8", {fatal:true}).decode(bytes)});
  }
  return result;
}

async function action(task) {
  if (busy) return;
  busy = true; refresh();
  try { await task(); }
  catch (error) { notice(error.safeMessage || "Unable to analyze this selection. Use valid UTF-8 HTML, HAR or JavaScript within the displayed limits. No source content is included in errors.", true); }
  finally { busy = false; refresh(); }
}

function showDiff(value) {
  diff = value; $("#diff-panel").hidden = false;
  $("#diff-summary").textContent = `${value.added.length} added · ${value.removed.length} absent · ${value.changed.length} changed`;
  const list = $("#diff-list"); list.replaceChildren();
  for (const [key, label] of [["added", "Added"], ["removed", "Absent"], ["changed", "Changed"]]) {
    for (const route of value[key]) {
      const row = node("div", "diff-row");
      row.append(node("strong", "", label), node("code", "", `${route.method} ${route.path}`), node("span", "", route.changed_fields ? route.changed_fields.join(", ") : "Capture evidence only"));
      list.append(row);
    }
  }
  if (!list.children.length) list.append(node("div", "diff-row", "No changes in the normalized evidence."));
}

function download(value, suffix) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type:"application/json"}));
  const link = node("a"); link.href = url; link.download = `surface-map-${suffix}.json`; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$("#files").addEventListener("change", () => {
  files = Array.from($("#files").files); current = null; selected = null; diff = null; $("#diff-panel").hidden = true;
  for (const id of ["routes", "observed", "inferred", "sources"]) $("#"+id+"-count").textContent = "0";
  $("#detail").replaceChildren(node("h3", "", "Selection awaiting analysis."));
  sourceList(files.map((file,index) => ({id:`source_${index + 1}`,kind:kind(file) || "unsupported",bytes:file.size})));
  table(); notice("Selection ready. Analyze artifacts to build a fresh evidence map."); refresh();
});
$("#analyze").addEventListener("click", () => action(async () => show(await request("/api/analyze", {artifacts:await read(files)}))));
$("#demo").addEventListener("click", () => action(async () => { files=[]; $("#files").value=""; show(await request("/api/demo", {}), true); }));
$("#baseline").addEventListener("click", () => { baselineFiles=files.slice(); $("#baseline-status").textContent=`Baseline kept: ${baselineFiles.length} artifacts in browser memory. Import a second capture, analyze it, then compare.`; refresh(); });
$("#compare").addEventListener("click", () => action(async () => showDiff((await request("/api/compare", {baseline:await read(baselineFiles),candidate:await read(files)})).diff)));
$("#demo-diff").addEventListener("click", () => action(async () => showDiff((await request("/api/demo-diff", {})).diff)));
for (const button of document.querySelectorAll("[data-filter]")) button.addEventListener("click", () => {filter=button.dataset.filter; document.querySelectorAll("[data-filter]").forEach(item => item.classList.toggle("selected", item===button)); table();});
$("#search").addEventListener("input", table);
for (const name of ["report","openapi","contract"]) $("#export-"+name).addEventListener("click", () => { if(current) download(current[name], name); });
$("#export-diff").addEventListener("click", () => { if(diff) download(diff,"diff"); });
$("#clear").addEventListener("click", () => { files=[]; baselineFiles=[]; current=null; diff=null; selected=null; $("#files").value=""; $("#source-list").replaceChildren(); $("#diff-panel").hidden=true; for(const id of ["routes","observed","inferred","sources"]) $("#"+id+"-count").textContent="0"; $("#detail").replaceChildren(node("h3","","Session cleared.")); $("#baseline-status").textContent="No baseline kept. All files and reports have been cleared from this workspace."; table(); refresh(); notice("Session cleared. The server does not retain uploads or reports."); });
action(async () => show(await request("/api/demo", {}), true));
