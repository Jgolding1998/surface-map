import argparse
import json
import sys
import webbrowser
from pathlib import Path

from .server import make_server
from .source_io import capture_sources, inspect_sources, selected_sources, read_ordinary
from .analyze import strict_json
from .project import inspect_project, selected_project


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid command line. Use --help for supported options.\n")


def main(argv=None):
    parser = Parser(prog="surface-map", description="Explore saved web artifacts and selected source files. The optional MCP interface also supports bounded public HTTPS capture. Captured source is never executed.")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--open", action="store_true", dest="open_browser",
                        help="Open the local workspace in your browser after the server starts")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("mcp", help="Run the optional MCP stdio interface for saved contents and public HTTPS pages")
    commands.add_parser("setup-js", help="Explicitly install the pinned optional JavaScript parser with lifecycle scripts disabled")
    analyze = commands.add_parser("analyze", help="Print a bounded offline saved-web-capture report as JSON")
    analyze.add_argument("--capture", type=Path, required=True, help="Local JSON with artifacts and optional document_url")
    analyze.add_argument("--javascript-engine", choices=("auto", "basic", "enhanced"), default="enhanced")
    compare = commands.add_parser("compare", help="Compare corresponding offline captures and apply explicit evidence-change policies")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    compare.add_argument("--javascript-engine", choices=("auto", "basic", "enhanced"), default="enhanced")
    compare.add_argument("--format", choices=("json", "markdown"), default="json")
    compare.add_argument("--fail-on", default="", help="Comma-separated added, absent, changed, unresolved, warnings; default advisory")
    inspect = commands.add_parser("inspect", help="Print a minimized static source inventory as JSON")
    inspect.add_argument("--javascript-engine", choices=("auto", "basic", "enhanced"), default="auto")
    inputs = inspect.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--capture", type=Path, help="Source capture JSON with an artifacts array")
    inputs.add_argument("--files", nargs="+", type=Path, help="1 to 6 explicit local manifests or supported source files")
    project = commands.add_parser("project", help="Inspect explicitly selected project files without walking directories")
    project.add_argument("--root", type=Path, required=True, help="Explicit local root containing the selected files")
    project.add_argument("--files", nargs="+", type=Path, required=True, help="1 to 24 files; relative names are resolved within root")
    project.add_argument("--javascript-engine", choices=("auto", "basic", "enhanced"), default="auto")
    rebuild = commands.add_parser("rebuild", help="Plan a rebuild from saved evidence, optionally write a new local scaffold")
    rebuild.add_argument("--capture", type=Path, required=True, help="Local JSON containing only an artifacts array")
    rebuild.add_argument("--javascript-engine", choices=("auto", "basic", "enhanced"), default="auto")
    rebuild.add_argument("--scaffold", type=Path, help="Create a new directory for scaffold files; existing directories are refused")
    args = parser.parse_args(argv)
    if args.command == "mcp":
        from .mcp_server import main as mcp_main
        return mcp_main()
    if args.command == "setup-js":
        from .js_parser import setup
        return setup()
    if args.command in {"analyze", "compare"}:
        from . import ci
        try:
            if args.command == "analyze":
                report = ci.analyze_capture(ci.load_capture(args.capture), javascript_engine=args.javascript_engine)
                output, code = ci.json_output(report), 0
            else:
                report = ci.compare_captures(ci.load_capture(args.before), ci.load_capture(args.after),
                                             javascript_engine=args.javascript_engine, fail_on=args.fail_on)
                output = ci.markdown(report) if args.format == "markdown" else ci.json_output(report)
                code = 0 if report["policy"]["passed"] else 1
        except ci.CIError as error:
            print(str(error), file=sys.stderr)
            return 2
        except Exception:
            print("Offline capture rejected: unsupported, malformed, unavailable, or over limit. No input text or paths are returned.", file=sys.stderr)
            return 2
        print(output)
        return code
    if args.command == "inspect":
        try:
            artifacts = capture_sources(args.capture) if args.capture else selected_sources(args.files)
            report = inspect_sources(artifacts, javascript_engine=args.javascript_engine)
            print(json.dumps(report, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
            return 0
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            print("Source inspection rejected: unsupported, malformed, or over limit. No source text or paths are returned.", file=sys.stderr)
            return 2
    if args.command in {"project", "rebuild"}:
        try:
            if args.command == "project":
                report = inspect_project(selected_project(args.files, root=args.root), javascript_engine=args.javascript_engine)
            else:
                from .rebuild import plan_rebuild, write_scaffold
                document = strict_json(read_ordinary(args.capture, 16 * 1024 * 1024), depth=6, nodes=100)
                if not isinstance(document, dict) or set(document) != {"artifacts"}:
                    raise ValueError
                report = plan_rebuild(document["artifacts"], javascript_engine=args.javascript_engine)
                if args.scaffold is not None:
                    write_scaffold(report, args.scaffold)
            print(json.dumps(report, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
            return 0
        except Exception:
            print("Selection rejected: unsupported, malformed, over limit, or destination already exists. No input text or paths are returned.", file=sys.stderr)
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
        if args.open_browser:
            try:
                opened = webbrowser.open(f"http://127.0.0.1:{args.port}", new=2)
            except Exception:
                opened = False
            if not opened:
                print(f"Browser could not open automatically. Open http://127.0.0.1:{args.port} yourself.")
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
