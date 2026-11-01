"use strict";

const $ = selector => document.querySelector(selector);
const token = $('meta[name="surface-token"]').content;
let files = [], baselineFiles = [], current = null, diff = null, filter = "all", selected = null, busy = false;
let sourceCurrent = null, sourceDiff = null, rebuildCurrent = null;
let rebuildExample = false, rebuildEngine = "auto";
let publicRoutesShown = false;
let limits = null;
const sourceMode = () => $("#analysis-mode").value === "source";
const projectMode = () => $("#analysis-mode").value === "project";
const publicMode = () => $("#analysis-mode").value === "url";
const codeMode = () => sourceMode() || projectMode();
const byteLabel = value => value >= 1024 * 1024 ? `${value / (1024 * 1024)} MiB` : `${value / 1024} KiB`;
function base64Text(content) {
  const bytes = new TextEncoder().encode(content);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  return btoa(binary);
}
const node = (tag, className, value) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined) element.textContent = value;
  return element;
};

// Tone is presentation only: "success" after a completed result, otherwise neutral.
function notice(message, error = false, tone = "") {
  $("#notice").textContent = message;
  $("#notice").classList.toggle("error", error);
  $("#notice").classList.toggle("success", !error && tone === "success");
}
const modeLabel = () => projectMode() ? "SELECTED PROJECT INSPECTION" : sourceMode() ? "STATIC SOURCE INSPECTION" : publicMode() ? "PUBLIC HTTPS INSPECTION" : "WEB ARTIFACT INSPECTION";
function emptyDetail(label, title, text) {
  $("#detail").replaceChildren(node("span", "detail-label", label), node("h3", "", title), node("p", "", text));
}

function refresh() {
  const source = codeMode(), project = projectMode(), active = source ? sourceCurrent : current;
  const publicPage = publicMode();
  const blocked = busy || !limits;
  document.body.classList.toggle("is-busy", busy);
  $("#report").setAttribute("aria-busy", String(busy));
  $("#analysis-mode").disabled = blocked;
  $("#javascript-engine").disabled = blocked;
  $("#files").disabled = blocked || publicPage;
  $("#file-upload").hidden = publicPage;
  $("#public-url-form").hidden = !publicPage;
  $("#public-url").disabled = blocked;
  $("#url-include-scripts").disabled = blocked;
  $("#inspect-url").disabled = blocked || !$("#public-url").value.trim();
  $("#analyze").disabled = blocked || files.length === 0;
  $("#analyze").hidden = publicPage;
  $("#demo").disabled = blocked || project;
  $("#demo").hidden = project || publicPage;
  $("#demo-plan").disabled = blocked;
  $("#demo-plan").hidden = source || publicPage;
  $("#build-plan").hidden = source || publicPage;
  $("#build-plan").disabled = blocked || source || !files.length;
  $("#export-plan").disabled = busy || !rebuildCurrent;
  $("#export-scaffold").disabled = busy || !rebuildCurrent || publicPage;
  $("#export-scaffold").hidden = publicPage;
  $("#public-plan-note").hidden = !publicPage;
  $("#mock-plan-note").hidden = publicPage;
  $("#view-public-routes").disabled = busy || !rebuildCurrent;
  $("#view-public-routes").textContent = publicRoutesShown ? "Hide route inventory and exports" : "Show route inventory and exports";
  $("#view-public-routes").setAttribute("aria-expanded", String(publicRoutesShown));
  $("#brief-style").disabled = busy || !rebuildCurrent;
  for (const id of ["copy-brief", "export-brief"]) $("#" + id).disabled = busy || !rebuildCurrent || !$("#agent-brief").value;
  $("#demo-diff").disabled = blocked;
  $("#demo-diff").hidden = source || publicPage;
  const rejected = Boolean(source && active && active.sources.some(item => item.status !== "parsed"));
  $(".baseline-box").hidden = project || publicPage;
  $("#baseline").disabled = busy || project || !active || rejected || files.length === 0;
  $("#compare").disabled = busy || project || !active || rejected || files.length === 0 || baselineFiles.length === 0;
  $("#clear").disabled = busy;
  $("#retry-bootstrap").disabled = busy;
  for (const kind of ["report", "openapi", "contract"]) $("#export-" + kind).disabled = source || !current || busy;
  $("#export-source").disabled = !sourceCurrent || busy;
  $("#network-label").textContent = publicPage ? "PUBLIC HTTPS ON REQUEST" : "OFFLINE FILE ANALYSIS";
  $("#flow-input").textContent = publicPage ? "Fetched public artifacts" : "Saved artifacts";
  $("#flow-scope").textContent = publicPage ? "No code execution or API replay" : "No execution or network retrieval";
  $("#footer-scope").textContent = publicPage ? "No AI account · No telemetry · Public HTTPS fetched on request" : "No AI account · No telemetry · No external retrieval in file mode";
  if (limits) {
    const policy = project ? limits.project : limits.source;
    $("#upload-limits").textContent = source
      ? `Up to ${policy.max_artifacts || policy.max_files} sources; ${byteLabel(policy.file_bytes)} each, ${byteLabel(policy.total_bytes)} combined`
      : `HTML up to ${byteLabel(limits.web.html_bytes)}; HAR/JS/TS up to ${byteLabel(limits.web.other_file_bytes)} each. Up to ${limits.web.max_artifacts} sources, ${byteLabel(limits.web.total_bytes)} combined; work limits apply.`;
  }
}

function sourceList(sources) {
  const list = $("#source-list"); list.replaceChildren();
  for (const source of sources) {
    const row = node("div", "source-row");
    row.append(node("span", "source-type", source.kind === "javascript" ? "JS" : source.kind.toUpperCase()), node("span", "", source.id.replace("_", " ")), node("span", "source-size", `${(source.bytes / 1024).toFixed(1)} KiB`));
    list.append(row);
  }
}

// Fixed server codes map to plain text. Unknown or future codes get a generic
// label; raw codes, names and values from a response are never used as labels.
const label = (map, code, fallback) => Object.hasOwn(map, code) ? map[code] : fallback;
const count = value => Number.isSafeInteger(value) && value >= 0 ? value : 0;
const plural = (value, one, many = one + "s") => `${value.toLocaleString()} ${value === 1 ? one : many}`;
const sourceName = value => /^source_[1-9][0-9]{0,2}$/.test(value) ? value.replace("_", " ") : "a source";
const locationName = value => /^(entry|line) [1-9][0-9]{0,8}$/.test(value) ? value : "an unknown location";
const BODY_READ = {json_shape:"JSON shape read, values discarded", form_fields:"Form field names read, values discarded"};
const BODY_SKIPPED = {
  unavailable_text:"Body text was not saved in the HAR",
  body_too_large:"Larger than the inspection limit",
  invalid_base64:"Marked base64 but not valid base64",
  invalid_utf8:"Not readable UTF-8 text",
  invalid_json:"Labelled JSON but does not parse",
  json_structure_limit:"JSON too deeply nested or too large",
  unsupported_mime:"A media type that is not inspected",
  unsupported_encoding:"An encoding other than base64",
  invalid_text:"Body text has an unexpected type",
  invalid_body:"Body record is malformed",
  invalid_form_params:"Form parameters are malformed",
  invalid_form_text:"Form text could not be decoded",
  too_many_form_fields:"More form fields than the limit",
  multipart_params_unavailable:"Multipart body without saved field names",
  conflicting_form_representations:"Form text and saved fields disagree"
};
const BODY_OTHER = "Not inspected for another reason";
const BODY_NOT_RECORDED = "No body record in the HAR";
const TEXT_OMISSIONS = {multipart_text_not_inspected:"multipart text not read; only saved field names were used"};
const FORM_FORMATS = {urlencoded:"URL-encoded form", multipart:"Multipart form"};

function bodyRows(report) {
  return Array.isArray(report?.har_body_analysis) ? report.har_body_analysis.filter(isRecordValue) : [];
}
function isRecordValue(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }

function bodyTotals(report) {
  const totals = {sources:0, entries:0, bodies:0, inspected:0, omitted:0, notRecorded:0, skipped:new Map(), textOmissions:new Map(), truncated:0};
  for (const item of bodyRows(report)) {
    const counts = isRecordValue(item.counts) ? item.counts : {};
    const reasons = isRecordValue(counts.reasons) ? counts.reasons : {};
    totals.sources++; totals.entries += count(item.entries); totals.bodies += count(counts.bodies);
    totals.inspected += count(counts.inspected); totals.omitted += count(counts.omitted);
    totals.notRecorded += count(reasons.not_present);
    for (const [code, value] of Object.entries(reasons)) {
      if (code === "not_present" || Object.hasOwn(BODY_READ, code)) continue;
      const text = label(BODY_SKIPPED, code, BODY_OTHER);
      totals.skipped.set(text, (totals.skipped.get(text) || 0) + count(value));
    }
    for (const [code, value] of Object.entries(isRecordValue(counts.text_omissions) ? counts.text_omissions : {})) {
      const text = label(TEXT_OMISSIONS, code, "some saved text not read");
      totals.textOmissions.set(text, (totals.textOmissions.get(text) || 0) + count(value));
    }
    if (item.truncated === true) totals.truncated += count(item.omitted_body_records);
  }
  totals.notInspected = Math.max(0, totals.omitted - totals.notRecorded);
  totals.reconciled = totals.inspected + totals.omitted === totals.bodies
    && totals.notRecorded <= totals.omitted
    && [...totals.skipped.values()].reduce((sum, value) => sum + value, 0) === totals.notInspected;
  return totals;
}

function showBodyCoverage(report) {
  const panel = $("#body-coverage"), rows = bodyRows(report);
  panel.hidden = !rows.length;
  $("#body-coverage-reasons").replaceChildren(); $("#body-coverage-notes").replaceChildren();
  if (!rows.length) {
    $("#body-coverage-summary").replaceChildren(); $("#body-coverage-total").textContent = "";
    $("#body-coverage-why").hidden = true;
    return;
  }
  const totals = bodyTotals(report);
  const figure = (value, text) => { const item = node("span", "coverage-figure"); item.append(node("strong", "", value.toLocaleString()), document.createTextNode(" " + text)); return item; };
  $("#body-coverage-summary").replaceChildren(
    figure(totals.inspected, "inspected"), figure(totals.notInspected, "not inspected"), figure(totals.notRecorded, "not recorded in the HAR"));
  $("#body-coverage-total").textContent = `${plural(totals.bodies, "request or response body slot")} across ${plural(totals.entries, "HAR entry", "HAR entries")}`;
  const list = $("#body-coverage-reasons");
  for (const [text, value] of [...totals.skipped].sort((a, b) => b[1] - a[1])) {
    if (!value) continue;
    const item = node("li"); item.append(node("span", "", text), node("span", "reason-count", value.toLocaleString())); list.append(item);
  }
  if (totals.notRecorded) { const item = node("li", "muted-reason"); item.append(node("span", "", BODY_NOT_RECORDED), node("span", "reason-count", totals.notRecorded.toLocaleString())); list.append(item); }
  if (!list.children.length) list.append(node("li", "muted-reason", "Every recorded body was inspected"));
  $("#body-coverage-why").hidden = !(totals.notInspected || totals.notRecorded);
  const notes = $("#body-coverage-notes");
  if (totals.notRecorded) notes.append(node("li", "", "A missing body record does not mean the request or response had no body."));
  for (const [text, value] of totals.textOmissions) if (value) notes.append(node("li", "", `${plural(value, "body", "bodies")}: ${text}.`));
  if (totals.truncated) notes.append(node("li", "", `${plural(totals.truncated, "body row")} beyond the per-source limit are counted but not listed in route details.`));
  if (!totals.reconciled) notes.append(node("li", "", "These counts did not add up as expected. Use the report JSON as the source of truth."));
}

function routeBodyRecords(route) {
  const records = [], wanted = new Set((route.evidence || []).filter(entry => entry.kind === "har").map(entry => `${entry.source}\u0000${entry.location}`));
  if (!wanted.size || !current) return {records, missing:0, sources:new Set()};
  const sources = new Set();
  for (const item of bodyRows(current.report)) {
    for (const body of Array.isArray(item.bodies) ? item.bodies : []) {
      if (isRecordValue(body) && wanted.has(`${item.source}\u0000${body.location}`)) records.push({source:item.source, ...body});
    }
    if (item.truncated === true) sources.add(item.source);
  }
  const found = new Set(records.map(body => `${body.source}\u0000${body.location}`));
  return {records, missing:[...wanted].filter(key => !found.has(key)).length, sources};
}

function bodyRecordText(body) {
  if (body.state === "inspected") return label(BODY_READ, body.reason, "Inspected");
  if (body.reason === "not_present") return BODY_NOT_RECORDED;
  return label(BODY_SKIPPED, body.reason, BODY_OTHER);
}

function appendRouteBodies(panel, route) {
  const {records, missing} = routeBodyRecords(route);
  if (!records.length && !missing) return;
  panel.append(node("h4", "", "Body coverage"));
  const list = node("ul", "body-records");
  for (const body of records.slice(0, 24)) {
    const item = node("li", body.state === "inspected" ? "inspected" : "");
    item.append(node("span", "body-direction", body.direction === "response" ? "Response" : body.direction === "request" ? "Request" : "Body"),
      node("span", "", bodyRecordText(body)), node("span", "provenance", `${sourceName(body.source)} · ${locationName(body.location)}`));
    list.append(item);
  }
  panel.append(list);
  if (records.length > 24) panel.append(node("p", "detail-note", `${plural(records.length - 24, "more body row")} in the report JSON.`));
  if (missing) panel.append(node("p", "detail-note", `${plural(missing, "HAR entry", "HAR entries")} for this route fell outside the retained body rows. Totals above still count them.`));
}

function appendRequestFields(panel, route) {
  const observations = (Array.isArray(route.request_fields) ? route.request_fields : []).filter(isRecordValue);
  if (!observations.length) return;
  panel.append(node("h4", "", "Observed form fields"));
  for (const observation of observations.slice(0, 16)) {
    const block = node("div", "field-observation");
    block.append(node("p", "field-source", `${label(FORM_FORMATS, observation.format, "Form")} · ${sourceName(observation.source)} · ${locationName(observation.location)}`));
    const list = node("ul", "field-list");
    for (const field of (Array.isArray(observation.fields) ? observation.fields : []).filter(isRecordValue)) {
      const times = count(field.occurrences);
      const item = node("li");
      item.append(node("code", "", typeof field.name === "string" ? field.name : "unnamed field"), node("span", "field-count", times > 1 ? `${times} times` : "once"));
      item.setAttribute("aria-label", `${typeof field.name === "string" ? field.name : "unnamed field"}, sent ${times === 1 ? "once" : times + " times"} in this entry`);
      list.append(item);
    }
    block.append(list); panel.append(block);
  }
  if (observations.length > 16) panel.append(node("p", "detail-note", `${plural(observations.length - 16, "more observation")} in the report JSON.`));
  panel.append(node("p", "detail-note", "Names and repeat counts only. Values, types and file data are never kept. Unknown names are renamed."));
}

function details(route) {
  const panel = $("#detail"); panel.replaceChildren();
  panel.append(node("span", "detail-label", "Route evidence"), node("h3", "", `${route.method || "METHOD UNKNOWN"} ${route.path}`), node("p", "", `${route.confidence.toUpperCase()} · Path parameters and unknown names are conservative aliases.`));
  if (!route.method) panel.append(node("p", "", "The target is known, but its method is unresolved. This is retained as an extension, never invented as an OpenAPI operation."));
  const origins = {same_origin: "Same origin as the page", cross_origin: "Different origin from the page", unknown: "Origin could not be determined"};
  if (route.origin_relations?.length) panel.append(node("h4", "", "Origin context"), node("p", "", route.origin_relations.map(value => origins[value] || origins.unknown).join("; ")));
  if (route.assumptions?.includes("unverified_global_axios")) panel.append(node("h4", "", "Client assumption"), node("p", "", "Axios is assumed from a conventional global name. Its library identity and prior configuration were not verified."));
  panel.append(node("h4", "", "Source locations"));
  const evidence = node("ul");
  for (const entry of route.evidence) evidence.append(node("li", "", `${entry.source.replace("_", " ")} · ${entry.location} · ${entry.kind}${entry.origin_relation ? " · " + (origins[entry.origin_relation] || origins.unknown) : ""}`));
  panel.append(evidence);
  panel.append(node("h4", "", "Query fields"), node("p", "", route.query_fields.join(", ") || "None observed"));
  appendRequestFields(panel, route);
  const hasBodyRows = routeBodyRecords(route).records.length > 0;
  for (const [key, title] of [["request_shapes", "Request JSON shapes"], ["response_shapes", "Response JSON shapes"]]) {
    panel.append(node("h4", "", title));
    panel.append(route[key]?.length ? node("pre", "", JSON.stringify(route[key], null, 2))
      : node("p", "", hasBodyRows ? "No JSON shape kept. See body coverage below for why." : "No JSON shape kept."));
  }
  appendRouteBodies(panel, route);
}

const routeFieldNames = route => (Array.isArray(route.request_fields) ? route.request_fields : [])
  .flatMap(observation => Array.isArray(observation?.fields) ? observation.fields : [])
  .map(field => typeof field?.name === "string" ? field.name.toLowerCase() : "");

function allRoutes(report) {
  return [...report.routes, ...(report.unresolved_operations || [])];
}

function table(focusRoute = null) {
  const rows = $("#route-rows"); rows.replaceChildren();
  const query = $("#search").value.toLowerCase();
  const routes = current ? allRoutes(current.report).filter(route => (filter === "all" || route.confidence === filter)
    && (route.path.toLowerCase().includes(query) || (query && routeFieldNames(route).some(name => name.includes(query))))) : [];
  $("#empty-routes").hidden = routes.length !== 0;
  const all = current ? allRoutes(current.report) : [];
  $("#empty-routes").textContent = !current ? "No evidence yet. Add saved artifacts and analyze them, or explore the example workspace."
    : !all.length ? "This analysis retained no routes. Missing evidence does not prove that an API is absent; review the warnings or add a HAR capture."
    : "No routes match this filter or search.";
  for (const button of document.querySelectorAll("[data-filter]")) {
    const count = all.filter(route => button.dataset.filter === "all" || route.confidence === button.dataset.filter).length;
    let badge = button.querySelector(".filter-count");
    if (!badge) { badge = node("span", "filter-count"); button.append(badge); }
    badge.textContent = current ? String(count) : "";
  }
  for (const route of routes) {
    const row = node("tr", selected === route.id ? "selected" : "");
    if (selected === route.id) row.setAttribute("aria-current", "true");
    const method = node("td"); method.append(node("span", "method " + (route.method || "unknown").toLowerCase(), route.method || "UNKNOWN"));
    const path = node("td", "", route.path);
    const confidence = node("td"); confidence.append(node("span", "confidence " + route.confidence, route.confidence));
    const status = node("td", "", (route.statuses || []).join(", ") || "Unobserved");
    const action = node("td"); const button = node("button", "row-detail", "↗");
    button.setAttribute("aria-label", `Inspect ${route.method || "unknown method"} ${route.path}`);
    button.setAttribute("aria-controls", "detail");
    button.addEventListener("click", () => { selected = route.id; details(route); table(route.id); });
    action.append(button); row.append(method, path, confidence, status, action); rows.append(row);
    // Re-rendering replaces the row; keep keyboard focus on the same route's control.
    if (focusRoute !== null && focusRoute === route.id) button.focus();
  }
}

function show(result, example = false) {
  clearPlan();
  current = result; selected = null;
  const report = result.report;
  const routes = allRoutes(report);
  $("#routes-count").textContent = routes.length;
  const observed = report.routes.filter(route => route.confidence === "observed").length;
  $("#observed-count").textContent = observed;
  $("#inferred-count").textContent = routes.length - observed;
  $("#sources-count").textContent = report.sources.length;
  $("#workspace-mode").textContent = example ? "EXAMPLE WORKSPACE" : "YOUR SAVED ARTIFACTS";
  sourceList(report.sources); showBodyCoverage(report); table();
  if (routes.length) { selected = routes[0].id; details(routes[0]); table(); }
  else emptyDetail("No routes retained", "Nothing to inspect yet", "Supported routes, forms and literal requests appear here with their source locations. A HAR capture usually gives the strongest evidence.");
  const engines = [...new Set((report.javascript_analysis || []).map(item => item.engine))].join(", ");
  const bodies = bodyRows(report).length ? bodyTotals(report) : null;
  notice(`${example ? "Synthetic example loaded." : "Analysis complete."} ${report.routes.length} known-method routes, ${(report.unresolved_operations || []).length} unresolved-method targets. ${engines ? `JavaScript engine: ${engines}. ` : ""}${bodies ? `HAR bodies: ${bodies.inspected} inspected, ${bodies.notInspected} not inspected, ${bodies.notRecorded} not recorded. ` : ""}${report.warnings.length ? `${report.warnings.length} analysis warnings; inspect the report. ` : ""}${publicMode() ? "A public page and selected scripts were fetched; discovered forms and APIs were not called." : "No service was contacted."} Exports discard body and query values.`, false, "success");
  refresh();
}

async function request(path, payload) {
  payload = {javascript_engine:$("#javascript-engine").value, ...payload};
  const body = JSON.stringify(payload), size = new TextEncoder().encode(body).length;
  const limit = path === "/api/project" ? limits.project_request_bytes : ["/api/analyze", "/api/compare", "/api/rebuild"].includes(path) ? limits.request_bytes : limits.source_request_bytes;
  if (size > limit) {
    const error = new Error("Request too large");
    error.safeMessage = `This capture needs ${size.toLocaleString()} encoded JSON bytes; the request limit is ${limit.toLocaleString()}. Analyze fewer files or a smaller comparison.`;
    throw error;
  }
  let response;
  try {
    response = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json", "X-Surface-Token":token}, body, signal:AbortSignal.timeout(path === "/api/public-url" ? limits.public_url.request_timeout_ms : limits.request_timeout_ms)});
  } catch (failure) {
    const error = new Error("Local request unavailable");
    error.safeMessage = failure.name === "TimeoutError" || failure.name === "AbortError"
      ? `The request timed out after ${(path === "/api/public-url" ? limits.public_url.request_timeout_ms : limits.request_timeout_ms) / 1000} seconds. Retry with a smaller selection or check the local server.`
      : "The local server could not be reached. Check that it is running and retry.";
    throw error;
  }
  let result;
  try { result = await response.json(); }
  catch (_) {
    const error = new Error("Response unavailable");
    error.safeMessage = response.status === 503 ? "Analysis is busy; wait for the current request to finish, then try again." : "The local server response could not be read. Retry after checking the server.";
    throw error;
  }
  if (!response.ok) {
    const error = new Error("Request rejected");
    error.safeMessage = typeof result.error === "string" ? result.error : "Request rejected";
    throw error;
  }
  return result;
}

function kind(file) {
  if (codeMode()) {
    const manifests = new Map([["requirements.txt","requirements"], ["pyproject.toml","pyproject"], ["package.json","package"], ["cargo.toml","cargo"]]);
    const filename = file.name.toLowerCase();
    const extensions = new Map([["py","python"], ["ts","typescript"], ["tsx","typescript"], ["js","javascript"], ["jsx","javascript"], ["mjs","javascript"], ["cjs","javascript"], ["rs","rust"], ["sh","shell"], ["bash","shell"]]);
    const dot = filename.lastIndexOf(".");
    return manifests.get(filename) || (dot > 0 ? extensions.get(filename.slice(dot + 1)) : null) || null;
  }
  const dot = file.name.lastIndexOf(".");
  const extension = dot > 0 ? file.name.slice(dot + 1).toLowerCase() : "";
  return ["har", "json"].includes(extension) ? "har" : ["html", "htm"].includes(extension) ? "html" : ["js", "jsx", "mjs", "cjs"].includes(extension) ? "javascript" : ["ts", "tsx"].includes(extension) ? "typescript" : null;
}

async function read(selection) {
  const policy = projectMode() ? limits.project : sourceMode() ? limits.source : limits.web;
  const reject = message => { const error = new Error("Unsupported selection"); error.safeMessage = message; throw error; };
  const maxFiles = policy.max_artifacts || policy.max_files;
  if (!selection.length || selection.length > maxFiles) reject(`Choose between 1 and ${maxFiles} files.`);
  let total = 0;
  for (const file of selection) {
    const format = kind(file);
    if (!format) reject("Choose supported UTF-8 artifacts for the selected analysis mode.");
    const cap = codeMode() ? policy.file_bytes : format === "html" ? policy.html_bytes : policy.other_file_bytes;
    if (file.size > cap) reject(`${format.toUpperCase()} input is ${file.size.toLocaleString()} bytes; its limit is ${cap.toLocaleString()} bytes. Use a smaller artifact.`);
    total += file.size;
  }
  if (total > policy.total_bytes) reject(`Selection contains ${total.toLocaleString()} bytes; the combined limit is ${policy.total_bytes.toLocaleString()} bytes. Analyze fewer files at a time.`);
  const result = [];
  for (const file of selection) {
    const bytes = await file.arrayBuffer();
    const content = new TextDecoder("utf-8", {fatal:true}).decode(bytes);
    result.push(projectMode() ? {kind:kind(file), content, logical_path:file.webkitRelativePath || file.name}
      : sourceMode() ? {kind:kind(file), content} : {kind:kind(file), text_b64:base64Text(content)});
  }
  return result;
}

async function action(task) {
  if (busy) return;
  busy = true; refresh();
  try { await task(); }
  catch (error) { notice(error.safeMessage || "Unable to analyze this selection. Choose supported UTF-8 artifacts or source files within the displayed limits. No source content is included in errors.", true); }
  finally { busy = false; refresh(); }
}

function showDiff(value, review = null, context = null) {
  // Keep the existing normalized keys in downloads while retaining the review
  // and per-side coverage that explain what the comparison could inspect.
  diff = {...value};
  if (isRecordValue(review)) diff.review_signals = review;
  if (isRecordValue(context)) for (const side of ["before", "after"])
    if (isRecordValue(context[side])) diff[side] = context[side];
  $("#diff-panel").hidden = false;
  const unresolved = (value.unresolved_added || []).length + (value.unresolved_removed || []).length + (value.unresolved_changed || []).length;
  $("#diff-summary").textContent = `${value.added.length} added · ${value.removed.length} absent · ${value.changed.length} changed${unresolved ? ` · ${unresolved} unresolved-method changes` : ""}`;
  const list = $("#diff-list"); list.replaceChildren();
  for (const [key, label] of [["added", "Added"], ["removed", "Absent"], ["changed", "Changed"]]) {
    for (const route of value[key]) {
      const row = node("div", "diff-row");
      const absenceLabels = {
        prior_source_kind_not_supplied:"Prior source kind not supplied",
        same_path_unresolved_after:"Same path has an unknown method in new capture",
        same_path_unresolved_before:"Same path had an unknown method in prior capture",
        source_kind_not_supplied_before:"Source kind not supplied in prior capture",
        not_seen_in_prior_capture:"Absent from prior capture",
        scripts_omitted_from_analysis:"Scripts omitted from analysis",
        scripts_omitted_before:"Scripts omitted from prior analysis",
        javascript_engine_changed:"JavaScript engine changed",
        capture_bodies_uninspected_before:"The baseline capture contains bodies that were not inspected",
        capture_bodies_uninspected_after:"The new capture contains bodies that were not inspected",
        not_seen_in_supplied_capture:"Absent from supplied capture"
      };
      const context = (route.absence_context || route.presence_context || route.context || []).map(code => absenceLabels[code]).filter(Boolean).join("; ");
      const detail = [route.changed_fields?.join(", "), context].filter(Boolean).join("; ");
      row.append(node("strong", "", label), node("code", "", `${route.method} ${route.path}`), node("span", "", detail || "Capture evidence only"));
      list.append(row);
    }
  }
  for (const [key, label] of [["unresolved_added", "Added unknown method"], ["unresolved_removed", "Absent unknown method"], ["unresolved_changed", "Changed unknown method"]]) {
    for (const route of value[key] || []) {
      const row = node("div", "diff-row");
      row.append(node("strong", "", label), node("code", "", route.path));
      if (route.changed_fields) row.append(node("span", "", route.changed_fields.join(", ")));
      list.append(row);
    }
  }
  if (!list.children.length) list.append(node("div", "diff-row", "No changes in the normalized evidence."));
  showReviewSignals(review);
}

function clearDiff() {
  diff = null; $("#diff-panel").hidden = true;
  $("#diff-summary").textContent = ""; $("#diff-list").replaceChildren();
  showReviewSignals(null);
}

const SIGNAL_CATEGORIES = [
  ["form_field", "Form fields", "form field"], ["script", "Scripts", "script"], ["stylesheet", "Stylesheets", "stylesheet"],
  ["navigation", "Navigation", "navigation target"], ["origin", "Origins", "origin"], ["tracking", "Tracking calls", "tracking call"],
  ["document_base", "Document base", "document base"], ["endpoint", "Request targets", "request target"]
];
const SIGNAL_STATUS = {
  available:"Static page review complete for the paired HTML and scripts.",
  partial:"Partial static page review. Supported changes remain listed below. Uninspected evidence is unknown; this is not an unchanged or passing result."
};
const SIGNAL_REASONS = {
  unpaired_artifact_kinds:"Static page review did not run: the two captures do not contain the same kinds of files in the same order.",
  har_only_capture:"Static page review does not apply: these captures contain only HAR files, which are compared above as observed routes.",
  document_context_mismatch:"Static page review is unavailable: a page URL was supplied for only one side.",
  unsupported_or_limit:"Static page review is unavailable: an input was unsupported or reached a limit."
};
const SIGNAL_WARNINGS = {
  javascript_syntax_analysis_unavailable:"JavaScript syntax analysis was unavailable, so script-based hints are partial",
  navigation_static_coverage_partial:"Some navigation could not be resolved statically",
  navigation_target_unresolved:"A navigation target could not be resolved",
  global_client_identity_uncertain:"A global client could not be identified with certainty",
  global_tracking_identity_uncertain:"A global tracking client could not be identified with certainty",
  global_navigation_identity_uncertain:"A global navigation helper could not be identified with certainty",
  html_uninspected:"An HTML source was not inspected",
  inline_javascript_uninspected:"Inline JavaScript was not inspected",
  document_context_uninspected:"Document context was not inspected",
  javascript_uninspected:"A script source was not inspected",
  review_record_limit:"A review record limit was reached",
  html_static_coverage_partial:"Some HTML declarations could not be fully inspected",
  tracking_static_coverage_partial:"Some script-based hints could not be fully inspected",
  review_unavailable:"The review could not run"
};
const SCOPE_NOTES = {
  document_path_unknown:"The page URL is unknown, so relative links are compared as written",
  first_party_origin_unknown:"No site origin was supplied, so origins are not classed as first or third party",
  scheme_relative_origin_unclassified:"Scheme-relative origins are not classified",
  forms_aggregated_by_target:"Form fields are grouped by form target",
  template_content_excluded:"Template content is excluded",
  non_http_references_excluded:"Non-HTTP references are excluded",
  tracking_static_syntax_only:"Tracking calls are found from static syntax only",
  tracking_wrappers_and_aliases_not_resolved:"Tracking wrappers and aliases are not resolved"
};
const INPUT_TYPES = new Set("text email password number checkbox radio submit button file search tel url date datetime-local time month week color range reset image textarea select unknown".split(" "));
const SIGNAL_APIS = new Set("gtag_event posthog_capture analytics_track mixpanel_track fbq_track fbq_track_custom data_layer_event navigator_send_beacon gtag_config fbq_init analytics_page location_assign location_replace location_href location_assignment window_open".split(" "));
const ATTRIBUTE_TEXT = {
  input_type:value => value === "hidden" ? "hidden input" : value === "mixed" ? "mixed input types" : INPUT_TYPES.has(value) ? `${value} input` : "input",
  asset_kind:value => ({script:"script", stylesheet:"stylesheet", link:"link", "embedded-document":"embedded document"})[value] || "",
  origin_relation:value => ({same_origin:"same origin", cross_origin:"different origin", relative:"relative", unknown:"origin not classified"})[value] || "",
  method:value => /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)$/.test(value) ? value : "",
  api:value => SIGNAL_APIS.has(value) ? value.replaceAll("_", " ") : "other call"
};
const OPAQUE_ID = /^(signal|origin|target|path|field|event)_[1-9][0-9]{0,5}$/;
const opaque = value => typeof value === "string" && OPAQUE_ID.test(value) ? value : null;

function signalAttributes(row) {
  const parts = [];
  for (const attributes of (Array.isArray(row.attributes) ? row.attributes : []).filter(isRecordValue)) {
    for (const [key, render] of Object.entries(ATTRIBUTE_TEXT)) {
      if (typeof attributes[key] === "string") { const text = render(attributes[key]); if (text && !parts.includes(text)) parts.push(text); }
    }
  }
  return parts;
}

function signalEvidence(row, side) {
  const where = rows => (Array.isArray(rows) ? rows : []).filter(isRecordValue).slice(0, 3)
    .map(entry => `${sourceName(entry.source)} · ${locationName(entry.location)}`).join("; ");
  if (side === "changed") return [where(row.before_evidence) && `Baseline: ${where(row.before_evidence)}`, where(row.evidence) && `New: ${where(row.evidence)}`].filter(Boolean).join(". ");
  const text = where(row.evidence);
  return text ? `${side === "removed" ? "Baseline" : "New"}: ${text}` : "";
}

function signalRow(row, change, noun) {
  const item = node("li", "signal-row");
  const refs = ["field_id", "target_id", "origin_id", "path_id", "event_id"].map(key => opaque(row[key])).filter(Boolean);
  const reference = opaque(row.id);
  const attributes = signalAttributes(row);
  const times = count(row.count);
  item.append(node("span", "signal-change " + change, {added:"Added", removed:"Absent", changed:"Changed"}[change]));
  const body = node("span", "signal-body");
  body.append(node("span", "", `${attributes.length ? attributes.join(", ") : noun}${times > 1 ? ` · ${times} occurrences` : ""}`));
  const ids = [reference, ...refs].filter((value, index, all) => value && all.indexOf(value) === index);
  body.append(node("code", "signal-ref", ids.length ? ids.join(" · ") : "reference withheld"));
  const evidence = signalEvidence(row, change);
  if (evidence) body.append(node("span", "provenance", evidence));
  item.append(body);
  return item;
}

function showReviewSignals(review) {
  const panel = $("#review-signals"), groups = $("#review-groups"), notes = $("#review-notes");
  groups.replaceChildren(); notes.replaceChildren();
  for (const id of ["review-quiet", "review-withheld", "review-summary", "review-status"]) $("#" + id).textContent = "";
  panel.hidden = !isRecordValue(review);
  if (!isRecordValue(review)) return;
  const status = review.status;
  const banner = $("#review-status");
  banner.className = "review-status" + (status === "available" ? "" : " partial");
  if (status === "available" || status === "partial") banner.textContent = SIGNAL_STATUS[status];
  else banner.textContent = label(SIGNAL_REASONS, review.reason, status === "not_applicable" ? "Static page review does not apply to these captures." : "Static page review is unavailable for these captures.") + " This is not an unchanged or passing result.";
  const comparison = isRecordValue(review.comparison) ? review.comparison : null;
  const coverage = isRecordValue(review.coverage) ? review.coverage : {};
  if (!comparison || !(status === "available" || status === "partial")) { $("#review-summary").textContent = ""; return; }
  const incomplete = new Set();
  for (const source of (Array.isArray(coverage.incomplete_sources) ? coverage.incomplete_sources : []).filter(isRecordValue))
    for (const category of Array.isArray(source.categories) ? source.categories : []) incomplete.add(category);
  if (status === "partial" && !incomplete.size) for (const [key, value] of [["tracking", coverage.tracking_analysis], ["navigation", coverage.javascript_navigation_analysis]]) if (value === "unavailable_or_partial") incomplete.add(key);
  let total = 0; const quiet = [], incompleteNames = [];
  const known = new Set(SIGNAL_CATEGORIES.map(([key]) => key));
  const extra = Object.keys(comparison).filter(key => !known.has(key)).map(key => [key, "Other static signals", "signal"]);
  for (const [key, title, noun] of [...SIGNAL_CATEGORIES, ...extra]) {
    const group = comparison[key];
    if (!isRecordValue(group)) continue;
    // The backend excludes only affected sources and JavaScript scopes. A
    // category can still contain independent supported HTML/source changes.
    if (incomplete.has(key)) incompleteNames.push(title);
    const changes = ["added", "removed", "changed"].map(change => [change, (Array.isArray(group[change]) ? group[change] : []).filter(isRecordValue)]);
    const size = changes.reduce((sum, [, rows]) => sum + rows.length, 0);
    if (!size) { if (status === "available" && !incomplete.has(key)) quiet.push(title); continue; }
    total += size;
    const details = node("details", "signal-group"); details.open = true;
    const summary = node("summary");
    const [added, removed, changed] = changes.map(([, rows]) => rows.length);
    summary.append(node("span", "signal-title", title), node("span", "signal-counts", `${added} added · ${removed} absent · ${changed} changed${incomplete.has(key) ? " · partial coverage" : ""}`));
    const list = node("ul", "signal-list");
    for (const [change, rows] of changes) for (const row of rows.slice(0, 50)) list.append(signalRow(row, change, noun));
    details.append(summary, list);
    if (size > 50 * 3 || changes.some(([, rows]) => rows.length > 50)) details.append(node("p", "detail-note", "Some rows are not listed. Use the comparison JSON for the full set."));
    groups.append(details);
  }
  $("#review-summary").textContent = `${plural(total, "supported page change")} beyond routes${incompleteNames.length ? ` · ${incompleteNames.length} categories with incomplete coverage` : ""}`;
  if (incompleteNames.length) $("#review-withheld").textContent = `Coverage is incomplete for: ${incompleteNames.join(", ")}. Affected source evidence is withheld; supported changes remain listed.`;
  if (quiet.length) $("#review-quiet").textContent = `No differences in the inspected static source for: ${quiet.join(", ")}.`;
  const excluded = (Array.isArray(coverage.excluded_sources) ? coverage.excluded_sources : []).filter(isRecordValue);
  if (excluded.length) notes.append(node("li", "", `${plural(excluded.length, "HAR source")} (${excluded.map(item => sourceName(item.source)).join(", ")}) compared above as observed routes, not here.`));
  for (const note of Array.isArray(coverage.scope_notes) ? coverage.scope_notes : []) if (Object.hasOwn(SCOPE_NOTES, note)) notes.append(node("li", "", SCOPE_NOTES[note] + "."));
  const warnings = (Array.isArray(coverage.warnings) ? coverage.warnings : []).filter(isRecordValue);
  const warningText = new Map();
  for (const warning of warnings) { const text = label(SIGNAL_WARNINGS, warning.reason, "A source could not be fully compared"); warningText.set(text, (warningText.get(text) || 0) + 1); }
  for (const [text, value] of warningText) notes.append(node("li", "", `${text}${value > 1 ? ` (${value} times)` : ""}.`));
  notes.append(node("li", "", "IDs such as field_2 or origin_1 are private to this comparison. Original names, domains and values are not shown."));
}

function reviewNotice(review) {
  if (!isRecordValue(review)) return "";
  return review.status === "available" ? " Static page review complete." : review.status === "partial" ? " Static page review is partial." : " Static page review did not run.";
}

function download(value, suffix) {
  downloadText(JSON.stringify(value, null, 2), suffix, "json", "application/json");
}

function downloadText(value, suffix, extension, mime) {
  const url = URL.createObjectURL(new Blob([value], {type:mime}));
  const link = node("a"); link.href = url; link.download = `surface-map-${suffix}.${extension}`; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function updateHandoff() {
  $("#brief-status").textContent = "";
  $("#agent-brief").value = "";
  $("#plan-explanation").textContent = "";
  $("#explanation-status").textContent = "";
  $("#explanation-detail").textContent = "";
  if (!rebuildCurrent) return;
  const style = $("#brief-style").value;
  try {
    const explanation = SurfaceMapHandoff.explain(rebuildCurrent.plan, style).split("\n\n");
    $("#plan-explanation").textContent = explanation.slice(0, 2).join("\n\n");
    $("#explanation-detail").textContent = explanation.slice(2).join("\n\n");
    const coverage = rebuildCurrent.plan.coverage || {};
    const status = [`Partial evidence: ${(coverage.warnings || []).length} analysis warnings; ${(coverage.capture_warnings || []).length} capture warnings. Review the evidence notes before implementation.`];
    if ((coverage.javascript_analysis || []).some(row => row.engine === "basic")) status.push("JavaScript uses basic lexical hints; syntax and scope are not validated.");
    if (!coverage.origin_classification_available) status.push("Origin scope remains unclassified.");
    if ((coverage.script_omissions || []).length) status.push(`${coverage.script_omissions.length} fetched scripts were omitted from analysis.`);
    if (coverage.reference_inventory_omission) status.push("String and module references were not inventoried.");
    if ([...(rebuildCurrent.plan.operations || []), ...(rebuildCurrent.plan.unresolved || [])].some(row => row.assumptions?.length)) status.push("Client identity or configuration assumptions need verification.");
    $("#explanation-status").textContent = status.join(" ");
    $("#agent-brief").value = SurfaceMapHandoff.markdown(rebuildCurrent.plan, style);
  } catch (_) {
    $("#brief-status").textContent = "The brief could not be prepared within its limits. Download Plan JSON to review the full retained plan.";
  }
}

function clearPlan() {
  if (rebuildCurrent && !codeMode()) $("#workspace-mode").textContent = "WEB ARTIFACT INSPECTION";
  rebuildCurrent = null;
  rebuildExample = false;
  publicRoutesShown = false;
  $("#plan-panel").hidden = true;
  $("#agent-brief").value = "";
  $("#plan-explanation").textContent = "";
  $("#explanation-status").textContent = "";
  $("#explanation-detail").textContent = "";
  $("#explanation-notes").open = false;
  $("#brief-status").textContent = "";
  $("#brief-preview-details").open = false;
  for (const selector of [".stats-grid", ".evidence-flow", ".map-panel", ".bottom-grid"]) $(selector).hidden = codeMode();
  for (const id of ["plan-summary", "plan-unknowns", "plan-operations", "plan-candidates", "plan-capture-guide", "plan-coverage", "plan-scope"]) $("#" + id).replaceChildren();
}

function showPlan(result, example = false) {
  rebuildCurrent = result;
  rebuildExample = example;
  rebuildEngine = $("#javascript-engine").value;
  const plan = result.plan, operations = plan.operations || [];
  const candidates = plan.candidate_references?.candidates || [];
  $("#plan-panel").hidden = false;
  $("#workspace-mode").textContent = example ? "EXAMPLE AGENT BRIEF" : publicMode() ? "PUBLIC PAGE EVIDENCE" : "YOUR REBUILD PLAN";
  for (const selector of [".stats-grid", ".evidence-flow", ".map-panel", ".bottom-grid"]) $(selector).hidden = true;
  const stubCount = operations.filter(operation => operation.stub === "not_implemented").length;
  const label = (count, name) => `${count} ${name}${count === 1 ? "" : "s"}`;
  const omitted = plan.coverage || {};
  const groups = [label(operations.length, "operation group"), ...(!publicMode() ? [label(stubCount, "planned stub")] : []), label((plan.pages || []).length, "page reference"), label((plan.unresolved || []).length, "unresolved target"), label(candidates.length, "string candidate")];
  $("#plan-summary").textContent = `${groups.join(" · ")}. Omitted by plan limits: ${label(omitted.omitted_operations || 0, "operation group")}, ${label(omitted.omitted_pages || 0, "page reference")}, ${label(omitted.omitted_unresolved_targets || 0, "unresolved target")}.`;
  $("#plan-unknowns").replaceChildren();
  for (const item of plan.unknowns || []) $("#plan-unknowns").append(node("li", "", item));
  const rows = $("#plan-operations"); rows.replaceChildren();
  for (const operation of operations) {
    const row = node("div", "inventory-row");
    row.append(node("code", "", `${operation.method || "UNKNOWN"} ${operation.path}`),
      node("span", "inventory-confidence", operation.confidence || "unverified"),
      node("span", "inventory-ecosystem", operation.stub === "not_implemented" ? publicMode() ? "Behavior unverified" : "Not implemented" : "Scope review required"));
    row.append(node("p", "", (operation.evidence || []).map(item => `${item.source.replace("_", " ")} · ${item.location}`).join("; ") || "No source location retained."));
    if (operation.assumptions?.includes("unverified_global_axios")) row.append(node("p", "", "Client assumption: global Axios identity and prior configuration were not verified."));
    rows.append(row);
  }
  if (!operations.length) rows.append(node("p", "source-empty", "No known-method operations were retained. A string candidate alone does not define an operation."));
  $("#plan-candidate-summary").textContent = `String references (${candidates.length})`;
  const candidateRows = $("#plan-candidates"); candidateRows.replaceChildren();
  for (const candidate of candidates) {
    const row = node("div", "inventory-row");
    row.append(node("code", "", candidate.path), node("span", "inventory-confidence", "String candidate"),
      node("p", "", (candidate.evidence || []).map(item => `${item.source.replace("_", " ")} · ${item.location}`).join("; ")));
    candidateRows.append(row);
  }
  if (!candidates.length) candidateRows.append(node("p", "source-empty", "No supported strings were retained."));
  const guide = $("#plan-capture-guide"); guide.replaceChildren();
  for (const step of plan.capture_guide || []) guide.append(node("li", "", step));
  const coverage = $("#plan-coverage"); coverage.replaceChildren();
  coverage.append(node("pre", "plan-coverage-json", JSON.stringify(plan.coverage || {}, null, 2)));
  if (plan.candidate_references?.limits?.truncated) coverage.append(node("p", "source-empty", "Some candidate references exceeded a limit or could not be resolved without a document base. Inspect the plan JSON for omission counts."));
  $("#plan-scope").textContent = plan.scope || "Reconstruction still requires your implementation and verification.";
  updateHandoff();
  notice(publicMode() ? "Public-page capture ready. Review capture limits and the brief before using it. A page and selected scripts were fetched; forms and APIs were not called." : "Plan ready. Choose an explanation style, review the agent brief, and copy or download it. Check the unknowns before implementing behavior.", false, "success");
  refresh();
}

function showSource(inventory, example = false) {
  sourceCurrent = inventory;
  $("#workspace-mode").textContent = example ? "SYNTHETIC SOURCE EXAMPLE" : "STATIC SOURCE EVIDENCE";
  $("#language-count").textContent = inventory.languages.length;
  $("#dependency-count").textContent = inventory.dependencies.length;
  $("#capability-count").textContent = inventory.capabilities.length;
  sourceList(inventory.sources);
  const container = $("#source-inventory"); container.replaceChildren();
  for (const [key, title] of [["languages","Languages and formats"], ["dependencies","Known dependencies"], ["capabilities","Library capability hints"], ["install_hints","Installer and entry-point hints"]]) {
    const section = node("section", "source-section");
    section.append(node("h3", "", title));
    if (!inventory[key].length) section.append(node("p", "source-empty", "No supported evidence in the selection."));
    for (const entry of inventory[key]) {
      const row = node("div", "inventory-row");
      const name = node("strong", "", entry.name);
      row.append(name, node("span", "inventory-confidence", entry.confidence || "static"));
      if (entry.ecosystem) row.append(node("span", "inventory-ecosystem", entry.ecosystem));
      const evidence = node("p", "", entry.evidence.map(item => `${item.source.replace("_", " ")} · ${item.location}`).join("; "));
      row.append(evidence); section.append(row);
    }
    container.append(section);
  }
  if (inventory.warnings.length) {
    const section = node("section", "source-section"); section.append(node("h3", "", "Unsupported or omitted evidence"));
    for (const warning of inventory.warnings) section.append(node("p", "source-empty", warning));
    container.append(section);
  }
  const rejected = inventory.sources.filter(item => item.status === "rejected").length;
  const engines = [...new Set(inventory.sources.map(item => item.engine).filter(Boolean))].join(", ");
  notice(`${example ? "Synthetic example." : "Static inspection complete."} ${inventory.sources.length} logical sources, ${inventory.dependencies.length} known dependency labels. ${engines ? `JavaScript engine: ${engines}. ` : ""}${rejected ? `${rejected} sources were rejected; inspect the warnings. ` : ""}No source code was executed, dependency installed, or service contacted. Declarations and imports do not prove runtime behavior.`, false, rejected ? "" : "success");
  refresh();
}

function showProject(inventory) {
  showSource(inventory);
  $("#workspace-mode").textContent = "SELECTED PROJECT EVIDENCE";
  $("#project-graph").hidden = false;
  const graph = inventory.graph || {}, edges = graph.edges || [];
  $("#project-graph-summary").textContent = `${edges.length} candidate links between explicitly selected files. A link does not prove runtime loading.`;
  const list = $("#project-graph-list"); list.replaceChildren();
  const relationNames = {static_import:"Literal import", static_reexport:"Literal re-export", dynamic_import_literal:"Literal dynamic import"};
  for (const edge of edges) {
    const row = node("div", "inventory-row");
    row.append(node("code", "", `${edge.source.replace("_", " ")} → ${edge.target.replace("_", " ")}`),
      node("span", "inventory-confidence", relationNames[edge.relation] || "Candidate link"),
      node("p", "", `Source line ${edge.line}`));
    list.append(row);
  }
  if (!edges.length) list.append(node("p", "source-empty", "No supported literal imports matched another selected file. Missing files and unsupported syntax can leave gaps."));
  const omissions = $("#project-omission-list"); omissions.replaceChildren();
  const omissionNames = {outside_root:"Outside the selected root", missing_selected_target:"Target file not selected", external_or_package:"External or package reference", unsupported_reference:"Unsupported reference", edge_limit:"Link limit reached", unparsed_sources:"Unparsed sources", work_limit:"Sources beyond the work limit", inventory_evidence_limit:"Inventory evidence limit reached", superseded_evidence:"Lower-confidence evidence replaced", warning_limit:"Warning limit reached"};
  for (const [category, value] of Object.entries({...inventory.omissions, ...graph.omissions})) {
    if (value) omissions.append(node("p", "source-empty", `${omissionNames[category] || "Other omitted evidence"}: ${value}`));
  }
  if (!omissions.children.length) omissions.append(node("p", "source-empty", "No counted omissions. Unsupported syntax may still be absent without a count."));
  $("#project-graph-scope").textContent = graph.scope || "Only relationships between supplied files are considered.";
  notice(`Project inspection complete. ${inventory.sources.length} selected files, ${edges.length} candidate file links.${inventory.coverage?.partial ? " Coverage is partial; inspect omissions." : ""} Input names are used transiently for matching; results contain generic source IDs. No code was run.`, false, "success");
  refresh();
}

function showSourceDiff(value) {
  sourceDiff = value;
  $("#source-diff").hidden = false;
  const list = $("#source-diff-list"); list.replaceChildren();
  let count = 0;
  for (const [group, changes] of Object.entries(value.groups)) {
    for (const [change, entries] of Object.entries(changes)) {
      for (const entry of entries) {
        const label = change === "added" ? "Added" : change === "changed" ? "Evidence changed" : "Absent";
        list.append(node("p", "", `${label} ${group.replace("_", " ")}: ${entry.name}${entry.ecosystem ? ` (${entry.ecosystem})` : ""}`));
        count++;
      }
    }
  }
  if (!count) list.append(node("p", "", "No change in the minimized static labels."));
  list.append(node("p", "source-empty", value.note));
}

function clearSourceView() {
  sourceCurrent = null; sourceDiff = null;
  for (const name of ["language", "dependency", "capability"]) $("#" + name + "-count").textContent = "0";
  $("#source-inventory").replaceChildren(node("p", "source-empty", "Select manifests or source files, then analyze."));
  $("#source-diff").hidden = true; $("#source-diff-list").replaceChildren();
  $("#project-graph").hidden = true; $("#project-graph-list").replaceChildren();
  $("#project-omission-list").replaceChildren(); $("#project-graph-summary").textContent = ""; $("#project-graph-scope").textContent = "";
}

function resetWorkspace(clearURL = true) {
  files=[]; baselineFiles=[]; current=null; diff=null; selected=null;
  $("#files").value="";
  if (clearURL) $("#public-url").value="";
  $("#source-list").replaceChildren(); clearDiff(); showBodyCoverage(null);
  for (const id of ["routes", "observed", "inferred", "sources"]) $("#"+id+"-count").textContent="0";
  $("#workspace-mode").textContent = modeLabel();
  emptyDetail("Session cleared", "Nothing selected", "Add evidence in the inputs panel, or explore an example. The server does not retain uploads or reports.");
  $("#baseline-status").textContent="No baseline kept. All files and reports have been cleared from this workspace.";
  clearSourceView(); clearPlan(); table(); refresh();
}

for (const id of ["public-url", "url-include-scripts"]) $("#" + id).addEventListener(id === "public-url" ? "input" : "change", () => {
  resetWorkspace(false);
  notice("Public-page selection ready. Inspecting fetches the page and selected scripts; it does not contact discovered forms or APIs.");
});
$("#public-url-form").addEventListener("submit", event => {
  event.preventDefault();
  action(async () => {
    if (!publicMode()) return;
    const url = $("#public-url").value.trim(), includeScripts = $("#url-include-scripts").checked;
    if (!url) return;
    resetWorkspace(false);
    notice("Fetching a bounded public HTTPS page and selected external scripts. Source code is not executed; discovered forms and APIs are not called.");
    const result = await request("/api/public-url", {url, include_scripts:includeScripts});
    show(result);
    showPlan(result);
    $("#plan-heading").focus();
  });
});
$("#view-public-routes").addEventListener("click", () => {
  if (!publicMode() || !current || !rebuildCurrent) return;
  publicRoutesShown = !publicRoutesShown;
  for (const selector of [".stats-grid", ".evidence-flow", ".map-panel", ".bottom-grid"]) $(selector).hidden = !publicRoutesShown;
  refresh();
});

$("#files").addEventListener("change", () => {
  clearPlan();
  clearSourceView();
  files = Array.from($("#files").files); current = null; selected = null;
  clearDiff(); showBodyCoverage(null);
  for (const id of ["routes", "observed", "inferred", "sources"]) $("#"+id+"-count").textContent = "0";
  $("#workspace-mode").textContent = modeLabel();
  emptyDetail("Selection ready", "Selection awaiting analysis", "Analyze artifacts to map routes, or build a plan for an agent brief. Nothing is sent until you choose an action.");
  sourceList(files.map((file,index) => ({id:`source_${index + 1}`,kind:kind(file) || "unsupported",bytes:file.size})));
  table(); notice("Selection ready. Analyze artifacts to build a fresh evidence map."); refresh();
});
$("#analyze").addEventListener("click", () => action(async () => {
  clearPlan();
  const selectedFiles = await read(files);
  const result = await request(projectMode() ? "/api/project" : sourceMode() ? "/api/source" : "/api/analyze",
    projectMode() ? {files:selectedFiles} : {artifacts:selectedFiles});
  if (projectMode()) showProject(result.inventory); else if (sourceMode()) showSource(result.inventory); else show(result);
}));
$("#build-plan").addEventListener("click", () => action(async () => {
  clearPlan();
  notice("Building a plan from your selected saved artifacts. Candidates remain separate from operation stubs.");
  showPlan(await request("/api/rebuild", {artifacts:await read(files)}));
}));
$("#demo-plan").addEventListener("click", () => action(async () => {
  clearPlan();
  files=[]; baselineFiles=[]; current=null; diff=null; selected=null; $("#files").value="";
  clearSourceView(); showBodyCoverage(null); clearDiff();
  $("#baseline-status").textContent="No baseline kept. Select and analyze artifacts to compare evidence.";
  const result = await request("/api/rebuild-demo", {});
  sourceList(result.plan.coverage.sources || []);
  showPlan(result, true);
}));
$("#demo").addEventListener("click", () => action(async () => {
  clearPlan();
  files=[]; baselineFiles=[]; current=null; diff=null; selected=null; $("#files").value="";
  clearSourceView(); showBodyCoverage(null); clearDiff();
  $("#baseline-status").textContent="No baseline kept. Select and analyze artifacts to compare evidence.";
  const result = await request(sourceMode() ? "/api/source-demo" : "/api/demo", {});
  if (sourceMode()) showSource(result.inventory, true); else show(result, true);
}));
$("#baseline").addEventListener("click", () => { baselineFiles=files.slice(); $("#baseline-status").textContent=`Baseline kept: ${baselineFiles.length} artifacts in browser memory. Import a second capture, analyze it, then compare.`; refresh(); });
$("#compare").addEventListener("click", () => action(async () => {
  const result = await request(sourceMode() ? "/api/source-compare" : "/api/compare", {baseline:await read(baselineFiles),candidate:await read(files),javascript_engine:$("#javascript-engine").value});
  if (sourceMode()) showSourceDiff(result.diff);
  else { showDiff(result.diff, result.review_signals, result); notice(`Comparison ready. ${$("#diff-summary").textContent}.${reviewNotice(result.review_signals)}`, false, "success"); }
}));
$("#demo-diff").addEventListener("click", () => action(async () => {
  const result = await request("/api/demo-diff", {});
  showDiff(result.diff, result.review_signals, result);
  notice(`Example comparison ready. ${$("#diff-summary").textContent}.${reviewNotice(result.review_signals)}`, false, "success");
}));
for (const button of document.querySelectorAll("[data-filter]")) button.addEventListener("click", () => {filter=button.dataset.filter; document.querySelectorAll("[data-filter]").forEach(item => { item.classList.toggle("selected", item===button); item.setAttribute("aria-pressed", String(item===button)); }); table();});
$("#search").addEventListener("input", table);
for (const name of ["report","openapi","contract"]) $("#export-"+name).addEventListener("click", () => { if(current) download(current[name], name); });
$("#export-diff").addEventListener("click", () => { if(diff) download(diff,"diff"); });
$("#export-source").addEventListener("click", () => { if(sourceCurrent) download(sourceCurrent,projectMode() ? "project-inventory" : "source-inventory"); });
$("#export-plan").addEventListener("click", () => { if(rebuildCurrent) download(rebuildCurrent.plan,"rebuild-plan"); });
$("#brief-style").addEventListener("change", () => { updateHandoff(); refresh(); });
$("#export-brief").addEventListener("click", () => {
  if (rebuildCurrent && $("#agent-brief").value) downloadText($("#agent-brief").value, "agent-brief", "md", "text/markdown;charset=utf-8");
});
$("#copy-brief").addEventListener("click", () => action(async () => {
  const text = $("#agent-brief").value;
  if (!rebuildCurrent || !text) return;
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
    await navigator.clipboard.writeText(text);
    $("#brief-status").textContent = "Agent brief copied. Review it before sharing it with your agent.";
  } catch (_) {
    $("#brief-preview-details").open = true;
    $("#agent-brief").focus();
    $("#agent-brief").select();
    $("#brief-status").textContent = "Clipboard access is unavailable. The brief is selected below; use your device's copy command or download the Markdown file.";
  }
}));
$("#export-scaffold").addEventListener("click", () => action(async () => {
  if (!rebuildCurrent || publicMode()) return;
  if (!rebuildCurrent.files) {
    const input = rebuildExample ? {} : {artifacts:await read(files)};
    const result = await request(rebuildExample ? "/api/rebuild-demo" : "/api/rebuild", {...input, javascript_engine:rebuildEngine, include_scaffold:true});
    if (JSON.stringify(result.plan) !== JSON.stringify(rebuildCurrent.plan)) {
      const example = rebuildExample, engine = rebuildEngine;
      showPlan(result, example);
      rebuildEngine = engine;
      notice("The plan changed while preparing mock files. Review the updated evidence and copy or download the brief again; earlier copies may be out of date. Then select Mock files again to download.");
      return;
    }
    rebuildCurrent.files = result.files;
  }
  download(rebuildCurrent.files, "mock-bundle");
}));
$("#export-source-diff").addEventListener("click", () => { if(sourceDiff) download(sourceDiff,"source-comparison"); });
$("#clear").addEventListener("click", () => { resetWorkspace(); notice("Session cleared. The server does not retain uploads or reports."); });
$("#analysis-mode").addEventListener("change", () => {
  $("#clear").click();
  const source = codeMode(), project = projectMode();
  for (const selector of [".stats-grid", ".evidence-flow", ".map-panel", ".bottom-grid"]) $(selector).hidden = source;
  $("#source-panel").hidden = !source;
  $("#source-heading").textContent = project ? "Selected project inventory" : "Static source inventory";
  $("#source-description").textContent = project ? "Inspect selected files and literal links between them. No code is run." : "Declared dependencies and inferred import hints, with generic source locations. No code is run.";
  $("#project-input-note").hidden = !project;
  $("#upload-title").textContent = project ? "Add selected project files" : source ? "Add selected source files" : "Add saved artifacts";
  $("#upload-formats").textContent = source ? "Manifests, Python, JS/TS, Rust or shell" : "HTML, HAR or JavaScript";
  $("#files").accept = source ? ".txt,.toml,.json,.py,.ts,.tsx,.js,.jsx,.mjs,.cjs,.rs,.sh,.bash" : ".html,.htm,.har,.json,.js,.jsx,.mjs,.cjs,.ts,.tsx";
  $("#workspace-mode").textContent = modeLabel();
  notice(project ? "Select individual project files to inspect. Literal relative imports can match another selected file; nested projects work best through the CLI with an explicit root." : source ? "Select saved requirements.txt, pyproject.toml, package.json, Cargo.toml, or supported source files. No GitHub fetch, execution or dependency installation occurs." : publicMode() ? "Enter a public HTTPS page URL. Requests begin only when you select Inspect page + create brief. No login, browser rendering, form submission or API testing occurs." : "Select saved web artifacts, or load the synthetic example. Build a plan creates a mock starting point from your selected files.");
  refresh();
});
// Startup needs a valid limits contract before any input or analysis control is
// enabled. The request and its body share one bounded deadline; a late or
// superseded response is discarded rather than applied.
const BOOTSTRAP_TIMEOUT_MS = 15000, BOOTSTRAP_RESPONSE_CHARS = 65536, MAX_CLIENT_TIMEOUT_MS = 600000;
let bootstrapAttempt = 0;
const isRecord = value => value !== null && typeof value === "object" && !Array.isArray(value);
const isCount = value => Number.isSafeInteger(value) && value > 0;
const isDeadline = value => isCount(value) && value <= MAX_CLIENT_TIMEOUT_MS;
function validLimits(value) {
  if (!isRecord(value) || !["web", "source", "project", "public_url"].every(key => isRecord(value[key]))) return false;
  const {web, source, project} = value;
  return [value.request_bytes, value.source_request_bytes, value.project_request_bytes,
    web.max_artifacts, web.html_bytes, web.other_file_bytes, web.total_bytes,
    source.max_artifacts || source.max_files, source.file_bytes, source.total_bytes,
    project.max_artifacts || project.max_files, project.file_bytes, project.total_bytes].every(isCount)
    && isDeadline(value.request_timeout_ms) && isDeadline(value.public_url.request_timeout_ms);
}
async function loadLimits() {
  const controller = new AbortController();
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      const error = new Error("Input limits timed out"); error.name = "TimeoutError"; reject(error);
    }, BOOTSTRAP_TIMEOUT_MS);
  });
  const work = (async () => {
    const response = await fetch("/api/limits", {cache:"no-store", signal:controller.signal});
    if (!response.ok) throw new Error("Input limits unavailable");
    const text = await response.text();
    if (text.length > BOOTSTRAP_RESPONSE_CHARS) throw new Error("Input limits unavailable");
    const value = JSON.parse(text);
    if (!validLimits(value)) throw new Error("Input limits unavailable");
    return value;
  })();
  work.catch(() => {});
  try { return await Promise.race([work, deadline]); }
  finally { clearTimeout(timer); controller.abort(); }
}
function bootstrap() {
  return action(async () => {
    const attempt = ++bootstrapAttempt;
    notice("Loading input limits and the synthetic example. No requests are made to the mapped service.");
    let loaded;
    try { loaded = await loadLimits(); }
    catch (failure) {
      if (attempt !== bootstrapAttempt || limits) return;
      $("#upload-limits").textContent = "Input limits unavailable.";
      $("#retry-bootstrap").hidden = false;
      const error = new Error("Input limits unavailable");
      error.safeMessage = failure.name === "TimeoutError"
        ? `Input limits did not load within ${BOOTSTRAP_TIMEOUT_MS / 1000} seconds. File analysis stays unavailable until they load. Check that the local server is running, then select Retry.`
        : "Input limits could not load or were not in the expected format. File analysis stays unavailable until they load. Check the local server, then select Retry.";
      throw error;
    }
    if (attempt !== bootstrapAttempt || limits) return;
    limits = loaded;
    $("#retry-bootstrap").hidden = true;
    refresh();
    try { show(await request("/api/demo", {}), true); }
    catch (failure) {
      $("#workspace-mode").textContent = modeLabel();
      table();
      const error = new Error("Example unavailable");
      error.safeMessage = `Input limits loaded, but the synthetic example did not. ${failure.safeMessage || ""} You can add your own files, or select Explore example workspace to try the example again.`.replace(/\s+/g, " ");
      throw error;
    }
  });
}
$("#retry-bootstrap").addEventListener("click", async () => {
  if (busy || limits) return;
  await bootstrap();
  (limits ? $("#report") : $("#retry-bootstrap")).focus();
});
bootstrap();
