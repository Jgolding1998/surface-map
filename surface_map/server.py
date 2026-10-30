from __future__ import annotations

import hmac
import json
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .analyze import ArtifactError, MAX_ARTIFACTS, MAX_FILE_BYTES, MAX_HTML_BYTES, MAX_TOTAL_BYTES, analyze, strict_json
from .export import compare, contract, openapi
from .samples import artifacts
from .source_io import compare_sources, inspect_sources, source_examples
from .source import MAX_FILE_BYTES as SOURCE_FILE_BYTES, MAX_TOTAL_BYTES as SOURCE_TOTAL_BYTES

STATIC = Path(__file__).parent / "static"
MAX_REQUEST = 16 * 1024 * 1024 + 64 * 1024
MAX_SOURCE_REQUEST = 3 * 1024 * 1024
MAX_RESPONSE = 3 * 1024 * 1024
HEADER_TIMEOUT = 10.0
ANALYSIS_BUDGET = 10.0
CLIENT_TIMEOUT_MS = 35000


class OutputTooLarge(ValueError):
    pass


def encoded_response(value, limit):
    chunks, size = [], 0
    encoder = json.JSONEncoder(allow_nan=False, separators=(",", ":"))
    for chunk in encoder.iterencode(value):
        part = chunk.encode("utf-8")
        size += len(part)
        if size > limit:
            raise OutputTooLarge
        chunks.append(part)
    return b"".join(chunks)


def bundle(report):
    return {"report": report, "openapi": openapi(report), "contract": contract(report)}


def make_server(port=8768):
    token = secrets.token_hex(32)
    processing = threading.BoundedSemaphore(2)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def handle_one_request(self):
            self._headers_complete = threading.Event()

            def expire_headers():
                if not self._headers_complete.is_set():
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            self._header_timer = threading.Timer(HEADER_TIMEOUT, expire_headers)
            self._header_timer.daemon = True
            self._header_timer.start()
            try:
                super().handle_one_request()
            finally:
                self._header_timer.cancel()

        def parse_request(self):
            try:
                parsed = super().parse_request()
            finally:
                self._headers_complete.set()
                self._header_timer.cancel()
            if parsed and self.request_version not in {"HTTP/1.0", "HTTP/1.1"}:
                self.send_error(505)
                return False
            return parsed

        def log_message(self, *_):
            pass

        def reply(self, code, value, mime="application/json; charset=utf-8"):
            try:
                body = value if isinstance(value, bytes) else encoded_response(value, MAX_RESPONSE)
                if len(body) > MAX_RESPONSE:
                    raise OutputTooLarge
            except OutputTooLarge:
                code, body = 413, b'{"error":"Analysis output exceeds the 3 MiB limit. Use a smaller capture."}'
            self.send_response_only(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def send_error(self, code, message=None, explain=None):
            self.close_connection = True
            if getattr(self, "request_version", "HTTP/0.9") == "HTTP/0.9":
                self.request_version = "HTTP/1.0"
            self.reply(code, {"error": "Invalid HTTP request"})

        def allowed(self, post=False):
            if len(self.headers) > 32 or sum(len(key) + len(value) for key, value in self.headers.items()) > 16384:
                self.reply(431, {"error": "Request headers exceed the supported limit"})
                return False
            for name in ("Host", "Origin", "Content-Length", "Content-Type", "X-Surface-Token"):
                if len(self.headers.get_all(name, [])) > 1:
                    self.reply(400, {"error": "Ambiguous HTTP headers"})
                    return False
            port = self.server.server_address[1]
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in hosts:
                self.reply(403, {"error": "Unrecognized host"})
                return False
            origin = self.headers.get("Origin")
            if (origin is not None and origin != "http://" + self.headers["Host"]) or (post and origin is None) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.reply(403, {"error": "Cross-origin request rejected"})
                return False
            if post:
                received = self.headers.get("X-Surface-Token", "")
                if not re.fullmatch(r"[0-9a-f]{64}", received) or not hmac.compare_digest(received, token):
                    self.reply(403, {"error": "Missing or invalid request token"})
                    return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            if self.path == "/":
                body = (STATIC / "index.html").read_text(encoding="utf-8").replace("{{TOKEN}}", token).encode("utf-8")
                self.reply(200, body, "text/html; charset=utf-8")
            elif self.path in {"/app.js", "/styles.css"}:
                name = self.path[1:]
                mime = "text/javascript; charset=utf-8" if name.endswith("js") else "text/css; charset=utf-8"
                self.reply(200, (STATIC / name).read_bytes(), mime)
            elif self.path == "/health":
                self.reply(200, {"ok": True})
            elif self.path == "/api/limits":
                self.reply(200, {"web": {"max_artifacts": MAX_ARTIFACTS, "html_bytes": MAX_HTML_BYTES,
                                        "other_file_bytes": MAX_FILE_BYTES, "total_bytes": MAX_TOTAL_BYTES},
                                 "source": {"max_artifacts": MAX_ARTIFACTS, "file_bytes": SOURCE_FILE_BYTES,
                                            "total_bytes": SOURCE_TOTAL_BYTES},
                                 "request_bytes": MAX_REQUEST, "source_request_bytes": MAX_SOURCE_REQUEST,
                                 "response_bytes": MAX_RESPONSE, "analysis_seconds": ANALYSIS_BUDGET,
                                 "request_timeout_ms": CLIENT_TIMEOUT_MS})
            else:
                self.reply(404, {"error": "Not found"})

        def body(self):
            if self.headers.get("Transfer-Encoding") is not None:
                raise ArtifactError("Transfer encoding is unsupported")
            length = self.headers.get("Content-Length", "")
            limit = MAX_REQUEST if self.path in {"/api/analyze", "/api/compare"} else MAX_SOURCE_REQUEST
            if not re.fullmatch(r"[0-9]{1,10}", length) or not 1 <= int(length) <= limit:
                raise ArtifactError(f"Request must include Content-Length and fit within {limit:,} encoded JSON bytes. Use a smaller capture or comparison.")
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ArtifactError("Expected application/json")
            remaining, chunks, deadline = int(length), [], time.monotonic() + 8
            while remaining:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise ArtifactError("Upload took too long")
                self.connection.settimeout(min(2, left))
                block = self.rfile.read1(min(65536, remaining))
                if not block:
                    raise ArtifactError("Incomplete HTTP body")
                chunks.append(block)
                remaining -= len(block)
            return strict_json(b"".join(chunks).decode("utf-8"), depth=6, nodes=100, prescan=False)

        def do_POST(self):
            if not self.allowed(post=True):
                return
            if self.path not in {"/api/analyze", "/api/compare", "/api/demo", "/api/demo-diff", "/api/source", "/api/source-compare", "/api/source-demo"}:
                self.reply(404, {"error": "Not found"})
                return
            if not processing.acquire(blocking=False):
                self.reply(503, {"error": "Analysis is busy; try again"})
                return
            try:
                document = self.body()
                deadline = time.monotonic() + ANALYSIS_BUDGET
                if not isinstance(document, dict):
                    raise ArtifactError("Request must be a JSON object")
                engine = document.get("javascript_engine", "auto")
                if not isinstance(engine, str) or engine not in ("auto", "basic", "enhanced"):
                    raise ArtifactError("Unsupported JavaScript analysis engine")
                keys = set(document) - {"javascript_engine"}
                if self.path == "/api/analyze" and keys == {"artifacts"}:
                    result = bundle(analyze(document["artifacts"], javascript_engine=engine, deadline=deadline))
                elif self.path == "/api/compare" and keys == {"baseline", "candidate"}:
                    result = {"diff": compare(analyze(document["baseline"], javascript_engine=engine, deadline=deadline), analyze(document["candidate"], javascript_engine=engine, deadline=deadline))}
                elif self.path == "/api/demo" and not keys:
                    result = bundle(analyze(artifacts(), javascript_engine=engine))
                elif self.path == "/api/demo-diff" and not keys:
                    result = {"diff": compare(analyze(artifacts(), javascript_engine=engine), analyze(artifacts(2), javascript_engine=engine))}
                elif self.path == "/api/source" and keys == {"artifacts"}:
                    result = {"inventory": inspect_sources(document["artifacts"], javascript_engine=engine)}
                elif self.path == "/api/source-compare" and keys == {"baseline", "candidate"}:
                    result = {"diff": compare_sources(inspect_sources(document["baseline"], javascript_engine=engine), inspect_sources(document["candidate"], javascript_engine=engine))}
                elif self.path == "/api/source-demo" and not keys:
                    result = {"inventory": inspect_sources(source_examples(), javascript_engine=engine)}
                else:
                    raise ArtifactError("Unsupported request shape")
                self.reply(200, result)
            except ArtifactError as error:
                self.reply(422, {"error": str(error)})
            except (UnicodeError, TypeError, ValueError, RecursionError, OverflowError):
                self.reply(422, {"error": "Artifact rejected: unsupported, malformed or over limit. No source text is returned."})
            except (OSError, TimeoutError):
                try:
                    self.reply(408, {"error": "Upload disconnected or exceeded its deadline"})
                except OSError:
                    pass
            finally:
                processing.release()

        def do_OPTIONS(self):
            self.reply(405, {"error": "Method unsupported"})

        do_PUT = do_OPTIONS
        do_PATCH = do_OPTIONS
        do_DELETE = do_OPTIONS

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

        def server_bind(self):
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

        def __init__(self, *args):
            self.connections = threading.BoundedSemaphore(16)
            super().__init__(*args)

        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(2)
            return connection, address

        def process_request(self, request, address):
            if not self.connections.acquire(blocking=False):
                try:
                    request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    request.shutdown(socket.SHUT_WR)
                    request.settimeout(0.05)
                    remaining, deadline = 16 * 1024, time.monotonic() + 0.1
                    while remaining > 0 and time.monotonic() < deadline:
                        block = request.recv(min(4096, remaining))
                        if not block:
                            break
                        remaining -= len(block)
                except OSError:
                    pass
                finally:
                    self.shutdown_request(request)
                return
            try:
                super().process_request(request, address)
            except BaseException:
                self.connections.release()
                raise

        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                self.connections.release()

        def handle_error(self, request, address):
            pass

    return Server(("127.0.0.1", port), Handler)
