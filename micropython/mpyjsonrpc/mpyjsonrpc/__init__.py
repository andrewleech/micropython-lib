"""mpyjsonrpc: a reusable async newline-delimited JSON-RPC 2.0 peer.

Reads JSON-RPC 2.0 objects one per line from an async byte stream, dispatches
requests to registered method handlers, and writes responses/notifications
back one per line on a second stream. Symmetric: the same peer can also issue
outbound requests/notifications to whatever process is on the other end of
the pipe (e.g. a client-role use, or a bidirectional protocol), correlating
outbound-request replies by id.

Runtime support
---------------

MicroPython only. ``JsonRpcPeer`` wraps its streams in
``asyncio.StreamReader(obj)`` / ``asyncio.StreamWriter(obj)``, which is
MicroPython's constructor signature and not CPython's, so this module does
not import cleanly into CPython's asyncio. Param-mismatch detection
(``InvalidParams`` vs ``InternalError``, see ``register_method``) additionally
matches against the fixed error strings MicroPython's VM emits for a
mismatched call; on a runtime whose wording differs, an arity error degrades
to ``InternalError`` rather than being misreported. Both constraints are
deliberate: there is no portable way to distinguish a call-binding failure
from a handler-body ``TypeError`` without ``inspect``, which MicroPython
does not have.

The default streams read ``sys.stdin.buffer`` / ``sys.stdout.buffer``, which
exist only when the firmware was built with ``MICROPY_PY_SYS_STDIO_BUFFER``.
That option defaults on at ``MICROPY_CONFIG_ROM_LEVEL_AT_LEAST_EXTRA_FEATURES``
and off below it, so on a core-features build ``JsonRpcPeer()`` raises
``AttributeError`` at construction. Pass explicit ``reader=``/``writer=``
objects on such a build.

Public API
----------

``JsonRpcPeer(reader=None, writer=None, max_line_bytes=..., chunk_size=...,
log_prefix=..., log=None)``
    Constructs a peer over ``sys.stdin.buffer`` / ``sys.stdout.buffer`` by
    default. ``reader``/``writer`` accept any object exposing the
    non-blocking-friendly ``read``/``write``/``ioctl`` primitives
    ``asyncio.StreamReader``/``asyncio.StreamWriter`` need — a socket, a
    UART, or a test double — so the peer is not restricted to stdio despite
    that being the default.

``peer.register_method(name, handler)``
    Registers ``handler`` under ``name`` in the method-dispatch table.
    ``handler`` is called with the incoming ``params``: positionally
    (``handler(*params)``) when ``params`` is a JSON array, by keyword
    (``handler(**params)``) when it is a JSON object, or with no arguments
    when omitted. ``handler`` may be a plain function or an ``async def``;
    its return value (awaited first, if a coroutine) becomes the JSON-RPC
    result. Raise ``ParseError`` / ``InvalidRequest`` / ``MethodNotFound`` /
    ``InvalidParams`` / ``InternalError`` (or any ``RpcError`` subclass) to
    control the emitted JSON-RPC error object; any other exception is
    reported as ``InternalError``, except that a ``TypeError`` whose message
    matches one of MicroPython's own call-binding failures (wrong
    positional/keyword arity, unknown keyword, ...) is reported as
    ``InvalidParams`` instead, since that specific ``TypeError`` shape means
    ``params`` did not match ``handler``'s call signature. A ``TypeError``
    raised by the handler's own body (e.g. an unsupported operator) is not
    mistaken for this and is reported as ``InternalError``, in both the
    synchronous and ``async def`` cases. A returned result that is not JSON-encodable
    (or that MicroPython's ``json`` module can only render as invalid JSON,
    e.g. a ``set``, a plain class instance, or a non-finite ``float`` such
    as ``nan``/``inf``/``-inf``) is also reported as ``InternalError``
    rather than written to the wire — every line this peer emits is
    guaranteed to be valid JSON. Returns ``handler`` so it can be
    used directly.

``peer.method(name=None)``
    Decorator equivalent of ``register_method``: ``@peer.method("foo")`` or
    ``@peer.method()`` (uses the function's ``__name__``).

``await peer.notify(method, params=None)``
    Sends a server-to-client (or peer-to-peer) notification: a JSON-RPC
    object with no ``id``, so the remote end never replies to it.

``await peer.request(method, params=None, timeout=10.0)``
    Sends an outbound request, assigns it a correlation id, and awaits the
    matching response line for up to ``timeout`` seconds. Returns the
    remote ``result`` on success; raises ``RemoteError`` if the remote
    replied with a JSON-RPC error object, or ``asyncio.TimeoutError`` if no
    matching response arrives in time (the pending correlation entry is
    dropped either way). ``timeout=None`` waits indefinitely, which is only
    safe against a remote guaranteed to answer — nothing else ever drops the
    correlation entry.

``peer.log(msg)``
    Writes ``msg`` to the log sink with the peer's log prefix. The default
    sink is ``sys.stderr``, chosen so logging can never corrupt the JSON-RPC
    wire framing on stdout; pass ``log=`` to the constructor (a callable
    taking the already-prefixed string) to route elsewhere. A custom sink
    that writes to the same stream as ``writer`` will corrupt the framing.

``peer.on_shutdown(callback)``
    Registers ``callback`` (plain function or ``async def``) to run once,
    when ``serve()`` observes EOF on the reader (or is cancelled). Returns
    ``callback``.

``await peer.serve()``
    Runs the read loop until reader EOF (or cancellation): reads one line at
    a time, and dispatches each non-blank line as its own ``asyncio`` task
    (see "Concurrency model" below). Fires the registered shutdown
    callbacks before returning.

``peer.run()``
    Convenience blocking entry point: ``asyncio.run(peer.serve())``.

``is_awaitable(value)``
    Module-level predicate: true for a value that can be ``await``ed (an
    ``async def`` call result on either runtime, and any generator). Used
    internally to decide whether a handler's or callback's return value
    needs awaiting, and exported because layers built on this peer
    (``mpyfastmcp``, say) need the same decision and should not each
    reimplement it. See its own docstring for the caveat it cannot avoid.

Error types
-----------

``JsonRpcError`` is the root of everything this module raises, so
``except JsonRpcError`` catches any of them. Beneath it:

- ``RpcError`` (``.code``, ``.message``, ``.data``) and its per-code
  subclasses ``ParseError`` (-32700), ``InvalidRequest`` (-32600),
  ``MethodNotFound`` (-32601), ``InvalidParams`` (-32602), ``InternalError``
  (-32603). Raising one of these from a handler controls the emitted
  JSON-RPC error object.
- ``RemoteError`` wraps a JSON-RPC error object received in reply to an
  outbound ``request()`` call.
- ``LineTooLong`` is raised by the framing layer's oversized-line guard
  (see below). ``serve()`` handles it internally and turns it into a -32700
  response, so a handler never sees one; it is public — rather than
  ``_LineTooLong`` — because it is part of the contract for a consumer
  driving the reader itself, and because a public name inside a caught-by
  hierarchy costs nothing.

JSON-RPC 2.0 coverage
---------------------

Batch requests are **not implemented**: a top-level JSON array is rejected
with -32600 rather than being unpacked into individual calls. This is a
deliberate gap, not an oversight — MCP removed batching in its 2025-06-18
revision, and no consumer of this module has needed it. A peer that must
speak batches has to wrap the dispatch layer itself.

Framing and the oversized-line guard
-------------------------------------

Lines are read incrementally off an internal buffer rather than via
``asyncio.StreamReader.readline()`` directly, because that primitive has no
size limit: a peer that never sends a newline would otherwise grow the
buffer without bound. Once the unterminated portion of the current line
exceeds ``max_line_bytes`` (default 4 MiB — comfortably above legitimate
large payloads while still bounding a pathological unbounded line), the
reader discards it, resynchronises on the next newline it finds, and
``serve()`` responds with a -32700 parse error (id ``null``) and continues
the loop rather than crashing. The same recovery path handles random junk
bytes appearing between valid lines: whatever doesn't parse as JSON, or
doesn't parse as a valid JSON-RPC request/response object, gets a -32700 or
-32600 error (per JSON-RPC 2.0, both always carry ``id: null`` since the
malformed input can't reliably be attributed to a request id) and the loop
carries on with the next line.

Id-echo and notification rules
-------------------------------

Per JSON-RPC 2.0: a request's ``id`` (string, number, or ``null``) is
echoed back verbatim on its response. A *notification* is a request object
with the ``"id"`` key entirely absent (not merely ``null``) — for those,
no response is ever emitted, including on error; failures during a
notification's handling are reported only via ``log()``.

Concurrency model
------------------

Each inbound line is dispatched as its own ``asyncio`` task
(task-per-request), so one slow handler cannot stall the read loop or other
in-flight requests/notifications. All outbound writes — responses,
notifications, and outbound requests — funnel through a single
``asyncio.Lock``-guarded writer, so concurrent tasks can never interleave
partial JSON lines on the writer; a notification fired while a request is being
handled is guaranteed to land as a complete, separate line either before or
after the response, never spliced into it.
"""

import asyncio
import json
import math
import sys

__version__ = "0.1.0"


# ── JSON-RPC error types ─────────────────────────────────────────────────


class JsonRpcError(Exception):
    """Root of every exception this module raises.

    Exists so a consumer can write `except JsonRpcError` and catch anything
    originating here — a protocol error (`RpcError`), a remote peer's error
    reply (`RemoteError`), or a framing failure (`LineTooLong`) — without
    enumerating three unrelated hierarchies.
    """


class RpcError(JsonRpcError):
    """Base JSON-RPC 2.0 protocol error. Subclasses set `code` to the spec value.

    `message` and optional `data` become the `error.message` / `error.data`
    fields of the response object; `code` becomes `error.code`.
    """

    code = -32603

    def __init__(self, message, data=None):
        self.message = message
        self.data = data
        super().__init__(message)


class ParseError(RpcError):
    """Invalid JSON was received (JSON-RPC 2.0 code -32700)."""

    code = -32700


class InvalidRequest(RpcError):
    """The JSON received is not a valid JSON-RPC 2.0 request object (-32600)."""

    code = -32600


class MethodNotFound(RpcError):
    """No handler is registered for the requested method (-32601)."""

    code = -32601


class InvalidParams(RpcError):
    """`params` don't match the handler's call signature (-32602)."""

    code = -32602


class InternalError(RpcError):
    """The handler raised an exception other than `RpcError` (-32603)."""

    code = -32603


class RemoteError(JsonRpcError):
    """Raised by `request()` when the remote replies with a JSON-RPC error.

    `error` is the raw `{"code", "message", ...}` object from the response.
    """

    def __init__(self, error):
        self.error = error
        super().__init__(str(error))


class LineTooLong(JsonRpcError):
    """Raised by the framing layer when a line exceeds `max_line_bytes`
    with no `\\n`.

    `serve()` catches this itself and answers -32700, so a registered
    handler never sees one; it is nonetheless part of the public surface,
    for a consumer driving the line reader directly. By the time it
    propagates, the reader has already discarded the oversized data and
    resynchronised on the next newline, so the next read proceeds normally.
    """


# ── Line framing ──────────────────────────────────────────────────────────


class _LineReader:
    """Buffers `stream_reader` into complete `b"...\\n"`-terminated lines.

    Unlike `asyncio.StreamReader.readline()`, this enforces `max_line_bytes`
    on the unterminated portion of the *current* line, so a peer that never
    sends `\\n` cannot grow memory without bound.
    """

    def __init__(self, stream_reader, max_line_bytes, chunk_size):
        self._sr = stream_reader
        self._max = max_line_bytes
        self._chunk = chunk_size
        self._buf = bytearray()

    async def readline(self):
        """Return the next line (newline included), or b"" on EOF.

        Raises `LineTooLong` if the current line's bytes exceed
        `max_line_bytes` before a newline appears; by the time it raises,
        the reader has discarded the offending bytes and resynchronised on
        the next `\\n` it can find, so the next call proceeds normally.
        """
        while True:
            idx = self._buf.find(b"\n")
            if idx != -1:
                line = bytes(self._buf[: idx + 1])
                self._buf = bytearray(self._buf[idx + 1 :])
                return line
            if len(self._buf) > self._max:
                await self._discard_to_newline()
                raise LineTooLong
            chunk = await self._sr.read(self._chunk)
            if not chunk:
                # EOF. Flush whatever partial (unterminated) data remains
                # once; the next call finds an empty buffer and returns
                # b"" again, so callers see a stable EOF signal.
                if self._buf:
                    line = bytes(self._buf)
                    self._buf = bytearray()
                    return line
                return b""
            self._buf += chunk

    async def _discard_to_newline(self):
        self._buf = bytearray()
        while True:
            chunk = await self._sr.read(self._chunk)
            if not chunk:
                return  # EOF while resynchronising
            idx = chunk.find(b"\n")
            if idx != -1:
                self._buf = bytearray(chunk[idx + 1 :])
                return


# ── Outbound-request correlation ─────────────────────────────────────────


class _PendingRequest:
    def __init__(self):
        self.event = asyncio.Event()
        self.result = None
        self.error = None


# ── The peer ──────────────────────────────────────────────────────────────

_DEFAULT_MAX_LINE_BYTES = 4 * 1024 * 1024
_DEFAULT_CHUNK_SIZE = 4096
_DEFAULT_TIMEOUT = 10.0
_VALID_ID_TYPES = (str, int, float)
_JSON_SCALAR_TYPES = (type(None), bool, int, float, str)


def _validate_json_safe(value):
    """Raise `TypeError` if `value` isn't representable as JSON.

    MicroPython's `json.dumps` never raises on an unsupported type (a
    `set`, a plain class instance, ...) — it silently `str()`-renders it
    inline, producing syntactically invalid JSON embedded in whatever line
    it's part of. Round-tripping the encoded bytes back through
    `json.loads` is not a reliable substitute for a real check either:
    MicroPython's decoder is lenient enough to silently reinterpret some
    malformed input as a *different*, still well-formed-looking object
    rather than raising (e.g. a `set` literal's `{...}` braces get parsed
    as an object with implicit values) — confirmed by hand, this is not
    a hypothetical. Walking the object graph before encoding, rejecting
    anything that isn't one of JSON's own types, is what actually
    guarantees a valid line.

    A `float` that is `nan` or `inf`/`-inf` is likewise rejected: JSON has
    no token for either, and MicroPython's `json.dumps` renders them as the
    bare words `nan`/`inf`/`-inf`, which are not valid JSON.
    """
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise TypeError("value is not JSON-representable: %r" % (value,))
    if isinstance(value, _JSON_SCALAR_TYPES):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_safe(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("dict keys must be str, got %r" % (key,))
            _validate_json_safe(item)
        return
    raise TypeError("value is not JSON-representable: %r" % (value,))


def is_awaitable(value):
    """True if `value` must be awaited to get at its result.

    Duck-typed on the `send`/`throw` pair every awaitable carries, rather
    than on `type(value).__name__`: MicroPython names an `async def` call
    result `"generator"` while CPython names it `"coroutine"`, so a name
    comparison silently stops awaiting the moment the module is run on the
    other runtime — returning the coroutine object itself as the JSON-RPC
    result.

    A plain (non-async) generator satisfies the same duck-type and is
    therefore reported as awaitable. That is unavoidable on MicroPython,
    where an `async def` call result and a generator are the same type;
    the consequence is only that a generator function registered as a
    handler is driven to completion rather than returned, which is closer
    to the caller's likely intent than serialising a generator object.
    """
    return hasattr(value, "send") and hasattr(value, "throw")


def _is_valid_id(value):
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    return isinstance(value, _VALID_ID_TYPES)


# `handler(*params)` / `handler(**params)` raises `TypeError` in two distinct
# situations that are otherwise indistinguishable from the outside: the
# arguments failing to bind against the handler's parameter list (a client
# error -> InvalidParams), or the handler's body raising its own TypeError
# once running (a server-side fault -> InternalError, same as any other
# handler exception). MicroPython gives call sites no traceback or
# `__code__`/`inspect` access to tell these apart structurally, so binding
# failures are recognised by the fixed, interpreter-generated messages the
# VM itself raises for a mismatched call (`py/objfun.c` et al.) -- messages
# ordinary handler code essentially never produces on its own. Anything
# else -- including a message-alike mismatch surfacing from a call nested
# inside the handler's own body -- is treated as an internal fault, which is
# the conservative choice: it never hides a real bug behind "your params
# were wrong".
_PARAM_MISMATCH_MESSAGES = (
    "argument num/types mismatch",
    "unexpected keyword argument",
    "function missing keyword-only argument",
)
_PARAM_MISMATCH_PREFIXES = (
    "function missing required positional argument",
    "function got multiple values for argument",
)


def _is_param_mismatch(message):
    if message in _PARAM_MISMATCH_MESSAGES:
        return True
    for prefix in _PARAM_MISMATCH_PREFIXES:
        if message.startswith(prefix):
            return True
    return False


class JsonRpcPeer:
    """An async newline-delimited JSON-RPC 2.0 peer.

    Defaults to stdio but accepts any `read`/`write`-capable stream pair
    via `reader=`/`writer=`. See the module docstring for the full public
    API and the framing / concurrency contracts.
    """

    def __init__(
        self,
        reader=None,
        writer=None,
        max_line_bytes=_DEFAULT_MAX_LINE_BYTES,
        chunk_size=_DEFAULT_CHUNK_SIZE,
        log_prefix="[mpyjsonrpc] ",
        log=None,
    ):
        raw_in = reader if reader is not None else sys.stdin.buffer
        raw_out = writer if writer is not None else sys.stdout.buffer
        self._reader = _LineReader(asyncio.StreamReader(raw_in), max_line_bytes, chunk_size)
        self._sw = asyncio.StreamWriter(raw_out)
        self._write_lock = asyncio.Lock()
        self._methods = {}
        self._pending = {}
        self._next_id = 1
        self._shutdown_cbs = []
        self._log_prefix = log_prefix
        self._log_sink = log
        self._tasks = []

    # ── Method registry ───────────────────────────────────────────────

    def register_method(self, name, handler):
        """Register `handler` under `name`. Returns `handler`."""
        self._methods[name] = handler
        return handler

    def method(self, name=None):
        """Decorator form of `register_method`. `@peer.method()` uses `fn.__name__`."""

        def decorator(fn):
            self.register_method(name if name is not None else fn.__name__, fn)
            return fn

        return decorator

    # ── Shutdown hook ──────────────────────────────────────────────────

    def on_shutdown(self, callback):
        """Register `callback` to run once, on reader EOF. Returns `callback`."""
        self._shutdown_cbs.append(callback)
        return callback

    # ── Logging ────────────────────────────────────────────────────────

    def log(self, msg):
        """Write `msg` to the log sink with this peer's log prefix.

        The default sink is `sys.stderr`, which is never the JSON-RPC wire,
        so logging cannot corrupt framing. A `log=` callable given to the
        constructor receives the already-prefixed, newline-terminated
        string instead.
        """
        line = "%s%s\n" % (self._log_prefix, msg)
        if self._log_sink is not None:
            self._log_sink(line)
        else:
            sys.stderr.write(line)

    # ── Outbound: notifications and correlated requests ────────────────

    async def notify(self, method, params=None):
        """Send a notification (no `id`); the remote must not reply to it."""
        obj = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            obj["params"] = params
        await self._send_obj(obj)

    async def request(self, method, params=None, timeout=_DEFAULT_TIMEOUT):
        """Send an outbound request and await its correlated response.

        Returns the remote `result` on success. Raises `RemoteError` if the
        remote replies with a JSON-RPC error object, or
        `asyncio.TimeoutError` if no response arrives within `timeout`
        seconds.
        """
        req_id = self._next_id
        self._next_id += 1
        obj = {"jsonrpc": "2.0", "method": method, "id": req_id}
        if params is not None:
            obj["params"] = params
        pending = _PendingRequest()
        self._pending[req_id] = pending
        await self._send_obj(obj)
        try:
            await asyncio.wait_for(pending.event.wait(), timeout)
        finally:
            self._pending.pop(req_id, None)
        if pending.error is not None:
            raise RemoteError(pending.error)
        return pending.result

    def _handle_response(self, obj):
        pending = self._pending.get(obj.get("id"))
        if pending is None:
            return  # unknown/stale id (e.g. already timed out) — drop
        if "error" in obj:
            pending.error = obj["error"]
        else:
            pending.result = obj.get("result")
        pending.event.set()

    # ── Outbound: the single serialized writer ──────────────────────────

    async def _send_obj(self, obj):
        raw = self._encode(obj)
        async with self._write_lock:
            self._sw.write(raw)
            self._sw.write(b"\n")
            await self._sw.drain()

    def _encode(self, obj):
        """Encode `obj` to a JSON line, guaranteeing the result is valid JSON.

        Validates the object graph with `_validate_json_safe` before ever
        calling `json.dumps`, so an unencodable value raises `TypeError`
        here — where the caller can turn it into an `InternalError`
        response — instead of reaching the wire as a corrupt line. See
        `_validate_json_safe` for why a post-hoc round-trip through
        `json.loads` doesn't substitute for this.
        """
        _validate_json_safe(obj)
        return json.dumps(obj).encode()

    async def _send_error_response(self, req_id, exc):
        error = {"code": exc.code, "message": exc.message}
        if exc.data is not None:
            error["data"] = exc.data
        try:
            await self._send_obj({"jsonrpc": "2.0", "id": req_id, "error": error})
        except Exception:
            # `exc.data` came from handler/caller code and can itself be
            # unencodable (an `RpcError(..., data=some_object)`); drop it
            # rather than let a bad `data` payload corrupt the wire the way
            # an unencodable handler `result` would (see `_encode`).
            error.pop("data", None)
            error["message"] = "%s (data omitted: not JSON-serializable)" % (exc.message,)
            await self._send_obj({"jsonrpc": "2.0", "id": req_id, "error": error})

    # ── Inbound: the read loop ──────────────────────────────────────────

    async def serve(self):
        """Read and dispatch lines until reader EOF, then fire shutdown hooks.

        On EOF, first awaits any handler tasks still in flight (so a
        request read just before the peer's reader closed still gets its
        response written), then fires the registered shutdown callbacks.
        """
        try:
            while True:
                try:
                    line = await self._reader.readline()
                except LineTooLong:
                    await self._send_error_response(
                        None, ParseError("line exceeds max_line_bytes")
                    )
                    continue
                if not line:
                    break
                stripped = line.strip()
                if stripped:
                    self._spawn(self._handle_line(stripped))
        finally:
            pending = [t for t in self._tasks if not t.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await self._fire_shutdown()

    def run(self):
        """Blocking convenience entry point: `asyncio.run(self.serve())`."""
        asyncio.run(self.serve())

    def _spawn(self, coro):
        # Prune finished tasks on every spawn instead of retaining every
        # task for the peer's lifetime, so a long-running peer's task list
        # stays bounded by roughly the current in-flight count.
        self._tasks = [t for t in self._tasks if not t.done()]
        task = asyncio.create_task(coro)
        self._tasks.append(task)
        return task

    async def _fire_shutdown(self):
        for cb in self._shutdown_cbs:
            try:
                result = cb()
                if is_awaitable(result):
                    await result
            except Exception as exc:
                self.log("shutdown callback error: %s" % exc)

    async def _handle_line(self, raw):
        try:
            obj = json.loads(raw)
        except ValueError:
            await self._send_error_response(None, ParseError("Parse error"))
            return
        if isinstance(obj, dict) and "method" not in obj and ("result" in obj or "error" in obj):
            self._handle_response(obj)
            return
        await self._dispatch(obj)

    # ── Inbound: request/notification dispatch ──────────────────────────

    def _parse_request(self, obj):
        if not isinstance(obj, dict):
            raise InvalidRequest("request must be a JSON object")
        if obj.get("jsonrpc") != "2.0":
            raise InvalidRequest("missing or invalid jsonrpc version")
        method = obj.get("method")
        if not isinstance(method, str) or not method:
            raise InvalidRequest("missing or invalid method")
        has_id = "id" in obj
        req_id = obj.get("id") if has_id else None
        if has_id and not _is_valid_id(req_id):
            raise InvalidRequest("id must be a string, number, or null")
        params = obj.get("params")
        if params is not None and not isinstance(params, (list, dict)):
            raise InvalidRequest("params must be an array or object")
        return method, params, has_id, req_id

    def _call_handler(self, handler, params):
        if isinstance(params, list):
            return handler(*params)
        if isinstance(params, dict):
            return handler(**params)
        return handler()

    async def _dispatch(self, obj):
        try:
            method, params, has_id, req_id = self._parse_request(obj)
        except RpcError as exc:
            # Parse/structural failures always echo id: null — the object
            # was not well-formed enough to trust any id it might contain.
            await self._send_error_response(None, exc)
            return

        try:
            handler = self._methods.get(method)
            if handler is None:
                raise MethodNotFound("method not found: %s" % method)
            try:
                result = self._call_handler(handler, params)
            except TypeError as exc:
                if _is_param_mismatch(str(exc)):
                    raise InvalidParams(str(exc))
                raise
            if is_awaitable(result):
                result = await result
            # Encode (and validate) the response line while still inside
            # this try block: a handler result that `json.dumps` can't
            # faithfully render (a set, a class instance, ...) must become
            # an InternalError response, not a corrupt line on the wire — see
            # `_encode`'s docstring for why MicroPython needs this check.
            response = {"jsonrpc": "2.0", "id": req_id, "result": result}
            raw = self._encode(response) if has_id else None
        except RpcError as exc:
            if has_id:
                await self._send_error_response(req_id, exc)
            else:
                self.log("notification %r failed: %s" % (method, exc.message))
            return
        except Exception as exc:
            if has_id:
                await self._send_error_response(req_id, InternalError(str(exc)))
            else:
                self.log("notification %r failed: %s" % (method, exc))
            return

        if has_id:
            async with self._write_lock:
                self._sw.write(raw)
                self._sw.write(b"\n")
                await self._sw.drain()
