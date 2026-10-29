"""Bounded, non-executing interpretation of the reviewed parser's flat AST."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
import time
from urllib.parse import urljoin


class AnalysisLimit(ValueError):
    pass


@dataclass(slots=True)
class Value:
    kind: str
    data: object = None
    identity: int | str | None = None


UNKNOWN = Value("unknown")
UNDEFINED = Value("undefined")
NULL = Value("null")


@dataclass(slots=True)
class ObjectValue:
    fields: dict[str, Value] = field(default_factory=dict)
    open: bool = False


@dataclass(slots=True)
class Binding:
    name: str
    kind: str
    scope: int
    declaration: int
    init: int | None = None
    path: tuple = ()
    external: Value | None = None
    writes: int = 0
    duplicate: bool = False


@dataclass(slots=True)
class Scope:
    parent: int | None
    kind: str
    bindings: dict[str, int] = field(default_factory=dict)
    dynamic: bool = False


FUNCTIONS = {"FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression", "ObjectMethod", "ClassMethod", "ClassPrivateMethod", "TSDeclareFunction"}
WRAPPERS = {"ParenthesizedExpression", "TSAsExpression", "TSSatisfiesExpression", "TSNonNullExpression", "TSTypeAssertion", "TSInstantiationExpression"}
CALLS = {"CallExpression", "OptionalCallExpression"}
MEMBERS = {"MemberExpression", "OptionalMemberExpression"}
VERBS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
AXIOS_VERBS = {name.lower(): name for name in VERBS}
FETCH_MODULES = {"node-fetch", "cross-fetch", "isomorphic-fetch", "undici"}


class Analyzer:
    def __init__(self, document):
        self.nodes = document["nodes"]
        self.root = document["root"]
        self.scopes = [Scope(None, "program")]
        self.function_owners = [0]
        self.bindings: list[Binding] = []
        self.context = {}
        self.parents = {}
        self.mutated = set()
        self.client_mutated = set()
        self.base_mutated = set()
        self.config_url_mutated = set()
        self.global_mutated = set()
        self.member_writes = []
        self.cache = {}
        self.remaining = 2_000_000
        self.deadline = time.monotonic() + 3.0
        self.omitted = 0
        self.body_nodes = 0
        self.explicit_module = any(self.typ(child) in {"ImportDeclaration", "ExportNamedDeclaration", "ExportDefaultDeclaration", "ExportAllDeclaration"} for child in self.c(self.root, "body") or [])
        self._build_scopes()
        self._collect_writes()
        self._collect_effects()
        self.cache.clear()

    def tick(self, count=1):
        self.remaining -= count
        if self.remaining < 0:
            raise AnalysisLimit("JavaScript interpretation exceeded its work limit")
        if self.remaining % 4096 == 0 and time.monotonic() > self.deadline:
            raise AnalysisLimit("JavaScript interpretation exceeded its work deadline")

    def node(self, index):
        return self.nodes[index] if index is not None else {}

    def typ(self, index):
        return self.node(index).get("type")

    def f(self, index, key, default=None):
        return self.node(index).get("fields", {}).get(key, default)

    def c(self, index, key):
        return self.node(index).get("children", {}).get(key)

    def children(self, index):
        for key, value in self.node(index).get("children", {}).items():
            values = value if isinstance(value, list) else [value]
            for child in values:
                if child is not None:
                    yield key, child

    def _scope(self, parent, kind):
        self.scopes.append(Scope(parent, kind))
        index = len(self.scopes) - 1
        self.function_owners.append(index if kind == "function" else self.function_owners[parent])
        return index

    def _function_scope(self, scope):
        return self.function_owners[scope]

    def _declare(self, name, kind, scope, declaration, init=None, path=(), external=None):
        if not isinstance(name, str) or not name:
            return
        if kind == "var":
            scope = self._function_scope(scope)
        binding = Binding(name, kind, scope, declaration, init, path, external)
        if name in self.scopes[scope].bindings:
            self.bindings[self.scopes[scope].bindings[name]].duplicate = True
            return
        self.scopes[scope].bindings[name] = len(self.bindings)
        self.bindings.append(binding)

    def _pattern(self, index, kind, scope, declaration, init=None, path=(), depth=0):
        self.tick()
        if index is None or depth > 32:
            return
        typ = self.typ(index)
        if typ == "Identifier":
            self._declare(self.f(index, "name"), kind, scope, declaration, init, path)
        elif typ == "AssignmentPattern":
            self._pattern(self.c(index, "left"), kind, scope, declaration, init, path + (("default", self.c(index, "right")),), depth + 1)
        elif typ == "TSParameterProperty":
            self._pattern(self.c(index, "parameter"), kind, scope, declaration, init, path, depth + 1)
        elif typ == "ObjectPattern":
            for prop in self.c(index, "properties") or []:
                if prop is None:
                    continue
                if self.typ(prop) == "RestElement":
                    self._pattern(self.c(prop, "argument"), kind, scope, declaration, init, path + (("rest",),), depth + 1)
                else:
                    key = self._static_key(self.c(prop, "key"), self.f(prop, "computed", False))
                    self._pattern(self.c(prop, "value"), kind, scope, declaration, init if key is not None else None, path + (key,), depth + 1)
        elif typ == "ArrayPattern":
            for position, child in enumerate(self.c(index, "elements") or []):
                self._pattern(child, kind, scope, declaration, init, path + (str(position),), depth + 1)
        elif typ == "RestElement":
            self._pattern(self.c(index, "argument"), kind, scope, declaration, init, path + (("rest",),), depth + 1)

    @staticmethod
    def _module(name):
        if name == "axios":
            default = Value("axios", None, "axios-module")
            return Value("module", {"default": default})
        if name in FETCH_MODULES:
            default = Value("fetch")
            exports = {"fetch": default} if name == "undici" else {"default": default}
            if name == "cross-fetch":
                exports["fetch"] = default
            if name == "undici":
                exports.update(Request=Value("request_ctor"))
            return Value("module", exports)
        return UNKNOWN

    def _build_scopes(self):
        stack = [(self.root, 0, None, None, 0)]
        while stack:
            index, scope, parent, edge, depth = stack.pop()
            self.tick()
            if depth > 256:
                raise AnalysisLimit("JavaScript structure exceeds its nesting limit")
            self.parents[index] = (parent, edge)
            typ = self.typ(index)
            if typ in FUNCTIONS:
                if typ in {"FunctionDeclaration", "TSDeclareFunction"}:
                    self._declare(self.f(self.c(index, "id"), "name"), "function", scope, index)
                    if typ == "FunctionDeclaration" and not self.explicit_module and self._function_scope(scope) != scope:
                        self._declare(self.f(self.c(index, "id"), "name"), "function", self._function_scope(scope), index)
                scope = self._scope(scope, "function")
                if typ == "FunctionExpression":
                    self._declare(self.f(self.c(index, "id"), "name"), "function", scope, index)
                for param in self.c(index, "params") or []:
                    self._pattern(param, "parameter", scope, index)
                if typ != "ArrowFunctionExpression":
                    self._declare("arguments", "parameter", scope, index)
            elif typ in {"BlockStatement", "SwitchStatement", "ForStatement", "ForInStatement", "ForOfStatement", "CatchClause", "WithStatement", "ClassBody", "TSModuleBlock", "StaticBlock"}:
                scope = self._scope(scope, "with" if typ == "WithStatement" else "function" if typ == "StaticBlock" else "block")
                if typ == "CatchClause":
                    self._pattern(self.c(index, "param"), "parameter", scope, index)
            elif typ == "ClassExpression":
                scope = self._scope(scope, "block")
                self._declare(self.f(self.c(index, "id"), "name"), "class", scope, index)
            elif typ in {"ClassDeclaration", "TSEnumDeclaration", "TSModuleDeclaration"}:
                self._declare(self.f(self.c(index, "id"), "name"), "class", scope, index)
            elif typ == "TSImportEqualsDeclaration":
                reference = self.c(index, "moduleReference")
                name = self.f(self.c(reference, "expression"), "value") if self.typ(reference) == "TSExternalModuleReference" else None
                module = self._module(name)
                value = self.member(module, "default") if module.kind == "module" else UNKNOWN
                self._declare(self.f(self.c(index, "id"), "name"), "import", scope, index, external=value)
            self.context[index] = scope
            if typ == "VariableDeclaration":
                for decl in self.c(index, "declarations") or []:
                    self._pattern(self.c(decl, "id"), self.f(index, "kind", "var"), scope, index, self.c(decl, "init"))
            elif typ == "ImportDeclaration":
                name = self.f(self.c(index, "source"), "value")
                module = self._module(name)
                for spec in self.c(index, "specifiers") or []:
                    imported = self.f(self.c(spec, "imported"), "name") or self.f(self.c(spec, "imported"), "value")
                    if self.typ(spec) == "ImportDefaultSpecifier":
                        imported = "default"
                    value = module if self.typ(spec) == "ImportNamespaceSpecifier" else self.member(module, imported)
                    if self.f(index, "importKind") == "type" or self.f(spec, "importKind") == "type":
                        value = UNKNOWN
                    self._declare(self.f(self.c(spec, "local"), "name"), "import", scope, index, external=value)
            for key, child in reversed(list(self.children(index))):
                stack.append((child, scope, index, key, depth + 1))

    def lookup(self, name, scope):
        while scope is not None:
            self.tick()
            current = self.scopes[scope]
            if name in current.bindings:
                return current.bindings[name]
            scope = current.parent
        return None

    def _dynamic(self, scope):
        while scope is not None:
            self.tick()
            current = self.scopes[scope]
            if current.dynamic or current.kind == "with":
                return True
            scope = current.parent
        return False

    def _written_names(self, index, depth=0):
        if index is None or depth > 32:
            return
        typ = self.typ(index)
        if typ in WRAPPERS:
            yield from self._written_names(self.c(index, "expression"), depth + 1)
        elif typ == "Identifier" or typ in MEMBERS:
            yield index
        elif typ in {"ObjectPattern", "ArrayPattern", "RestElement", "AssignmentPattern", "ObjectProperty"}:
            for key, child in self.children(index):
                if key not in {"key", "right"}:
                    yield from self._written_names(child, depth + 1)

    def _collect_writes(self):
        for index in self.context:
            self.tick()
            typ = self.typ(index)
            targets = []
            if typ == "AssignmentExpression":
                targets = list(self._written_names(self.c(index, "left")))
            elif typ == "UpdateExpression" or typ == "UnaryExpression" and self.f(index, "operator") == "delete":
                targets = list(self._written_names(self.c(index, "argument")))
            elif typ in {"ForInStatement", "ForOfStatement"}:
                left = self.c(index, "left")
                if self.typ(left) == "VariableDeclaration":
                    for decl in self.c(left, "declarations") or []:
                        targets.extend(self._written_names(self.c(decl, "id")))
                else:
                    targets = list(self._written_names(left))
            for target in targets:
                if self.typ(target) in MEMBERS:
                    self.member_writes.append(target)
                    continue
                binding = self.lookup(self.f(target, "name"), self.context[target])
                if binding is not None:
                    self.bindings[binding].writes += 1
                elif self.f(target, "name") in {"fetch", "XMLHttpRequest", "Request", "URL", "JSON", "Object", "window", "self", "globalThis", "global", "require", "String", "Function"}:
                    self.global_mutated.add(self.f(target, "name"))
            if typ in CALLS:
                callee = self.c(index, "callee")
                if self.typ(callee) == "Identifier" and self.f(callee, "name") == "eval" and self.lookup("eval", self.context[index]) is None:
                    self.scopes[self._function_scope(self.context[index])].dynamic = True
            if typ == "Identifier" and self.f(index, "name") in {"eval", "Function"} and self.lookup(self.f(index, "name"), self.context[index]) is None:
                parent, edge = self.parents.get(index, (None, None))
                if self.f(index, "name") == "Function" or edge != "callee" or self.typ(parent) != "CallExpression":
                    self.global_mutated.add("*")

    def _static_key(self, index, computed=False):
        if index is None:
            return None
        if not computed and self.typ(index) in {"Identifier", "PrivateName"}:
            return self.f(index, "name")
        if self.typ(index) in {"StringLiteral", "NumericLiteral"}:
            value = self.f(index, "value")
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                return str(value)
        return None

    def property_key(self, index, depth=0):
        key = self._static_key(self.c(index, "property"), self.f(index, "computed", False))
        if key is None and self.f(index, "computed", False):
            value = self.value(self.c(index, "property"), depth + 1)
            key = value.data if value.kind == "string" else None
        return key

    def member(self, value, key):
        if key is None:
            return UNKNOWN
        if value.kind in {"url", "request"} and value.identity in self.mutated:
            return UNKNOWN
        if value.kind == "module":
            return value.data.get(key, UNKNOWN)
        if value.kind == "object":
            if value.identity in self.mutated:
                return UNKNOWN
            if key == "url" and value.identity in self.config_url_mutated:
                return UNKNOWN
            if key in {"__proto__", "constructor"}:
                return UNKNOWN
            return value.data.fields.get(key, UNKNOWN if value.data.open or "Object" in self.global_mutated else UNDEFINED)
        if value.kind == "array" and value.identity in self.mutated:
            return UNKNOWN
        if value.kind == "array" and isinstance(key, str) and key.isascii() and key.isdecimal() and (key == "0" or not key.startswith("0")):
            position = int(key) if len(key) < 7 else len(value.data)
            result = value.data[position] if position < len(value.data) else UNDEFINED
            return UNKNOWN if result.kind == "undefined" and self.global_mutated & {"Object", "Array"} else result
        if value.kind == "global":
            return self._global(key)
        if value.kind == "fetch" and key == "bind" and "Function" not in self.global_mutated and "*" not in self.global_mutated:
            return Value("bind_fetch", value)
        if value.kind == "axios" and value.identity not in self.client_mutated:
            if key in AXIOS_VERBS or key in {"request", "create"}:
                return Value("axios_member", (value, key))
            if key == "default" and value.identity == "axios-module":
                return value
            if key == "defaults":
                return Value("client_defaults", value, value.identity)
            if key == "interceptors":
                return Value("client_interceptors", value, value.identity)
            if key in {"isAxiosError", "isCancel", "all", "spread", "getUri"}:
                return Value("client_helper")
        if value.kind == "client_interceptors":
            if key == "response":
                return Value("response_interceptors", value.data, value.identity)
            return value
        if value.kind == "response_interceptors" and key in {"use", "eject", "clear"}:
            return Value("client_helper")
        if value.kind == "client_defaults":
            return self._axios_setting(UNDEFINED, value.data, key)
        if value.kind == "xhr" and value.identity not in self.client_mutated and key == "open":
            return Value("xhr_open", value)
        if value.kind == "xhr" and value.identity not in self.client_mutated and key in {"send", "abort", "setRequestHeader", "getResponseHeader", "getAllResponseHeaders", "overrideMimeType", "addEventListener", "removeEventListener"}:
            return Value("xhr_control", value)
        if value.kind == "xhr" and key == "upload":
            return Value("xhr_upload", value, value.identity)
        if value.kind == "xhr_upload" and key in {"addEventListener", "removeEventListener", "dispatchEvent"}:
            return Value("client_helper")
        if value.kind == "string" and key == "concat" and "String" not in self.global_mutated and "*" not in self.global_mutated:
            return Value("concat", value)
        if value.kind == "json" and key == "stringify":
            return Value("stringify")
        if value.kind == "object_builtin" and key == "freeze":
            return Value("freeze")
        if value.kind == "url" and key in {"href", "toString"}:
            return Value("string", value.data) if key == "href" else UNKNOWN
        return UNKNOWN

    def _global(self, name):
        if name in self.global_mutated or "*" in self.global_mutated:
            return UNKNOWN
        return {"fetch": Value("fetch"), "XMLHttpRequest": Value("xhr_ctor"), "Request": Value("request_ctor"),
                "URL": Value("url_ctor"), "globalThis": Value("global"), "window": Value("global"), "self": Value("global"), "global": Value("global"),
                "undefined": UNDEFINED, "JSON": Value("json"), "Object": Value("object_builtin"), "Reflect": Value("reflect_builtin")}.get(name, UNKNOWN)

    def _ambient_require(self, callee, use):
        scope = self.context[use]
        return (self.typ(callee) == "Identifier" and self.f(callee, "name") == "require"
                and not self._dynamic(scope) and self.lookup("require", scope) is None
                and "require" not in self.global_mutated and "*" not in self.global_mutated)

    def _resolve(self, binding_index, use, depth, visited):
        binding = self.bindings[binding_index]
        if binding.duplicate or binding.writes or binding.kind in {"parameter", "function", "class"}:
            return UNKNOWN
        if binding.external is not None:
            return binding.external
        if binding.init is None or binding_index in visited or len(visited) >= 8:
            return UNKNOWN
        declaration_position = self.node(binding.declaration).get("start", 0)
        use_position = self.node(use).get("start", 0)
        same_function = self._function_scope(binding.scope) == self._function_scope(self.context.get(use, 0))
        if same_function and declaration_position > use_position:
            return UNKNOWN
        if binding.kind in {"var", "let"}:
            if not same_function:
                return UNKNOWN
            parent, edge = self.parents.get(binding.declaration, (None, None))
            if edge != "body" or self.typ(parent) not in {"Program", "BlockStatement"}:
                return UNKNOWN
        key = (binding_index, self._function_scope(self.context.get(use, 0)), use_position < declaration_position)
        if key in self.cache:
            return self.cache[key]
        result = self.value(binding.init, depth + 1, visited | {binding_index})
        for part in binding.path:
            if isinstance(part, tuple):
                if part[0] == "rest":
                    return UNKNOWN
                if result.kind == "undefined":
                    result = self.value(part[1], depth + 1, visited | {binding_index})
            else:
                result = self.member(result, part)
        self.cache[key] = result
        return result

    def value(self, index, depth=0, visited=frozenset()):
        self.tick()
        if index is None or depth > 40:
            return UNKNOWN
        typ = self.typ(index)
        if typ in WRAPPERS:
            return self.value(self.c(index, "expression"), depth + 1, visited)
        if typ in {"StringLiteral", "DirectiveLiteral"}:
            text = self.f(index, "value")
            return Value("string", text) if isinstance(text, str) and len(text) <= 4096 else UNKNOWN
        if typ == "NumericLiteral":
            number = self.f(index, "value")
            return Value("number", number) if type(number) in {int, float} and math.isfinite(number) and abs(number) <= 2 ** 53 else UNKNOWN
        if typ == "BooleanLiteral":
            return Value("boolean", self.f(index, "value"))
        if typ == "NullLiteral":
            return NULL
        if typ == "Identifier":
            scope = self.context.get(index, 0)
            if self._dynamic(scope):
                return UNKNOWN
            name = self.f(index, "name")
            binding = self.lookup(name, scope)
            return self._global(name) if binding is None else self._resolve(binding, index, depth, visited)
        if typ in MEMBERS:
            return self.member(self.value(self.c(index, "object"), depth + 1, visited), self.property_key(index, depth + 1))
        if typ == "SequenceExpression":
            values = self.c(index, "expressions") or []
            return self.value(values[-1], depth + 1, visited) if values else UNKNOWN
        if typ == "TemplateLiteral":
            quasis, expressions = self.c(index, "quasis") or [], self.c(index, "expressions") or []
            if len(quasis) != len(expressions) + 1 or len(quasis) > 64:
                return UNKNOWN
            parts, length = [], 0
            for position, quasi in enumerate(quasis):
                text = self.f(quasi, "cooked")
                if not isinstance(text, str):
                    return UNKNOWN
                length += len(text)
                if length > 4096:
                    return UNKNOWN
                parts.append(text)
                if position < len(expressions):
                    value = self._string(self.value(expressions[position], depth + 1, visited))
                    if value is None:
                        return UNKNOWN
                    length += len(value)
                    if length > 4096:
                        return UNKNOWN
                    parts.append(value)
            text = "".join(parts)
            return Value("string", text) if len(text) <= 4096 else UNKNOWN
        if typ == "BinaryExpression" and self.f(index, "operator") == "+":
            left = self.value(self.c(index, "left"), depth + 1, visited)
            right = self.value(self.c(index, "right"), depth + 1, visited)
            if left.kind == right.kind == "number":
                number = left.data + right.data
                return Value("number", number) if abs(number) <= 2 ** 53 else UNKNOWN
            if "string" in {left.kind, right.kind}:
                a, b = self._string(left), self._string(right)
                return Value("string", a + b) if a is not None and b is not None and len(a) + len(b) <= 4096 else UNKNOWN
            return UNKNOWN
        if typ == "ConditionalExpression":
            condition = self.value(self.c(index, "test"), depth + 1, visited)
            if condition.kind == "boolean":
                return self.value(self.c(index, "consequent" if condition.data else "alternate"), depth + 1, visited)
            a = self.value(self.c(index, "consequent"), depth + 1, visited)
            b = self.value(self.c(index, "alternate"), depth + 1, visited)
            return a if a.kind in {"string", "number", "boolean", "null"} and a.kind == b.kind and a.data == b.data else UNKNOWN
        if typ == "ObjectExpression":
            fields, opened = {}, False
            for prop in self.c(index, "properties") or []:
                if len(fields) > 128:
                    return UNKNOWN
                if self.typ(prop) == "SpreadElement":
                    spread = self.value(self.c(prop, "argument"), depth + 1, visited)
                    if spread.kind == "object" and spread.identity not in self.mutated:
                        if spread.data.open:
                            fields = dict.fromkeys(fields, UNKNOWN)
                            opened = True
                        fields.update(spread.data.fields)
                    else:
                        fields = dict.fromkeys(fields, UNKNOWN)
                        opened = True
                    continue
                key = self._static_key(self.c(prop, "key"), self.f(prop, "computed", False))
                if key is None:
                    fields = dict.fromkeys(fields, UNKNOWN)
                    opened = True
                    continue
                value = self.value(self.c(prop, "value"), depth + 1, visited) if self.typ(prop) == "ObjectProperty" else UNKNOWN
                if key == "__proto__":
                    opened = opened or value.kind != "null"
                else:
                    fields[key] = value
            return Value("object", ObjectValue(fields, opened), index)
        if typ == "ArrayExpression":
            elements = self.c(index, "elements") or []
            if len(elements) > 128 or any(self.typ(child) == "SpreadElement" for child in elements):
                return UNKNOWN
            return Value("array", [self.value(child, depth + 1, visited) if child is not None else UNDEFINED for child in elements], index)
        if typ == "NewExpression":
            callee = self.value(self.c(index, "callee"), depth + 1, visited)
            args = self.c(index, "arguments") or []
            if any(self.typ(arg) == "SpreadElement" for arg in args):
                return UNKNOWN
            if callee.kind == "xhr_ctor":
                return Value("xhr", None, index)
            if callee.kind == "request_ctor" and args:
                target = self.value(args[0], depth + 1, visited)
                initial_method = "GET"
                if target.kind == "request":
                    if target.identity in self.mutated:
                        return UNKNOWN
                    target, initial_method = target.data
                options = self.value(args[1], depth + 1, visited) if len(args) > 1 else UNDEFINED
                method = self._method(options, default=True)
                if options.kind in {"null", "undefined"} or options.kind == "object" and self.member(options, "method").kind == "undefined":
                    method = initial_method
                return Value("request", (target, method), index)
            if callee.kind == "url_ctor" and args:
                target = self._string(self.value(args[0], depth + 1, visited))
                base = self._string(self.value(args[1], depth + 1, visited)) if len(args) > 1 else None
                if target is not None and "\\" not in target and (base is None or "\\" not in base):
                    if (base is not None and re.match(r"(?i)^https?://", base)) or base is None and re.match(r"(?i)^https?://", target):
                        return Value("url", urljoin(base, target) if base else target, index)
            return UNKNOWN
        if typ in CALLS:
            callee_index = self.c(index, "callee")
            args = self.c(index, "arguments") or []
            if any(self.typ(arg) == "SpreadElement" for arg in args):
                return UNKNOWN
            if self._ambient_require(callee_index, index) and len(args) == 1:
                name = self._string(self.value(args[0], depth + 1, visited))
                module = self._module(name)
                return module if name == "undici" else self.member(module, "default") if module.kind == "module" else UNKNOWN
            callee = self.value(callee_index, depth + 1, visited)
            if callee.kind == "axios_member" and callee.data[1] == "create":
                options = self.value(args[0], depth + 1, visited) if args else Value("object", ObjectValue())
                owner = callee.data[0]
                inherited = owner.data
                if owner.identity in self.base_mutated:
                    return Value("axios", UNKNOWN, index)
                if options.kind != "object" or options.identity in self.mutated or options.data.open:
                    return Value("axios", UNKNOWN, index)
                fields = {key: value for key, value in inherited.data.fields.items() if key not in {"url", "method", "data"}} if inherited is not None and inherited.kind == "object" else {}
                if inherited is not None and inherited.kind != "object":
                    return Value("axios", UNKNOWN, index)
                fields.update(options.data.fields)
                return Value("axios", Value("object", ObjectValue(fields)), index)
            if callee.kind == "bind_fetch" and len(args) == 1:
                receiver = self.value(args[0], depth + 1, visited)
                return Value("fetch") if receiver.kind in {"global", "null", "undefined"} else UNKNOWN
            if callee.kind == "concat":
                parts = [callee.data.data]
                length = len(callee.data.data)
                for arg in args:
                    part = self._string(self.value(arg, depth + 1, visited))
                    if part is None:
                        return UNKNOWN
                    length += len(part)
                    if length > 4096:
                        return UNKNOWN
                    parts.append(part)
                value = "".join(parts)
                return Value("string", value) if len(value) <= 4096 else UNKNOWN
            if callee.kind == "freeze" and len(args) == 1:
                return self.value(args[0], depth + 1, visited)
            if callee.kind == "stringify" and len(args) == 1:
                return Value("json_body", self.value(args[0], depth + 1, visited))
        return UNKNOWN

    def _string(self, value):
        if value.kind == "url" and value.identity in self.mutated:
            return None
        if value.kind in {"string", "url"}:
            return value.data
        if value.kind == "number":
            return str(int(value.data)) if value.data == int(value.data) else str(value.data)
        if value.kind == "boolean":
            return "true" if value.data else "false"
        if value.kind == "null":
            return "null"
        if value.kind == "undefined":
            return "undefined"
        return None

    def _identities(self, value):
        output, pending, visited = set(), [(value, 0)], set()
        while pending:
            current, depth = pending.pop()
            self.tick()
            if current.kind == "client_defaults":
                self.base_mutated.add(current.identity)
            elif current.kind in {"axios", "xhr", "client_interceptors"}:
                self.client_mutated.add(current.identity)
            if current.identity is not None:
                output.add(current.identity)
                if current.identity in visited or current.identity in self.mutated:
                    continue
                visited.add(current.identity)
            if depth >= 12:
                if current.kind in {"object", "array"} and current.data:
                    raise AnalysisLimit("JavaScript mutation graph exceeds its nesting limit")
                continue
            if current.kind == "object":
                pending.extend((child, depth + 1) for child in current.data.fields.values())
            elif current.kind == "array":
                pending.extend((child, depth + 1) for child in current.data)
        return output

    def _escape(self, index, *, receiver=False, written_key=None):
        pending, visited = [(index, receiver, 0, written_key)], set()
        while pending:
            index, receiver, depth, written_key = pending.pop()
            self.tick()
            if index is None or (index, receiver, written_key) in visited:
                continue
            if depth > 40:
                raise AnalysisLimit("JavaScript mutation alias exceeds its nesting limit")
            visited.add((index, receiver, written_key))
            value = self.value(index)
            if value.kind != "unknown":
                if value.kind == "global" and (not receiver or written_key is not None):
                    self.global_mutated.add(written_key or "*")
                elif (not receiver or written_key is not None) and value.kind in {"json", "object_builtin", "xhr_ctor", "request_ctor", "url_ctor", "fetch"}:
                    name = {"json": "JSON", "object_builtin": "Object", "xhr_ctor": "XMLHttpRequest", "request_ctor": "Request", "url_ctor": "URL", "fetch": "fetch"}[value.kind]
                    self.global_mutated.add(name)
                self.mutated |= self._identities(value)
            typ = self.typ(index)
            children = []
            if typ in {"ObjectExpression", "ArrayExpression"}:
                if typ == "ArrayExpression":
                    children = self.c(index, "elements") or []
                else:
                    children = [self.c(prop, "argument") if self.typ(prop) == "SpreadElement" else self.c(prop, "value") for prop in self.c(index, "properties") or []]
            elif value.kind != "unknown":
                continue
            elif typ == "ConditionalExpression":
                children = [self.c(index, "consequent"), self.c(index, "alternate")]
            elif typ == "LogicalExpression":
                children = [self.c(index, "left"), self.c(index, "right")]
            elif typ == "SequenceExpression":
                children = self.c(index, "expressions") or []
            elif typ in {"AwaitExpression", "SpreadElement"}:
                children = [self.c(index, "argument")]
            elif typ in MEMBERS:
                key = self.property_key(index)
                if key in {"__proto__", "constructor"} and (written_key is not None or not receiver):
                    self.global_mutated.add("Object")
                next_receiver = receiver and written_key is None if key in {"prototype", "__proto__", "constructor"} else True
                children, receiver, written_key = [self.c(index, "object")], next_receiver, None
            elif typ in WRAPPERS:
                children = [self.c(index, "expression")]
            elif typ == "Identifier":
                binding = self.lookup(self.f(index, "name"), self.context[index])
                children = [self.bindings[binding].init] if binding is not None else []
                if binding is not None:
                    children.extend(part[1] for part in self.bindings[binding].path if isinstance(part, tuple) and len(part) == 2)
            pending.extend((child, receiver, depth + 1, written_key) for child in children if child is not None)

    def _collect_effects(self):
        call_nodes, escapes = [], []
        for index in self.context:
            self.tick()
            typ = self.typ(index)
            if typ in CALLS or typ == "NewExpression":
                call_nodes.append(index)
            if typ == "AssignmentExpression":
                escapes.append(self.c(index, "right"))
            elif typ in {"ReturnStatement", "YieldExpression", "ThrowStatement"}:
                escapes.append(self.c(index, "argument"))
            elif typ == "ArrowFunctionExpression" and self.typ(self.c(index, "body")) != "BlockStatement":
                escapes.append(self.c(index, "body"))
            elif typ == "TaggedTemplateExpression":
                escapes.extend(self.c(self.c(index, "quasi"), "expressions") or [])
            elif typ in {"ForInStatement", "ForOfStatement"}:
                escapes.append(self.c(index, "right"))
            elif typ == "AssignmentPattern":
                ancestor = index
                for _ in range(40):
                    ancestor, edge = self.parents.get(ancestor, (None, None))
                    self.tick()
                    if self.typ(ancestor) in FUNCTIONS:
                        if edge == "params":
                            escapes.append(self.c(index, "right"))
                        break
                    if ancestor is None or self.typ(ancestor) in {"VariableDeclarator", "AssignmentExpression", "Program"}:
                        break
            elif typ in {"ClassProperty", "ClassPrivateProperty"}:
                escapes.append(self.c(index, "value"))
            elif typ == "JSXExpressionContainer" and self.typ(self.parents.get(index, (None, None))[0]) in {"JSXAttribute", "JSXElement", "JSXFragment"}:
                escapes.append(self.c(index, "expression"))
            elif typ in {"JSXSpreadAttribute", "JSXSpreadChild"}:
                escapes.append(self.c(index, "argument"))
            elif typ == "Identifier":
                binding_index = self.lookup(self.f(index, "name"), self.context[index])
                if binding_index is not None:
                    binding = self.bindings[binding_index]
                    if binding.kind in {"var", "let"} and (binding.writes or self._function_scope(binding.scope) != self._function_scope(self.context[index])):
                        escapes.append(binding.init)
        for target in self.member_writes:
            obj = self.value(self.c(target, "object"))
            key = self.property_key(target)
            if obj.kind == "global":
                self.global_mutated.add(key or "*")
            elif obj.kind in {"json", "object_builtin"}:
                self.global_mutated.add("JSON" if obj.kind == "json" else "Object")
            elif obj.kind in {"object", "array", "url", "request"}:
                self.mutated |= self._identities(obj)
            elif obj.kind == "client_defaults":
                if key is None or key in {"baseURL", "url", "method", "allowAbsoluteUrls"}:
                    self.base_mutated.add(obj.identity)
            elif obj.kind == "client_interceptors":
                self.client_mutated.add(obj.identity)
            elif obj.kind in {"axios", "xhr"}:
                if obj.kind == "axios" and key == "defaults":
                    self.base_mutated.add(obj.identity)
                elif key is None or key in ({"open"} if obj.kind == "xhr" else set(AXIOS_VERBS) | {"request", "create", "interceptors"}):
                    self.client_mutated.add(obj.identity)
            else:
                self._escape(self.c(target, "object"), receiver=True, written_key=key or "*")
            root = self.c(target, "object")
            while self.typ(root) in MEMBERS | WRAPPERS:
                self.tick()
                root = self.c(root, "object") if self.typ(root) in MEMBERS else self.c(root, "expression")
            if self.typ(root) == "Identifier":
                name = self.f(root, "name")
                if name in {"XMLHttpRequest", "Request", "URL", "JSON", "Object", "String", "Function", "Array", "fetch"} and self.lookup(name, self.context[root]) is None:
                    self.global_mutated.add(name)
            elif self.typ(root) in CALLS:
                callee = self.c(root, "callee")
                if self.typ(callee) in MEMBERS and self.property_key(callee) == "getPrototypeOf" and self.value(self.c(callee, "object")).kind in {"object_builtin", "reflect_builtin"}:
                    self.global_mutated.add("Object")
            elif self.typ(root) == "ThisExpression":
                ancestor = target
                object_method = False
                for _ in range(256):
                    ancestor, _ = self.parents.get(ancestor, (None, None))
                    if ancestor is None:
                        break
                    if self.typ(ancestor) in {"ObjectMethod", "ObjectProperty"}:
                        object_method = True
                    if self.typ(ancestor) == "ObjectExpression":
                        if object_method:
                            self.mutated |= self._identities(self.value(ancestor))
                        break
                    if self.typ(ancestor) in {"ClassBody", "Program"}:
                        break
        escapes = sorted(set(escapes) - {None})
        while True:
            before = tuple(map(len, (self.mutated, self.client_mutated, self.base_mutated, self.global_mutated, self.config_url_mutated)))
            self.cache.clear()
            for index in escapes:
                if index is not None:
                    self._escape(index)
            self._call_effects(call_nodes)
            after = tuple(map(len, (self.mutated, self.client_mutated, self.base_mutated, self.global_mutated, self.config_url_mutated)))
            if before == after:
                break
        self.cache.clear()

    def _call_effects(self, call_nodes):
        for index in call_nodes:
            self.tick()
            callee_node = self.c(index, "callee")
            args = self.c(index, "arguments") or []
            callee = self.value(callee_node)
            request = callee.kind == "axios" or callee.kind == "axios_member" and callee.data[1] == "request"
            if request and len(args) > 1 and self.value(args[0]).kind == "string":
                config = self.value(args[1])
                if config.kind == "object" and config.identity is not None:
                    self.config_url_mutated.add(config.identity)
            harmless = callee.kind in {"xhr_ctor", "request_ctor", "url_ctor"} if self.typ(index) == "NewExpression" else callee.kind in {"fetch", "axios", "axios_member", "xhr_open", "xhr_control", "client_helper", "freeze", "stringify", "concat", "bind_fetch"}
            if not harmless:
                if self.typ(callee_node) in MEMBERS:
                    self._escape(self.c(callee_node, "object"), receiver=True)
                for arg in args:
                    self._escape(self.c(arg, "argument") if self.typ(arg) == "SpreadElement" else arg)

    @staticmethod
    def _wire_method(text):
        if text in VERBS:
            return text
        normalized = text.upper() if text.isascii() else ""
        return normalized if normalized in VERBS - {"PATCH"} else None

    def _method(self, options, default=False):
        if options.kind in {"null", "undefined"}:
            return "GET" if default else None
        if options.kind != "object":
            return None
        method = self.member(options, "method")
        if method.kind == "undefined":
            return "GET" if default else None
        if method.kind == "string":
            return self._wire_method(method.data)
        return None

    def _axios_url(self, url, config, client, *, missing=False):
        base = self._axios_setting(config, client, "baseURL")
        if missing or url == "":
            return base.data if base.kind == "string" and base.data else None
        if not isinstance(url, str):
            return None
        allow = self._axios_setting(config, client, "allowAbsoluteUrls")
        absolute = bool(re.match(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:)?//", url))
        if absolute and (allow.kind == "undefined" or allow.kind == "boolean" and allow.data):
            return url
        if absolute and allow.kind != "boolean":
            return None
        if base.kind == "string" and base.data:
            return base.data.rstrip("/") + "/" + url.lstrip("/")
        return url if base.kind in {"null", "undefined"} or base.kind == "string" and not base.data else None

    def _axios_setting(self, config, client, name):
        value = self.member(config, name) if config.kind == "object" else UNDEFINED if config.kind in {"undefined", "null"} else UNKNOWN
        if value.kind != "undefined":
            return value
        if client.identity in self.base_mutated:
            return UNKNOWN
        return self.member(client.data, name) if client.data is not None else UNDEFINED

    def _plain(self, value, depth=0):
        self.tick()
        self.body_nodes += 1
        if depth > 12 or self.body_nodes > 20_000:
            raise ValueError()
        if value.kind in {"string", "number", "boolean", "null"}:
            return value.data
        if value.kind == "object" and not value.data.open and value.identity not in self.mutated:
            return {key: self._plain(child, depth + 1) for key, child in value.data.fields.items()}
        if value.kind == "array" and value.identity not in self.mutated:
            return [None if child.kind == "undefined" else self._plain(child, depth + 1) for child in value.data]
        raise ValueError()

    def result(self):
        output, imports = [], []
        seen = set()
        for index in self.context:
            self.tick()
            typ = self.typ(index)
            line = self.node(index).get("line", 1)
            if typ in {"ImportDeclaration", "ExportNamedDeclaration", "ExportAllDeclaration"}:
                name = self.f(self.c(index, "source"), "value")
                if isinstance(name, str):
                    imports.append({"module": name, "line": line})
            elif typ == "ImportExpression":
                name = self._string(self.value(self.c(index, "source")))
                if name is not None:
                    imports.append({"module": name, "line": line})
            elif typ == "TSImportEqualsDeclaration":
                reference = self.c(index, "moduleReference")
                if self.typ(reference) == "TSExternalModuleReference":
                    name = self.f(self.c(reference, "expression"), "value")
                    if isinstance(name, str):
                        imports.append({"module": name, "line": line})
            if typ not in CALLS:
                continue
            callee_node = self.c(index, "callee")
            args = self.c(index, "arguments") or []
            if any(self.typ(arg) == "SpreadElement" for arg in args):
                continue
            if self._ambient_require(callee_node, index) and len(args) == 1:
                name = self._string(self.value(args[0]))
                if name is not None:
                    imports.append({"module": name, "line": line})
            callee = self.value(callee_node)
            method, url, body, client_name, missing_url = None, None, UNKNOWN, None, False
            if callee.kind == "fetch" and args:
                target = self.value(args[0])
                initial_method = "GET"
                if target.kind == "request":
                    if target.identity in self.mutated:
                        self.omitted += 1
                        continue
                    target, initial_method = target.data
                url = self._string(target)
                options = self.value(args[1]) if len(args) > 1 else UNDEFINED
                method = self._method(options, default=True) if len(args) > 1 else initial_method
                if options.kind in {"null", "undefined"} or options.kind == "object" and self.member(options, "method").kind == "undefined":
                    method = initial_method
                if len(args) > 1 and options.kind in {"number", "string", "boolean"}:
                    url = None
                candidate_body = self.member(options, "body")
                body = candidate_body.data if candidate_body.kind == "json_body" else UNKNOWN
                client_name = "fetch"
            elif callee.kind == "xhr_open" and len(args) >= 2:
                if self.typ(callee_node) not in MEMBERS:
                    continue
                url = self._string(self.value(args[1]))
                verb = self._string(self.value(args[0]))
                method = self._wire_method(verb) if verb else None
                client_name = "xhr"
            elif callee.kind in {"axios", "axios_member"}:
                client, operation = callee.data if callee.kind == "axios_member" else (callee, "request")
                if client.identity in self.client_mutated or operation == "create":
                    continue
                config = UNDEFINED
                if operation in AXIOS_VERBS and args:
                    method = AXIOS_VERBS[operation]
                    config_at = 2 if method in {"POST", "PUT", "PATCH"} else 1
                    config = self.value(args[config_at]) if len(args) > config_at else UNDEFINED
                    target = self.value(args[0])
                    missing_url = target.kind == "undefined"
                    url = self._string(target) if target.kind in {"string", "url"} else None
                    if config_at == 2 and len(args) > 1:
                        body = self.value(args[1])
                elif operation == "request" and args:
                    first = self.value(args[0])
                    if first.kind == "string":
                        url = first.data
                        config = self.value(args[1]) if len(args) > 1 else UNDEFINED
                    else:
                        config = first
                        target = self.member(config, "url")
                        missing_url = target.kind == "undefined"
                        url = self._string(target) if target.kind in {"string", "url"} else None
                    setting = self._axios_setting(config, client, "method")
                    method = "GET" if setting.kind == "undefined" else setting.data.upper() if setting.kind == "string" and setting.data.isascii() and setting.data.upper() in VERBS else None
                    body = self.member(config, "data")
                url = self._axios_url(url, config, client, missing=missing_url)
                client_name = "axios"
            if client_name is None:
                continue
            if not isinstance(url, str) or not url or len(url) > 4096 or "\\" in url or any(ord(char) < 33 for char in url):
                self.omitted += 1
                continue
            key = (url, method, line, client_name)
            if key in seen:
                continue
            if len(output) >= 800:
                raise AnalysisLimit("JavaScript evidence exceeds its count limit")
            seen.add(key)
            fact = {"url": url, "method": method, "line": line, "client": client_name}
            try:
                fact["body"] = self._plain(body)
            except ValueError:
                pass
            output.append(fact)
        if len(imports) > 800:
            raise AnalysisLimit("JavaScript import evidence exceeds its count limit")
        return {"calls": output, "imports": imports, "omitted": self.omitted}


def inspect_ast(document):
    return Analyzer(document).result()
