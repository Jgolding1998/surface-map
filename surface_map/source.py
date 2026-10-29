"""Bounded static inventory of source manifests and code files.

Nothing supplied is imported, executed, or evaluated. Python is parsed with
ast.parse only, TypeScript and shell are scanned lexically, and manifests are
parsed with tomllib or the strict JSON reader. Output is limited to fixed
labels from curated tables plus generic source IDs and line numbers.
"""

from __future__ import annotations

import ast
import copy
import io
import re
import shlex
import threading
import time
import tokenize
import tomllib
import warnings

from .analyze import ArtifactError, strict_json

FORMAT = "surface-map-source-v1"
SOURCE_KINDS = ("requirements", "pyproject", "package", "cargo", "python", "javascript", "typescript", "rust", "shell")
MAX_ARTIFACTS = 6
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_NODES = 20000
MAX_DEPTH = 20
MAX_LINES = 50000
MAX_EVIDENCE = 5
MAX_LEX_TOKENS = 300000
PY_TOKEN_LIMIT = 4 * MAX_NODES
WORK_SECONDS = 5.0
PRIVACY = "Only fixed labels from curated tables, generic source IDs, and line numbers are reported. Filenames, paths, versions, project or package names outside the curated list, URLs, commands, option values, and source text are never returned."
LIMITS = "Static inspection only: supplied code is never imported or executed. Declared means a manifest lists the dependency; inferred means an import references it. Neither proves runtime use, and workspace inheritance or non-registry sources are not resolved."

# label: (ecosystem, capability or None)
PYPI = {
    "ollama": "local-llm-client", "openai": "hosted-llm-api-client", "anthropic": "hosted-llm-api-client",
    "google-generativeai": "hosted-llm-api-client", "langchain": "llm-orchestration-library", "llama-cpp-python": "local-llm-runtime",
    "fastapi": "http-server-framework", "flask": "http-server-framework", "django": "http-server-framework", "uvicorn": "http-server-framework",
    "starlette": "http-server-framework", "requests": "http-client-library", "httpx": "http-client-library", "aiohttp": "http-client-library",
    "websockets": "websocket-library", "pydantic": "data-validation-library",
    "torch": "ml-runtime-library", "tensorflow": "ml-runtime-library", "transformers": "ml-model-library", "onnxruntime": "ml-runtime-library",
    "scikit-learn": "ml-runtime-library", "sentence-transformers": "ml-model-library", "chromadb": "vector-store-library", "faiss-cpu": "vector-store-library",
    "vosk": "speech-recognition-library", "openai-whisper": "speech-recognition-library", "faster-whisper": "speech-recognition-library",
    "speechrecognition": "speech-recognition-library", "pyttsx3": "text-to-speech-library", "gtts": "text-to-speech-library", "piper-tts": "text-to-speech-library",
    "sounddevice": "audio-io-library", "pyaudio": "audio-io-library", "soundfile": "audio-io-library", "webrtcvad": "voice-activity-detection-library",
    "numpy": "numerical-computing-library", "pandas": "data-frame-library", "scipy": "numerical-computing-library",
    "opencv-python": "computer-vision-library", "mediapipe": "computer-vision-library", "pillow": "image-processing-library",
    "pyautogui": "desktop-automation-library", "pynput": "input-device-library", "keyboard": "input-device-library", "psutil": "system-information-library",
    "playwright": "browser-automation-library", "selenium": "browser-automation-library", "beautifulsoup4": "html-parsing-library",
    "sqlalchemy": "database-library", "pyyaml": "configuration-parsing-library", "python-dotenv": "configuration-parsing-library",
    "rich": "terminal-ui-library", "click": "cli-argument-parsing", "typer": "cli-argument-parsing",
    "pytest": "test-framework", "setuptools": None, "hatchling": None, "poetry-core": None, "wheel": None,
}
NPM = {
    "react": "web-ui-framework", "react-dom": "web-ui-framework", "next": "web-ui-framework", "vue": "web-ui-framework", "svelte": "web-ui-framework",
    "vite": "frontend-build-tooling", "webpack": "frontend-build-tooling", "typescript": None, "tailwindcss": "ui-styling-library",
    "tauri": "desktop-app-framework", "electron": "desktop-app-framework", "zustand": "ui-state-management", "jotai": "ui-state-management",
    "redux": "ui-state-management", "axios": "http-client-library", "ky": "http-client-library", "express": "http-server-framework",
    "openai": "hosted-llm-api-client", "anthropic-sdk": "hosted-llm-api-client", "ollama": "local-llm-client",
    "vitest": "test-framework", "jest": "test-framework", "playwright": "browser-automation-library", "eslint": "lint-tooling",
    "prettier": "format-tooling", "zod": "data-validation-library", "i18next": "localization-library", "better-sqlite3": "database-library",
}
NPM_ALIASES = {"@anthropic-ai/sdk": "anthropic-sdk", "@playwright/test": "playwright", "@reduxjs/toolkit": "redux", "@tailwindcss/vite": "tailwindcss"}
NPM_SCOPES = {"@tauri-apps/": "tauri"}
CRATES = {
    "serde": "serialization-library", "serde_json": "serialization-library", "tokio": "async-runtime", "reqwest": "http-client-library",
    "hyper": "http-client-library", "axum": "http-server-framework", "actix-web": "http-server-framework", "clap": "cli-argument-parsing",
    "sqlx": "database-library", "rusqlite": "database-library", "diesel": "database-library", "tauri": "desktop-app-framework",
    "anyhow": "error-handling-library", "thiserror": "error-handling-library", "tracing": "logging-library", "log": "logging-library",
    "rayon": "parallelism-library", "candle-core": "ml-runtime-library", "llama-cpp-2": "local-llm-runtime",
}
CRATE_PREFIXES = {"tauri-plugin-": "tauri", "tauri-build": "tauri"}
# Rust source spells crate names with underscores; only curated crates are ever mapped.
RUST_IDENTS = {name.replace("-", "_"): name for name in CRATES}
RUST_IDENT_PREFIXES = {prefix.replace("-", "_"): label for prefix, label in CRATE_PREFIXES.items()}
RUST_ROOT_SKIP = {"crate", "self", "super", "Self", "std", "core", "alloc"}
TS_REGEX_KEYWORDS = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case", "do", "else", "yield", "await"}
_PYTHON_LOCK = threading.Lock()
PY_IMPORTS = {
    "ollama": "ollama", "openai": "openai", "anthropic": "anthropic", "fastapi": "fastapi", "flask": "flask", "django": "django",
    "uvicorn": "uvicorn", "starlette": "starlette", "requests": "requests", "httpx": "httpx", "aiohttp": "aiohttp", "websockets": "websockets",
    "pydantic": "pydantic", "torch": "torch", "tensorflow": "tensorflow", "transformers": "transformers", "onnxruntime": "onnxruntime",
    "sklearn": "scikit-learn", "sentence_transformers": "sentence-transformers", "chromadb": "chromadb", "faiss": "faiss-cpu",
    "vosk": "vosk", "whisper": "openai-whisper", "faster_whisper": "faster-whisper", "speech_recognition": "speechrecognition",
    "pyttsx3": "pyttsx3", "gtts": "gtts", "piper": "piper-tts", "sounddevice": "sounddevice", "pyaudio": "pyaudio", "soundfile": "soundfile",
    "webrtcvad": "webrtcvad", "numpy": "numpy", "pandas": "pandas", "scipy": "scipy", "cv2": "opencv-python", "mediapipe": "mediapipe",
    "PIL": "pillow", "pyautogui": "pyautogui", "pynput": "pynput", "keyboard": "keyboard", "psutil": "psutil", "playwright": "playwright",
    "selenium": "selenium", "bs4": "beautifulsoup4", "sqlalchemy": "sqlalchemy", "yaml": "pyyaml", "dotenv": "python-dotenv",
    "rich": "rich", "click": "click", "typer": "typer", "pytest": "pytest", "langchain": "langchain", "llama_cpp": "llama-cpp-python",
    "google": None,
}
PY_ALIASES = {"opencv-python-headless": "opencv-python", "opencv-contrib-python": "opencv-python", "torch-cpu": "torch"}
LANGUAGE_BY_KIND = {"requirements": "python", "pyproject": "python", "python": "python", "package": "javascript", "javascript": "javascript", "typescript": "typescript", "cargo": "rust", "rust": "rust", "shell": "shell"}
PACKAGE_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
CARGO_SECTIONS = ("dependencies", "dev-dependencies", "build-dependencies")
REQUIREMENT = re.compile(r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[A-Za-z0-9._,\s-]*\])?\s*(.*)\Z", re.S)
# Single character class only: nested quantifiers here caused catastrophic backtracking.
REQUIREMENT_TAIL = re.compile(r"\(?(?:[<>=!~][<>=!~0-9A-Za-z*+._,\s-]*)?\)?")
REGISTRY_VALUE = re.compile(r"[A-Za-z0-9 ^~<>=*.,|+_-]{0,128}\Z")


def _pypi_label(raw):
    name = re.sub(r"[-_.]+", "-", raw).lower()
    name = PY_ALIASES.get(name, name)
    return name if name in PYPI else None


def _npm_label(raw):
    if raw in NPM_ALIASES:
        return NPM_ALIASES[raw]
    for prefix, label in NPM_SCOPES.items():
        if raw.startswith(prefix):
            return label
    return raw if raw in NPM and not raw.startswith("@") else None


def _crate_label(raw):
    if raw in CRATES:
        return raw
    for prefix, label in CRATE_PREFIXES.items():
        if raw.startswith(prefix):
            return label
    return None


ECOSYSTEMS = {"pypi": (_pypi_label, PYPI), "npm": (_npm_label, NPM), "crates": (_crate_label, CRATES)}


class _Inventory:
    def __init__(self):
        self.deadline = time.monotonic() + WORK_SECONDS
        self.languages, self.dependencies, self.capabilities, self.hints = {}, {}, {}, {}
        self.warnings, self.unknown = [], {}

    def tick(self):
        if time.monotonic() > self.deadline:
            raise ArtifactError("Source inspection exceeded its work deadline")

    @staticmethod
    def _evidence(store, key, source, location):
        evidence = store.setdefault(key, [])
        item = {"source": source, "location": location}
        if item not in evidence and len(evidence) < MAX_EVIDENCE:
            evidence.append(item)

    def language(self, name, source, location="document"):
        self._evidence(self.languages, name, source, location)

    def hint(self, name, source, location):
        self._evidence(self.hints, name, source, location)

    def warn(self, source, message):
        text = f"{source}: {message}"
        if text not in self.warnings and len(self.warnings) < 200:
            self.warnings.append(text)

    def dependency(self, raw, ecosystem, confidence, source, location):
        if not isinstance(raw, str) or len(raw) > 214:
            self.unknown[source] = self.unknown.get(source, 0) + 1
            return
        resolve, table = ECOSYSTEMS[ecosystem]
        label = resolve(raw)
        if label is None:
            if confidence == "declared":
                self.unknown[source] = self.unknown.get(source, 0) + 1
            return
        self._evidence(self.dependencies, (label, ecosystem, confidence), source, location)
        capability = table.get(label)
        if capability:
            self._evidence(self.capabilities, (capability, confidence), source, location)

    def result(self, sources):
        def merged(store):
            names = {}
            for (name, *rest, confidence), evidence in store.items():
                key = (name, *rest)
                current = names.setdefault(key, {"confidence": confidence, "evidence": []})
                if confidence == "declared" and current["confidence"] == "inferred":
                    current["confidence"], current["evidence"] = "declared", []
                if confidence == current["confidence"]:
                    for item in evidence:
                        if item not in current["evidence"] and len(current["evidence"]) < MAX_EVIDENCE:
                            current["evidence"].append(item)
            return names

        dependencies = [{"name": name, "ecosystem": ecosystem, **value} for (name, ecosystem), value in sorted(merged(self.dependencies).items())]
        capabilities = [{"name": name, **value} for (name,), value in sorted(merged(self.capabilities).items())]
        for entry in sources:
            entry["unknown_dependencies"] = self.unknown.get(entry["id"], 0)
        return {
            "format": FORMAT,
            "sources": sources,
            "languages": [{"name": name, "evidence": evidence} for name, evidence in sorted(self.languages.items())],
            "dependencies": dependencies,
            "capabilities": capabilities,
            "install_hints": [{"name": name, "evidence": evidence} for name, evidence in sorted(self.hints.items())],
            "warnings": list(self.warnings),
            "privacy": PRIVACY,
            "limits": LIMITS,
        }


class _Rejected(Exception):
    pass


def _lines(text):
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise _Rejected("document has too many lines")
    return lines


def _requirement(text, inventory, source, location):
    """Return True if a PEP 508 style registry requirement was accepted."""
    text = text.strip()
    match = REQUIREMENT.match(text)
    if not text or match is None or "://" in text or "@" in text.split(";", 1)[0] or not REQUIREMENT_TAIL.fullmatch(match.group(3).split(";", 1)[0].strip()):
        inventory.warn(source, "unsupported requirement form ignored")
        return False
    inventory.dependency(match.group(1), "pypi", "declared", source, location)
    return True


def _parse_requirements(text, inventory, source):
    logical, start = "", 0
    for number, line in enumerate(_lines(text), 1):
        if number % 1024 == 0:
            inventory.tick()
        if not logical:
            start = number
        stripped = re.sub(r"(^|\s)#.*", "", line).rstrip()
        if stripped.endswith("\\"):
            logical += stripped[:-1] + " "
            continue
        logical += stripped
        entry, logical = logical.strip(), ""
        if not entry:
            continue
        if entry.startswith("-"):
            inventory.warn(source, "requirements option line ignored")
        elif "://" in entry or re.match(r"(git|hg|svn|bzr)\+", entry, re.I):
            inventory.warn(source, "URL requirement ignored")
        elif entry[0] in "./~\\" or re.match(r"[A-Za-z]:[\\/]", entry) or "/" in entry.split(";", 1)[0] or entry.lower().startswith("file:"):
            inventory.warn(source, "local path requirement ignored")
        else:
            _requirement(entry, inventory, source, f"line {start}")


def _toml_depth_guard(text):
    """Lexical bracket depth check before tomllib, which recurses on nesting."""
    depth, index, length = 0, 0, len(text)
    while index < length:
        char = text[index]
        if char == "#":
            end = text.find("\n", index)
            index = length if end < 0 else end
            continue
        if char in "\"'":
            triple = text.startswith(char * 3, index)
            quote = char * 3 if triple else char
            index += len(quote)
            while index < length and not text.startswith(quote, index):
                if char == '"' and text[index] == "\\":
                    index += 1
                elif not triple and text[index] == "\n":
                    break
                index += 1
            index += len(quote)
            continue
        if char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise _Rejected("document nesting exceeds the supported limit")
        elif char in "]}":
            depth = max(0, depth - 1)
        index += 1


def _structure_guard(value):
    pending, seen = [(value, 0)], 0
    while pending:
        entry, depth = pending.pop()
        seen += 1
        if seen > MAX_NODES or depth > MAX_DEPTH:
            raise _Rejected("document structure exceeds the supported limit")
        if isinstance(entry, dict):
            pending.extend((item, depth + 1) for item in entry.values())
        elif isinstance(entry, list):
            pending.extend((item, depth + 1) for item in entry)


def _toml(text):
    _toml_depth_guard(text)
    try:
        value = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError, ValueError, MemoryError):
        raise _Rejected("document is not valid TOML") from None
    _structure_guard(value)
    return value


def _table(value, *keys):
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value if isinstance(value, dict) else {}


def _string_list(value):
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _parse_pyproject(text, inventory, source):
    document = _toml(text)
    project = _table(document, "project")
    for item in _string_list(project.get("dependencies")):
        _requirement(item, inventory, source, "project.dependencies")
    for group in _table(project, "optional-dependencies").values():
        for item in _string_list(group):
            _requirement(item, inventory, source, "project.optional-dependencies")
    for group in _table(document, "dependency-groups").values():
        for item in _string_list(group):
            _requirement(item, inventory, source, "dependency-groups")
    for item in _string_list(_table(document, "build-system").get("requires")):
        _requirement(item, inventory, source, "build-system.requires")
    poetry = _table(document, "tool", "poetry")
    sections = [("tool.poetry.dependencies", _table(poetry, "dependencies")), ("tool.poetry.dev-dependencies", _table(poetry, "dev-dependencies"))]
    sections += [("tool.poetry.group.dependencies", _table(group, "dependencies")) for group in _table(poetry, "group").values()]
    for location, table in sections:
        for name, spec in table.items():
            if name.lower() == "python":
                continue
            if isinstance(spec, dict) and any(key in spec for key in ("path", "git", "url", "file")):
                inventory.warn(source, "non-registry dependency source ignored")
            else:
                inventory.dependency(name, "pypi", "declared", source, location)
    if project.get("scripts") or project.get("gui-scripts") or poetry.get("scripts"):
        inventory.hint("python-console-entry-point", source, "project.scripts")
    if _table(document, "build-system"):
        inventory.hint("python-build-backend", source, "build-system")
    if _table(document, "tool", "uv", "workspace") or _table(document, "tool", "uv", "sources"):
        inventory.warn(source, "workspace or source overrides are not resolved")


def _parse_cargo(text, inventory, source):
    document = _toml(text)
    sections = [(name, _table(document, name)) for name in CARGO_SECTIONS]
    sections += [("workspace.dependencies", _table(document, "workspace", "dependencies"))]
    for target in _table(document, "target").values():
        sections += [(f"target.{name}", _table(target, name)) for name in CARGO_SECTIONS]
    for location, table in sections:
        for name, spec in table.items():
            inventory.tick()
            crate, where = name, location
            if isinstance(spec, dict):
                if spec.get("workspace") is True:
                    inventory.warn(source, "workspace-inherited dependency is unresolved")
                    where = f"{location} (workspace-inherited)"
                if any(key in spec for key in ("path", "git")):
                    inventory.warn(source, "non-registry dependency source ignored")
                    continue
                if isinstance(spec.get("package"), str):
                    crate = spec["package"]
            elif not isinstance(spec, str):
                inventory.warn(source, "unsupported dependency form ignored")
                continue
            inventory.dependency(crate, "crates", "declared", source, where)
    package = _table(document, "package")
    if any(isinstance(value, dict) and value.get("workspace") is True for value in package.values()):
        inventory.warn(source, "workspace-inherited package fields are unresolved")
    if _table(document, "workspace"):
        inventory.hint("cargo-workspace", source, "workspace")
    if document.get("bin") or _table(document, "lib") or package:
        inventory.hint("cargo-build", source, "package")


def _no_surrogates(value):
    pending = [value]
    while pending:
        entry = pending.pop()
        if isinstance(entry, str):
            entry = [entry]
        elif isinstance(entry, dict):
            pending.extend(entry.values())
            entry = list(entry)
        elif isinstance(entry, list):
            pending.extend(entry)
            continue
        else:
            continue
        for text in entry:
            if isinstance(text, str) and any(0xD800 <= ord(char) <= 0xDFFF or ord(char) == 0 for char in text):
                raise _Rejected("document contains unsupported Unicode")


def _parse_package(text, inventory, source):
    try:
        document = strict_json(text, depth=MAX_DEPTH, nodes=MAX_NODES)
    except ArtifactError:
        raise _Rejected("document is not supported JSON") from None
    if not isinstance(document, dict):
        raise _Rejected("document is not a package manifest object")
    _no_surrogates(document)
    for section in PACKAGE_SECTIONS:
        table = document.get(section)
        if not isinstance(table, dict):
            continue
        for name, spec in table.items():
            if not isinstance(spec, str):
                inventory.warn(source, "unsupported dependency form ignored")
            elif spec.startswith("workspace:"):
                inventory.warn(source, "workspace-inherited dependency is unresolved")
                inventory.dependency(name, "npm", "declared", source, f"{section} (workspace-inherited)")
            elif not REGISTRY_VALUE.match(spec):
                inventory.warn(source, "non-registry dependency source ignored")
            else:
                inventory.dependency(name, "npm", "declared", source, section)
    if isinstance(document.get("scripts"), dict) and document["scripts"]:
        inventory.hint("node-package-scripts", source, "scripts")
    if "workspaces" in document:
        inventory.warn(source, "package workspaces are not resolved")
    if any(isinstance(document.get(section), dict) and "typescript" in document[section] for section in PACKAGE_SECTIONS):
        inventory.language("typescript", source, "document")


def _python_preparse(text, inventory):
    """Lexical pass that bounds token count and bracket depth before any AST is built."""
    count = depth = 0
    skipped = (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER)
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.OP:
            if token.string in ("(", "[", "{"):
                depth += 1
                if depth > MAX_DEPTH:
                    raise _Rejected("document structure exceeds the supported limit")
            elif token.string in (")", "]", "}"):
                depth -= 1
        if token.type not in skipped:
            count += 1
            if count > PY_TOKEN_LIMIT:
                raise _Rejected("document structure exceeds the supported limit")
            if count % 4096 == 0:
                inventory.tick()


def _parse_python(text, inventory, source):
    _lines(text)
    # warnings.catch_warnings swaps process-global filter state, so concurrent analyses could restore
    # the default filters while another thread's compiler is still reporting SyntaxWarning text derived
    # from supplied source. Serialize only the tokenizer and parser; other source kinds never wait.
    with _PYTHON_LOCK, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            _python_preparse(text, inventory)
            tree = ast.parse(text, mode="exec")
        except (SyntaxError, tokenize.TokenError, ValueError, RecursionError, MemoryError, OverflowError):
            raise _Rejected("document is not parseable Python") from None
    pending, seen = [(tree, 0)], 0
    while pending:
        node, depth = pending.pop()
        seen += 1
        if seen > MAX_NODES or depth > MAX_DEPTH:
            raise _Rejected("document structure exceeds the supported limit")
        if seen % 1024 == 0:
            inventory.tick()
        if isinstance(node, ast.Import):
            for alias in node.names:
                _python_import(alias.name, inventory, source, node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            _python_import(node.module, inventory, source, node.lineno)
        pending.extend((child, depth + 1) for child in reversed(list(ast.iter_child_nodes(node))))


def _python_import(module, inventory, source, line):
    top = module.split(".", 1)[0]
    label = PY_IMPORTS.get(top)
    if top == "google" and module.startswith("google.generativeai"):
        label = "google-generativeai"
    if label:
        inventory.dependency(label, "pypi", "inferred", source, f"line {line}")


def _npm_package(specifier):
    if not specifier or specifier[0] in "./" or ":" in specifier or "\\" in specifier:
        return None
    parts = specifier.split("/")
    return "/".join(parts[:2]) if specifier.startswith("@") else parts[0]


def _word_char(char):
    return char.isalnum() or char in "_$"


class _Lexer:
    """Shared bounded token sink for the TypeScript and Rust lexers."""

    def __init__(self, text, inventory):
        self.text, self.inventory, self.tokens, self.line = text, inventory, [], 1

    def emit(self, kind, value, line, *flags):
        self.tokens.append((kind, value, line, *flags))
        if len(self.tokens) > MAX_LEX_TOKENS:
            raise _Rejected("document exceeds lexical work limits")
        if len(self.tokens) % 4096 == 0:
            self.inventory.tick()

    def skip_to(self, end):
        self.line += self.text.count("\n", self.index, end)
        self.index = end


def _ts_tokens(text, inventory):
    """Lex TypeScript into (kind, value, line, claimable) tokens.

    Strings end at an unescaped newline, regex literals are skipped where the grammar allows them, and template
    literals with nested expressions are tracked. Where a slash is ambiguous between division and a regex, the
    line is tainted, and any string or template that starts on a tainted line taints every line it spans, so a
    misread can only suppress import claims, never create them.
    """
    lexer, length = _Lexer(text, inventory), len(text)
    lexer.index, tainted, templates, previous = 0, set(), [], None

    def scan_template():
        # Called just after a backtick or a closing template-expression brace. Returns True when "${" opened.
        start = lexer.line
        index = lexer.index
        while index < length:
            char = text[index]
            if char == "\\":
                index += 2
                continue
            if char == "`":
                lexer.skip_to(index + 1)
                break
            if text.startswith("${", index):
                lexer.skip_to(index + 2)
                if start in tainted:
                    tainted.update(range(start, lexer.line + 1))
                return True
            index += 1
        else:
            lexer.skip_to(length)
        if start in tainted:
            tainted.update(range(start, lexer.line + 1))
        return False

    while lexer.index < length:
        index, char = lexer.index, text[lexer.index]
        if char == "\n":
            lexer.line += 1
            lexer.index += 1
            continue
        if char.isspace():
            lexer.index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            lexer.index = length if end < 0 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            lexer.skip_to(length if end < 0 else end + 2)
            continue
        if char in "\"'":
            start, value, escaped, index = lexer.line, [], False, index + 1
            while index < length and text[index] != char and text[index] != "\n":
                if text[index] == "\\":
                    escaped = True
                    index += 1
                if index < length and len(value) < 4097:
                    value.append(text[index])
                index += 1
            lexer.skip_to(min(length, index + 1) if index < length and text[index] == char else index)
            if start in tainted:
                tainted.update(range(start, lexer.line + 1))
            lexer.emit("escaped" if escaped else "string", "".join(value), start, bool(templates))
            previous = ("string", "")
            continue
        if char == "`":
            lexer.index += 1
            if scan_template():
                templates.append(0)
                previous = None
            else:
                lexer.emit("template", "", lexer.line, bool(templates))
                previous = ("string", "")
            continue
        if char == "/":
            kind, value = previous or ("punct", ";")
            ambiguous = kind == "punct" and value in (")", "}")
            regex = kind == "punct" and value not in (")", "]", "}") or kind == "ident" and value in TS_REGEX_KEYWORDS
            if ambiguous:
                tainted.add(lexer.line)
            if regex:
                index, in_class = index + 1, False
                while index < length and text[index] != "\n":
                    current = text[index]
                    if current == "\\":
                        index += 1
                    elif current == "[":
                        in_class = True
                    elif current == "]":
                        in_class = False
                    elif current == "/" and not in_class:
                        index += 1
                        break
                    index += 1
                while index < length and _word_char(text[index]):
                    index += 1
                lexer.skip_to(index)
                previous = ("string", "")
                continue
        if char == "{" and templates:
            templates[-1] += 1
        elif char == "}" and templates:
            if templates[-1] == 0:
                templates.pop()
                lexer.index += 1
                if scan_template():
                    templates.append(0)
                    previous = None
                else:
                    previous = ("string", "")
                continue
            templates[-1] -= 1
        if _word_char(char):
            end = index + 1
            while end < length and _word_char(text[end]):
                end += 1
            word = text[index:end]
            kind = "number" if char.isdigit() else "ident"
            lexer.emit(kind, word, lexer.line, bool(templates))
            previous = (kind, word)
            lexer.index = end
            continue
        lexer.emit("punct", char, lexer.line, bool(templates))
        previous = ("punct", char)
        lexer.index += 1
    return [(kind, value, line, not nested and line not in tainted) for kind, value, line, nested in lexer.tokens]


def _parse_typescript(text, inventory, source):
    tokens = _ts_tokens(text, inventory)
    count = len(tokens)

    def string_at(position):
        return position < count and tokens[position][0] == "string"

    def punct(position, value):
        return 0 <= position < count and tokens[position][0] == "punct" and tokens[position][1] == value

    for index, (kind, value, line, claimable) in enumerate(tokens):
        if kind != "ident" or value not in ("import", "export", "require") or punct(index - 1, ".") or not claimable:
            continue
        specifier, end = None, index
        if value == "require" or value == "import" and punct(index + 1, "("):
            if punct(index + 1, "(") and string_at(index + 2) and punct(index + 3, ")"):
                specifier, end = tokens[index + 2][1], index + 2
        elif value == "import" and string_at(index + 1):
            specifier, end = tokens[index + 1][1], index + 1
        else:
            for position in range(index + 1, min(count, index + 400)):
                current = tokens[position]
                if current[0] == "punct" and current[1] in ";=()" or current[0] == "ident" and current[1] in ("import", "export") or current[0] not in ("ident", "punct"):
                    break
                if current[0] == "ident" and current[1] == "from":
                    if string_at(position + 1):
                        specifier, end = tokens[position + 1][1], position + 1
                    break
        if specifier is not None and all(token[3] for token in tokens[index : end + 1]):
            name = _npm_package(specifier)
            if name is not None:
                inventory.dependency(name, "npm", "inferred", source, f"line {line}")


def _rust_tokens(text, inventory):
    """Lex Rust into (kind, value, line) tokens; comments, strings, chars, and lifetimes produce no identifiers."""
    lexer, length = _Lexer(text, inventory), len(text)
    lexer.index = 0
    while lexer.index < length:
        index, char = lexer.index, text[lexer.index]
        if char == "\n":
            lexer.line += 1
            lexer.index += 1
        elif char.isspace():
            lexer.index += 1
        elif text.startswith("//", index):
            end = text.find("\n", index)
            lexer.index = length if end < 0 else end
        elif text.startswith("/*", index):
            depth, index = 1, index + 2
            while index < length and depth:
                if text.startswith("/*", index):
                    depth, index = depth + 1, index + 2
                elif text.startswith("*/", index):
                    depth, index = depth - 1, index + 2
                else:
                    index += 1
            lexer.skip_to(index)
        elif char == '"':
            lexer.skip_to(_rust_string_end(text, index + 1))
            lexer.emit("literal", "", lexer.line)
        elif char == "'":
            if text.startswith("\\", index + 1):
                end = text.find("'", index + 3)
                lexer.skip_to(length if end < 0 or end - index > 12 else end + 1)
                lexer.emit("literal", "", lexer.line)
            elif index + 2 < length and text[index + 2] == "'":
                lexer.skip_to(index + 3)
                lexer.emit("literal", "", lexer.line)
            else:
                end = index + 1
                while end < length and _word_char(text[end]) and text[end] != "$":
                    end += 1
                lexer.index = max(end, index + 1)
                lexer.emit("lifetime", "", lexer.line)
        elif char.isalnum() or char == "_":
            end = index + 1
            while end < length and (text[end].isalnum() or text[end] == "_"):
                end += 1
            word = text[index:end]
            if word in ("r", "br", "cr") and end < length and text[end] in "#\"":
                hashes = end
                while hashes < length and text[hashes] == "#":
                    hashes += 1
                if hashes < length and text[hashes] == '"':
                    closing = text.find('"' + "#" * (hashes - end), hashes + 1)
                    lexer.skip_to(length if closing < 0 else closing + 1 + hashes - end)
                    lexer.emit("literal", "", lexer.line)
                    continue
                if word == "r" and hashes == end + 1 and hashes < length and (text[hashes].isalpha() or text[hashes] == "_"):
                    start = hashes
                    while hashes < length and (text[hashes].isalnum() or text[hashes] == "_"):
                        hashes += 1
                    lexer.emit("ident", text[start:hashes], lexer.line)
                    lexer.index = hashes
                    continue
            if word in ("b", "c") and end < length and text[end] == '"':
                lexer.skip_to(_rust_string_end(text, end + 1))
                lexer.emit("literal", "", lexer.line)
                continue
            if word == "b" and end < length and text[end] == "'":
                closing = text.find("'", end + 2 if text.startswith("\\", end + 1) else end + 1)
                lexer.skip_to(length if closing < 0 or closing - end > 12 else closing + 1)
                lexer.emit("literal", "", lexer.line)
                continue
            lexer.emit("number" if char.isdigit() else "ident", word, lexer.line)
            lexer.index = end
        elif text.startswith("::", index):
            lexer.emit("punct", "::", lexer.line)
            lexer.index += 2
        else:
            lexer.emit("punct", char, lexer.line)
            lexer.index += 1
    return lexer.tokens


def _rust_string_end(text, index):
    length = len(text)
    while index < length and text[index] != '"':
        index += 2 if text[index] == "\\" else 1
    return min(length, index + 1)


def _rust_crate_label(ident):
    if ident in RUST_IDENTS:
        return RUST_IDENTS[ident]
    for prefix, label in RUST_IDENT_PREFIXES.items():
        if ident.startswith(prefix):
            return label
    return None


def _parse_rust(text, inventory, source):
    tokens = _rust_tokens(text, inventory)
    count = len(tokens)

    def is_(position, kind, value=None):
        return 0 <= position < count and tokens[position][0] == kind and (value is None or tokens[position][1] == value)

    def group_end(position):
        # position is an opening delimiter; returns the index after its matching close (bounded by the token list).
        depth = 0
        while position < count:
            if tokens[position][0] == "punct":
                if tokens[position][1] in "([{":
                    depth += 1
                elif tokens[position][1] in ")]}":
                    depth -= 1
                    if depth == 0:
                        return position + 1
            position += 1
        return count

    local = {tokens[n + 1][1] for n in range(count - 1) if is_(n, "ident", "mod") and is_(n + 1, "ident")}

    def claim(ident, line):
        if ident in RUST_ROOT_SKIP or ident in local:
            return
        label = _rust_crate_label(ident)
        if label is not None:
            inventory.dependency(label, "crates", "inferred", source, f"line {line}")

    index = 0
    while index < count:
        kind, value, line = tokens[index]
        if kind == "punct" and value == "#" and (is_(index + 1, "punct", "[") or is_(index + 1, "punct", "!") and is_(index + 2, "punct", "[")):
            index = group_end(index + 1 if is_(index + 1, "punct", "[") else index + 2)
            continue
        if kind == "ident" and is_(index + 1, "punct", "!"):
            if value == "macro_rules" and is_(index + 2, "ident"):
                index = group_end(index + 3)
                continue
            if any(is_(index + 2, "punct", delimiter) for delimiter in "([{"):
                index = group_end(index + 2)
                continue
        if kind == "ident" and value == "extern" and is_(index + 1, "ident", "crate") and is_(index + 2, "ident"):
            claim(tokens[index + 2][1], line)
            index += 3
            continue
        if kind == "ident" and value == "use" and not is_(index - 1, "punct", "::"):
            position = index + 1 + is_(index + 1, "punct", "::")
            if is_(position, "ident"):
                claim(tokens[position][1], line)
            elif is_(position, "punct", "{"):
                end, position, expect = group_end(position), position + 1, True
                while position < end:
                    if tokens[position][0] == "punct" and tokens[position][1] in "{(":
                        position = group_end(position)
                        expect = False
                        continue
                    if expect and is_(position, "ident"):
                        claim(tokens[position][1], line)
                    expect = is_(position, "punct", ",") or expect and is_(position, "punct", "::") and not is_(position - 1, "ident")
                    position += 1
            while index < count and not is_(index, "punct", ";"):
                index += 1
            continue
        if kind == "ident" and is_(index + 1, "punct", "::") and not is_(index - 1, "punct", "::") and not is_(index - 1, "punct", "."):
            # Follow the path; a path ending in a macro invocation (for example a logging macro) is not claimed.
            position = index + 2
            while is_(position, "ident") and is_(position + 1, "punct", "::"):
                position += 2
            if not (is_(position, "ident") and is_(position + 1, "punct", "!")):
                claim(value, line)
        index += 1


SHELL_HINTS = (
    (("pip", "install"), "pip-install"), (("pip3", "install"), "pip-install"), (("python", "-m", "pip", "install"), "pip-install"),
    (("python3", "-m", "pip", "install"), "pip-install"), (("py", "-m", "pip", "install"), "pip-install"),
    (("python", "-m", "venv"), "python-virtualenv"), (("python3", "-m", "venv"), "python-virtualenv"), (("virtualenv",), "python-virtualenv"),
    (("uv", "pip", "install"), "uv-install"), (("uv", "sync"), "uv-install"), (("uv", "venv"), "python-virtualenv"),
    (("poetry", "install"), "poetry-install"), (("conda", "install"), "conda-install"), (("conda", "create"), "conda-install"),
    (("npm", "install"), "npm-install"), (("npm", "i"), "npm-install"), (("npm", "ci"), "npm-install"), (("pnpm", "install"), "pnpm-install"),
    (("pnpm", "i"), "pnpm-install"), (("yarn", "install"), "yarn-install"), (("yarn",), "yarn-install"), (("bun", "install"), "bun-install"),
    (("cargo", "install"), "cargo-install"), (("cargo", "build"), "cargo-build"), (("cargo", "tauri"), "tauri-cli"), (("rustup",), "rust-toolchain-setup"),
    (("apt", "install"), "system-package-install"), (("apt-get", "install"), "system-package-install"), (("dnf", "install"), "system-package-install"),
    (("yum", "install"), "system-package-install"), (("pacman", "-S"), "system-package-install"), (("brew", "install"), "homebrew-install"),
    (("winget", "install"), "system-package-install"), (("choco", "install"), "system-package-install"),
    (("ollama", "pull"), "local-model-pull"), (("ollama", "serve"), "local-model-server"), (("git", "clone"), "source-clone"),
    (("docker", "build"), "container-tooling"), (("docker", "run"), "container-tooling"), (("docker", "compose"), "container-tooling"),
    (("docker-compose",), "container-tooling"), (("make",), "make-build"),
)
SHELL_WRAPPERS = {"sudo", "env", "command", "exec", "time", "nohup"}
SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}


def _shell_words(line):
    lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    words = []
    for word in lexer:
        words.append(word)
        if len(words) > 512:
            break
    return words


def _parse_shell(text, inventory, source):
    lines, logical, start, heredoc = _lines(text), "", 0, None
    for number, line in enumerate(lines, 1):
        if number % 1024 == 0:
            inventory.tick()
        if heredoc is not None:
            if line.strip() == heredoc:
                heredoc = None
            continue
        if not logical:
            start = number
        if line.endswith("\\"):
            logical += line[:-1] + " "
            continue
        logical, current = "", logical + line
        if len(current) > 8192:
            inventory.warn(source, "overlong shell line skipped")
            continue
        marker = re.search(r"(?<!<)<<(?!<)-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)", current)
        if marker:
            heredoc = marker.group(1)
        try:
            words = _shell_words(current)
        except ValueError:
            inventory.warn(source, "shell line could not be tokenized")
            continue
        commands, command = [], []
        for word in words:
            if word in SHELL_SEPARATORS or set(word) <= set(";&|"):
                commands.append((command, word))
                command = []
            else:
                command.append(word)
        commands.append((command, ""))
        fetched = False
        for words, separator in commands:
            while words and (words[0] in SHELL_WRAPPERS or re.match(r"[A-Za-z_][A-Za-z0-9_]*=", words[0])):
                if words[0] == "sudo":
                    inventory.hint("elevated-privileges", source, f"line {start}")
                words = words[1:]
            if not words:
                continue
            head = words[0].rsplit("/", 1)[-1]
            if head in ("curl", "wget"):
                fetched = separator == "|"
                continue
            if fetched and head in ("sh", "bash", "zsh", "python", "python3"):
                inventory.hint("remote-script-piped-to-interpreter", source, f"line {start}")
                inventory.warn(source, "remote script piped to an interpreter")
            fetched = False
            sequence = (head, *[word for word in words[1:] if not word.startswith("-") or word in ("-m", "-S")])
            for pattern, label in SHELL_HINTS:
                if sequence[: len(pattern)] == pattern:
                    inventory.hint(label, source, f"line {start}")
                    break


PARSERS = {
    "requirements": _parse_requirements, "pyproject": _parse_pyproject, "package": _parse_package, "cargo": _parse_cargo,
    "python": _parse_python, "javascript": _parse_typescript, "typescript": _parse_typescript, "rust": _parse_rust, "shell": _parse_shell,
}


def _validate(artifacts):
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= MAX_ARTIFACTS:
        raise ArtifactError("Provide between 1 and 6 source artifacts")
    prepared, total = [], 0
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"kind", "content"}:
            raise ArtifactError("Each source artifact must contain exactly kind and content")
        kind, content = artifact["kind"], artifact["content"]
        if kind not in SOURCE_KINDS:
            raise ArtifactError("Unsupported source artifact kind")
        if not isinstance(content, str):
            raise ArtifactError("Source artifact content must be text")
        if len(content) > MAX_FILE_BYTES:
            raise ArtifactError("Source artifact exceeds the per-file size limit")
        try:
            size = len(content.encode("utf-8"))
        except UnicodeEncodeError:
            raise ArtifactError("Source artifact contains unsupported Unicode") from None
        total += size
        if size > MAX_FILE_BYTES:
            raise ArtifactError("Source artifact exceeds the per-file size limit")
        if total > MAX_TOTAL_BYTES:
            raise ArtifactError("Source artifacts exceed the total size limit")
        prepared.append((kind, content.removeprefix("﻿"), size))
    return prepared


def source_inventory(artifacts):
    prepared = _validate(artifacts)
    inventory, sources = _Inventory(), []
    for number, (kind, content, size) in enumerate(prepared, 1):
        source = f"source_{number}"
        entry = {"id": source, "kind": kind, "bytes": size, "status": "parsed"}
        saved = copy.deepcopy((inventory.languages, inventory.dependencies, inventory.capabilities, inventory.hints))
        try:
            PARSERS[kind](content, inventory, source)
        except _Rejected as error:
            entry["status"] = "rejected"
            inventory.languages, inventory.dependencies, inventory.capabilities, inventory.hints = saved
            inventory.unknown.pop(source, None)
            inventory.warn(source, str(error))
        else:
            inventory.language(LANGUAGE_BY_KIND[kind], source)
        sources.append(entry)
    return inventory.result(sources)
