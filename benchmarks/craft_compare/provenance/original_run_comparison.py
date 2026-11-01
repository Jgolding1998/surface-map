"""Run only the audited offline HAR paths on predeclared, synthetic inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_inputs():
    raw = (ROOT / "manifest.json").read_bytes()
    expected = (ROOT / "manifest.sha256").read_text().split()[0]
    assert hashlib.sha256(raw).hexdigest() == expected, "Manifest changed after freeze"
    manifest = json.loads(raw)
    for case in manifest["cases"]:
        assert digest(ROOT/case["file"]) == case["sha256"], case["id"]
    for rel, sha in manifest["craft"]["source_sha256"].items():
        assert digest(ROOT/"reference"/rel) == sha, rel
    assert digest(ROOT/"vendor-cli"/"craft-anything-cli.exe") == "52a4249d3d9b6f321883b5048e774026856f5fe8e8afaca89984d02ffe8f0fd5"
    return manifest


def isolated_env(tool, identifier):
    isolated = ROOT/"isolation"/tool/identifier
    for name in ["home", "roaming", "local", "config", "data", "temp", "cwd"]:
        (isolated/name).mkdir(parents=True, exist_ok=True)
    systemroot = os.environ.get("SYSTEMROOT", r"C:\Windows")
    env = {"SYSTEMROOT":systemroot, "WINDIR":systemroot, "PATH":str(Path(systemroot)/"System32"),
           "HOME":str(isolated/"home"), "USERPROFILE":str(isolated/"home"),
           "APPDATA":str(isolated/"roaming"), "LOCALAPPDATA":str(isolated/"local"),
           "XDG_CONFIG_HOME":str(isolated/"config"), "XDG_DATA_HOME":str(isolated/"data"),
           "TEMP":str(isolated/"temp"), "TMP":str(isolated/"temp"),
           "CRAFT_ANYTHING_CONFIG_DIR":str(isolated/"config"),
           "PYTHONIOENCODING":"utf-8", "PYTHONDONTWRITEBYTECODE":"1", "PYTHONUTF8":"1"}
    (isolated/"config"/"config.toml").write_text("version = 3\n[plugins]\nmcp = []\nwasm = []\n",encoding="utf-8")
    return isolated, env


def surface_worker(source, fixture):
    sys.path.insert(0, str(Path(source).resolve()))
    from surface_map.analyze import analyze
    try:
        result = analyze([{"kind":"har","text":Path(fixture).read_text(encoding="utf-8")}],javascript_engine="basic")
        print(json.dumps({"result":result},ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error":{"type":type(exc).__name__,"message":str(exc)}}))
        raise SystemExit(1)


def prepare_baseline():
    destination = ROOT/"surface-baseline"
    if destination.exists():
        return destination
    with zipfile.ZipFile(ROOT/"surface-baseline.zip") as archive:
        for name in archive.namelist():
            path = destination/name
            assert path.resolve().is_relative_to(destination.resolve()), name
        archive.extractall(destination)
    return destination


def freeze_surface(source):
    """Copy analyzer Python modules into an immutable content-named local input."""
    package = source/"surface_map"
    files = sorted(package.rglob("*.py"))
    records = {}
    contents = {}
    for file in files:
        if file.is_symlink() or not file.is_file():
            raise ValueError("Only ordinary local Python module files can be frozen")
        relative = file.relative_to(source).as_posix()
        raw = file.read_bytes()
        contents[relative] = raw
        records[relative] = hashlib.sha256(raw).hexdigest()
    source_hash = hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()
    destination = ROOT/("surface-upgraded-freeze-"+source_hash[:16])
    destination.mkdir(exist_ok=True)
    for relative, raw in contents.items():
        target = destination/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            assert target.read_bytes()==raw, "Content-named source snapshot changed"
        else:
            target.write_bytes(raw)
    provenance = {"source_path":str(source),"frozen_path":str(destination),"module_tree_sha256":source_hash,
                  "files_sha256":records,"scope":"Python analyzer modules only, using basic HAR analysis. Not a full repository or build snapshot."}
    (destination/"freeze.json").write_text(json.dumps(provenance,indent=2)+"\n",encoding="utf-8")
    return destination, provenance


def run_one(tool, case, source=None):
    isolated, env = isolated_env(tool, case["id"])
    fixture = ROOT/case["file"]
    if tool == "craft-cli":
        params = {"path":str(fixture),"format":"har","limit":1000,"offset":0,"hidden_values":[]}
        argv = [str(ROOT/"vendor-cli"/"craft-anything-cli.exe"),"--deep","web-recording-show","--json",json.dumps(params)]
    else:
        argv = [sys.executable,"-I",str(Path(__file__).resolve()),"--worker",str(source),str(fixture)]
    started = time.perf_counter()
    try:
        process = subprocess.run(argv,input=b"",stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,cwd=isolated/"cwd",timeout=20)
        stdout,stderr,code = process.stdout,process.stderr,process.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout,stderr,code = exc.stdout or b"",exc.stderr or b"",None
        timed_out = True
    elapsed = time.perf_counter()-started
    output = ROOT/"outputs"/tool
    output.mkdir(parents=True,exist_ok=True)
    (output/(case["id"]+".stdout.json")).write_bytes(stdout)
    (output/(case["id"]+".stderr.txt")).write_bytes(stderr)
    try:
        parsed = json.loads(stdout)
        json_ok = True
    except (ValueError,UnicodeError):
        parsed = None
        json_ok = False
    record = {"tool":tool,"case":case["id"],"exit_code":code,"timeout":timed_out,"runtime_ms":round(elapsed*1000,3),
              "stdout_bytes":len(stdout),"stderr_bytes":len(stderr),"stdout_sha256":hashlib.sha256(stdout).hexdigest(),
              "json_output":json_ok,"accepted":code==0 and parsed is not None and "error" not in parsed,
              "fixture_sha256":case["sha256"],"environment_keys":sorted(env),"isolation":str(isolated.relative_to(ROOT))}
    (output/(case["id"]+".run.json")).write_text(json.dumps(record,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(record))
    return record


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--tool",choices=["craft-cli","surface-baseline","surface-upgraded"],required=True)
    parser.add_argument("--source")
    parser.add_argument("--only")
    args=parser.parse_args()
    manifest=verify_inputs()
    if args.tool=="surface-baseline":
        source=prepare_baseline()
    elif args.tool=="surface-upgraded":
        if not args.source:
            parser.error("--source is required for the upgraded frozen checkout")
        source, frozen = freeze_surface(Path(args.source).resolve())
    else:
        source=None
    if source:
        snapshot={"tool":args.tool,"path":str(source),"analyze_sha256":digest(source/"surface_map"/"analyze.py"),
                  "init_sha256":digest(source/"surface_map"/"__init__.py"),"fixture_manifest_sha256":digest(ROOT/"manifest.json")}
        if args.tool=="surface-upgraded":
            snapshot["freeze"]=frozen
        output=ROOT/"outputs"/args.tool
        output.mkdir(parents=True,exist_ok=True)
        (output/"source-snapshot.json").write_text(json.dumps(snapshot,indent=2)+"\n",encoding="utf-8")
    rows=[run_one(args.tool,case,source) for case in manifest["cases"] if not args.only or args.only==case["id"]]
    (ROOT/"outputs"/args.tool/"runs.json").write_text(json.dumps(rows,indent=2)+"\n",encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv)>1 and sys.argv[1]=="--worker":
        surface_worker(*sys.argv[2:])
    else:
        main()
