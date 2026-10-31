"""Private, bounded syntax hints for comparison-local tracking review.

The return value contains private event identifiers and must never be serialized
directly. A caller must jointly alias identities before producing any report.
No code, SDK, browser or request is executed or verified by this module.
"""
from __future__ import annotations

import math
import re
import time

from .js_analysis import Analyzer, CALLS, FUNCTIONS, MEMBERS, WRAPPERS
from .js_parser import available, parse

MAX_RECORDS = 256
MAX_SOURCE = 512 * 1024
MAX_EVENT = 4096
ROOTS = frozenset({"gtag", "posthog", "analytics", "mixpanel", "fbq", "dataLayer", "navigator"})
QUALIFIERS = frozenset({"window", "globalThis", "self", "global", "parent", "top", "frames", "this"})
_SOURCE = re.compile(r"source_[1-6]\Z")
_ERROR = "Tracking syntax inspection rejected: invalid, unsupported, unavailable or over limit."
ASSUMPTION = "unverified_global_tracking_sdk"
NAVIGATION_ASSUMPTION = "unverified_global_navigation"
NAVIGATION_ROOTS = frozenset({"location", "open"})
_OMITTED = object()


class _TrackingAnalyzer(Analyzer):
    def __init__(self, document, module_mode, deadline):
        self._forced_module_mode = module_mode
        self._tracking_deadline = deadline
        super().__init__(document)

    def _build_scopes(self):
        if time.monotonic() >= self._tracking_deadline:
            raise ValueError
        self.deadline = min(self.deadline, self._tracking_deadline)
        self.explicit_module |= self._forced_module_mode
        super()._build_scopes()

    def _collect_writes(self):
        super()._collect_writes()
        # The HTTP analyzer treats every opaque call receiving the global
        # object as a possible mutation. Tracking has its own explicit escape
        # pass, including a small read-only builtin allowlist. Keep only the
        # pre-effect dynamic-code/global-reassignment signals from the core.
        self.tracking_unsafe_globals = set(self.global_mutated)

    def tick(self, count=1):
        super().tick(count)
        if self.remaining % 256 == 0 and time.monotonic() >= self._tracking_deadline:
            raise ValueError


def _path(analyzer, index):
    """Syntactic member path; None entries represent computed unknown keys."""
    properties = []
    for _ in range(12):
        typ = analyzer.typ(index)
        if typ in WRAPPERS:
            index = analyzer.c(index, "expression")
        elif typ in MEMBERS:
            key = analyzer._static_key(analyzer.c(index, "property"), analyzer.f(index, "computed", False))
            properties.append(key)
            index = analyzer.c(index, "object")
        elif typ == "Identifier":
            return analyzer.f(index, "name"), tuple(reversed(properties)), index
        elif typ == "ThisExpression":
            return "this", tuple(reversed(properties)), index
        else:
            return None
    return None


def _classic_global_this(analyzer, index):
    if analyzer.explicit_module:
        return False
    for _ in range(256):
        typ = analyzer.typ(index)
        if typ == "Program":
            return True
        if (typ in FUNCTIONS and typ != "ArrowFunctionExpression"
                or typ in {"ClassBody", "ClassProperty", "ClassPrivateProperty", "StaticBlock"}):
            return False
        index = analyzer.parents.get(index, (None, None))[0]
        if index is None:
            return False
    raise ValueError


def _global_properties(path):
    """Known window/frame references; the original root still checks scope."""
    root, properties, _ = path
    if root == "document" and properties[:1] == ("defaultView",):
        properties = properties[1:]
    elif root not in QUALIFIERS:
        return None
    while properties:
        if properties[0] in QUALIFIERS - {"this"}:
            properties = properties[1:]
        elif properties[:2] == ("document", "defaultView"):
            properties = properties[2:]
        elif type(properties[0]) is str and properties[0].isascii() and properties[0].isdecimal():
            # Indexed window/frame access also yields an unknown frame window.
            properties = properties[1:]
        else:
            break
    return properties


def _ambient(analyzer, path, use):
    if path is None:
        return False
    root, _, root_node = path
    scope = analyzer.context.get(root_node, analyzer.context.get(use, 0))
    return (not analyzer._dynamic(scope)
            and (root != "this" or _classic_global_this(analyzer, root_node))
            and analyzer.lookup(root, scope) is None
            and "*" not in analyzer.tracking_unsafe_globals
            and root not in analyzer.tracking_unsafe_globals)


def _sdk(path):
    if path is None:
        return None
    root, properties, _ = path
    qualified = _global_properties(path)
    if qualified is not None:
        properties = qualified
        return properties[0] if properties and properties[0] in ROOTS else None
    return root if root in ROOTS else None


def _qualified_parts(path):
    root, properties, _ = path
    qualified = _global_properties(path)
    if qualified:
        return qualified[0], qualified[1:]
    return root, properties


def _string(analyzer, index):
    value = analyzer.value(index)
    return value.data if value.kind == "string" and type(value.data) is str and len(value.data) <= MAX_EVENT else None


def _data_layer_event(analyzer, index):
    # Read literal object syntax directly. An unknown later spread/computed key
    # can override event; a later explicit event property can override it again.
    if analyzer.typ(index) != "ObjectExpression":
        return False, None, True
    seen = False
    uncertain = False
    result = None
    for prop in analyzer.c(index, "properties") or []:
        if analyzer.typ(prop) == "SpreadElement":
            result = None
            uncertain = True
            continue
        key = analyzer._static_key(analyzer.c(prop, "key"), analyzer.f(prop, "computed", False))
        if key is None:
            result = None
            uncertain = True
        elif key == "event":
            seen = True
            result = _string(analyzer, analyzer.c(prop, "value")) if analyzer.typ(prop) == "ObjectProperty" else None
    return seen, result, uncertain


def _classify(analyzer, path, arguments, limitations):
    root, properties = _qualified_parts(path)
    if root == "gtag" and not properties:
        if len(arguments) >= 2:
            command = _string(analyzer, arguments[0])
            if command in {"event", "config"}:
                return "gtag_event" if command == "event" else "gtag_config", _string(analyzer, arguments[1])
        if arguments and _string(analyzer, arguments[0]) is None:
            limitations.add("tracking_dynamic_command")
            return _OMITTED
        return None
    if root == "fbq" and not properties:
        if len(arguments) >= 2:
            command = _string(analyzer, arguments[0])
            if command in {"track", "trackCustom", "init"}:
                return {"track": "fbq_track", "trackCustom": "fbq_track_custom", "init": "fbq_init"}[command], _string(analyzer, arguments[1])
            if command is None:
                limitations.add("tracking_dynamic_command")
                return _OMITTED
        return None
    label = {("posthog", ("capture",)): "posthog_capture",
             ("analytics", ("track",)): "analytics_track",
             ("mixpanel", ("track",)): "mixpanel_track",
             ("navigator", ("sendBeacon",)): "navigator_send_beacon"}.get((root, properties))
    if label and arguments:
        return label, _string(analyzer, arguments[0])
    if root == "analytics" and properties == ("page",):
        return "analytics_page", _string(analyzer, arguments[0]) if arguments else None
    if root == "dataLayer" and properties == ("push",) and arguments:
        seen, event, uncertain = _data_layer_event(analyzer, arguments[0])
        if seen:
            return "data_layer_event", event
        if uncertain:
            limitations.add("tracking_dynamic_event_object")
            return _OMITTED
    if root in ROOTS and any(key is None for key in properties):
        limitations.add("tracking_dynamic_callee")
        return _OMITTED
    return None


def _declared_imports(analyzer, check):
    imports = {}
    for index in analyzer.context:
        check()
        if analyzer.typ(index) != "ImportDeclaration" or analyzer.f(index, "importKind") == "type":
            continue
        root = {"posthog-js": "posthog", "mixpanel-browser": "mixpanel"}.get(
            analyzer.f(analyzer.c(index, "source"), "value"))
        if not root:
            continue
        for spec in analyzer.c(index, "specifiers") or []:
            if analyzer.f(spec, "importKind") == "type":
                continue
            name = analyzer.f(analyzer.c(spec, "local"), "name")
            binding = analyzer.scopes[0].bindings.get(name)
            if binding is None:
                continue
            imported = analyzer.f(analyzer.c(spec, "imported"), "name") or analyzer.f(analyzer.c(spec, "imported"), "value")
            typ = analyzer.typ(spec)
            prefix = ()
            if typ in {"ImportDefaultSpecifier", "ImportNamespaceSpecifier"} or imported in {"default", root}:
                pass
            elif imported == ("capture" if root == "posthog" else "track"):
                prefix = (imported,)
            else:
                continue
            imports[binding] = (root, prefix, typ == "ImportNamespaceSpecifier")
    return imports


def _escape_inputs(analyzer, index):
    """Values leaving a local expression, including aliases held for later use."""
    typ = analyzer.typ(index)
    if typ == "VariableDeclarator":
        return [analyzer.c(index, "init")]
    if typ == "AssignmentExpression":
        return [analyzer.c(index, "right")]
    if typ in {"ReturnStatement", "YieldExpression", "ThrowStatement", "JSXSpreadAttribute", "JSXSpreadChild"}:
        return [analyzer.c(index, "argument")]
    if typ in {"ClassProperty", "ClassPrivateProperty"}:
        return [analyzer.c(index, "value")]
    if typ == "ArrowFunctionExpression" and analyzer.typ(analyzer.c(index, "body")) != "BlockStatement":
        return [analyzer.c(index, "body")]
    if typ == "ExportSpecifier":
        return [analyzer.c(index, "local")]
    if typ == "ExportDefaultDeclaration":
        return [analyzer.c(index, "declaration")]
    if typ == "JSXExpressionContainer":
        return [analyzer.c(index, "expression")]
    return []


def _escape_paths(analyzer, index, check):
    pending, visited = [(index, 0)], set()
    while pending:
        current, depth = pending.pop()
        check()
        if current is None or current in visited:
            continue
        if depth > 32:
            raise ValueError
        visited.add(current)
        path = _path(analyzer, current)
        if path is not None:
            yield path
            continue
        typ = analyzer.typ(current)
        children = []
        if typ == "LogicalExpression":
            children = [analyzer.c(current, "left"), analyzer.c(current, "right")]
        elif typ == "ConditionalExpression":
            children = [analyzer.c(current, "consequent"), analyzer.c(current, "alternate")]
        elif typ == "SequenceExpression":
            children = analyzer.c(current, "expressions") or []
        elif typ == "ArrayExpression":
            children = analyzer.c(current, "elements") or []
        elif typ == "ObjectExpression":
            children = [analyzer.c(prop, "argument") if analyzer.typ(prop) == "SpreadElement"
                        else analyzer.c(prop, "value") for prop in analyzer.c(current, "properties") or []]
        elif typ in WRAPPERS:
            children = [analyzer.c(current, "expression")]
        elif typ in {"SpreadElement", "AwaitExpression", "AssignmentPattern"}:
            children = [analyzer.c(current, "right" if typ == "AssignmentPattern" else "argument")]
        pending.extend((child, depth + 1) for child in children)


def _readonly_global_call(analyzer, index):
    """Only builtins whose results cannot contain an SDK/global reference."""
    if analyzer.typ(index) != "CallExpression":
        return False
    callee = _path(analyzer, analyzer.c(index, "callee"))
    if not callee or len(callee[1]) != 1:
        return False
    root, properties, node = callee
    methods = {"Object": {"keys", "getOwnPropertyNames", "getOwnPropertySymbols", "hasOwn", "isExtensible", "isFrozen", "isSealed"},
               "Reflect": {"ownKeys", "has", "isExtensible"}}
    return (properties[0] in methods.get(root, set())
            and analyzer.lookup(root, analyzer.context[node]) is None
            and root not in analyzer.global_mutated
            and "*" not in analyzer.tracking_unsafe_globals)


def _import_mutations(analyzer, imports, limitations, check):
    invalid = {index for index in imports if analyzer.bindings[index].writes or analyzer.bindings[index].duplicate}
    if ("*" in analyzer.tracking_unsafe_globals
            or any(scope.dynamic or scope.kind == "with" for scope in analyzer.scopes)):
        invalid.update(imports)

    def binding_at(index):
        path = _path(analyzer, index)
        return analyzer.lookup(path[0], analyzer.context[path[2]]) if path else None

    for target in analyzer.member_writes:
        binding = binding_at(target)
        if binding in imports:
            invalid.add(binding)
    for index in analyzer.context:
        check()
        for value in _escape_inputs(analyzer, index):
            for path in _escape_paths(analyzer, value, check):
                binding = analyzer.lookup(path[0], analyzer.context[path[2]])
                if binding in imports:
                    invalid.add(binding)
        if analyzer.typ(index) not in CALLS | {"NewExpression"}:
            continue
        for argument in analyzer.c(index, "arguments") or []:
            for path in _escape_paths(analyzer, argument, check):
                binding = analyzer.lookup(path[0], analyzer.context[path[2]])
                if binding in imports:
                    invalid.add(binding)
    if invalid:
        limitations.add("tracking_import_mutated")
    return invalid


def _classic_queue_shim(analyzer, check):
    empty = {"binding": None, "call": None, "assignments": set(), "bindings": set()}
    if analyzer.explicit_module:
        return empty
    binding = analyzer.scopes[0].bindings.get("gtag")
    if binding is None:
        return empty
    item = analyzer.bindings[binding]
    function = item.declaration
    if (item.kind != "function" or item.writes or item.duplicate
            or analyzer.typ(function) != "FunctionDeclaration"
            or function not in (analyzer.c(analyzer.root, "body") or [])
            or analyzer.c(function, "params") or analyzer.f(function, "async") or analyzer.f(function, "generator")):
        return empty
    body = analyzer.c(analyzer.c(function, "body"), "body") or []
    if len(body) != 1 or analyzer.typ(body[0]) != "ExpressionStatement":
        return empty
    call = analyzer.c(body[0], "expression")
    path = _path(analyzer, analyzer.c(call, "callee"))
    args = analyzer.c(call, "arguments") or []
    if (analyzer.typ(call) != "CallExpression" or not path or _qualified_parts(path) != ("dataLayer", ("push",))
            or len(args) != 1 or analyzer.typ(args[0]) != "Identifier" or analyzer.f(args[0], "name") != "arguments"):
        return empty
    root, _, root_node = path
    if root not in {"dataLayer", "window", "globalThis", "self", "global"}:
        return empty
    if path[1] != (("push",) if root == "dataLayer" else ("dataLayer", "push")):
        return empty
    scope = analyzer.context[root_node]
    data_binding = analyzer.lookup(root, scope)
    if root in QUALIFIERS and data_binding is not None:
        return empty
    assignments, bindings = set(), set()

    def idempotent(value):
        if analyzer.typ(value) != "LogicalExpression" or analyzer.f(value, "operator") != "||":
            return False
        left = _path(analyzer, analyzer.c(value, "left"))
        right = analyzer.c(value, "right")
        if (not left or _qualified_parts(left) != ("dataLayer", ())
                or left[0] not in {"dataLayer", "window", "globalThis", "self", "global"}
                or left[1] != (() if left[0] == "dataLayer" else ("dataLayer",))
                or analyzer.typ(right) != "ArrayExpression" or analyzer.c(right, "elements")):
            return False
        if left[0] in QUALIFIERS:
            return analyzer.lookup(left[0], analyzer.context[left[2]]) is None
        found = analyzer.lookup("dataLayer", analyzer.context[left[2]])
        return found is None or analyzer.bindings[found].kind == "var" and analyzer.bindings[found].scope == 0

    for index in analyzer.c(analyzer.root, "body") or []:
        check()
        if analyzer.typ(index) == "VariableDeclaration" and analyzer.f(index, "kind") == "var":
            for decl in analyzer.c(index, "declarations") or []:
                if (analyzer.typ(analyzer.c(decl, "id")) == "Identifier"
                        and analyzer.f(analyzer.c(decl, "id"), "name") == "dataLayer" and idempotent(analyzer.c(decl, "init"))):
                    found = analyzer.scopes[0].bindings.get("dataLayer")
                    if found is not None and not analyzer.bindings[found].duplicate and not analyzer.bindings[found].writes:
                        bindings.add(found)
        expression = analyzer.c(index, "expression") if analyzer.typ(index) == "ExpressionStatement" else None
        if analyzer.typ(expression) == "AssignmentExpression" and analyzer.f(expression, "operator") == "=":
            target = _path(analyzer, analyzer.c(expression, "left"))
            if (target and _qualified_parts(target) == ("dataLayer", ())
                    and target[0] in {"dataLayer", "window", "globalThis", "self", "global"}
                    and target[1] == (() if target[0] == "dataLayer" else ("dataLayer",))
                    and idempotent(analyzer.c(expression, "right"))
                    and (target[0] not in QUALIFIERS or analyzer.lookup(target[0], analyzer.context[target[2]]) is None)):
                assignments.add(expression)
    if root == "dataLayer" and data_binding is not None and data_binding not in bindings:
        return empty
    return {"binding": binding, "call": call, "assignments": assignments, "bindings": bindings}


def _mutations(analyzer, limitations, check, shim):
    mutated = set()

    def affect(path, index, *, escape=False):
        if path is None:
            return
        scope = analyzer.context.get(path[2], analyzer.context.get(index, 0))
        if analyzer._dynamic(scope) or analyzer.lookup(path[0], scope) is not None:
            return
        root, properties, _ = path
        # Ordinary callbacks receive an unknown local receiver. Passing that
        # receiver to jQuery/call/bind does not establish a global-object escape.
        # Direct writes to a named SDK on it remain conservative below.
        if root == "this" and escape and not _classic_global_this(analyzer, path[2]):
            return
        sdk = _sdk(path)
        qualified = _global_properties(path)
        if sdk:
            mutated.add(sdk)
        elif (qualified is not None and (not qualified or qualified[0] in {None, "__proto__", "constructor"})
              or root == "document" and not properties and not escape):
            mutated.update(ROOTS)

    for index in analyzer.context:
        check()
        typ = analyzer.typ(index)
        exempt_alias = index in shim["assignments"]
        if typ == "VariableDeclarator":
            target = analyzer.c(index, "id")
            binding = analyzer.lookup(analyzer.f(target, "name"), analyzer.context.get(target, 0))
            exempt_alias |= binding in shim["bindings"]
        if not exempt_alias:
            for value in _escape_inputs(analyzer, index):
                for path in _escape_paths(analyzer, value, check):
                    affect(path, index, escape=True)
        if typ == "AssignmentExpression":
            if index in shim["assignments"]:
                continue
            for target in analyzer._written_names(analyzer.c(index, "left")):
                affect(_path(analyzer, target), index)
        elif typ == "UpdateExpression" or typ == "UnaryExpression" and analyzer.f(index, "operator") == "delete":
            for target in analyzer._written_names(analyzer.c(index, "argument")):
                affect(_path(analyzer, target), index)
        elif typ in {"ForInStatement", "ForOfStatement"}:
            for target in analyzer._written_names(analyzer.c(index, "left")):
                affect(_path(analyzer, target), index)
        if typ not in CALLS and typ != "NewExpression":
            continue
        callee = _path(analyzer, analyzer.c(index, "callee"))
        args = analyzer.c(index, "arguments") or []
        # Escaping an ambient SDK/global object to an arbitrary call makes its
        # later identity uncertain. Its tracking call arguments are only values;
        # passing the SDK object itself is still an escape, even to another SDK.
        if not _readonly_global_call(analyzer, index):
            for argument in args:
                for path in _escape_paths(analyzer, argument, check):
                    affect(path, index, escape=True)
        # Reflective writes naming a property on an ambient global object.
        if callee and callee[0] in {"Object", "Reflect"} and len(callee[1]) == 1 and args:
            method = callee[1][0]
            target = _path(analyzer, args[0])
            if (method in {"defineProperty", "defineProperties", "assign", "set", "deleteProperty"}
                    and target and _ambient(analyzer, target, index)):
                # A global root handed to these helpers can change any SDK.
                affect(target, index, escape=True)
    if (analyzer.tracking_unsafe_globals & ({"*"} | QUALIFIERS)
            or any(scope.dynamic or scope.kind == "with" for scope in analyzer.scopes)):
        mutated.update(ROOTS)
    if not analyzer.explicit_module:
        # Classic-script top-level declarations can affect peer scripts. A
        # lexical declaration shadows their bare name; var/function can also
        # replace the global-object property. The coordinator suppresses both
        # qualified and bare hints conservatively across the comparison.
        declared = set(analyzer.scopes[0].bindings)
        if shim["binding"] is not None:
            declared.discard("gtag")
        if shim["bindings"]:
            declared.discard("dataLayer")
        mutated.update(declared & ROOTS)
        if declared & QUALIFIERS:
            mutated.update(ROOTS)
    if mutated:
        limitations.add("global_tracking_mutated")
    if shim["binding"] is not None and "dataLayer" in mutated:
        mutated.add("gtag")
    return mutated


def _navigation_parts(path, *, mutation=False):
    """A narrow browser-navigation surface, independent of SDK recognition."""
    if path is None:
        return None
    root, properties, _ = path
    if root == "location":
        return "location", properties
    if root == "document" and properties[:1] == ("location",):
        return "location", properties[1:]
    if root in {"window", "globalThis", "self"} or mutation:
        qualified = _global_properties(path) if mutation else properties
        if qualified and qualified[0] in NAVIGATION_ROOTS:
            return qualified[0], qualified[1:]
    return None


def _navigation_unknown_function_this(analyzer, index):
    """Plain functions may receive window; known method/class receivers don't."""
    for _ in range(256):
        typ = analyzer.typ(index)
        if typ in {"FunctionDeclaration", "FunctionExpression"}:
            return True
        if (typ in FUNCTIONS and typ != "ArrowFunctionExpression"
                or typ in {"Program", "ClassBody", "ClassProperty", "ClassPrivateProperty", "StaticBlock"}):
            return False
        index = analyzer.parents.get(index, (None, None))[0]
        if index is None:
            return False
    raise ValueError


def _navigation_signals(analyzer, source, limitations, check):
    """Return PRIVATE literal hints; never resolve a dynamic destination."""
    candidates, navigation_writes, blocked, partial = [], set(), set(), set()
    omitted, identity_uncertain, target_unresolved = 0, False, False

    def ambient(path, *, safe=False, allow_unknown_this=False):
        if path is None:
            return False
        root, _, node = path
        scope = analyzer.context.get(node, 0)
        if (analyzer.lookup(root, scope) is not None
                or root == "this" and not _classic_global_this(analyzer, node)
                and not (allow_unknown_this and _navigation_unknown_function_this(analyzer, node))):
            return False
        return not safe or not (analyzer._dynamic(scope)
                                or analyzer.tracking_unsafe_globals & {"*", root})

    def resolve_alias(path):
        # Resolve only explicit local references/destructuring for mutation
        # guards. This does not promote alias calls to navigation evidence.
        seen = set()
        for _ in range(12):
            check()
            if path is None:
                return None
            root, properties, node = path
            binding = analyzer.lookup(root, analyzer.context.get(node, 0))
            if binding is None:
                return path
            if binding in seen:
                return None
            seen.add(binding)
            item = analyzer.bindings[binding]
            origin = _path(analyzer, item.init)
            if (origin is None or item.duplicate or any(type(key) is not str for key in item.path)
                    or len(origin[1]) + len(item.path) + len(properties) > 12):
                return None
            path = origin[0], origin[1] + item.path + properties, origin[2]
        return None

    def readonly_call(index):
        if _readonly_global_call(analyzer, index):
            return True
        if analyzer.typ(index) != "NewExpression":
            return False
        callee = _path(analyzer, analyzer.c(index, "callee"))
        if not ambient(callee, safe=True) or analyzer.global_mutated & {"URL", "*"}:
            return False
        root, properties, _ = callee
        return root == "URL" and not properties or root in {"window", "globalThis", "self"} and properties == ("URL",)

    # Candidate recognition does not execute an expression or reconstruct a
    # browser. A conditional/optional call remains a syntactic inferred hint.
    for index in analyzer.context:
        check()
        typ = analyzer.typ(index)
        path, expression, api = None, None, None
        if typ in CALLS:
            path = _path(analyzer, analyzer.c(index, "callee"))
            parts = _navigation_parts(path)
            alias = False
            if parts is None and path is not None:
                resolved = resolve_alias(path)
                parts = _navigation_parts(resolved)
                if parts is not None and resolved != path:
                    path, alias = resolved, True
                elif (path[0] in {"window", "globalThis", "self"} and None in path[1]
                      and path[1][-1:] in {("assign",), ("replace",), ("open",)}):
                    # A computed global receiver could be a Location object.
                    # Count the uninspected syntax without inventing a target.
                    if ambient(path):
                        target_unresolved = True
                        omitted += 1
                    continue
            if parts is None:
                continue
            family, properties = parts
            if alias:
                api = "unresolved"
            elif family == "open" and not properties:
                api = "window_open"
            elif family == "location" and properties in {("assign",), ("replace",)}:
                api = "location_" + properties[0]
            elif (family == "location" and properties and
                  (properties[0] is None or properties[0] in {"assign", "replace"})
                  or family == "open" and properties):
                # Unknown computed methods and .call/.bind wrappers are not
                # promoted to browser operations or silently called absent.
                api = "unresolved"
            else:
                continue
            arguments = analyzer.c(index, "arguments") or []
            expression = arguments[0] if arguments else None
        elif typ == "AssignmentExpression":
            target = analyzer.c(index, "left")
            path = _path(analyzer, target)
            parts = _navigation_parts(path)
            if parts is None or parts[0] != "location" or parts[1] not in {(), ("href",)}:
                continue
            api = "location_href" if parts[1] else "location_assignment"
            expression = analyzer.c(index, "right") if analyzer.f(index, "operator") == "=" else None
            navigation_writes.add(target)
        else:
            continue
        if not ambient(path):
            continue
        candidates.append((index, path, expression, api))

    def affect(path, *, escape=False):
        # An opaque function can receive the global object as its receiver.
        # Direct navigation-API writes through it are uncertain; ordinary local
        # callback receiver escapes such as $(this) remain unrelated.
        # A local variable assignment rebinds that variable, not its original
        # global referent. Member writes through a known alias do affect it.
        if path is not None and path[1]:
            path = resolve_alias(path)
        elif escape:
            path = resolve_alias(path)
        if not ambient(path, allow_unknown_this=not escape):
            return
        root, properties, _ = path
        if root == "open" and not properties:
            (partial if escape else blocked).add("open")
            return
        parts = _navigation_parts(path, mutation=True)
        if parts is not None:
            family, rest = parts
            # These are primitive URL properties, not an escaped mutable
            # Location object or navigation function.
            if family == "location" and len(rest) == 1 and rest[0] in {
                    "href", "pathname", "hash", "search", "host", "hostname", "port", "protocol", "origin"}:
                return
            (partial if escape else blocked).add(family)
        else:
            global_properties = _global_properties(path)
            if global_properties is not None and (not global_properties or global_properties[0] is None):
                (partial if escape else blocked).update(NAVIGATION_ROOTS)
            elif root == "document" and not properties:
                (partial if escape else blocked).add("location")

    # Alias escapes and property writes can invalidate hints in peer scripts.
    # Direct URL assignments are navigation candidates rather than API writes.
    for index in analyzer.context:
        check()
        typ = analyzer.typ(index)
        values = _escape_inputs(analyzer, index)
        if typ == "VariableDeclarator" and _path(analyzer, analyzer.c(index, "init")) is not None:
            # A direct reference or primitive destructuring is a read. Known
            # subsequent member writes are caught by resolve_alias above.
            values = []
        for value in values:
            for path in _escape_paths(analyzer, value, check):
                affect(path, escape=True)
        targets = []
        if typ == "AssignmentExpression":
            targets = list(analyzer._written_names(analyzer.c(index, "left")))
        elif typ == "UpdateExpression" or typ == "UnaryExpression" and analyzer.f(index, "operator") == "delete":
            targets = list(analyzer._written_names(analyzer.c(index, "argument")))
        elif typ in {"ForInStatement", "ForOfStatement"}:
            targets = list(analyzer._written_names(analyzer.c(index, "left")))
        for target in targets:
            if target not in navigation_writes:
                affect(_path(analyzer, target))
        if typ in CALLS | {"NewExpression"} and not readonly_call(index):
            for value in analyzer.c(index, "arguments") or []:
                for path in _escape_paths(analyzer, value, check):
                    affect(path, escape=True)
    if (analyzer.tracking_unsafe_globals & ({"*"} | QUALIFIERS)
            or any(scope.dynamic or scope.kind == "with" for scope in analyzer.scopes)):
        blocked.update(NAVIGATION_ROOTS)
    if not analyzer.explicit_module:
        declared = set(analyzer.scopes[0].bindings)
        if declared & (QUALIFIERS | {"document"}):
            blocked.update(NAVIGATION_ROOTS)
        if "location" in declared:
            blocked.add("location")
        binding = analyzer.scopes[0].bindings.get("open")
        if binding is not None and analyzer.bindings[binding].kind in {"var", "function"}:
            blocked.add("open")
    calls = []
    for index, path, expression, api in candidates:
        check()
        family, _ = _navigation_parts(path)
        if api == "unresolved":
            target_unresolved = True
            omitted += 1
            continue
        if family in blocked or not ambient(path, safe=True):
            identity_uncertain = True
            omitted += 1
            continue
        # Literal strings only: constants, concatenation and URL construction
        # remain outside this deliberately small source-navigation feature.
        for _ in range(12):
            if analyzer.typ(expression) not in WRAPPERS:
                break
            expression = analyzer.c(expression, "expression")
        value = analyzer.f(expression, "value") if analyzer.typ(expression) == "StringLiteral" else None
        if analyzer.typ(expression) == "TemplateLiteral" and not analyzer.c(expression, "expressions"):
            quasis = analyzer.c(expression, "quasis") or []
            if len(quasis) == 1:
                # Babel stores the decoded template value in a validated field.
                value = analyzer.f(quasis[0], "cooked")
        if type(value) is not str or not value or len(value) > MAX_EVENT:
            target_unresolved = True
            omitted += 1
            continue
        if len(calls) >= MAX_RECORDS:
            raise ValueError
        calls.append({"url": value, "api": api, "navigation_root": family,
                      "evidence": {"source": source, "location": "line " + str(analyzer.node(index)["line"])},
                      "confidence": "inferred", "assumptions": [NAVIGATION_ASSUMPTION]})
    if blocked and candidates:
        limitations.add("navigation_global_identity_uncertain")
    if identity_uncertain:
        limitations.add("navigation_identity_uncertain")
    if target_unresolved:
        limitations.add("navigation_target_unresolved")
    return {"navigation_calls": calls, "navigation_omitted_count": omitted,
            "navigation_identity_uncertain": identity_uncertain, "navigation_target_unresolved": target_unresolved,
            "navigation_mutated_roots": sorted(blocked), "navigation_partial_roots": sorted(partial)}


def collect_javascript(text, source="source_1", *, kind="javascript", engine="enhanced", deadline=None, module_mode=False):
    """Collect PRIVATE tracking-call identities and fixed scope metadata.

    Basic mode deliberately does no tracking scan. Enhanced/available auto use
    the reviewed AST parser; syntax/worker/limits reject with a fixed message.
    Every record is a source-level hint, never proof of emission or SDK identity.
    """
    try:
        if (type(text) is not str or type(source) is not str or not _SOURCE.fullmatch(source)
                or kind not in {"javascript", "typescript"} or engine not in {"enhanced", "auto", "basic"}
                or type(module_mode) is not bool or len(text.encode("utf-8")) > MAX_SOURCE):
            raise ValueError
        if deadline is not None and (type(deadline) not in {int, float} or not math.isfinite(deadline)):
            raise ValueError
        end = time.monotonic() + 5.0 if deadline is None else min(deadline, time.monotonic() + 5.0)

        def check():
            if time.monotonic() >= end:
                raise ValueError

        check()
        limitations = {"tracking_static_syntax_only", "tracking_wrappers_and_aliases_not_resolved"}
        metadata = {"tracking_identity_uncertain": False, "axios_identity_uncertain": False,
                    "global_axios_modified": False, "global_axios_lexical": False,
                    "global_axios_uncertain": False,
                    "navigation_calls": [], "navigation_omitted_count": 0,
                    "navigation_identity_uncertain": False, "navigation_target_unresolved": False,
                    "navigation_mutated_roots": [], "navigation_partial_roots": []}
        if engine == "basic":
            limitations.add("tracking_requires_enhanced_parser")
            return {"records": [], "limitations": sorted(limitations), "engine": "basic", "mutated_roots": [], "omitted_count": 0, "endpoint_calls": [], "endpoint_omitted_count": 0, **metadata}
        if engine == "auto":
            ready, _ = available()
            check()
            if not ready:
                limitations.add("tracking_requires_enhanced_parser")
                return {"records": [], "limitations": sorted(limitations), "engine": "unavailable", "mutated_roots": [], "omitted_count": 0, "endpoint_calls": [], "endpoint_omitted_count": 0, **metadata}
        check()
        document = parse(text, kind)
        check()
        analyzer = _TrackingAnalyzer(document, module_mode, end)
        check()
        metadata.update(_navigation_signals(analyzer, source, limitations, check))
        imports = _declared_imports(analyzer, check)
        invalid_imports = _import_mutations(analyzer, imports, limitations, check)
        shim = _classic_queue_shim(analyzer, check)
        mutated = _mutations(analyzer, limitations, check, shim)
        check()
        endpoint_result = analyzer.result()
        check()
        metadata["global_axios_modified"] = bool(endpoint_result.get("global_axios_modified"))
        metadata["global_axios_lexical"] = bool(endpoint_result.get("global_axios_lexical"))
        metadata["global_axios_uncertain"] = metadata["global_axios_modified"] or metadata["global_axios_lexical"]
        endpoint_calls = []
        for call in endpoint_result["calls"]:
            check()
            if len(endpoint_calls) >= MAX_RECORDS:
                raise ValueError
            assumed = bool(call.get("assumed_global"))
            bare = bool(call.get("assumed_bare"))
            if assumed and (metadata["global_axios_modified"] or bare and metadata["global_axios_lexical"]):
                metadata["axios_identity_uncertain"] = True
                continue
            endpoint_calls.append({"url": call["url"], "method": call["method"], "client": call["client"],
                                   "evidence": {"source": source, "location": "line " + str(call["line"])},
                                   "confidence": "inferred", "assumptions": ["unverified_global_axios"] if assumed else [],
                                   "assumed_global": assumed, "assumed_bare": bare})
        records = []
        omitted = 0
        for index in analyzer.context:
            check()
            if analyzer.typ(index) not in CALLS:
                continue
            if index == shim["call"]:
                continue
            path = _path(analyzer, analyzer.c(index, "callee"))
            if path is None:
                continue
            local_binding = analyzer.lookup(path[0], analyzer.context[path[2]])
            axios_root, axios_properties = _qualified_parts(path)
            if (metadata["global_axios_uncertain"] and axios_root == "axios"
                    and axios_properties in {(), ("request",), ("get",), ("post",), ("put",), ("patch",), ("delete",), ("head",), ("options",)}
                    and (local_binding is None or not analyzer.explicit_module)):
                metadata["axios_identity_uncertain"] = True
            imported = imports.get(local_binding)
            ambient = imported is None
            assumptions = [ASSUMPTION]
            effective_path = path
            shim_call = False
            if imported is not None:
                if local_binding in invalid_imports or analyzer._dynamic(analyzer.context[path[2]]):
                    omitted += 1
                    metadata["tracking_identity_uncertain"] = True
                    continue
                root, prefix, namespace = imported
                properties = path[1]
                if namespace and properties[:1] == ("default",):
                    properties = properties[1:]
                effective_path = (root, prefix + properties, path[2])
                assumptions = ["declared_tracking_import"]
            elif shim["binding"] is not None and _qualified_parts(path) == ("gtag", ()):
                shim_call = (local_binding == shim["binding"] if path[0] == "gtag" else _ambient(analyzer, path, index))
                if shim_call:
                    assumptions = ["static_queue_shim_only"]
            sdk = _sdk(effective_path)
            if not sdk:
                continue
            if ambient and not shim_call and not _ambient(analyzer, path, index):
                limitations.add("tracking_shadowed_or_dynamic_scope")
                omitted += 1
                metadata["tracking_identity_uncertain"] = True
                continue
            # In classic scripts a top-level var/function also writes the
            # global object's SDK property. Module locals do not do so.
            if ambient and not shim_call and path[0] in QUALIFIERS and not analyzer.explicit_module:
                binding = analyzer.scopes[0].bindings.get(sdk)
                if binding is not None and analyzer.bindings[binding].kind in {"var", "function"}:
                    limitations.add("tracking_shadowed_or_dynamic_scope")
                    omitted += 1
                    metadata["tracking_identity_uncertain"] = True
                    continue
            if ambient and sdk in mutated:
                omitted += 1
                metadata["tracking_identity_uncertain"] = True
                continue
            selected = _classify(analyzer, effective_path, analyzer.c(index, "arguments") or [], limitations)
            if selected is _OMITTED:
                omitted += 1
                continue
            if selected is None:
                continue
            api, event = selected
            if event is None:
                limitations.add("tracking_dynamic_event")
            if len(records) >= MAX_RECORDS:
                raise ValueError
            records.append({"category": "tracking", "identity": (api, event),
                            "sdk_root": sdk, "api": api, "event_literal": event is not None,
                            "ambient": ambient,
                            "evidence": {"source": source, "location": "line " + str(analyzer.node(index)["line"])},
                            "confidence": "inferred", "assumptions": assumptions})
        check()
        return {"records": records, "limitations": sorted(limitations), "engine": "enhanced",
                "mutated_roots": sorted(mutated), "omitted_count": omitted,
                "endpoint_calls": endpoint_calls, "endpoint_omitted_count": endpoint_result["omitted"], **metadata}
    except Exception:
        raise ValueError(_ERROR) from None
