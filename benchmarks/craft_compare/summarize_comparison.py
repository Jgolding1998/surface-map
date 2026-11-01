"""Project all recorded cases into a readable table without rerunning analyzers."""
from __future__ import annotations

from collections import Counter
import argparse
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent
TOOLS = ["surface-baseline", "surface-upgraded", "craft-cli"]


def property_count(value):
    return len(value.get("properties", {})) if isinstance(value, dict) else 0


def facts(tool, case):
    output = ROOT/"outputs"/tool
    path = output/(case["id"]+".stdout.json")
    if not path.exists():
        return {"pending": True}
    raw = path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    run = json.loads((output/(case["id"]+".run.json")).read_text())
    result = parsed.get("result", {})
    answer = {"accepted":run["accepted"], "runtime_ms":run["runtime_ms"],
              "stderr_bytes":run["stderr_bytes"], "timeout":run["timeout"], "output_bytes":run["stdout_bytes"],
              "forbidden_canaries_retained":[v for v in case["privacy"].get("forbidden_values",[]) if v in raw],
              "policy_difference_canaries_retained":[v for v in case["privacy"].get("surface_only_omitted_values",[]) if v in raw]}
    if not run["accepted"]:
        answer["error"] = parsed.get("error")
        return answer
    if tool == "craft-cli":
        routes = result.get("api_surface", [])
        entries = result.get("entries", [])
        answer.update(routes=len(routes), statuses=sorted({s for r in routes for s in r.get("statuses",[])}),
                      request_properties=sum(property_count(r.get("request_outline")) for r in routes),
                      response_properties=sum(property_count(r.get("response_outline")) for r in routes),
                      entries=result.get("entries_total"), coverage=parsed.get("coverage"), truncated=result.get("truncated"),
                      timing_entries=sum(bool(e.get("timings")) for e in entries),
                      body_coverage="body kind/size or null; no explicit decode/parse omission reasons")
        all_fields = [f for e in entries for f in (e.get("request_body") or {}).get("fields",[])]
        answer["form_field_occurrences"] = len(all_fields)
        answer["form_unique_fields"] = len(set(all_fields))
        answer["form_names"] = all_fields
        answer["response_body_kinds"] = [None if e.get("response_body") is None else e["response_body"].get("kind") for e in entries]
    else:
        routes=result.get("routes", [])
        observations=[f for r in routes for observation in r.get("request_fields",[]) for f in observation.get("fields",[])]
        coverage=result.get("har_body_analysis")
        reasons=Counter()
        if isinstance(coverage,dict):
            for row in coverage.get("entries",[]):
                for side in ("request","response"):
                    item=row.get(side,{})
                    if item.get("state")=="omitted" and item.get("reason")!="not_present":
                        reasons[item.get("reason","unknown")]+=1
        elif isinstance(coverage,list):
            for row in coverage:
                for item in row.get("bodies",[]):
                    if item.get("state")=="omitted" and item.get("reason")!="not_present":
                        reasons[item.get("reason","unknown")]+=1
        answer.update(routes=len(routes),statuses=sorted({s for r in routes for s in r.get("statuses",[])}),
                      request_properties=sum(property_count(s) for r in routes for s in r.get("request_shapes",[])),
                      response_properties=sum(property_count(s) for r in routes for s in r.get("response_shapes",[])),
                      form_field_occurrences=sum(f.get("occurrences",0) for f in observations),
                      form_unique_fields=len({f.get("name") for f in observations}),
                      form_names=[f.get("name") for f in observations],
                      body_coverage=coverage if coverage is not None else "no explicit HAR body coverage",
                      body_omission_reasons=dict(reasons), warnings=result.get("warnings",[]))
    return answer


def display(case, value):
    if value.get("pending"):
        return "Pending frozen implementation"
    if not value["accepted"]:
        return "Rejected (exit 1)"
    identifier=case["id"]
    if identifier=="01_json_control":
        return f'{value["routes"]} routes; request {value["request_properties"]}, response {value["response_properties"]} JSON fields'
    if identifier in {"02_base64_json","03_invalid_base64","04_non_utf8_base64","05_decoded_body_over_64k","09_problem_json_mime","10_unknown_encoding","11_plaintext_json_policy","12_malformed_json"}:
        omitted=value.get("body_omission_reasons",{})
        suffix=("; "+", ".join(omitted)) if omitted else ""
        return f'{value["response_properties"]} response fields'+suffix
    if identifier in {"06_urlencoded_text","07_urlencoded_params","08_multipart_params"}:
        return f'{value["form_field_occurrences"]} field occurrences / {value["form_unique_fields"]} names'
    if identifier=="13_privacy_defaults":
        return f'{len(value["forbidden_canaries_retained"])} credential/body canaries; {len(value["policy_difference_canaries_retained"])} policy-difference values retained'
    if identifier=="15_malformed_entry":
        return f'Accepted {value["routes"]} operation; statuses {value["statuses"]}'
    if identifier=="16_entry_cap_301":
        return f'Accepted {value.get("entries", "?")} entries'
    if identifier=="17_timings_and_headers":
        return f'{value["routes"]} route; '+(f'{value["timing_entries"]} timing records' if "timing_entries" in value else 'timings outside scope')
    return f'{value["routes"]} routes'


def main():
    manifest=json.loads((ROOT/"manifest.json").read_text())
    rows=[{"case":c["id"],"purpose":c["purpose"],"expected_facts":c["expected_facts"],"boundary_case":c["boundary_case"],
           "results":{t:facts(t,c) for t in TOOLS}} for c in manifest["cases"]]
    summary={"scope":manifest["purpose"],"manifest_sha256":(ROOT/"manifest.sha256").read_text().split()[0],"cases":rows}
    timing={}
    for tool in TOOLS:
        values=[r["results"][tool]["runtime_ms"] for r in rows if not r["results"][tool].get("pending")]
        if values:
            timing[tool]={"process_runs":len(values),"median_ms":round(statistics.median(values),3),"min_ms":min(values),"max_ms":max(values),"total_ms":round(sum(values),3)}
    smoke="An initial Craft control smoke invocation preceded that suite (755.141 ms, recorded separately). " if (ROOT/"initial-cli-smoke.json").exists() else "No dedicated smoke invocation is recorded in this run. "
    summary["timing"]={"note":"One process per fixture in the reported suite on this Windows host. "+smoke+"Times include process/interpreter startup; execution order and OS caching are uncontrolled. These are reproducibility observations, not a speed benchmark or general performance ranking.","tools":timing}
    (ROOT/"comparison-results.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    upgraded_snapshot=ROOT/"outputs"/"surface-upgraded"/"source-snapshot.json"
    upgraded_hash=None
    if upgraded_snapshot.exists():
        snapshot=json.loads(upgraded_snapshot.read_text(encoding="utf-8"))
        upgraded_hash=snapshot.get("analyze_sha256") or snapshot.get("files_sha256",{}).get("surface_map/analyze.py",{}).get("sha256")
    summary["upgraded_analyzer_sha256"]=upgraded_hash
    if (ROOT/"source-pins.json").exists():
        pins=json.loads((ROOT/"source-pins.json").read_text(encoding="utf-8"))["tools"]
        summary["source_module_pins"]={name:{"package_version":data["package_version"],"modules":len(data["files_lf_sha256"]),"canonical_module_map_sha256":data["canonical_module_map_sha256"]} for name,data in pins.items()}
    (ROOT/"comparison-results.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    lines=["# Actual offline HAR comparison", "", manifest["purpose"], "",
           "Craft is the verified released Windows x64 CLI v0.1.0 (`web-recording-show`), commit `c316051c9e01e1bf9863f3c802a805179afa4663`. Surface baseline is v1.6.3, commit `2a516820cf1836d7025df90b81df1b1cdfbaea23`. Surface was invoked through its analyzer library (also used by its CLI/MCP), not through MCP transport. Every tool received the same frozen fixture bytes. `manifest.json`, its SHA-256, source hashes, binary provenance, source audit, raw outputs and per-process metadata are retained alongside this report.", "",
           "The upgraded version 1.7.0 HAR analyzer is frozen by SHA-256 `"+str(upgraded_hash)+"`. Its complete Python module map is recorded in `source-pins.json` and enforced by the portable runner before analyzer execution. Fixture expectations were not changed after implementation or execution. The projection reads each tool's actual schema: Craft outlines and entry-level form-name lists; Surface JSON shapes, aliased request-field occurrence records, and per-source body-coverage records.", "",
           "| Predeclared fixture | Surface 1.6.3 | Upgraded Surface | Craft released CLI |", "|---|---|---|---|"]
    for case,row in zip(manifest["cases"],rows):
        lines.append("| "+case["id"]+" | "+" | ".join(display(case,row["results"][tool]) for tool in TOOLS)+" |")
    lines += ["", "JSON-field counts are top-level retained property counts. Form counts show occurrences and unique names; serialization, aliases and grouped paths differ by design. These counts do not measure exact application reconstruction or general website accuracy.", "",
              "The malformed-entry case is deliberately invalid HAR. Craft accepts it as an observed operation with an empty method and status 0; Surface rejects it. The 301-entry case deliberately crosses Surface's documented 300-entry cap; Craft's larger supported input is a scope difference. Craft also retains historical timing information that Surface does not inspect.", "",
              "The default privacy case distinguishes shared promises from different minimization policies. Credential values, URL user-info and JSON body values must be absent in both. Craft intentionally retains noncredential query/header values and private field names unless declared through `hidden_values`; Surface drops or aliases them. No `hidden_values` declarations were supplied to Craft. A retained policy-difference canary is not labeled a violation of Craft's documented default behavior.", "",
              "Craft body outlines expose kind/size but generally omit explicit reasons for invalid encodings or malformed JSON. Its unknown-encoding and text/plain cases parse JSON-looking plaintext. Surface's explicit MIME and decoding policies intentionally differ.", "",
              "Only the audited offline handler was invoked, with fresh config/home/data folders and an environment allowlist without provider credentials. No MCP server, GUI, model, provider, device, setup or target website operation was invoked. This is environment isolation and a reviewed call path, not an OS/network sandbox or independent traffic capture.", "", "## Observed process times", "", summary["timing"]["note"], "", "| Tool | Runs | Median ms | Min–max ms |", "|---|---:|---:|---:|"]
    for tool,row in timing.items():
        lines.append(f'| {tool} | {row["process_runs"]} | {row["median_ms"]} | {row["min_ms"]}–{row["max_ms"]} |')
    lines += ["", "No overall winner or accuracy percentage is inferred from these fixtures.", ""]
    (ROOT/"COMPARISON.md").write_text("\n".join(lines),encoding="utf-8")
    print(json.dumps({"cases":len(rows),"tools_completed":list(timing),"outputs":"comparison-results.json and COMPARISON.md"}))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=ROOT,help="Directory containing frozen manifest and recorded outputs")
    ROOT=parser.parse_args().root.resolve()
    main()
