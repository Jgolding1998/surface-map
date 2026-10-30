"""Absolute-path launcher for MCP clients; run from any working directory."""

from pathlib import Path
import sys

# PYTHONSAFEPATH/-P can omit the script directory. Resolve our own trusted
# checkout explicitly; never search a caller-selected working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from surface_map.mcp_server import main

if __name__ == "__main__":
    raise SystemExit(main())
