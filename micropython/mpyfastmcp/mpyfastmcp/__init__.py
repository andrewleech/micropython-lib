"""mpyfastmcp: a FastMCP-style MCP server layer over mpyjsonrpc + mpyschema.

Composes `mpyjsonrpc.JsonRpcPeer` (transport: framing, dispatch, the single
serialized stdout writer) with `mpyschema` (explicit param specs ->
`inputSchema` / prompt `arguments`, plus argument validation) into an
`MCPServer` object that speaks the Model Context Protocol's `initialize`,
`tools/list`, `tools/call`, `prompts/list`, `prompts/get`, `resources/list`,
and `resources/read` methods.

This module is deliberately generic: it has no knowledge of any specific
app's tools, prompts, or notification method names. An app built on top of
it (see `demo_server.py` for a worked example) supplies its own tool/prompt
handlers and its own notification method names via `server.notify(...)`.

Runtime support: MicroPython only, inherited from `mpyjsonrpc` — see that
module's docstring for what the constraint actually consists of.

Public API
----------

``MCPServer(name, version, instructions=None, capabilities=None, reader=None, writer=None, log_prefix=None, log=None, protocol_versions=None)``
    Constructs the server. `name`/`version` populate the `serverInfo` object
    of the `initialize` result; `instructions` (if given) becomes the
    result's `instructions` string. `capabilities` is merged over the
    layer's own default (`{"tools": {}}` once at least one tool is
    registered, `{"prompts": {}}` once at least one prompt is registered,
    `{"resources": {}}` once at least one resource is registered) — pass
    e.g. `capabilities={"experimental": {"foo": {}}}` to declare additional
    capability blocks; keys here always win over the computed default.
    `reader`/`writer`/`log` are forwarded to the underlying
    `mpyjsonrpc.JsonRpcPeer` (defaults `sys.stdin.buffer` /
    `sys.stdout.buffer` / `sys.stderr`), so a server over a socket or a
    UART is spelled the same way as one over stdio. `log_prefix` sets the
    peer's log prefix; defaults to `"[%s] " % name`, so two servers with
    different names never share an indistinguishable log tag.
    `protocol_versions` overrides the module's `PROTOCOL_VERSIONS` tuple
    (newest first) for a consumer tracking a revision this package predates.
    The peer instance is available as `server.peer` for direct access to
    `log()`, `register_method()`, or other transport-level primitives.

``server.add_tool(name, description, handler, params=None)``
``server.add_prompt(name, description, handler, arguments=None)``
``server.add_resource(uri, name, handler, description=None, mime_type=None)``
    Imperative registration; each returns `handler`. These are the primary
    forms, and the decorators below are thin wrappers over them — a handler
    bound to an app instance, registered from inside a method, reads better
    as a call than as a decorator invoked as one. Registering a second time
    under an existing tool name, prompt name, or resource URI raises
    `ValueError` rather than silently replacing the first.

``@server.tool(name, description, params=None)``
    Registers a tool. `params` is an `mpyschema` spec (a list of `Field`
    instances, or `None`/`[]` for a zero-argument tool); it drives both the
    `inputSchema` emitted from `tools/list` and the argument validation run
    before the decorated handler is called on `tools/call`. The handler is
    called as `handler(**validated_arguments)` — a plain function or an
    `async def`; declare defaults for any optional (non-required,
    default-less) parameters the handler wants to accept, since a validated
    dict omits a key entirely when the client didn't send it and the field
    has no `default`. The handler's return value becomes the tool result
    via `tool_result()` (see below) unless it already returns a
    `{"content": [...]}`-shaped dict, which is passed through unchanged. An
    `mpyschema.SchemaError` raised by `validate()` (bad arguments) or any
    exception raised by the handler itself both surface
    as an `isError` result (via `error_result()`), never as a JSON-RPC
    protocol error — matching the MCP convention that a tool call reaching
    its handler is a "successful" JSON-RPC exchange even when the tool
    itself failed. An unrecognised `name` at `tools/call` time likewise
    produces an `isError` result rather than `-32601`, since the `name` is
    a `tools/call` argument, not the JSON-RPC method. Returns the
    undecorated handler, so `@server.tool(...)` composes with other
    decorators.

``@server.prompt(name, description, arguments=None)``
    Registers a prompt. `arguments` is an `mpyschema` spec (only `desc`/
    `required` are used — prompt arguments carry no JSON type). The
    decorated handler is called as `handler(**validated_arguments)` on
    `prompts/get` and must return `{"description": ..., "messages": [...]}`
    (the shape `prompts/get` sends back verbatim). An unrecognised prompt
    `name`, or an argument that fails `mpyschema.validate()` (a
    `MissingParameter` or `InvalidParameter`), both raise `mpyjsonrpc.InvalidParams`,
    a real JSON-RPC error (`-32602`) — unlike `tools/call`, MCP's
    `prompts/get` has no `isError` result convention of its own. Returns
    the undecorated handler.

``@server.resource(uri, name, description=None, mime_type=None)``
    Registers a resource. `uri` is the resource's stable identifier and
    registry key; `name` is the human-readable label. `description` and
    `mime_type` are optional and, when given, surface as `description` /
    `mimeType` in `resources/list` (omitted entirely when absent, rather
    than emitted as `null`). The decorated handler takes no arguments and
    is called on `resources/read` for its `uri`; it may be a plain function
    or an `async def`. Its return value becomes the `resources/read`
    result via `resource_result()` (see below) unless it already returns a
    `{"contents": [...]}`-shaped dict, which is passed through unchanged.
    An unrecognised `uri` at `resources/read` time raises
    `mpyjsonrpc.InvalidParams` (`-32602`, message `"Resource not found"`,
    `data` carrying the `uri`), which is the code the MCP spec assigns to
    this case. Returns the undecorated handler, so `@server.resource(...)`
    composes with other decorators.

    Resource handlers take no arguments, and `resources/templates/list` is
    not implemented, so a parameterized resource (a URI template) cannot be
    expressed through this layer. A consumer who needs one can register the
    method directly on the transport via
    `server.peer.register_method("resources/templates/list", handler)`,
    which is what that escape hatch is for.

``server.on_initialized(callback)``
    Registers `callback()` to run once, when the client's
    `notifications/initialized` notification arrives (i.e. once the MCP
    handshake has completed). `callback` may be a plain function or an
    `async def`. Call `server.get_client_capabilities()` /
    `server.get_client_info()` from inside `callback` to read what the
    client declared in its `initialize` request. Each registered callback
    is isolated: one raising does not prevent the others from running (the
    exception is logged via `server.peer.log()` and swallowed), matching
    `on_shutdown`'s isolation below. Returns `callback`.

``server.get_client_capabilities()`` / ``server.get_client_info()``
    Return the `capabilities` / `clientInfo` objects the client sent with
    `initialize`. Both return `None` until `initialize` has been handled --
    a shared sentinel for "not yet known", distinct from the client having
    sent an empty `capabilities: {}`.

``server.on_tool_result(callback)``
    Registers `callback(tool_name, result)` to run, in registration order,
    on every outgoing `tools/call` result (`result` is the
    `{"content": [...], "isError": ...}` dict about to be sent back) before
    it is written to stdout. **Fires on both success and `isError` results**
    — a callback that only cares about successful calls must check
    `result.get("isError")` itself and skip when it is set. A callback may
    be a plain function or an `async def`, and either returns a replacement
    result dict or returns nothing after mutating `result` in place (a
    `None` return leaves the result unchanged rather than sending `null`).
    The most common shape appends one or more content blocks to
    `result["content"]`, e.g. how an app might drain a queue of pending
    out-of-band notices into the next tool result rather than losing them.
    One callback raising is logged via `server.peer.log()` and skipped, and
    the remaining callbacks still run, matching `on_initialized` and
    `on_shutdown`. This is the layer's only opinion on result
    post-processing — what gets queued and when is entirely up to the app.
    Does not run for `prompts/get` or `initialize`. Returns `callback`.

``await server.notify(method, params=None)``
    Sends an arbitrary server-to-client notification (no `id`, so the
    client never replies) via the underlying peer. `method` is entirely
    caller-supplied — this layer does not hard-code any notification
    method name.

``server.on_shutdown(callback)``
    Delegates to `mpyjsonrpc.JsonRpcPeer.on_shutdown` — registers
    `callback` (plain function or `async def`) to run once, on stdin EOF.
    Returns `callback`.

``await server.serve()``
    The coroutine form: serves requests until stdin EOF, a thin wrapper
    over `JsonRpcPeer.serve()`. Use this when the caller already has its
    own event loop running (composing this server with other `asyncio`
    tasks). See `mpyjsonrpc` for the framing, dispatch, and concurrency
    contracts this relies on (task-per-request dispatch, a single
    serialized stdout writer so a `notify()` fired mid-request can never
    corrupt another response's framing, EOF -> shutdown-callback
    behaviour).

``server.run()``
    The blocking convenience entry point: `asyncio.run(self.serve())`.
    Mirrors `JsonRpcPeer.run()`/`JsonRpcPeer.serve()` exactly, so
    `server.run()` and `server.peer.run()` (both blocking) behave the same
    way — a caller who reaches for the peer-familiar bare `server.run()`
    (no `await`) gets the running server, not a silently-discarded
    coroutine.

``tool_result(data, indent=None)``
    Module-level helper: wraps arbitrary tool-handler return data as an MCP
    `tools/call` result. A dict already shaped like `{"content": [...]}` is
    returned unchanged (a handler that wants full control — multiple
    content blocks, a non-text content type, an explicit `isError` — can
    just build the result itself and return it). A `str` becomes a single
    text content block verbatim. Anything else is JSON-encoded into a
    single text content block, compactly by default or indented when
    `indent=N` is given.

``json_pretty(obj, indent=2)``
    Module-level helper: renders `obj` as indented JSON. MicroPython's
    `json.dumps` accepts no `indent` keyword, so this exists rather than
    leaving every consumer that wants readable results to write it again.

``error_result(message)``
    Module-level helper: builds `{"isError": True, "content":
    [{"type": "text", "text": message}]}` — the generic form of "this tool
    call did not succeed", used automatically for validation failures,
    handler exceptions, and unknown tool names, and available for a
    handler to return directly for its own domain-specific failures.

``resource_result(uri, data, mime_type=None)``
    Module-level helper: wraps arbitrary resource-handler return data as an
    MCP `resources/read` result. A dict already shaped like `{"contents":
    [...]}` is returned unchanged. A `list` is used directly as the
    `contents` array (each item already a content block the handler built
    itself). A `str` becomes a single text content block for `uri` (tagged
    with `mime_type` when given). Anything else is JSON-encoded into a
    single text content block for `uri`.

Standard MCP method coverage
-----------------------------

`initialize`, `ping`, `tools/list`, `tools/call`, `prompts/list`,
`prompts/get`, `resources/list`, and `resources/read` are registered as
`mpyjsonrpc` method handlers at construction time. Any other inbound method
falls through to `mpyjsonrpc`'s own "no handler registered" path and is
reported as JSON-RPC `-32601` (`MethodNotFound`), exactly as for any
unregistered method on a bare `JsonRpcPeer`. `resources/subscribe`,
`resources/templates/list`, `completion/complete` and the logging methods
are all in that category; `server.peer.register_method` is the way to add
one without waiting on this layer.

`tools/list`, `tools/call`, `prompts/list`, `prompts/get`, `resources/list`,
and `resources/read` are gated on the MCP lifecycle: a client that calls
any of them before `initialize` has been handled gets `NotInitialized`
(`-32600`) instead of being served. `initialize`, `ping` and
`notifications/initialized` are never gated — the spec explicitly permits
a client to ping before the handshake completes.

`initialize` handshake / protocol version negotiation
-------------------------------------------------------

`initialize`'s `protocolVersion` is negotiated the way the MCP spec (and
the observed behaviour of at least one popular SDK) does it: if the
client's requested version is one this server knows about
(`protocol_versions=`, defaulting to the module's `PROTOCOL_VERSIONS`,
newest first), it is echoed back verbatim; otherwise the newest known
version is returned instead of failing the handshake.
Capabilities and `clientInfo` from the request are captured for
`get_client_capabilities()`/`get_client_info()`, and `serverInfo` /
`capabilities` (and `instructions`, if given) are returned per the spec's
`initialize` result shape. `notifications/initialized` (the client's
handshake-complete notification) fires the `on_initialized()` callbacks.
"""

import asyncio
import json

from mpyjsonrpc import InvalidParams, JsonRpcPeer, RpcError, is_awaitable
from mpyschema import SchemaError, emit_prompt_args, emit_schema, validate


__version__ = "0.1.0"

# Newest first. A client's requested `protocolVersion` is echoed back
# verbatim when it appears here; otherwise the handshake falls back to the
# newest version this layer knows, rather than failing outright — the
# behaviour observed from popular MCP client/server SDKs when negotiating
# with an unfamiliar version. This tuple is the one value in the package
# guaranteed to go stale, which is why `MCPServer(protocol_versions=...)`
# exists: a consumer tracking a newer revision overrides it without
# editing the installed package.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


class NotInitialized(RpcError):
    """A lifecycle-gated method (`tools/list`, `tools/call`, `prompts/list`,
    `prompts/get`, `resources/list`, `resources/read`) was called before
    `initialize` completed.

    Reported as -32600 (Invalid Request), which is the closest pre-defined
    JSON-RPC code for a request that is well-formed but not permitted in
    the connection's current state. No MCP revision defines a code for this
    condition, and the 2026-07-28 revision's error-code policy closes off
    inventing one: `-32000`..`-32019` is legacy that new implementations
    SHOULD NOT draw from, `-32020`..`-32099` is reserved to the spec
    itself, and anything genuinely novel SHOULD sit outside the JSON-RPC
    reserved range entirely -- where no client would recognise it anyway.
    """

    code = -32600


def json_pretty(obj, indent=2, _level=0):
    """`json.dumps(obj, indent=N)`-equivalent rendering.

    MicroPython's `json` module is encode/decode only -- `json.dumps` takes
    no `indent` keyword and raises "argument num/types mismatch" if given
    one -- so the indented, one-entry-per-line layout is built by hand,
    recursing into dicts and lists and deferring scalar encoding to
    `json.dumps`. Exported because a consumer wanting indented tool results
    would otherwise have to write this a second time; `tool_result(data,
    indent=N)` is the usual way in.
    """
    pad_unit = " " * indent
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        pad = pad_unit * (_level + 1)
        parts = [
            "%s%s: %s" % (pad, json.dumps(k), json_pretty(v, indent, _level + 1))
            for k, v in obj.items()
        ]
        return "{\n" + ",\n".join(parts) + "\n" + pad_unit * _level + "}"
    if isinstance(obj, list):
        if not obj:
            return "[]"
        pad = pad_unit * (_level + 1)
        parts = [pad + json_pretty(v, indent, _level + 1) for v in obj]
        return "[\n" + ",\n".join(parts) + "\n" + pad_unit * _level + "]"
    return json.dumps(obj)


def tool_result(data, indent=None):
    """Wrap arbitrary tool-handler return data as an MCP `tools/call` result.

    A dict is passed through unchanged only when it is already shaped like
    a result -- `content` present as a *list* (the content-block array) --
    so a handler's own data dict that merely happens to have a `content`
    key (e.g. `{"content": "file text", "path": "/x"}`) is not mistaken for
    a pre-built result. A `str` becomes a single text content block
    verbatim. Anything else, including such a dict, is JSON-encoded into a
    single text content block.

    `indent=N` renders that JSON indented (via `json_pretty`) rather than
    compact, for a server whose results are read by a human as often as
    parsed by a model. The default stays compact.
    """
    if isinstance(data, dict) and isinstance(data.get("content"), list):
        return data
    if isinstance(data, str):
        text = data
    elif indent:
        text = json_pretty(data, indent)
    else:
        text = json.dumps(data)
    return {"content": [{"type": "text", "text": text}]}


def error_result(message):
    """Build an `isError` MCP `tools/call` result carrying `message` as text."""
    return {"isError": True, "content": [{"type": "text", "text": message}]}


def resource_result(uri, data, mime_type=None):
    """Wrap arbitrary resource-handler return data as an MCP
    `resources/read` result.

    A dict is passed through unchanged only when it is already shaped like
    a result -- `contents` present as a *list* (the content-block array).
    A bare `list` is used directly as that `contents` array (each item
    already a content block the handler built itself). A `str` becomes a
    single text content block for `uri`, tagged with `mime_type` when
    given. Anything else is JSON-encoded into a single text content block
    for `uri`.
    """
    if isinstance(data, dict) and isinstance(data.get("contents"), list):
        return data
    if isinstance(data, list):
        return {"contents": data}
    block = {"uri": uri}
    if mime_type:
        block["mimeType"] = mime_type
    block["text"] = data if isinstance(data, str) else json.dumps(data)
    return {"contents": [block]}


class _Tool:
    __slots__ = ("description", "handler", "input_schema", "name", "params")

    def __init__(self, name, description, params, handler):
        self.name = name
        self.description = description
        self.params = params or []
        self.handler = handler
        self.input_schema = emit_schema(self.params)

    def definition(self):
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class _Prompt:
    __slots__ = ("arguments", "description", "handler", "name")

    def __init__(self, name, description, arguments, handler):
        self.name = name
        self.description = description
        self.arguments = arguments or []
        self.handler = handler

    def definition(self):
        return {
            "name": self.name,
            "description": self.description,
            "arguments": emit_prompt_args(self.arguments),
        }


class _Resource:
    __slots__ = ("description", "handler", "mime_type", "name", "uri")

    def __init__(self, uri, name, description, mime_type, handler):
        self.uri = uri
        self.name = name
        self.description = description
        self.mime_type = mime_type
        self.handler = handler

    def definition(self):
        d = {"uri": self.uri, "name": self.name}
        if self.description:
            d["description"] = self.description
        if self.mime_type:
            d["mimeType"] = self.mime_type
        return d


class MCPServer:
    """A FastMCP-style MCP server: `@tool`/`@prompt`/`@resource` decorators
    over `mpyjsonrpc.JsonRpcPeer`. See the module docstring for the full public
    API.
    """

    def __init__(
        self,
        name,
        version,
        instructions=None,
        capabilities=None,
        reader=None,
        writer=None,
        log_prefix=None,
        log=None,
        protocol_versions=None,
    ):
        self._name = name
        self._version = version
        self._instructions = instructions
        self._capabilities = capabilities or {}
        self._protocol_versions = protocol_versions or PROTOCOL_VERSIONS

        # Plain dicts on MicroPython don't preserve insertion order (see
        # mpyschema's docstring) -- registration order for `tools/list` /
        # `prompts/list` / `resources/list` is tracked separately via the
        # `_order` lists rather than relying on dict iteration order.
        self._tools = {}
        self._tool_order = []
        self._prompts = {}
        self._prompt_order = []
        self._resources = {}
        self._resource_order = []

        self._tool_result_cbs = []
        self._oninitialized_cbs = []
        # `None` is the shared "not yet known" sentinel for both, distinct
        # from the client having sent an empty `capabilities: {}` -- see
        # `get_client_capabilities()`/`get_client_info()`.
        self._client_capabilities = None
        self._client_info = None
        self._initialized = False

        if log_prefix is None:
            log_prefix = "[%s] " % name
        self.peer = JsonRpcPeer(reader=reader, writer=writer, log_prefix=log_prefix, log=log)
        self.peer.register_method("initialize", self._handle_initialize)
        self.peer.register_method("notifications/initialized", self._handle_initialized)
        # `ping` is defined for both parties by every MCP revision, and is
        # the one request a client is explicitly permitted to send before
        # `initialize`, so it is registered ungated.
        self.peer.register_method("ping", self._handle_ping)
        self.peer.register_method("tools/list", self._handle_tools_list)
        self.peer.register_method("tools/call", self._handle_tools_call)
        self.peer.register_method("prompts/list", self._handle_prompts_list)
        self.peer.register_method("prompts/get", self._handle_prompts_get)
        self.peer.register_method("resources/list", self._handle_resources_list)
        self.peer.register_method("resources/read", self._handle_resources_read)

    # ── Registration ─────────────────────────────────────────────────────
    #
    # The `add_*` forms are primary; the decorators below wrap them.

    def add_tool(self, name, description, handler, params=None):
        """Register `handler` as the tool `name`. Returns `handler`."""
        if name in self._tools:
            raise ValueError("tool already registered: %s" % name)
        self._tools[name] = _Tool(name, description, params, handler)
        self._tool_order.append(name)
        return handler

    def add_prompt(self, name, description, handler, arguments=None):
        """Register `handler` as the prompt `name`. Returns `handler`."""
        if name in self._prompts:
            raise ValueError("prompt already registered: %s" % name)
        self._prompts[name] = _Prompt(name, description, arguments, handler)
        self._prompt_order.append(name)
        return handler

    def add_resource(self, uri, name, handler, description=None, mime_type=None):
        """Register `handler` as the resource at `uri`. Returns `handler`."""
        if uri in self._resources:
            raise ValueError("resource already registered: %s" % uri)
        self._resources[uri] = _Resource(uri, name, description, mime_type, handler)
        self._resource_order.append(uri)
        return handler

    def tool(self, name, description, params=None):
        """Decorator form of `add_tool`:
        `@server.tool("name", "description", params=[...])`."""

        def decorator(fn):
            return self.add_tool(name, description, fn, params=params)

        return decorator

    def resource(self, uri, name, description=None, mime_type=None):
        """Decorator form of `add_resource`:
        `@server.resource("uri", "name", description=None, mime_type=None)`."""

        def decorator(fn):
            return self.add_resource(uri, name, fn, description=description, mime_type=mime_type)

        return decorator

    def prompt(self, name, description, arguments=None):
        """Decorator form of `add_prompt`:
        `@server.prompt("name", "description", arguments=[...])`."""

        def decorator(fn):
            return self.add_prompt(name, description, fn, arguments=arguments)

        return decorator

    # ── Handshake ────────────────────────────────────────────────────────

    def on_initialized(self, callback):
        """Register `callback()` to run once `notifications/initialized`
        arrives. Returns `callback`."""
        self._oninitialized_cbs.append(callback)
        return callback

    def get_client_capabilities(self):
        """Return the `capabilities` object from the client's `initialize`
        request, or `None` if `initialize` hasn't been handled yet -- the
        same "not yet known" sentinel `get_client_info()` uses, distinct
        from the client having sent an empty `capabilities: {}`."""
        return self._client_capabilities

    def get_client_info(self):
        """Return the `clientInfo` object from the client's `initialize`
        request, or `None` if `initialize` hasn't been handled yet."""
        return self._client_info

    def _effective_capabilities(self):
        caps = {}
        if self._tool_order:
            caps["tools"] = {}
        if self._prompt_order:
            caps["prompts"] = {}
        if self._resource_order:
            caps["resources"] = {}
        caps.update(self._capabilities)
        return caps

    async def _handle_initialize(self, **params):
        # `**params` (rather than a fixed `protocolVersion`/`capabilities`/
        # `clientInfo` signature) tolerates a client that sends additional
        # `initialize` properties the spec allows but this layer doesn't
        # otherwise use -- a fixed signature would reject those as an
        # mpyjsonrpc `InvalidParams` binding failure.
        requested = params.get("protocolVersion")
        self._client_capabilities = params.get("capabilities") or {}
        self._client_info = params.get("clientInfo")
        known = self._protocol_versions
        negotiated = requested if requested in known else known[0]
        result = {
            "protocolVersion": negotiated,
            "capabilities": self._effective_capabilities(),
            "serverInfo": {"name": self._name, "version": self._version},
        }
        if self._instructions:
            result["instructions"] = self._instructions
        self._initialized = True
        return result

    def _require_initialized(self):
        """Raise `NotInitialized` unless `initialize` has already been
        handled. Called by every lifecycle-gated method (`tools/list`,
        `tools/call`, `prompts/list`, `prompts/get`) -- never by
        `initialize` itself or by the `notifications/initialized` handler."""
        if not self._initialized:
            raise NotInitialized("server not initialized: call initialize first")

    async def _handle_initialized(self, **_params):
        for cb in self._oninitialized_cbs:
            try:
                result = cb()
                if is_awaitable(result):
                    await result
            except Exception as exc:
                self.peer.log("on_initialized callback failed: %s" % exc)

    async def _handle_ping(self, **_params):
        # MCP's `ping` carries no data in either direction: the empty
        # result *is* the "still alive" answer. Ungated on purpose -- a
        # client is permitted to ping before `initialize`.
        return {}

    # ── Tools ────────────────────────────────────────────────────────────

    async def _handle_tools_list(self, **_params):
        self._require_initialized()
        return {"tools": [self._tools[n].definition() for n in self._tool_order]}

    async def _handle_tools_call(self, **params):
        # Read only the fields this method uses off the request params,
        # ignoring the reserved MCP `_meta` field (and any other/unknown
        # params) — the structured field-extraction model the MCP SDKs use
        # (the TS SDK destructures `req.params.{name,arguments}`; the Python
        # SDK parses a typed params model). This is what avoids coupling the
        # JSON-RPC params keys to a handler's Python signature, which made a
        # client's `_meta` surface as -32602 "unexpected keyword argument".
        self._require_initialized()
        name = params.get("name")
        arguments = params.get("arguments")
        tool = self._tools.get(name)
        if tool is None:
            return await self._finalize_result(name, error_result("Unknown tool: %s" % name))

        try:
            validated = validate(tool.params, arguments)
        except SchemaError as exc:
            return await self._finalize_result(name, error_result(str(exc)))

        try:
            raw = tool.handler(**validated)
            if is_awaitable(raw):
                raw = await raw
        except Exception as exc:
            # Surface to the client as an isError result, but ALSO log
            # server-side: a swallowed handler exception (notably a
            # MemoryError from a large payload) would otherwise leave no
            # trace of hitting a limit. peer.log writes to stderr.
            self.peer.log("tool %r raised: %s" % (name, exc))
            return await self._finalize_result(name, error_result(str(exc)))

        return await self._finalize_result(name, tool_result(raw))

    def on_tool_result(self, callback):
        """Register `callback(tool_name, result)` to run, in registration
        order, on every outgoing `tools/call` result before it is sent --
        for both success and `isError` results; a callback that only wants
        to act on success should check `result.get("isError")` itself and
        skip when it is set.

        `callback` may be a plain function or an `async def`. Return the
        replacement result dict, or return nothing and mutate `result` in
        place; a `None` return leaves the result as it was rather than
        sending `null`. A callback that raises is logged and skipped, and
        the remaining callbacks still run -- the same isolation
        `on_initialized` and `on_shutdown` give. Returns `callback`."""
        self._tool_result_cbs.append(callback)
        return callback

    async def _finalize_result(self, tool_name, result):
        for cb in self._tool_result_cbs:
            try:
                returned = cb(tool_name, result)
                if is_awaitable(returned):
                    returned = await returned
                if returned is not None:
                    result = returned
            except Exception as exc:
                # Isolated for the same reason the other two hook points
                # are: one app's post-processing bug should not turn every
                # tool call on the server into a -32603.
                self.peer.log("on_tool_result callback failed: %s" % exc)
        return result

    # ── Prompts ──────────────────────────────────────────────────────────

    async def _handle_prompts_list(self, **_params):
        self._require_initialized()
        return {"prompts": [self._prompts[n].definition() for n in self._prompt_order]}

    async def _handle_prompts_get(self, **params):
        # Field extraction (see _handle_tools_call): read name/arguments,
        # ignore `_meta` and unknown params.
        self._require_initialized()
        name = params.get("name")
        arguments = params.get("arguments")
        prompt = self._prompts.get(name)
        if prompt is None:
            raise InvalidParams("unknown prompt: %s" % name)
        try:
            validated = validate(prompt.arguments, arguments)
        except SchemaError as exc:
            raise InvalidParams(str(exc))
        result = prompt.handler(**validated)
        if is_awaitable(result):
            result = await result
        return result

    # ── Resources ────────────────────────────────────────────────────────

    async def _handle_resources_list(self, **_params):
        self._require_initialized()
        return {"resources": [self._resources[u].definition() for u in self._resource_order]}

    async def _handle_resources_read(self, **params):
        # Field extraction (see _handle_tools_call): read uri, ignore
        # `_meta` and unknown params.
        self._require_initialized()
        uri = params.get("uri")
        resource = self._resources.get(uri)
        if resource is None:
            # Shape per the MCP spec's resource-not-found example. Not
            # -32002, which SEP-2164 retired for this case.
            raise InvalidParams("Resource not found", {"uri": uri})
        raw = resource.handler()
        if is_awaitable(raw):
            raw = await raw
        return resource_result(uri, raw, resource.mime_type)

    # ── Notifications / lifecycle ───────────────────────────────────────

    async def notify(self, method, params=None):
        """Send an arbitrary server->client notification. `method` is
        entirely caller-supplied."""
        await self.peer.notify(method, params)

    def on_shutdown(self, callback):
        """Delegates to `JsonRpcPeer.on_shutdown`. Returns `callback`."""
        return self.peer.on_shutdown(callback)

    async def serve(self):
        """Serve MCP requests until stdin EOF. The coroutine form; thin
        wrapper over `JsonRpcPeer.serve()`. Use this directly if the
        caller already has its own event loop running (e.g. is composing
        this server with other `asyncio` tasks); otherwise use the
        blocking `run()`."""
        await self.peer.serve()

    def run(self):
        """Blocking convenience entry point: `asyncio.run(self.serve())`.
        Mirrors `JsonRpcPeer.run()`/`JsonRpcPeer.serve()` exactly, so
        `server.run()` and `server.peer.run()` behave the same way (both
        block until stdin EOF) rather than one being a bare coroutine that
        silently does nothing if called without `await`."""
        asyncio.run(self.serve())
