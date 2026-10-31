"""Run the existing browser checks against a temporary local workspace server.

This is a development/CI entry point, not a dependency of the user-facing tool.
It never installs packages, creates screenshots or uploads artifacts.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading


ROOT = Path(__file__).resolve().parents[1]
CHECKS = ("handoff.cjs", "browser_rebuild.cjs", "enhanced_browser.cjs", "public_url_browser.cjs")
CHECK_TIMEOUT_SECONDS = 90


def main():
    # Running this file directly otherwise puts tests/, rather than the checkout,
    # first on sys.path. Keep imports independent of the caller's working directory.
    sys.path.insert(0, str(ROOT))
    from surface_map import js_parser
    from surface_map.server import make_server

    node = os.environ.get("SURFACE_MAP_NODE") or shutil.which("node")
    if not node:
        print("Browser checks require Node.js and the optional syntax parser.", file=sys.stderr)
        return 2
    if not js_parser.available()[0]:
        print("Browser checks require the supported Node version and installed optional syntax parser.", file=sys.stderr)
        return 2

    environment = os.environ.copy()
    browser_modules = ROOT / "tests" / "browser" / "node_modules"
    configured_modules = environment.get("NODE_PATH")
    if browser_modules.is_dir():
        environment["NODE_PATH"] = str(browser_modules) + (os.pathsep + configured_modules if configured_modules else "")
    elif not configured_modules:
        print("Install the pinned development browser driver in tests/browser before running these checks.", file=sys.stderr)
        return 2
    # A test fixture starts Python itself; use this interpreter, not another
    # Python found incidentally on PATH. Leave PATH and the parent environment alone.
    environment["SURFACE_MAP_TEST_PYTHON"] = sys.executable
    environment.pop("SURFACE_REBUILD_SCREENSHOT_DIR", None)
    environment.pop("SURFACE_PUBLIC_SCREENSHOT_DIR", None)

    server = make_server(0)
    server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    environment["SURFACE_TEST_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        server_thread.start()
        for name in CHECKS:
            print(f"Running {name}", flush=True)
            try:
                result = subprocess.run([node, str(ROOT / "tests" / name)], cwd=ROOT,
                                        env=environment, timeout=CHECK_TIMEOUT_SECONDS, check=False)
            except subprocess.TimeoutExpired:
                print(f"{name} exceeded its {CHECK_TIMEOUT_SECONDS}-second limit.", file=sys.stderr)
                return 1
            except OSError:
                print(f"{name} could not start. Check the local Node and browser setup.", file=sys.stderr)
                return 2
            if result.returncode:
                return 1
        return 0
    finally:
        if server_thread.is_alive():
            server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
