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

function notice(message, error = false) {
  $("#notice").textContent = message;
  $("#notice").classList.toggle("error", error);
}

function refresh() {
  const source = codeMode(), project = projectMode(), active = source ? sourceCurrent : current;
  const publicPage = publicMode();
  const blocked = busy || !limits;
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

function details(route) {
  const panel = $("#detail"); panel.replaceChildren();
  panel.append(node("span", "detail-label", "ROUTE EVIDENCE"), node("h3", "", `${route.method || "METHOD UNKNOWN"} ${route.path}`), node("p", "", `${route.confidence.toUpperCase()} · Path parameters and unknown names are conservative aliases.`));
  if (!route.method) panel.append(node("p", "", "The target is known, but its method is unresolved. This is retained as an extension, never invented as an OpenAPI operation."));
  const origins = {same_origin: "Same origin as the page", cross_origin: "Different origin from the page", unknown: "Origin could not be determined"};
  if (route.origin_relations?.length) panel.append(node("h4", "", "Origin context"), node("p", "", route.origin_relations.map(value => origins[value] || origins.unknown).join("; ")));
  if (route.assumptions?.includes("unverified_global_axios")) panel.append(node("h4", "", "Client assumption"), node("p", "", "Axios is assumed from a conventional global name. Its library identity and prior configuration were not verified."));
  panel.append(node("h4", "", "Source locations"));
  const evidence = node("ul");
  for (const entry of route.evidence) evidence.append(node("li", "", `${entry.source.replace("_", " ")} · ${entry.location} · ${entry.kind}${entry.origin_relation ? " · " + (origins[entry.origin_relation] || origins.unknown) : ""}`));
  panel.append(evidence);
  panel.append(node("h4", "", "Query fields"), node("p", "", route.query_fields.join(", ") || "None observed"));
  for (const [key, title] of [["request_shapes", "Request field shapes"], ["response_shapes", "Response field shapes"]]) {
    panel.append(node("h4", "", title));
    panel.append(route[key]?.length ? node("pre", "", JSON.stringify(route[key], null, 2)) : node("p", "", "No supported body shape observed."));
  }
}

function allRoutes(report) {
  return [...report.routes, ...(report.unresolved_operations || [])];
}

function table() {
  const rows = $("#route-rows"); rows.replaceChildren();
  const query = $("#search").value.toLowerCase();
  const routes = current ? allRoutes(current.report).filter(route => (filter === "all" || route.confidence === filter) && route.path.toLowerCase().includes(query)) : [];
  $("#empty-routes").hidden = routes.length !== 0;
  for (const route of routes) {
    const row = node("tr", selected === route.id ? "selected" : "");
    const method = node("td"); method.append(node("span", "method " + (route.method || "unknown").toLowerCase(), route.method || "UNKNOWN"));
    const path = node("td", "", route.path);
    const confidence = node("td"); confidence.append(node("span", "confidence " + route.confidence, route.confidence));
    const status = node("td", "", (route.statuses || []).join(", ") || "Unobserved");
    const action = node("td"); const button = node("button", "row-detail", "↗");
    button.setAttribute("aria-label", `Inspect ${route.method || "unknown method"} ${route.path}`);
    button.addEventListener("click", () => { selected = route.id; details(route); table(); });
    action.append(button); row.append(method, path, confidence, status, action); rows.append(row);
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
  sourceList(report.sources); table();
  if (routes.length) { selected = routes[0].id; details(routes[0]); table(); }
  const engines = [...new Set((report.javascript_analysis || []).map(item => item.engine))].join(", ");
  notice(`${example ? "Synthetic example loaded." : "Analysis complete."} ${report.routes.length} known-method routes, ${(report.unresolved_operations || []).length} unresolved-method targets. ${engines ? `JavaScript engine: ${engines}. ` : ""}${report.warnings.length ? `${report.warnings.length} analysis warnings; inspect the report. ` : ""}${publicMode() ? "A public page and selected scripts were fetched; discovered forms and APIs were not called." : "No service was contacted."} Exports discard body and query values.`);
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

function showDiff(value) {
  diff = value; $("#diff-panel").hidden = false;
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
  notice(publicMode() ? "Public-page capture ready. Review capture limits and the brief before using it. A page and selected scripts were fetched; forms and APIs were not called." : "Plan ready. Choose an explanation style, review the agent brief, and copy or download it. Check the unknowns before implementing behavior.");
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
  notice(`${example ? "Synthetic example." : "Static inspection complete."} ${inventory.sources.length} logical sources, ${inventory.dependencies.length} known dependency labels. ${engines ? `JavaScript engine: ${engines}. ` : ""}${rejected ? `${rejected} sources were rejected; inspect the warnings. ` : ""}No source code was executed, dependency installed, or service contacted. Declarations and imports do not prove runtime behavior.`);
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
  notice(`Project inspection complete. ${inventory.sources.length} selected files, ${edges.length} candidate file links.${inventory.coverage?.partial ? " Coverage is partial; inspect omissions." : ""} Input names are used transiently for matching; results contain generic source IDs. No code was run.`);
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
  $("#source-list").replaceChildren(); $("#diff-panel").hidden=true;
  for (const id of ["routes", "observed", "inferred", "sources"]) $("#"+id+"-count").textContent="0";
  $("#detail").replaceChildren(node("h3", "", "Session cleared."));
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
  files = Array.from($("#files").files); current = null; selected = null; diff = null; $("#diff-panel").hidden = true;
  for (const id of ["routes", "observed", "inferred", "sources"]) $("#"+id+"-count").textContent = "0";
  $("#detail").replaceChildren(node("h3", "", "Selection awaiting analysis."));
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
  clearSourceView(); $("#diff-panel").hidden=true; $("#diff-list").replaceChildren();
  $("#baseline-status").textContent="No baseline kept. Select and analyze artifacts to compare evidence.";
  const result = await request("/api/rebuild-demo", {});
  sourceList(result.plan.coverage.sources || []);
  showPlan(result, true);
}));
$("#demo").addEventListener("click", () => action(async () => {
  clearPlan();
  files=[]; baselineFiles=[]; current=null; diff=null; selected=null; $("#files").value="";
  clearSourceView(); $("#diff-panel").hidden=true; $("#diff-list").replaceChildren();
  $("#baseline-status").textContent="No baseline kept. Select and analyze artifacts to compare evidence.";
  const result = await request(sourceMode() ? "/api/source-demo" : "/api/demo", {});
  if (sourceMode()) showSource(result.inventory, true); else show(result, true);
}));
$("#baseline").addEventListener("click", () => { baselineFiles=files.slice(); $("#baseline-status").textContent=`Baseline kept: ${baselineFiles.length} artifacts in browser memory. Import a second capture, analyze it, then compare.`; refresh(); });
$("#compare").addEventListener("click", () => action(async () => {
  const result = await request(sourceMode() ? "/api/source-compare" : "/api/compare", {baseline:await read(baselineFiles),candidate:await read(files),javascript_engine:$("#javascript-engine").value});
  if (sourceMode()) showSourceDiff(result.diff); else showDiff(result.diff);
}));
$("#demo-diff").addEventListener("click", () => action(async () => showDiff((await request("/api/demo-diff", {})).diff)));
for (const button of document.querySelectorAll("[data-filter]")) button.addEventListener("click", () => {filter=button.dataset.filter; document.querySelectorAll("[data-filter]").forEach(item => item.classList.toggle("selected", item===button)); table();});
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
  $("#workspace-mode").textContent = project ? "SELECTED PROJECT INSPECTION" : source ? "STATIC SOURCE INSPECTION" : publicMode() ? "PUBLIC HTTPS INSPECTION" : "WEB ARTIFACT INSPECTION";
  notice(project ? "Select individual project files to inspect. Literal relative imports can match another selected file; nested projects work best through the CLI with an explicit root." : source ? "Select saved requirements.txt, pyproject.toml, package.json, Cargo.toml, or supported source files. No GitHub fetch, execution or dependency installation occurs." : publicMode() ? "Enter a public HTTPS page URL. Requests begin only when you select Inspect page + create brief. No login, browser rendering, form submission or API testing occurs." : "Select saved web artifacts, or load the synthetic example. Build a plan creates a mock starting point from your selected files.");
  refresh();
});
action(async () => {
  try {
    const response = await fetch("/api/limits");
    if (!response.ok) throw new Error("Input limits unavailable");
    limits = await response.json();
  } catch (_) {
    const error = new Error("Input limits unavailable");
    error.safeMessage = "Input limits could not load. Check the local server and reload this page.";
    throw error;
  }
  show(await request("/api/demo", {}), true);
});
