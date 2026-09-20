"""Conformance suite for mpyjsonrpc, run against a real MicroPython binary.

Spawns a MicroPython interpreter as a subprocess speaking real stdio and
drives it with hand-crafted request/notification/junk bytes, asserting on
the JSON-RPC responses (and, where relevant, stderr and exit status) it
produces. Deliberately black-box: it never imports mpyjsonrpc from CPython
(it cannot — the module targets MicroPython's runtime primitives, see the
module docstring's "Runtime support"), it only proves the on-the-wire
behaviour of a MicroPython process using it.

Run it with pytest:

    MPY_BIN=/path/to/micropython python3 -m pytest test_mpyjsonrpc.py

`MPY_BIN` names the interpreter to drive. With it unset, a `micropython`
on `PATH` is used; with neither available every test skips rather than
failing, so a consumer running the suite without a built interpreter gets
a clear skip instead of a `FileNotFoundError`.
"""

import json
import os
import shutil
import subprocess
import textwrap

import pytest


def _import_root():
    """Directory to add to `sys.path` so `import mpyjsonrpc` resolves.

    Handles this file sitting either inside the package directory or
    beside it.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isdir(os.path.join(here, "mpyjsonrpc")):
        return here
    return os.path.dirname(here)


LIB_DIR = _import_root()
MPY_BIN = os.environ.get("MPY_BIN") or shutil.which("micropython")

pytestmark = pytest.mark.skipif(
    not (MPY_BIN and os.path.exists(MPY_BIN)),
    reason="no MicroPython interpreter found (set MPY_BIN, or put `micropython` on PATH)",
)

SERVER_SCRIPT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, %r)
    import asyncio
    import mpyjsonrpc as rpc

    def add(a, b):
        return a + b

    def no_params():
        return "ok"

    def boom():
        raise ValueError("kaboom")

    def big(payload):
        return {"len": len(payload)}

    async def slow_echo(msg, delay=0.05):
        await asyncio.sleep(delay)
        return msg

    def unencodable_set():
        return {1, 2, 3}

    class Thing:
        pass

    def unencodable_object():
        return Thing()

    def nan_result():
        return float("nan")

    def inf_nested_result():
        return {"a": 1, "b": float("inf"), "c": {"d": float("-inf")}}

    def boom_typeerror():
        return None + 1

    async def aboom_typeerror():
        return None + 1

    async def main():
        peer = rpc.JsonRpcPeer(max_line_bytes=%d)
        peer.register_method("add", add)
        peer.register_method("no_params", no_params)
        peer.register_method("boom", boom)
        peer.register_method("boom_typeerror", boom_typeerror)
        peer.register_method("aboom_typeerror", aboom_typeerror)
        peer.register_method("big", big)
        peer.register_method("slow_echo", slow_echo)
        peer.register_method("unencodable_set", unencodable_set)
        peer.register_method("unencodable_object", unencodable_object)
        peer.register_method("nan_result", nan_result)
        peer.register_method("inf_nested_result", inf_nested_result)

        async def notify_bad_nonfinite():
            # The non-finite value must be rejected before this
            # notification ever reaches the wire; the handler must never
            # get to return.
            await peer.notify("bad_event", {"x": float("nan")})
            return "unreachable"

        peer.register_method("notify_bad_nonfinite", notify_bad_nonfinite)

        async def notify_during_big(n, size):
            # Interleaves outbound notifications with assembling a large
            # result, so the single serialized writer is exercised against
            # a concurrent notify() call mid-handler, not just sequential
            # request/response traffic.
            for i in range(n):
                await peer.notify("progress", {"i": i})
            return "y" * size

        peer.register_method("notify_during_big", notify_during_big)

        def on_eof():
            peer.log("shutdown-callback-fired")

        peer.on_shutdown(on_eof)
        await peer.serve()

    asyncio.run(main())
    """
)


def _write_server(tmp_path, max_line_bytes=2 * 1024 * 1024):
    script = tmp_path / "server.py"
    script.write_text(SERVER_SCRIPT % (LIB_DIR, max_line_bytes))
    return str(script)


def _run(
    tmp_path,
    payload_lines,
    max_line_bytes=2 * 1024 * 1024,
    timeout=10,
    want_rc=False,
):
    """Run the server against a fixed input, return (stdout_lines, stderr_bytes).

    `payload_lines` is a list of already-encoded bytes objects (each is
    written verbatim, back to back — include trailing `\\n` explicitly
    where a well-formed line is wanted). `want_rc=True` appends the
    process's exit status to the returned tuple.
    """
    script = _write_server(tmp_path, max_line_bytes)
    payload = b"".join(payload_lines)
    proc = subprocess.run(
        [MPY_BIN, script],
        input=payload,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    stdout_lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    if want_rc:
        return stdout_lines, proc.stderr, proc.returncode
    return stdout_lines, proc.stderr


def _req(id_, method, params=None):
    obj = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        obj["params"] = params
    return (json.dumps(obj) + "\n").encode()


def _notif(method, params=None):
    obj = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        obj["params"] = params
    return (json.dumps(obj) + "\n").encode()


# ── Happy path ────────────────────────────────────────────────────────────


def test_happy_path_positional_and_named_params(tmp_path):
    lines, stderr = _run(
        tmp_path,
        [
            _req(1, "add", [2, 3]),
            _req("s1", "add", {"a": 10, "b": 5}),
        ],
    )
    by_id = {r["id"]: r for r in lines}
    assert by_id[1]["result"] == 5
    assert by_id["s1"]["result"] == 15


def test_no_params_call(tmp_path):
    lines, _ = _run(tmp_path, [_req(1, "no_params")])
    assert lines[0]["result"] == "ok"


# ── Batch-of-lines burst ────────────────────────────────────────────────


def test_batch_burst_all_get_responses(tmp_path):
    reqs = [_req(i, "add", [i, 1]) for i in range(20)]
    lines, _ = _run(tmp_path, reqs)
    by_id = {r["id"]: r["result"] for r in lines}
    assert by_id == {i: i + 1 for i in range(20)}


# ── JSON-RPC error objects ───────────────────────────────────────────────


def test_malformed_json_is_parse_error(tmp_path):
    lines, _ = _run(tmp_path, [b"not json at all\n"])
    assert lines[0]["error"]["code"] == -32700
    assert lines[0]["id"] is None


def test_unknown_method_is_method_not_found(tmp_path):
    lines, _ = _run(tmp_path, [_req(1, "does_not_exist")])
    assert lines[0]["error"]["code"] == -32601
    assert lines[0]["id"] == 1


def test_wrong_arity_params_is_invalid_params(tmp_path):
    lines, _ = _run(tmp_path, [_req(1, "add", [1])])
    assert lines[0]["error"]["code"] == -32602
    assert lines[0]["id"] == 1


def test_handler_exception_is_internal_error(tmp_path):
    lines, _ = _run(tmp_path, [_req(1, "boom")])
    assert lines[0]["error"]["code"] == -32603
    assert lines[0]["id"] == 1


def test_handler_body_typeerror_is_internal_error_not_invalid_params(tmp_path):
    # A TypeError raised by the handler's own body (not by `params` failing
    # to bind against its call signature) must be reported as -32603, the
    # same code as any other handler-side fault -- never -32602, which
    # would mislead the caller into thinking its arguments were wrong.
    lines, _ = _run(tmp_path, [_req(1, "boom_typeerror")])
    assert lines[0]["error"]["code"] == -32603
    assert lines[0]["id"] == 1


def test_async_handler_body_typeerror_is_internal_error(tmp_path):
    # Same fault as above, raised from an `async def` handler's body, must
    # produce the same -32603 code as the synchronous case.
    lines, _ = _run(tmp_path, [_req(1, "aboom_typeerror")])
    assert lines[0]["error"]["code"] == -32603
    assert lines[0]["id"] == 1


def test_wrong_arity_typeerror_is_invalid_params_not_internal_error(tmp_path):
    # Converse of the two tests above: a genuine call-signature mismatch
    # must still map to -32602, not regress to -32603.
    lines, _ = _run(tmp_path, [_req(1, "add", [1])])
    assert lines[0]["error"]["code"] == -32602
    assert lines[0]["id"] == 1


# ── Unencodable handler results never reach stdout as corrupt JSON ───────
#
# MicroPython's json.dumps doesn't raise on an unsupported type; it
# str()-renders it inline (e.g. `{"result": <Thing object at ...>}`), which
# would otherwise corrupt the response line. `_run` already round-trips
# every stdout line through `json.loads`, so a regression here would fail
# the harness itself (a `json.JSONDecodeError`) rather than just an
# assertion below — these tests exist to pin the -32603 behaviour on top
# of that.


def test_unencodable_set_result_is_internal_error(tmp_path):
    lines, _ = _run(tmp_path, [_req(1, "unencodable_set")])
    assert lines[0]["id"] == 1
    assert lines[0]["error"]["code"] == -32603


def test_unencodable_object_result_is_internal_error(tmp_path):
    lines, _ = _run(tmp_path, [_req(1, "unencodable_object")])
    assert lines[0]["id"] == 1
    assert lines[0]["error"]["code"] == -32603


def test_nan_result_is_internal_error(tmp_path):
    # A non-finite float is the subtler half of the same problem: JSON has
    # no token for nan/inf, and MicroPython's json.dumps renders them as
    # the bare words `nan`/`inf`/`-inf`, which are not valid JSON.
    lines, _ = _run(tmp_path, [_req(1, "nan_result")])
    assert len(lines) == 1
    assert lines[0]["id"] == 1
    assert lines[0]["error"]["code"] == -32603
    assert "result" not in lines[0]


def test_inf_nested_result_is_internal_error(tmp_path):
    # Nested rather than top-level, so the check has to walk the object
    # graph rather than only inspecting the result itself.
    lines, _ = _run(tmp_path, [_req(1, "inf_nested_result")])
    assert len(lines) == 1
    assert lines[0]["id"] == 1
    assert lines[0]["error"]["code"] == -32603
    assert "result" not in lines[0]


def test_notify_with_nonfinite_param_writes_nothing_invalid(tmp_path):
    # Outbound direction of the same guard: the notification must never
    # reach the wire, and the triggering request must fail cleanly.
    lines, _ = _run(tmp_path, [_req(1, "notify_bad_nonfinite")])
    assert not any(l.get("method") == "bad_event" for l in lines)
    assert len(lines) == 1
    assert lines[0]["id"] == 1
    assert lines[0]["error"]["code"] == -32603


def test_unencodable_notification_result_emits_no_response(tmp_path):
    # A notification's return value is never written to stdout at all (no
    # id to reply to), so an unencodable result is silently discarded
    # rather than surfaced anywhere -- this just pins that it doesn't
    # crash the loop or produce a stray line.
    lines, _ = _run(
        tmp_path,
        [
            _notif("unencodable_set"),
            _req(1, "no_params"),  # sentinel so we know the notif was processed
        ],
    )
    assert len(lines) == 1
    assert lines[0]["id"] == 1


def test_non_object_request_is_invalid_request(tmp_path):
    lines, _ = _run(tmp_path, [b"[1, 2, 3]\n"])
    assert lines[0]["error"]["code"] == -32600
    assert lines[0]["id"] is None


def test_missing_method_is_invalid_request(tmp_path):
    lines, _ = _run(tmp_path, [b'{"jsonrpc":"2.0","id":1}\n'])
    assert lines[0]["error"]["code"] == -32600
    assert lines[0]["id"] is None


# ── Notification handling ────────────────────────────────────────────────


def test_notification_emits_no_response(tmp_path):
    lines, _ = _run(
        tmp_path,
        [
            _notif("add", [1, 1]),
            _req(1, "add", [1, 1]),  # sentinel so we know the notif was processed
        ],
    )
    assert len(lines) == 1
    assert lines[0]["id"] == 1


def test_notification_error_does_not_respond(tmp_path):
    lines, _ = _run(
        tmp_path,
        [
            _notif("boom"),
            _req(1, "add", [1, 1]),
        ],
    )
    assert len(lines) == 1
    assert lines[0]["id"] == 1


# ── Id-type preservation ─────────────────────────────────────────────────


@pytest.mark.parametrize("id_value", [1, "abc", None, 3.5])
def test_id_type_is_echoed_exactly(tmp_path, id_value):
    lines, _ = _run(tmp_path, [_req(id_value, "no_params")])
    assert lines[0]["id"] == id_value


# ── Huge payload ─────────────────────────────────────────────────────────


def test_huge_payload_round_trips(tmp_path):
    payload = "x" * 300_000
    lines, _ = _run(tmp_path, [_req(1, "big", [payload])])
    assert lines[0]["result"]["len"] == 300_000


# ── Oversized-line guard / framing fuzz ─────────────────────────────────


def test_oversized_line_guard_recovers(tmp_path):
    oversized = b"z" * 5000 + b"\n"  # no newline until well past the tiny cap below
    good = _req(1, "no_params")
    lines, _ = _run(tmp_path, [oversized, good], max_line_bytes=1024)
    codes = [l["error"]["code"] for l in lines if "error" in l]
    assert -32700 in codes
    assert any(l.get("result") == "ok" for l in lines)


def test_framing_fuzz_junk_between_valid_lines_never_crashes(tmp_path):
    junk = b"\x00\x01\xff\xfe garbage { not json \n"
    lines, _ = _run(
        tmp_path,
        [_req(1, "no_params"), junk, _req(2, "no_params")],
    )
    by_id = {l.get("id"): l for l in lines}
    assert by_id[1]["result"] == "ok"
    assert by_id[2]["result"] == "ok"
    assert any(l["error"]["code"] == -32700 for l in lines if "error" in l)


# ── Concurrency: task-per-request completion order, notify() mid-handler ──


def test_slow_async_handler_does_not_block_faster_requests(tmp_path):
    # Task-per-request dispatch means a slow `async def` handler must not
    # stall the fast requests queued behind it in the same read burst: the
    # fast responses are expected to land before the slow one, i.e. NOT in
    # input order, proving the read loop doesn't serialize handler
    # execution sequentially per-connection.
    reqs = [
        _req("slow", "slow_echo", ["late"]),  # default delay 0.05s
        _req(1, "add", [1, 1]),
        _req(2, "add", [2, 1]),
        _req(3, "add", [3, 1]),
    ]
    lines, _ = _run(tmp_path, reqs)
    order = [l["id"] for l in lines]
    assert order.index("slow") > order.index(1)
    assert order.index("slow") > order.index(2)
    assert order.index("slow") > order.index(3)
    by_id = {l["id"]: l["result"] for l in lines}
    assert by_id == {"slow": "late", 1: 2, 2: 3, 3: 4}


def test_notify_fired_mid_handler_interleaves_safely_with_large_response(tmp_path):
    # The mandated concurrency-policy test: a handler that calls
    # `peer.notify()` repeatedly *while* assembling a large result exercises
    # the single serialized writer against real interleaving, not just
    # sequential request/response traffic. Every stdout line -- each
    # notification and the final response -- must still be a complete,
    # independently parseable JSON line; `_run` already round-trips every
    # line through `json.loads`, so a framing corruption would fail there
    # rather than in the assertions below.
    lines, _ = _run(tmp_path, [_req(1, "notify_during_big", {"n": 50, "size": 100_000})])
    notifications = [l for l in lines if "id" not in l]
    responses = [l for l in lines if l.get("id") == 1]
    assert len(notifications) == 50
    assert [n["params"]["i"] for n in notifications] == list(range(50))
    assert len(responses) == 1
    assert len(responses[0]["result"]) == 100_000


# ── EOF / shutdown / stderr separation ───────────────────────────────────


def test_eof_fires_shutdown_callback(tmp_path):
    _, stderr, returncode = _run(tmp_path, [_req(1, "no_params")], want_rc=True)
    assert b"shutdown-callback-fired" in stderr
    # EOF is a normal end of service, not a fault: the process must exit 0
    # rather than unwinding through an exception after the last response.
    assert returncode == 0


def test_stderr_never_pollutes_stdout(tmp_path):
    lines, stderr = _run(tmp_path, [_req(1, "no_params")])
    assert lines[0]["result"] == "ok"
    assert b"shutdown-callback-fired" in stderr
    # every stdout line must be valid, complete JSON (already enforced by
    # `_run`'s json.loads over each line) and none of them contain the
    # log prefix that only ever goes to stderr
    for line in lines:
        assert "shutdown-callback-fired" not in json.dumps(line)


# ── The outbound half: request(), RemoteError, method(), log= ────────────
#
# Correlation ids are deterministic (`_next_id` starts at 1 and increments
# per outbound request), so a reply can be staged in the input payload
# ahead of time. Task-per-request dispatch is what makes that work: the
# handler awaiting its reply does not block the read loop from reading it.

OUTBOUND_SCRIPT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, %r)
    import asyncio
    import mpyjsonrpc as rpc

    peer = rpc.JsonRpcPeer()

    @peer.method("call_out")
    async def call_out():
        result = await peer.request("client/echo", {"v": 1}, timeout=5)
        return {"got": result}

    @peer.method("call_out_err")
    async def call_out_err():
        try:
            await peer.request("client/fails", timeout=5)
        except rpc.RemoteError as exc:
            return {"remote_error": exc.error}
        return {"remote_error": None}

    @peer.method("call_out_timeout")
    async def call_out_timeout():
        try:
            await peer.request("client/silent", timeout=0.2)
        except asyncio.TimeoutError:
            return {"timed_out": True}
        return {"timed_out": False}

    @peer.method("caught_as_base")
    async def caught_as_base():
        try:
            await peer.request("client/fails", timeout=5)
        except rpc.JsonRpcError as exc:
            return {"base": type(exc).__name__}
        return {"base": None}

    @peer.method()
    def named_from_function():
        return "implicit-name"

    async def main():
        await peer.serve()

    asyncio.run(main())
    """
)


def _run_outbound(tmp_path, payload_lines, timeout=10):
    script = tmp_path / "outbound_server.py"
    script.write_text(OUTBOUND_SCRIPT % LIB_DIR)
    proc = subprocess.run(
        [MPY_BIN, str(script)],
        input=b"".join(payload_lines),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    return lines, proc.stderr


def _reply(id_, result):
    return (json.dumps({"jsonrpc": "2.0", "id": id_, "result": result}) + "\n").encode()


def _reply_error(id_, code, message):
    return (
        json.dumps({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})
        + "\n"
    ).encode()


def test_request_sends_a_correlated_request_and_returns_its_result(tmp_path):
    lines, _ = _run_outbound(tmp_path, [_req(1, "call_out"), _reply(1, "pong")])
    outbound = [l for l in lines if l.get("method") == "client/echo"]
    assert len(outbound) == 1, "request() must emit exactly one outbound request"
    assert outbound[0]["params"] == {"v": 1}
    assert outbound[0]["id"] == 1, "an outbound request must carry a correlation id"
    response = [l for l in lines if l.get("id") == 1 and "result" in l]
    assert response[-1]["result"] == {"got": "pong"}


def test_request_raises_remote_error_on_an_error_reply(tmp_path):
    lines, _ = _run_outbound(tmp_path, [_req(1, "call_out_err"), _reply_error(1, -32601, "nope")])
    result = [l for l in lines if l.get("id") == 1 and "result" in l][-1]["result"]
    assert result["remote_error"] == {"code": -32601, "message": "nope"}


def test_remote_error_is_catchable_as_the_library_base(tmp_path):
    lines, _ = _run_outbound(tmp_path, [_req(1, "caught_as_base"), _reply_error(1, -32000, "x")])
    result = [l for l in lines if l.get("id") == 1 and "result" in l][-1]["result"]
    assert result["base"] == "RemoteError"


def test_request_times_out_when_no_reply_arrives(tmp_path):
    # No staged reply: the correlation entry must be dropped and
    # asyncio.TimeoutError raised, rather than hanging until EOF.
    lines, _ = _run_outbound(tmp_path, [_req(1, "call_out_timeout")])
    result = [l for l in lines if l.get("id") == 1 and "result" in l][-1]["result"]
    assert result["timed_out"] is True


def test_notification_carries_no_id(tmp_path):
    # notify() is covered indirectly elsewhere; asserted here against the
    # outbound peer so the "no id, never answered" rule is explicit.
    lines, _ = _run(tmp_path, [_req(1, "notify_during_big", {"n": 2, "size": 4})])
    notifications = [l for l in lines if l.get("method") == "progress"]
    assert len(notifications) == 2
    for n in notifications:
        assert "id" not in n


def test_method_decorator_registers_under_the_function_name(tmp_path):
    lines, _ = _run_outbound(tmp_path, [_req(1, "named_from_function")])
    assert lines[-1]["result"] == "implicit-name"


def test_log_sink_override_replaces_stderr(tmp_path):
    """`log=` must receive the already-prefixed line and stderr must stay
    clean -- the point being a port without usable stderr, or a consumer
    wanting structured logs, no longer has to patch the method."""
    script = tmp_path / "log_sink_server.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys
            sys.path.insert(0, %r)
            import asyncio
            import mpyjsonrpc as rpc

            captured = []
            peer = rpc.JsonRpcPeer(log_prefix="[sink] ", log=captured.append)

            @peer.method("emit")
            def emit():
                peer.log("routed")
                return {"captured": captured}

            asyncio.run(peer.serve())
            """
        )
        % LIB_DIR
    )
    proc = subprocess.run(
        [MPY_BIN, str(script)],
        input=_req(1, "emit"),
        capture_output=True,
        timeout=10,
        check=False,
    )
    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    assert lines[-1]["result"]["captured"] == ["[sink] routed\n"]
    assert b"routed" not in proc.stderr, "a custom sink must not also write stderr"
