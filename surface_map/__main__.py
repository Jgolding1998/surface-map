import argparse
import json
import sys
from pathlib import Path

from .server import make_server
from .source_io import capture_sources, inspect_sources, selected_sources


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid command line. Use --help for supported options.\n")


def main(argv=None):
    parser = Parser(prog="surface-map", description="Explore authorized saved web artifacts or selected source files. No network retrieval or execution.")
    parser.add_argument("--port", type=int, default=8768)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("setup-js", help="Explicitly install the pinned optional JavaScript parser with lifecycle scripts disabled")
    inspect = commands.add_parser("inspect", help="Print a minimized static source inventory as JSON")
    inspect.add_argument("--javascript-engine", choices=("auto", "basic", "enhanced"), default="auto")
    inputs = inspect.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--capture", type=Path, help="Source capture JSON with an artifacts array")
    inputs.add_argument("--files", nargs="+", type=Path, help="1 to 6 explicit local manifests or supported source files")
    args = parser.parse_args(argv)
    if args.command == "setup-js":
        from .js_parser import setup
        return setup()
    if args.command == "inspect":
        try:
            artifacts = capture_sources(args.capture) if args.capture else selected_sources(args.files)
            report = inspect_sources(artifacts, javascript_engine=args.javascript_engine)
            print(json.dumps(report, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
            return 0
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            print("Source inspection rejected: unsupported, malformed, or over limit. No source text or paths are returned.", file=sys.stderr)
            return 2
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    try:
        server = make_server(args.port)
    except OSError:
        parser.error("Could not bind the local port; select another port")
    print(f"Surface Map: http://127.0.0.1:{args.port}")
    print("Offline artifact analysis only. Ctrl+C stops the server.")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
