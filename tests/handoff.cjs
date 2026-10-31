"use strict";
const assert = require("node:assert/strict");
const {explain, markdown} = require("../surface_map/static/handoff.js");
let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };
const base = () => ({format:"surface-map-rebuild-v1", operations:[], pages:[], unresolved:[],
  candidate_references:{candidates:[], module_references:[], limits:{truncated:false}},
  coverage:{sources:[],warnings:[],omitted_operations:0,omitted_pages:0,omitted_unresolved_targets:0,origin_classification_available:false},unknowns:[],capture_guide:[],privacy:{mode:"conservative"},scope:"Partial saved evidence.",limits:{operations:50}});
const evidence = n => ({source:"source_1",location:`line ${n}`,kind:"javascript",origin_relation:"same_origin"});
const operation = n => ({method:"POST",path:`/api/orders/${n}`,confidence:"inferred",origin_relations:["same_origin"],evidence:[evidence(n)],statuses:[],query_fields:["page"],request_shapes:[],response_shapes:[],stub:"not_implemented",unknowns:["Allowed callers are unknown."]});

const plan = base();
plan.operations.push(operation(1));
plan.pages.push({path:"/dashboard",confidence:"declared",evidence:[{source:"source_2",location:"line 2",kind:"link"}]});
plan.unresolved.push({path:"/api/export",method:null,confidence:"inferred",query_fields:["format"],evidence:[]});
plan.candidate_references.candidates.push({path:"/api/inventory",confidence:"candidate",method:null});
const brief = markdown(plan);
check(brief.includes("POST") && brief.includes("source_1") && !brief.includes("source\\_1") && brief.includes("line 1"), "Brief retains exact operation and source evidence without Markdown backslashes");
check(brief.includes("Allowed callers are unknown") && brief.includes("Required fields and allowed callers are not established"), "Per-operation and requiredness unknowns remain visible");
check(brief.includes("Method: UNKNOWN; do not invent GET or a handler") && !brief.includes("GET /api/export"), "Unknown method is never fabricated");
check(brief.includes("## Navigation references") && brief.includes("/dashboard"), "Navigation is a separate section");
check(!brief.includes("/api/inventory") && brief.includes("none becomes an API operation"), "Candidates remain counts, never operations");
check(brief.includes("Full request/response shapes") && brief.includes("passing mock routing checks does not demonstrate functional parity"), "Preview limits and mock scope remain clear");

for (const style of ["simple","developer","genz"]) {
  const text = explain(plan,style);
  check(text.includes("Included in this plan: 1 operation group, 1 navigation reference, 1 target with an unknown method, and 1 string candidate"), `${style} explanation retains grammatical evidence counts`);
  check(text.includes("None proves that a feature works now") && text.includes("Origin scope is unclassified"), `${style} explanation keeps uncertainty`);
  check(text.split("\n\n")[0].split(/[.!?]\s|[.!?]$/).filter(Boolean).length >= 4, `${style} provides a substantive explanatory introduction`);
}
const normalize = value => value.split("\n\n").filter((_, index) => index !== 1).join("\n\n");
check(normalize(markdown(plan,"simple")) === normalize(markdown(plan,"developer")) && normalize(markdown(plan,"simple")) === normalize(markdown(plan,"genz")), "Styles change only the introduction, not agent facts or instructions");
check(explain(plan,"simple").split("\n\n").slice(1).join("\n") === explain(plan,"genz").split("\n\n").slice(1).join("\n"), "Explanation facts are identical across styles");

const assumed = base();
assumed.operations = [operation(1)];
assumed.operations[0].assumptions = ["unverified_global_axios"];
assumed.operations[0].stub = "none";
assumed.operations[0].origin_relations = ["cross_origin"];
assumed.operations[0].evidence = [];
check(markdown(assumed).includes("Browser-global Axios identity and prior configuration are assumed, not verified"), "Client assumption survives absent evidence preview");
check(explain(assumed).includes("Browser-global Axios identity"), "Simple explanation retains global-client uncertainty");
check(markdown(assumed).includes("excluded from generated handlers; review cross-origin scope"), "Cross-origin exclusion is explicit");
assumed.operations[0].assumptions = [];
assumed.operations[0].evidence = [{...evidence(1), assumptions:["unverified_global_axios"]}];
check(markdown(assumed).includes("Client assumption: Browser-global Axios"), "Evidence-level assumptions are also preserved");

const limited = base();
limited.operations = Array.from({length:22},(_,i)=>operation(i));
limited.operations[0].evidence = [evidence(1),evidence(2),evidence(3)];
limited.operations[0].additional_evidence_count = 4;
limited.pages = Array.from({length:10},(_,i)=>({path:`/page/${i}`,confidence:"declared",evidence:[]}));
limited.unresolved = Array.from({length:9},(_,i)=>({path:`/api/unknown/${i}`,method:null,confidence:"inferred",evidence:[]}));
limited.coverage = {sources:[{id:"source_2",kind:"javascript",bytes:30}],warnings:[{source:"source_1",reason:"Basic JavaScript hints only."}],omitted_operations:3,omitted_pages:2,omitted_unresolved_targets:1,evidence_limits:{truncated:true,omissions:[{source:"source_1",query_fields:7}]},javascript_analysis:[{source:"source_1",engine:"basic",reason:"node_unavailable",assumed_global_axios_omissions:{global_client_modified:5}}],script_omissions:[{source:"source_2",code:"unsupported_or_limit"}]};
limited.candidate_references.limits = {truncated:true,omitted_occurrences:{relative_strings_without_base:12}};
const limitedBrief = markdown(limited);
check(limitedBrief.includes("Showing 20 of 22 retained rows") && limitedBrief.includes("2 retained rows are omitted"), "Operation preview truncation is disclosed");
check(limitedBrief.includes("Showing 8 of 10 retained rows") && limitedBrief.includes("Showing 8 of 9 retained rows"), "Navigation and unresolved preview caps are disclosed");
check(limitedBrief.includes("1 of 3") === false && limitedBrief.includes("2 of 3 retained locations") && limitedBrief.includes("Additional retained evidence locations omitted from this brief: 1") && limitedBrief.includes("per-row limit: 4"), "Both brief and plan evidence omissions are disclosed");
const metadataText = limitedBrief.split("````json\n")[1].split("\n````")[0];
const metadata = JSON.parse(metadataText);
assert.deepEqual(metadata.coverage,limited.coverage); checks++;
assert.deepEqual(metadata.reference_coverage.limits,limited.candidate_references.limits); checks++;
check(limitedBrief.includes("fallback reason: ` node_unavailable `") && limitedBrief.includes("Script analysis omissions: 1 source"), "Fallback and actual script omission metadata are obvious without invented source statuses");
check(explain(limited).includes("Some evidence was omitted") && explain(limited).includes("Capture warnings: 1"), "Explanation announces warnings and partial coverage");
check(explain(limited).includes("Plan-limit omissions: 3 operation groups, 2 navigation references, 1 unresolved target"), "Headline discloses reported plan omissions with correct plural forms");
check(explain(limited).includes("Capture warning from source_1: Basic JavaScript hints only."), "Explanation includes the actual retained warning reason");
check(explain(limited).includes("Omitted assumed-global calls reported by JavaScript analysis: 5 occurrences"), "Explanation includes actual structured per-source omission count");
const cappedWarnings = base();
cappedWarnings.coverage.javascript_analysis = [{source:"source_1",engine:"enhanced",assumed_global_axios_omissions:{global_client_modified:5}}];
check(explain(cappedWarnings).includes("Some evidence was omitted"), "Structured per-source omissions remain visible even when warnings were capped");
const explicitBasic = base();
explicitBasic.coverage.javascript_analysis = [{source:"source_1",engine:"basic"}];
check(explain(explicitBasic).includes("syntax and scope are not validated") && explain(explicitBasic).includes("miss real calls or suggest calls that would not run"), "Explicit basic mode always carries the lexical-hints caveat even without fallback reason");
check(markdown(explicitBasic).includes("syntax and scope are not validated"), "Basic-mode caveat remains in the agent brief");
const warningPreview = base();
warningPreview.coverage.warnings = Array.from({length:5},(_,i)=>({source:"source_1",reason:`Warning ${i}`}));
check(explain(warningPreview).includes("2 additional warnings omitted from this explanation") && !explain(warningPreview).includes("Warning 4"), "Warning preview discloses omitted retained reasons");
assert.deepEqual(JSON.parse(markdown(warningPreview).split("````json\n")[1].split("\n````")[0]).coverage.warnings,warningPreview.coverage.warnings); checks++;

// Read CommonMark backtick spans independently of the renderer: a closer must
// have exactly the opening run length, and one surrounding space is removed.
// Fenced metadata is parsed separately below rather than treated as inline text.
function inlineSpans(document) {
  const text = document.replace(/^````json\n[\s\S]*?\n````(?=\n|$)/gm, "");
  const literals = [], outside = [];
  let position = 0;
  while (position < text.length) {
    const start = text.indexOf("`",position);
    if (start < 0) {outside.push(text.slice(position)); break;}
    const opening = /^`+/.exec(text.slice(start))[0];
    const runs = /`+/g; runs.lastIndex = start + opening.length;
    let match, close;
    while ((match = runs.exec(text)) !== null) if (match[0].length === opening.length) {close=match;break;}
    if (!close) {outside.push(text.slice(position,start+opening.length));position=start+opening.length;continue;}
    outside.push(text.slice(position,start));
    let literal = text.slice(start+opening.length,close.index).replace(/\r\n?|\n/g," ");
    if (literal.startsWith(" ") && literal.endsWith(" ") && /[^ ]/.test(literal)) literal=literal.slice(1,-1);
    literals.push(literal); position=close.index+close[0].length;
  }
  return {literals, outside:outside.join("")};
}

const hostile = base();
hostile.operations = [operation(1)];
hostile.operations[0].path = "/api/[click](https://bad.invalid)<script>``ticks``_{}\\";
hostile.operations[0].query_fields = ["[click](https://bad.invalid)","` field `","  edge spaces  ","# forged\n<script>"];
hostile.coverage.warnings = [{reason:"\n````\n# fake\n<script>alert(1)</script>"}];
const safe = markdown(hostile);
const parsed = inlineSpans(safe);
check(parsed.literals.includes(hostile.operations[0].path), "Code spans preserve exact normalized-path text, including backticks and Markdown punctuation");
check(parsed.literals.includes("[click](https://bad.invalid)") && parsed.literals.includes("` field `") && parsed.literals.includes("  edge spaces  "), "Data values stay literal even with links, backticks, and surrounding spaces");
check(!parsed.outside.includes("\n# forged") && !parsed.outside.includes("[click](https://bad.invalid)") && !parsed.outside.includes("<script>"), "Untrusted values cannot add active Markdown headings, links, or raw HTML outside code spans");
check(safe.split("\n````").length === 3, "Coverage data cannot close its JSON fence");
assert.deepEqual(JSON.parse(safe.split("````json\n")[1].split("\n````")[0]).coverage,hostile.coverage); checks++;
const aliased = base(); aliased.operations = [{...operation(1),path:"/api/{segment_2}/my_path"}];
check(inlineSpans(markdown(aliased)).literals.includes("/api/{segment_2}/my_path") && !markdown(aliased).includes("{segment\\_2}"), "A normal aliased path reaches the agent unchanged");

const empty = markdown(base());
check(empty.includes("0 operation groups") && empty.includes("No rows retained in this selection") && empty.includes("Source evidence: none retained") === false, "Empty capture has explicit empty sections without invented evidence omissions");
const noEvidence = base(); noEvidence.operations = [operation(1)]; noEvidence.operations[0].evidence = [];
check(markdown(noEvidence).includes("Source evidence: none retained") && !markdown(noEvidence).includes("Additional retained evidence locations"), "Absent evidence is not claimed to be a truncation");
assert.throws(()=>markdown({...base(),scope:"x".repeat(140000)}),/too large/); checks++;
assert.throws(()=>markdown({...base(),scope:"`x".repeat(140000)}),/too large/); checks++;
assert.throws(()=>markdown(plan,"unknown"),/supported explanation style/); checks++;
assert.throws(()=>explain(null),/supported rebuild plan/); checks++;
check(markdown(plan) === markdown(plan), "Rendering is deterministic");
console.log(JSON.stringify({checks}));
