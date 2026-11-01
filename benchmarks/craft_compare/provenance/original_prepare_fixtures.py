"""Freeze synthetic, source-informed HAR comparison inputs before either analyzer runs."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
CRAFT_SHA = "c316051c9e01e1bf9863f3c802a805179afa4663"
BASELINE_SHA = "2a516820cf1836d7025df90b81df1b1cdfbaea23"


def body(mime="application/json", text=None, **extra):
    value = {"mimeType": mime, **extra}
    if text is not None:
        value["text"] = text
    return value


def entry(method="GET", path="/api/orders/1", *, post=None, content=None, status=200, **extra):
    request = {"method": method, "url": "https://fixture.example" + path, "headers": []}
    if post is not None:
        request["postData"] = post
    return {"request": request, "response": {"status": status, "headers": [],
            "content": content if content is not None else body(text="{}")}, **extra}


def main():
    if (ROOT / "manifest.json").exists():
        raise SystemExit("Manifest already exists; refusing to replace a predeclared comparison.")
    FIXTURES.mkdir(exist_ok=True)
    rows = []

    def add(identifier, entries, purpose, expected, *, boundary=False, privacy=None):
        data = {"log": {"version": "1.2", "creator": {"name": "synthetic-har-comparison", "version": "1"}, "entries": entries}}
        raw = (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        path = FIXTURES / (identifier + ".har")
        path.write_bytes(raw)
        rows.append({"id": identifier, "file": "fixtures/" + path.name, "sha256": hashlib.sha256(raw).hexdigest(),
                     "bytes": len(raw), "entry_count": len(entries), "purpose": purpose,
                     "expected_facts": expected, "boundary_case": boundary, "privacy": privacy or {}})

    add("01_json_control", [entry(content=body(text='{"id":1,"total":19.5}')),
        entry("POST", "/api/orders", post=body(text='{"name":"BODY_PRIVATE_CANARY","quantity":2,"active":true,"tags":["x"]}'), status=201)],
        "Shared ordinary JSON control, including request and response type-only facts.",
        {"accepted": True, "routes": 2, "statuses": [200, 201], "request_properties": 4, "response_properties": 2})
    add("02_base64_json", [entry(content=body(text=base64.b64encode(b'{"id":1,"total":19.5}').decode(), encoding="base64"))],
        "Saved base64 JSON response; encoded bodies are a known upgrade target.",
        {"accepted": True, "routes": 1, "response_properties": 2})
    add("03_invalid_base64", [entry(content=body(text="%%%invalid%%%", encoding="base64"))],
        "Invalid base64 must not produce a fabricated JSON body.",
        {"accepted": True, "routes": 1, "response_properties": 0, "body_inspection_gap": True}, boundary=True)
    add("04_non_utf8_base64", [entry(content=body(text=base64.b64encode(b'\xff{"id":1}').decode(), encoding="base64"))],
        "Non-UTF-8 decoded bytes must not become trusted JSON shape evidence.",
        {"accepted": True, "routes": 1, "response_properties": 0, "body_inspection_gap": True}, boundary=True)
    add("05_decoded_body_over_64k", [entry(content=body(text=base64.b64encode(json.dumps({"name": "x" * 70000}).encode()).decode(), encoding="base64"))],
        "A documented Surface decoded-body cap; higher Craft acceptance is a scope difference.",
        {"accepted": True, "routes": 1, "surface_response_properties": 0, "surface_body_inspection_gap": True}, boundary=True)
    add("06_urlencoded_text", [entry("POST", "/api/orders", post=body("application/x-www-form-urlencoded", "name=FORM_VALUE_CANARY&tag=one&tag=two&empty=&encoded%20field=value"))],
        "Observed form field names, decoding, empty values and duplicate names; all values must stay private.",
        {"accepted": True, "routes": 1, "form_field_occurrences": 5, "form_unique_fields": 4},
        privacy={"forbidden_values": ["FORM_VALUE_CANARY"]})
    add("07_urlencoded_params", [entry("POST", "/api/orders", post=body("application/x-www-form-urlencoded", params=[{"name": "name", "value": "PARAM_VALUE_CANARY"}, {"name": "tag", "value": "one"}, {"name": "tag", "value": "two"}]))],
        "HAR postData.params without text and with duplicated field names.",
        {"accepted": True, "routes": 1, "form_field_occurrences": 3, "form_unique_fields": 2},
        privacy={"forbidden_values": ["PARAM_VALUE_CANARY"]})
    add("08_multipart_params", [entry("POST", "/api/uploads", post=body("multipart/form-data; boundary=synthetic", params=[{"name": "title", "value": "MULTIPART_VALUE_CANARY"}, {"name": "tag", "value": "one"}, {"name": "tag", "value": "two"}, {"name": "avatar", "fileName": "FILE_NAME_CANARY.png", "value": "FILE_CONTENT_CANARY"}]))],
        "Multipart field names and duplicates; file names, contents and boundary details are not needed.",
        {"accepted": True, "routes": 1, "form_field_occurrences": 4, "form_unique_fields": 3},
        privacy={"forbidden_values": ["MULTIPART_VALUE_CANARY", "FILE_NAME_CANARY", "FILE_CONTENT_CANARY"]})
    add("09_problem_json_mime", [entry(content=body("application/problem+json; charset=utf-8", '{"status":400,"message":"VALUE_CANARY"}'), status=400)],
        "Existing structured JSON MIME and parameter control.", {"accepted": True, "routes": 1, "response_properties": 2})
    add("10_unknown_encoding", [entry(content=body(text='{"id":1}', encoding="gzip"))],
        "Unknown HAR body encoding must be identified; parsing it as plaintext is reported separately.",
        {"accepted": True, "routes": 1, "response_properties": 0, "body_inspection_gap": True}, boundary=True)
    add("11_plaintext_json_policy", [entry(content=body("text/plain", '{"id":1}'))],
        "JSON-looking text/plain: Surface MIME policy differs from Craft content sniffing.",
        {"accepted": True, "routes": 1, "surface_response_properties": 0}, boundary=True)
    add("12_malformed_json", [entry(content=body(text='{"id":'))],
        "Malformed supported JSON; retain route and identify missing body evidence.",
        {"accepted": True, "routes": 1, "response_properties": 0, "body_inspection_gap": True}, boundary=True)
    private = entry(path="/api/orders/1?token=TOKEN_CANARY&q=SEARCH_VALUE_CANARY", content=body(text='{"PRIVATE_FIELD_CANARY":"BODY_VALUE_CANARY"}'))
    private["request"]["url"] = private["request"]["url"].replace("https://", "https://USERINFO_CANARY:PASS_CANARY@")
    private["request"]["headers"] = [{"name": "Authorization", "value": "AUTH_CANARY"}, {"name": "Cookie", "value": "COOKIE_CANARY"}, {"name": "X-Trace", "value": "TRACE_VALUE_CANARY"}]
    private["response"]["headers"] = [{"name": "Set-Cookie", "value": "SET_COOKIE_CANARY"}]
    add("13_privacy_defaults", [private], "Shared credential/value-removal controls plus explicitly different identifier/header/query minimization policies.",
        {"accepted": True, "routes": 1}, privacy={"forbidden_values": ["USERINFO_CANARY", "PASS_CANARY", "AUTH_CANARY", "COOKIE_CANARY", "SET_COOKIE_CANARY", "TOKEN_CANARY", "BODY_VALUE_CANARY"],
        "surface_only_omitted_values": ["SEARCH_VALUE_CANARY", "TRACE_VALUE_CANARY", "PRIVATE_FIELD_CANARY"],
        "note": "Craft hidden_values is empty, its documented default. Noncredential query/header values and field names are intentionally outside its default redaction promise."})
    paths = ["/api/orders/1", "/api/orders/2", "/api/events/2026-10-31", "/api/events/2026-11-01", "/api/items/123e4567-e89b-12d3-a456-426614174000", "/api/items/123e4567-e89b-12d3-a456-426614174001", "/api/files/0123456789abcdef0123456789abcdef", "/api/files/1123456789abcdef0123456789abcdef"]
    add("14_path_templates", [entry(path=p) for p in paths], "Numeric, UUID, ISO-date and long-hex grouping; compare group counts, not policy-specific path spellings.",
        {"accepted": True, "routes": 4, "request_entries": 8})
    add("15_malformed_entry", [None], "Malformed HAR control: rejection versus permissive empty records must remain visible.", {"accepted": False}, boundary=True)
    add("16_entry_cap_301", [entry(path=f"/api/orders/{i}") for i in range(301)],
        "Above Surface's documented 300-entry cap; not a shared-capacity accuracy score.",
        {"surface_accepted": False, "craft_accepted": True, "craft_entries": 301}, boundary=True)
    add("17_timings_and_headers", [entry(time=140, timings={"wait":100,"receive":40}, _resourceType="fetch"), entry(path="/api/orders/2", time=10, timings={"wait":5,"receive":5})],
        "Craft historical timing metadata is additional scope, not Surface failure.",
        {"accepted": True, "routes": 1, "request_entries": 2, "craft_timing_entries": 2}, boundary=True)
    add("18_empty_capture", [], "Empty valid HAR control.", {"accepted": True, "routes": 0})
    hashes = {}
    for path in ["LICENSE-MIT", "NOTICE", "crates/analysis/src/web_har.rs", "crates/formats/src/har.rs", "crates/formats/src/sourcemap.rs", "apps/craft-anything-cli/src/main.rs", "crates/engine/src/tools/web_tools.rs", "crates/engine/src/plugins.rs", "crates/engine/src/settings.rs"]:
        hashes[path] = hashlib.sha256((ROOT/"reference"/path).read_bytes()).hexdigest()
    manifest = {"schema":"craft-surface-har-comparison-v1", "frozen_at":datetime.now(timezone.utc).isoformat(),
        "status":"predeclared_before_analyzer_execution", "purpose":"Source-informed capability and boundary comparison of a pinned offline Craft HAR CLI against Surface 1.6.3 and the upgraded checkout. Not an independent broad accuracy benchmark.",
        "craft":{"commit":CRAFT_SHA,"release":"v0.1.0", "binary_zip_sha256":"2544a2c457e5bc22ab0b57dd757ece6bb35e622de35cffed2a3e0f216d832e9c", "command":"--deep web-recording-show --json <path,format=har,limit=1000,offset=0,hidden_values=[]>", "source_sha256":hashes,
                 "scope":"Actual released Windows x64 offline HAR CLI, no MCP server, GUI, model, device, target website, setup or package installation.", "configured_entry_cap":500000},
        "surface_baseline":{"commit":BASELINE_SHA,"version":"1.6.3","call":"surface_map.analyze.analyze([{'kind':'har','text': fixture_utf8}], javascript_engine='basic')"},
        "surface_upgraded":{"commit":"root_will_freeze_after_implementation", "call":"same analyzer invocation and bytes as baseline"},
        "execution":{"timeout_seconds_per_process":20,"env":"allowlist with isolated HOME/APPDATA/LOCALAPPDATA/config/data/TEMP, no API/provider/agent credentials", "network":"offline code path; no target fetch/replay and no model/provider calls; OS networking is not sandbox-blocked"},
        "scoring":"Report every case and raw synthetic output. Extract route/status/body-field counts and privacy canaries without changing fixtures or their expected facts. Shared controls and advertised policies are separate from intentional scope/limit differences. No overall winner or fabricated percentage.",
        "cases":rows}
    raw = (json.dumps(manifest,indent=2)+"\n").encode()
    (ROOT/"manifest.json").write_bytes(raw)
    (ROOT/"manifest.sha256").write_text(hashlib.sha256(raw).hexdigest()+"  manifest.json\n",encoding="ascii")
    print(json.dumps({"cases":len(rows),"manifest_sha256":hashlib.sha256(raw).hexdigest(),"total_fixture_bytes":sum(x["bytes"] for x in rows)}))


if __name__ == "__main__":
    main()
