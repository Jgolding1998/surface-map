"""Re-run the frozen synthetic HAR comparison using explicitly supplied local tools.

No downloads, installations, Git commands, MCP servers, models or target requests.
The vendor executable is pinned by SHA-256. Surface Python code is supplied by the
caller; only its HAR analyzer is invoked. Read README.md before running.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
CLI_SHA256 = "52a4249d3d9b6f321883b5048e774026856f5fe8e8afaca89984d02ffe8f0fd5"
ANALYZER_LF_SHA256 = {
    "surface-baseline": "283f58903eb81e01b5c24ec9c1826150d180b47b45390b02ff7e22c16ad7e5f6",
    "surface-upgraded": "ab5634c835742e4c1d768853251f955f3d6849f3972661d2ba986d57c12c84ef",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def worker(source, fixture):
    # -I prevents inherited PYTHONPATH/site-directory injection. The explicit
    # caller-supplied Surface checkout is the only added import location.
    try:
        sys.path.insert(0, str(Path(source).resolve()))
        from surface_map.analyze import analyze
        value = analyze([{"kind":"har", "text":Path(fixture).read_text(encoding="utf-8")}],
                        javascript_engine="basic")
        print(json.dumps({"result":value},ensure_ascii=False))
    except Exception as exc:
        # Surface's expected errors are minimized; preserve their factual reason.
        # Unexpected import/runtime errors get only a type, not a traceback/path.
        error={"type":type(exc).__name__}
        if type(exc).__name__=="ArtifactError":
            error["message"]=str(exc)
        print(json.dumps({"error":error}))
        raise SystemExit(1)


def verify_manifest():
    raw=(HERE/"manifest.json").read_bytes()
    expected=(HERE/"manifest.sha256").read_text(encoding="ascii").split()[0]
    if sha(raw)!=expected:
        raise ValueError("The predeclared manifest hash does not match")
    manifest=json.loads(raw)
    for case in manifest["cases"]:
        if sha((HERE/case["file"]).read_bytes())!=case["sha256"]:
            raise ValueError("A frozen fixture hash does not match: "+case["id"])
    return manifest


def source_pins():
    raw=(HERE/"source-pins.json").read_bytes()
    expected=(HERE/"source-pins.sha256").read_text(encoding="ascii").split()[0]
    if sha(raw)!=expected:
        raise ValueError("The recorded Python module pin-map hash does not match")
    return json.loads(raw)["tools"]


def freeze_source(source, output, tool, allow_new):
    package=source/"surface_map"
    records={}
    contents={}
    for path in sorted(package.rglob("*.py")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Source snapshots require ordinary local Python files")
        rel=path.relative_to(source).as_posix()
        raw=path.read_bytes()
        contents[rel]=raw
        records[rel]={"sha256":sha(raw),"lf_sha256":sha(raw.replace(b"\r\n",b"\n"))}
    analyzer=records.get("surface_map/analyze.py")
    if analyzer is None:
        raise ValueError("No Surface analyzer found for "+tool)
    matches=analyzer["lf_sha256"]==ANALYZER_LF_SHA256[tool]
    canonical={path:value["lf_sha256"] for path,value in records.items()}
    expected=source_pins()[tool]
    module_matches=canonical==expected["files_lf_sha256"]
    canonical_sha=sha(json.dumps(canonical,sort_keys=True).encode())
    if not module_matches and not allow_new:
        raise ValueError("Python module map differs from recorded source for "+tool+"; use matching modules or explicitly pass --allow-new-source")
    tree_sha=sha(json.dumps(records,sort_keys=True).encode())
    destination=output/"source-snapshots"/(tool+"-"+tree_sha[:16])
    for rel,raw in contents.items():
        path=destination/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
    provenance={"tool":tool,"source_label":"explicitly supplied local Surface Python modules",
                "analyzer_matches_recorded_lf_hash":matches,"module_tree_sha256":tree_sha,
                "complete_module_map_matches_recorded":module_matches,"canonical_module_map_sha256":canonical_sha,
                "expected_canonical_module_map_sha256":expected["canonical_module_map_sha256"],
                "files_sha256":records,"scope":"Basic HAR analyzer; no target source execution or syntax-parser dependency setup"}
    path=output/"outputs"/tool/"source-snapshot.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(provenance,indent=2)+"\n",encoding="utf-8")
    return destination


def environment(output, tool, identifier):
    isolated=output/"isolation"/tool/identifier
    for folder in ["home","roaming","local","config","data","temp","cwd"]:
        (isolated/folder).mkdir(parents=True,exist_ok=True)
    systemroot=os.environ.get("SYSTEMROOT",r"C:\Windows")
    env={"SYSTEMROOT":systemroot,"WINDIR":systemroot,"PATH":str(Path(systemroot)/"System32"),
         "HOME":str(isolated/"home"),"USERPROFILE":str(isolated/"home"),
         "APPDATA":str(isolated/"roaming"),"LOCALAPPDATA":str(isolated/"local"),
         "XDG_CONFIG_HOME":str(isolated/"config"),"XDG_DATA_HOME":str(isolated/"data"),
         "TEMP":str(isolated/"temp"),"TMP":str(isolated/"temp"),
         "CRAFT_ANYTHING_CONFIG_DIR":str(isolated/"config"),
         "PYTHONIOENCODING":"utf-8","PYTHONDONTWRITEBYTECODE":"1","PYTHONUTF8":"1"}
    (isolated/"config"/"config.toml").write_text("version = 3\n[plugins]\nmcp = []\nwasm = []\n",encoding="utf-8")
    return isolated,env


def run_one(output,tool,case,source,cli):
    fixture=HERE/case["file"]
    isolated,env=environment(output,tool,case["id"])
    if tool=="craft-cli":
        params={"path":str(fixture),"format":"har","limit":1000,"offset":0,"hidden_values":[]}
        command=[str(cli),"--deep","web-recording-show","--json",json.dumps(params)]
    else:
        command=[sys.executable,"-I",str(Path(__file__).resolve()),"--worker",str(source),str(fixture)]
    start=time.perf_counter()
    try:
        result=subprocess.run(command,input=b"",capture_output=True,env=env,cwd=isolated/"cwd",timeout=20)
        stdout,stderr,code,timed_out=result.stdout,result.stderr,result.returncode,False
    except subprocess.TimeoutExpired as exc:
        stdout,stderr,code,timed_out=exc.stdout or b"",exc.stderr or b"",None,True
    elapsed=time.perf_counter()-start
    destination=output/"outputs"/tool
    destination.mkdir(parents=True,exist_ok=True)
    (destination/(case["id"]+".stdout.json")).write_bytes(stdout)
    (destination/(case["id"]+".stderr.txt")).write_bytes(stderr)
    try:
        parsed=json.loads(stdout)
    except (ValueError,UnicodeError):
        parsed=None
    record={"tool":tool,"case":case["id"],"exit_code":code,"timeout":timed_out,
            "runtime_ms":round(elapsed*1000,3),"stdout_bytes":len(stdout),"stderr_bytes":len(stderr),
            "stdout_sha256":sha(stdout),"json_output":parsed is not None,
            "accepted":code==0 and parsed is not None and "error" not in parsed,
            "fixture_sha256":case["sha256"],"environment_keys":sorted(env),
            "isolation":isolated.relative_to(output).as_posix()}
    (destination/(case["id"]+".run.json")).write_text(json.dumps(record,indent=2)+"\n",encoding="utf-8")
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source",type=Path,required=True)
    parser.add_argument("--upgraded-source",type=Path,required=True)
    parser.add_argument("--craft-cli",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True,help="New, nonexistent directory for this run")
    parser.add_argument("--allow-new-source",action="store_true",help="Explicitly compare a different Python module map and record its hashes; this is not exact reproduction")
    args=parser.parse_args()
    if os.name!="nt":
        parser.error("The pinned released CLI is Windows x64. Projection of recorded outputs works on other systems; this runner requires Windows.")
    manifest=verify_manifest()
    cli=args.craft_cli.resolve()
    if not cli.is_file() or sha(cli.read_bytes())!=CLI_SHA256:
        parser.error("The supplied CLI does not match the pinned release executable SHA-256")
    output=args.output.resolve()
    if output.exists():
        parser.error("Choose a nonexistent output directory; historical results are never overwritten")
    output.mkdir(parents=True)
    sources={tool:freeze_source(source.resolve(),output,tool,args.allow_new_source)
             for tool,source in [("surface-baseline",args.baseline_source),("surface-upgraded",args.upgraded_source)]}
    for name in ["manifest.json","manifest.sha256","source-pins.json","source-pins.sha256"]:
        (output/name).write_bytes((HERE/name).read_bytes())
    for tool in ["surface-baseline","surface-upgraded","craft-cli"]:
        records=[run_one(output,tool,case,sources.get(tool),cli) for case in manifest["cases"]]
        (output/"outputs"/tool/"runs.json").write_text(json.dumps(records,indent=2)+"\n",encoding="utf-8")
        print(json.dumps({"tool":tool,"cases":len(records),"timeouts":sum(r["timeout"] for r in records),
                          "accepted":sum(r["accepted"] for r in records)}))
    print("Finished. Use summarize_comparison.py --root <output-directory> to project this run. Review raw output before publishing.")


if __name__=="__main__":
    if len(sys.argv)>1 and sys.argv[1]=="--worker":
        worker(*sys.argv[2:])
    else:
        try:
            main()
        except ValueError as exc:
            print("Preflight rejected: "+str(exc),file=sys.stderr)
            raise SystemExit(2)
        except OSError:
            print("Preflight or result I/O failed; check the supplied ordinary local paths and permissions.",file=sys.stderr)
            raise SystemExit(2)
