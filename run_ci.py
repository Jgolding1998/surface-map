"""Absolute-path CLI launcher for use outside the trusted Surface Map checkout."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from surface_map.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
