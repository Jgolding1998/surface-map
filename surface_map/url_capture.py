"""Bounded public HTTPS retrieval. Raw contents and document_url stay internal.

This module fetches a document and selected same-origin external scripts; it
does not render pages, execute source, replay APIs/forms or follow page links.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from html.parser import HTMLParser
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from .analyze import HTML_FOREIGN_BREAKOUT, MAX_ARTIFACTS, MAX_FILE_BYTES, MAX_HTML_BYTES, MAX_HTML_TAGS, MAX_TOTAL_BYTES

MAX_URL_LENGTH = 2048
MAX_SCRIPTS = 5
MAX_REQUESTS = 9
MAX_REDIRECTS = 3
CAPTURE_SECONDS = 20.0
DNS_SECONDS = 4.0
SOCKET_SECONDS = 5.0
MAX_DNS_ANSWERS = 32
MAX_HEADER_BYTES = 32768
_DNS_SLOT = threading.BoundedSemaphore(1)
_HTML_TYPES = {"text/html"}
_JS_TYPES = {"text/javascript", "application/javascript", "application/ecmascript", "text/ecmascript", "application/x-javascript"}
_REDIRECTS = {301, 302, 303, 307, 308}
_HTML_WHITESPACE = "\t\n\f\r "
_LOCAL_SUFFIXES = ("localhost", "local", "localdomain", "internal", "lan", "home.arpa", "onion", "invalid", "test", "example")
_IPV4_EXCLUDED = tuple(ipaddress.ip_network(value) for value in ("192.0.0.0/24", "192.88.99.0/24", "168.63.129.16/32"))
_IPV6_GLOBAL = ipaddress.ip_network("2000::/3")
_IPV6_EXCLUDED = tuple(ipaddress.ip_network(value) for value in ("2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20"))

_MESSAGES = {
    "invalid_url": "Provide a valid public HTTPS URL on port 443 without credentials or control characters.",
    "blocked_target": "The target is not an allowed public unicast HTTPS destination.",
    "dns_busy": "A previous DNS lookup is still pending; retry later.",
    "dns_timeout": "DNS resolution exceeded its time limit.",
    "dns_failed": "Public DNS resolution failed or returned unsupported answers.",
    "timeout": "Capture exceeded its network time limit.",
    "request_limit": "Capture reached its request limit.",
    "redirect_limit": "Capture reached its redirect limit.",
    "redirect_origin": "A script redirect left the document origin.",
    "http_status": "The server did not return a supported successful response.",
    "response_headers": "The response headers were missing, ambiguous or unsupported.",
    "content_type": "The response did not declare a supported HTML or JavaScript media type.",
    "content_encoding": "Compressed or unsupported response encodings are not accepted.",
    "text_encoding": "Only UTF-8 text and UTF-8 encoding declarations are supported.",
    "too_large": "The response exceeded the remaining capture byte limit.",
    "html_limit": "HTML script discovery exceeded its structural limit.",
    "html_unsupported": "The fetched HTML could not be parsed for supported script discovery.",
    "tls_error": "The verified TLS connection failed during certificate verification, handshake or transfer. Check the server and local trust configuration; verification remains enabled.",
    "network": "The HTTPS request failed; no target or response details are returned.",
}
_WARNINGS = {
    "scope": "Static HTTPS capture only: no browser rendering, API traffic, form submission, stylesheets, embedded pages or dynamic imports were captured.",
    "inline": "Inline JavaScript was not extracted or executed.",
    "scripts_disabled": "External script fetching was disabled.",
    "script_omitted": "Some external scripts were omitted because of origin, URL, type, duplication or selection limits.",
    "script_failed": "Some selected external scripts could not be captured within the public HTTPS, content or byte limits.",
    "deadline": "Script capture stopped at the shared time or network request limit.",
    "base": "An unsupported base URL prevented relative script resolution.",
    "foreign": "SVG and MathML content was omitted from script discovery; foreign-content coverage is incomplete.",
}


class CaptureError(ValueError):
    def __init__(self, code):
        self.code = code if code in _MESSAGES else "network"
        super().__init__(_MESSAGES[self.code])


@dataclass(frozen=True)
class _Target:
    url: str
    host: str
    path: str

    @property
    def origin(self):
        return self.host, 443


def _public_address(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not address.is_global or any((address.is_multicast, address.is_unspecified, address.is_reserved,
                                    address.is_loopback, address.is_link_local, address.is_private)):
        return False
    if address.version == 4:
        return not any(address in network for network in _IPV4_EXCLUDED)
    # Conservative across Python 3.11+ classifications: exclude translation,
    # mapped, transition, documentation and special-purpose IPv6 ranges.
    return address in _IPV6_GLOBAL and not any(address in network for network in _IPV6_EXCLUDED)


def _target(value):
    try:
        if (type(value) is not str or not value or len(value) > MAX_URL_LENGTH
                or any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value):
            raise CaptureError("invalid_url")
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or "@" in parsed.netloc
                or "%" in parsed.netloc or parsed.port not in (None, 443)):
            raise CaptureError("invalid_url")
        if parsed.hostname.endswith(".."):
            raise CaptureError("invalid_url")
        host = parsed.hostname.removesuffix(".").encode("idna").decode("ascii").lower()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if (len(host) > 253 or len(labels) < 2 or all(label.isdigit() for label in labels)
                    or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
                    or any(host == suffix or host.endswith("." + suffix) for suffix in _LOCAL_SUFFIXES)):
                raise CaptureError("blocked_target")
            authority = host
        else:
            if not _public_address(literal):
                raise CaptureError("blocked_target")
            host = str(literal)
            authority = f"[{host}]" if literal.version == 6 else host
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        normalized = urlunsplit(("https", authority, path, query, ""))
        if len(normalized) > MAX_URL_LENGTH:
            raise CaptureError("invalid_url")
        return _Target(normalized, host, path + ("?" + query if query else ""))
    except CaptureError:
        raise
    except (ValueError, UnicodeError, TypeError):
        raise CaptureError("invalid_url") from None


def _reference(base, value):
    # urljoin/urlsplit strip some leading control characters. Reject before
    # resolving a redirect, script or base attribute rather than after it.
    if (type(value) is not str or len(value) > MAX_URL_LENGTH or "\\" in value
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise CaptureError("invalid_url")
    try:
        return _target(urljoin(base, value))
    except CaptureError:
        raise
    except ValueError:
        raise CaptureError("invalid_url") from None


class _Budget:
    def __init__(self):
        self.deadline = time.monotonic() + CAPTURE_SECONDS
        self.requests = 0
        self.redirects = 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CaptureError("timeout")
        return remaining

    def request(self):
        self.remaining()
        if self.requests >= MAX_REQUESTS:
            raise CaptureError("request_limit")
        self.requests += 1


def _resolve(host, budget):
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not _public_address(literal):
            raise CaptureError("blocked_target")
        return [(socket.AF_INET6 if literal.version == 6 else socket.AF_INET, str(literal))]
    if not _DNS_SLOT.acquire(blocking=False):
        raise CaptureError("dns_busy")
    completed, result = threading.Event(), []

    def lookup():
        try:
            result.append(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP))
        except Exception:
            result.append(None)
        finally:
            # A timeout cannot cancel getaddrinfo. Retain the only slot until
            # it really exits, bounding abandoned resolver threads to one.
            _DNS_SLOT.release()
            completed.set()

    try:
        worker = threading.Thread(target=lookup, name="surface-map-dns", daemon=True)
        worker.start()
    except Exception:
        _DNS_SLOT.release()
        raise CaptureError("dns_failed") from None
    if not completed.wait(min(DNS_SECONDS, budget.remaining())):
        raise CaptureError("dns_timeout")
    budget.remaining()
    records = result[0]
    if not records or len(records) > MAX_DNS_ANSWERS:
        raise CaptureError("dns_failed")
    addresses = []
    for family, socktype, protocol, _, address in records:
        if (family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM
                or protocol != socket.IPPROTO_TCP or not address or not _public_address(address[0])
                or (family == socket.AF_INET6 and len(address) > 3 and address[3] != 0)):
            raise CaptureError("blocked_target")
        item = (family, str(ipaddress.ip_address(address[0])))
        if item not in addresses:
            addresses.append(item)
    # Many desktop networks have DNS AAAA answers but no usable IPv6 route.
    # Every answer was vetted; choosing IPv4 first does not re-resolve it.
    return sorted(addresses, key=lambda item: item[0] != socket.AF_INET)


class _PinnedHTTPSConnection(http.client.HTTPConnection):
    default_port = 443

    def __init__(self, target, address, budget):
        super().__init__(target.host, 443, timeout=min(SOCKET_SECONDS, budget.remaining()))
        self._address, self._budget = address, budget
        self._active = None
        self._socket_lock = threading.Lock()
        self.expired = False

    def _use_socket(self, sock):
        with self._socket_lock:
            if self.expired:
                sock.close()
                raise CaptureError("timeout")
            self.sock = self._active = sock

    def connect(self):
        family, address = self._address
        plain = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        self._use_socket(plain)
        plain.settimeout(min(SOCKET_SECONDS, self._budget.remaining()))
        # Numeric connect performs no second DNS lookup after validation.
        plain.connect((address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443))
        context = ssl.create_default_context()
        context.set_alpn_protocols(["http/1.1"])
        secured = context.wrap_socket(plain, server_hostname=self.host, do_handshake_on_connect=False)
        self._use_socket(secured)
        secured.settimeout(min(SOCKET_SECONDS, self._budget.remaining()))
        secured.do_handshake()

    def abort(self):
        with self._socket_lock:
            self.expired = True
            active = self._active
        if active is not None:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                active.close()
            except OSError:
                pass


@contextmanager
def _open_response(target, address, budget):
    connection = _PinnedHTTPSConnection(target, address, budget)
    response = None
    # Socket timeouts alone reset on progress. Shutdown also interrupts slow
    # status/header lines and chunk framing at the shared capture deadline.
    timer = threading.Timer(budget.remaining(), connection.abort)
    timer.daemon = True
    timer.start()
    try:
        connection.request("GET", target.path, headers={"Accept-Encoding": "identity", "Connection": "close",
                                                      "User-Agent": "Surface-Map/1.0"})
        response = connection.getresponse()
        yield response
    except CaptureError:
        raise
    except ssl.SSLError:
        if connection.expired or time.monotonic() >= budget.deadline:
            raise CaptureError("timeout") from None
        raise CaptureError("tls_error") from None
    except Exception:
        if connection.expired or time.monotonic() >= budget.deadline:
            raise CaptureError("timeout") from None
        raise CaptureError("network") from None
    finally:
        timer.cancel()
        if response is not None:
            response.close()
        connection.close()
        connection.abort()


def _headers(response):
    pairs = response.getheaders()
    if len(pairs) > 64 or sum(len(key) + len(value) for key, value in pairs) > MAX_HEADER_BYTES:
        raise CaptureError("response_headers")
    result = {}
    for key, value in pairs:
        name = key.lower()
        if name in {"content-type", "content-encoding", "content-length", "transfer-encoding", "location"}:
            if name in result or any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise CaptureError("response_headers")
            result[name] = value.strip()
    if "transfer-encoding" in result:
        if result["transfer-encoding"].lower() != "chunked" or "content-length" in result:
            raise CaptureError("response_headers")
    return result


def _media_type(header):
    if not header:
        raise CaptureError("content_type")
    parts = header.split(";")
    charsets = [part.split("=", 1)[1].strip().strip('"').lower() for part in parts[1:]
                if "=" in part and part.split("=", 1)[0].strip().lower() == "charset"]
    if len(charsets) > 1 or any(charset not in {"utf-8", "utf8"} for charset in charsets):
        raise CaptureError("text_encoding")
    return parts[0].strip().lower()


def _fetch(target, kind, budget, byte_limit, origin=None):
    while True:
        budget.remaining()
        if origin is not None and target.origin != origin:
            raise CaptureError("redirect_origin")
        addresses = _resolve(target.host, budget)
        budget.request()
        with _open_response(target, addresses[0], budget) as response:
            headers = _headers(response)
            if response.status in _REDIRECTS:
                if budget.redirects >= MAX_REDIRECTS:
                    raise CaptureError("redirect_limit")
                location = headers.get("location")
                if not location:
                    raise CaptureError("response_headers")
                # Validate each hop before another DNS lookup or request.
                target = _reference(target.url, location)
                budget.redirects += 1
                continue
            if response.status != 200:
                raise CaptureError("http_status")
            if headers.get("content-encoding", "identity").lower() != "identity":
                raise CaptureError("content_encoding")
            if _media_type(headers.get("content-type")) not in (_HTML_TYPES if kind == "html" else _JS_TYPES):
                raise CaptureError("content_type")
            length = headers.get("content-length")
            if length is not None:
                if not re.fullmatch(r"[0-9]{1,20}", length):
                    raise CaptureError("response_headers")
                length = int(length)
                if length > byte_limit:
                    raise CaptureError("too_large")
            chunks, size = [], 0
            while True:
                budget.remaining()
                chunk = response.read1(min(65536, byte_limit - size + 1))
                budget.remaining()
                if not chunk:
                    break
                size += len(chunk)
                if size > byte_limit:
                    raise CaptureError("too_large")
                chunks.append(chunk)
            if length is not None and length != size:
                raise CaptureError("response_headers")
            try:
                text = b"".join(chunks).decode("utf-8")
            except UnicodeError:
                raise CaptureError("text_encoding") from None
            return text, target


class _Scripts(HTMLParser):
    def __init__(self, budget):
        super().__init__(convert_charrefs=True)
        self.budget = budget
        self.tags = 0
        self.base = None
        self.scripts = []
        self.inline = 0
        self._raw_text = None
        self._templates = 0
        self._foreign = []
        self.foreign_seen = False

    def set_cdata_mode(self, *args, **kwargs):
        if not getattr(self, "_foreign", None):
            return super().set_cdata_mode(*args, **kwargs)

    def _tag(self):
        self.budget.remaining()
        self.tags += 1
        if self.tags > MAX_HTML_TAGS:
            raise CaptureError("html_limit")

    def _foreign_breakout(self, tag, attrs):
        return tag in HTML_FOREIGN_BREAKOUT or (tag == "font" and any(
            key in {"color", "face", "size"} for key, _ in attrs))

    def handle_starttag(self, tag, attrs):
        self._tag()
        if self._raw_text is not None:
            return
        if self._templates:
            if tag == "template":
                self._templates += 1
            return
        if self._foreign and self._foreign_breakout(tag, attrs):
            self._foreign.clear()
        if self._foreign or tag in {"svg", "math"}:
            if tag in {"svg", "math"}:
                if len(self._foreign) >= 64:
                    raise CaptureError("html_limit")
                self._foreign.append(tag)
                self.foreign_seen = True
            return
        if tag == "template":
            self._templates += 1
            return
        if tag in {"title", "textarea", "xmp", "iframe", "noembed", "noframes", "noscript", "plaintext"}:
            self._raw_text = tag
            return
        attributes = {}
        for name, value in attrs:
            attributes.setdefault(name, value)
        if tag == "base" and self.base is None and "href" in attributes:
            self.base = (attributes["href"] or "").strip(_HTML_WHITESPACE)
        if tag == "meta":
            charset = attributes.get("charset")
            if charset is not None and charset.lower() not in {"utf-8", "utf8"}:
                raise CaptureError("text_encoding")
            if (attributes.get("http-equiv") or "").lower() == "content-type":
                _media_type(attributes.get("content"))
        if tag == "script":
            kind = (attributes.get("type") or "").strip().lower()
            javascript = kind in {"", "module"} or kind in _JS_TYPES
            if "src" in attributes:
                self.scripts.append(((attributes["src"] or "").strip(_HTML_WHITESPACE), self.getpos()[0], javascript))
            elif javascript:
                self.inline += 1

    def handle_endtag(self, tag):
        self.budget.remaining()
        if self._foreign:
            found = next((index for index in range(len(self._foreign) - 1, -1, -1)
                          if self._foreign[index] == tag), None)
            if found is not None:
                del self._foreign[found:]
            return
        if self._raw_text is not None:
            if self._raw_text == tag and tag != "plaintext":
                self._raw_text = None
        elif tag == "template" and self._templates:
            self._templates -= 1

    def handle_startendtag(self, tag, attrs):
        ignored = self._raw_text is not None or bool(self._templates)
        if not ignored and self._foreign and self._foreign_breakout(tag, attrs):
            self.handle_starttag(tag, attrs)
            return
        if not ignored and (self._foreign or tag in {"svg", "math"}):
            self._tag()
            if tag in {"svg", "math"}:
                self.foreign_seen = True
            return
        # In HTML, a slash does not close non-void raw-text/template elements.
        # Treat their following text conservatively instead of fetching an
        # example that HTMLParser would otherwise expose as a real script.
        self.handle_starttag(tag, attrs)
        if not ignored and tag in {"script", "style"}:
            self._raw_text = tag

    def handle_pi(self, data):
        if data.lower().startswith("xml "):
            encoding = re.search(r"\bencoding\s*=\s*(['\"])([^'\"]+)\1", data, re.IGNORECASE)
            if encoding and encoding[2].lower() not in {"utf-8", "utf8"}:
                raise CaptureError("text_encoding")


def capture_public_url(url, include_scripts=True):
    """Fetch a bounded public document selection; do not return artifacts to an agent.

    ``document_url`` is the final validated URL for resolving document-relative
    evidence during analysis. It and ``artifacts`` are internal inputs, unlike
    the minimized ``capture`` metadata and fixed ``warnings``.
    """
    try:
        if type(include_scripts) is not bool:
            raise CaptureError("invalid_url")
        budget = _Budget()
        text, document = _fetch(_target(url), "html", budget, min(MAX_HTML_BYTES, MAX_TOTAL_BYTES))
        parser = _Scripts(budget)
        try:
            parser.feed(text)
            parser.close()
        except CaptureError:
            raise
        except Exception:
            raise CaptureError("html_unsupported") from None
        budget.remaining()
        artifacts = [{"kind": "html", "text": text}]
        total = len(text.encode("utf-8"))
        sources = [{"id": "source_1", "kind": "html", "bytes": total}]
        warnings = [_WARNINGS["scope"]]
        if parser.foreign_seen:
            warnings.append(_WARNINGS["foreign"])
        if parser.inline:
            warnings.append(_WARNINGS["inline"])
        base, bad_base = document.url, False
        if parser.base is not None:
            try:
                base = _reference(document.url, parser.base).url
            except (CaptureError, ValueError):
                bad_base = True
                warnings.append(_WARNINGS["base"])
        requested, seen, omitted, failed = 0, set(), False, False
        if not include_scripts:
            warnings.append(_WARNINGS["scripts_disabled"])
        else:
            for reference, line, javascript in parser.scripts:
                try:
                    if not javascript or not reference or (bad_base and not reference.startswith("https://")):
                        raise CaptureError("invalid_url")
                    script = _reference(base, reference)
                    if script.origin != document.origin or script.url in seen or requested >= MAX_SCRIPTS:
                        raise CaptureError("blocked_target")
                except (CaptureError, ValueError):
                    omitted = True
                    continue
                seen.add(script.url)
                requested += 1
                try:
                    if total >= MAX_TOTAL_BYTES:
                        raise CaptureError("too_large")
                    source_text, _ = _fetch(script, "javascript", budget, min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total), document.origin)
                except CaptureError as error:
                    failed = True
                    if error.code in {"timeout", "request_limit", "dns_timeout", "dns_busy"}:
                        warnings.append(_WARNINGS["deadline"])
                        break
                    continue
                size = len(source_text.encode("utf-8"))
                total += size
                artifacts.append({"kind": "javascript", "text": source_text})
                sources.append({"id": f"source_{len(artifacts)}", "kind": "javascript", "bytes": size,
                                "declared_by": "source_1", "location": f"line {line}"})
        if omitted:
            warnings.append(_WARNINGS["script_omitted"])
        if failed:
            warnings.append(_WARNINGS["script_failed"])
        return {"artifacts": artifacts, "document_url": document.url,
                "capture": {"mode": "public_https_static", "requested": 1 + requested, "fetched": len(artifacts),
                            "external_scripts_seen": len(parser.scripts), "scripts_requested": requested,
                            "scripts_fetched": len(artifacts) - 1, "scripts_skipped": len(parser.scripts) - len(artifacts) + 1,
                            "inline_scripts_skipped": parser.inline, "network_requests": budget.requests,
                            "redirects": budget.redirects, "bytes": total, "sources": sources,
                            "limits": {"artifacts": MAX_ARTIFACTS, "scripts": MAX_SCRIPTS, "requests": MAX_REQUESTS,
                                       "redirects": MAX_REDIRECTS, "seconds": CAPTURE_SECONDS,
                                       "html_bytes": MAX_HTML_BYTES, "script_bytes": MAX_FILE_BYTES, "total_bytes": MAX_TOTAL_BYTES}},
                "warnings": warnings}
    except CaptureError:
        raise
    except Exception:
        raise CaptureError("network") from None
