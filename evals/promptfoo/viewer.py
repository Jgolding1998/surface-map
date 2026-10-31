"""Serve only generated HTML reports over loopback, without exposing UI history."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

FOLDER = Path(__file__).resolve().parent / "results"
REPORTS = ("basic.html", "enhanced.html", "mcp.html")


class ReportHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        host = self.headers.get("Host", "")
        if host not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
            self.send_error(403)
            return
        path = urlsplit(self.path).path
        if path == "/":
            links = "".join(f'<li><a href="/{name}">{name}</a></li>' for name in REPORTS
                            if (FOLDER / name).is_file() and not (FOLDER / name).is_symlink())
            content = ('<!doctype html><html lang="en"><meta charset="utf-8">'
                       '<title>Surface Map evaluation reports</title>'
                       '<h1>Surface Map evaluation reports</h1>'
                       '<p>Local reports of existing synthetic tasks. Repetitions are not independent sites.</p>'
                       f'<ul>{links}</ul></html>').encode("utf-8")
        elif path[1:] in REPORTS:
            target = FOLDER / path[1:]
            if not target.is_file() or target.is_symlink() or target.stat().st_size > 32 * 1024 * 1024:
                self.send_error(404)
                return
            content = target.read_bytes()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        pass


def main():
    with ThreadingHTTPServer(("127.0.0.1", 15500), ReportHandler) as server:
        print("Local evaluation reports: http://127.0.0.1:15500 (Ctrl+C to stop)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
