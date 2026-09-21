"""Conformance suite for mpyfastmcp, run against a real MicroPython binary.

Spawns a MicroPython interpreter running `demo_server.py` (this directory)
as a real stdio subprocess and drives it as an MCP client would:
`initialize` handshake, `ping`, `tools/list`, `tools/call` (success,
validation error, handler exception, unknown tool, reserved `_meta`),
`prompts/list`, `prompts/get` (success and unknown prompt),
`resources/list`, `resources/read` (success and unknown URI), lifecycle
gating, the `on_tool_result` / `on_initialized` hook contracts, and an
unknown JSON-RPC method. Black-box on purpose — it never imports
`mpyfastmcp` from CPython (it cannot; see `mpyjsonrpc`'s module docstring
for why), it only proves the on-the-wire behaviour of a MicroPython
process using it. The few assertions that are genuinely about in-process
API shape rather than the wire go through `_probe`, which also runs on the
interpreter.

Run it with pytest:

    MPY_BIN=/path/to/micropython python3 -m pytest test_mpyfastmcp.py

`MPY_BIN` names the interpreter to drive. With it unset, a `micropython`
on `PATH` is used; with neither available every test skips rather than
failing, so a consumer running the suite without a built interpreter gets
a clear skip instead of a `FileNotFoundError`.
"""

import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_SERVER = os.path.join(HERE, "demo_server.py")
MPY_BIN = os.environ.get("MPY_BIN") or shutil.which("micropython")


def _import_roots():
    """Directories to add to `sys.path` so `mpyfastmcp` and its two
    dependencies resolve.

    Handles this file sitting either inside the package directory or
    beside it, with the dependencies in sibling package directories. A
    published install resolves them via `require()` instead.
    """
    parent = os.path.dirname(HERE)
    if not os.path.isdir(os.path.join(HERE, "mpyfastmcp")):
        return [parent]
    roots = [HERE]
    for dep in ("mpyjsonrpc", "mpyschema"):
        dep_root = os.path.join(parent, dep)
        if os.path.isdir(os.path.join(dep_root, dep)):
            roots.append(dep_root)
    return roots


IMPORT_ROOTS = _import_roots()

pytestmark = pytest.mark.skipif(
    not (MPY_BIN and os.path.exists(MPY_BIN)),
    reason="no MicroPython interpreter found (set MPY_BIN, or put `micropython` on PATH)",
)


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


def _run(payload_lines, script=DEMO_SERVER, timeout=10):
    """Run `script` against a fixed scripted input, return (lines, stderr).

    Every line of stdout is round-tripped through `json.loads` here, so a
    framing corruption (a spliced or truncated line) fails the harness
    itself with a `JSONDecodeError` rather than a downstream assertion.
    """
    payload = b"".join(payload_lines)
    proc = subprocess.run(
        [MPY_BIN, script],
        input=payload,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return lines, proc.stderr


def _by_id(lines):
    return {l["id"]: l for l in lines if "id" in l}


INIT_REQ = _req(
    1,
    "initialize",
    {
        "protocolVersion": "2025-06-18",
        "capabilities": {"experimental": {"probe": True}},
        "clientInfo": {"name": "conformance-client", "version": "0.0.1"},
    },
)
INITIALIZED_NOTIF = _notif("notifications/initialized")


# ── initialize handshake ─────────────────────────────────────────────────


def test_initialize_echoes_known_protocol_version():
    lines, _ = _run([INIT_REQ])
    result = _by_id(lines)[1]["result"]
    assert result["protocolVersion"] == "2025-06-18"


def test_initialize_falls_back_to_latest_for_unknown_protocol_version():
    lines, _ = _run([_req(1, "initialize", {"protocolVersion": "1999-01-01"})])
    result = _by_id(lines)[1]["result"]
    assert result["protocolVersion"] == "2025-06-18"


def test_initialize_result_shape():
    lines, _ = _run([INIT_REQ])
    result = _by_id(lines)[1]["result"]
    assert result["serverInfo"] == {"name": "mpyfastmcp-demo", "version": "0.1.0"}
    assert result["capabilities"] == {"tools": {}, "prompts": {}, "resources": {}}
    assert "instructions" in result


def test_oninitialized_notification_fires_after_handshake():
    # demo_server's on_initialized hook fires a custom notification once
    # `notifications/initialized` is received -- must land as its own,
    # separately-parseable line (no id), never corrupting other framing.
    lines, _ = _run([INIT_REQ, INITIALIZED_NOTIF, _req(2, "tools/list")])
    notifications = [l for l in lines if "id" not in l]
    assert any(n["method"] == "notifications/demo/ready" for n in notifications)
    assert _by_id(lines)[2]["result"]["tools"]


# ── tools/list ────────────────────────────────────────────────────────────


def test_tools_list_golden_schema():
    lines, _ = _run([INIT_REQ, _req(2, "tools/list")])
    tools = _by_id(lines)[2]["result"]["tools"]
    by_name = {t["name"]: t for t in tools}
    assert set(by_name) == {"echo", "add"}
    assert by_name["echo"]["inputSchema"] == {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Text to echo back"},
            "shout": {
                "type": "number",
                "description": "Non-zero to uppercase the echoed text",
            },
        },
        "required": ["message"],
    }
    assert by_name["add"]["inputSchema"] == {
        "type": "object",
        "properties": {
            "a": {"type": "number", "description": "First addend"},
            "b": {"type": "number", "description": "Second addend"},
        },
        "required": ["a", "b"],
    }
    # Registration order preserved (see mpyfastmcp docstring on why plain
    # dicts can't be relied on for this on MicroPython).
    assert [t["name"] for t in tools] == ["echo", "add"]


# ── tools/call ────────────────────────────────────────────────────────────


def test_tools_call_success_returns_content_shape():
    lines, _ = _run(
        [
            INIT_REQ,
            _req(2, "tools/call", {"name": "add", "arguments": {"a": 2, "b": 3}}),
        ]
    )
    result = _by_id(lines)[2]["result"]
    assert "isError" not in result
    assert result["content"] == [{"type": "text", "text": json.dumps({"sum": 5})}]


def test_meta_param_is_tolerated_on_tools_call_and_prompts_get():
    """Regression: MCP clients attach a reserved `_meta` field to request
    params. Handlers are reached via `handler(**params)`, so a `_meta`
    sibling of `arguments` must not surface as -32602 "unexpected keyword
    argument" -- which is exactly what a fixed
    `(name, arguments)` signature would have done, and why the three
    request handlers extract fields off `**params` instead."""
    lines, _ = _run(
        [
            INIT_REQ,
            _req(
                2,
                "tools/call",
                {
                    "name": "add",
                    "arguments": {"a": 2, "b": 3},
                    "_meta": {"progressToken": "t"},
                },
            ),
            _req(
                3,
                "prompts/get",
                {
                    "name": "greet",
                    "arguments": {"name": "x"},
                    "_meta": {"progressToken": "t"},
                },
            ),
        ]
    )
    by_id = _by_id(lines)
    assert "error" not in by_id[2], "_meta must not fail tools/call"
    assert not by_id[2]["result"].get("isError")
    assert json.loads(by_id[2]["result"]["content"][0]["text"]) == {"sum": 5}
    assert "error" not in by_id[3], "_meta must not fail prompts/get"
    assert "messages" in by_id[3]["result"]


def test_client_capabilities_and_info_are_captured_from_initialize():
    """The converse of the "None before initialize" test: once the
    handshake has happened, what the client declared must be readable by an
    `on_initialized` callback, which is the only reason it is captured."""
    lines, _ = _run([INIT_REQ, INITIALIZED_NOTIF, _req(2, "tools/list")])
    ready = [l for l in lines if l.get("method") == "notifications/demo/ready"]
    assert ready, "demo_server reports what it saw via this notification"
    params = ready[0].get("params") or {}
    assert params.get("clientInfo") == {
        "name": "conformance-client",
        "version": "0.0.1",
    }
    assert params.get("capabilities") == {"experimental": {"probe": True}}


def test_tools_call_validation_error_is_isError_result_not_rpc_error():
    lines, _ = _run(
        [
            INIT_REQ,
            _req(2, "tools/call", {"name": "add", "arguments": {"a": "nope"}}),
        ]
    )
    response = _by_id(lines)[2]
    assert "error" not in response
    result = response["result"]
    assert result["isError"] is True
    assert "content" in result


def test_tools_call_unknown_tool_is_isError_result():
    lines, _ = _run([INIT_REQ, _req(2, "tools/call", {"name": "does_not_exist", "arguments": {}})])
    result = _by_id(lines)[2]["result"]
    assert result["isError"] is True
    assert "does_not_exist" in result["content"][0]["text"]


def test_tools_call_handler_exception_is_isError_result(tmp_path):
    script = tmp_path / "boom_server.py"
    script.write_text(
        "import sys\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "import asyncio\n"
        "from mpyfastmcp import MCPServer\n"
        "server = MCPServer('boom-demo', '0.0.1')\n"
        "\n"
        "@server.tool('boom', 'always raises', params=[])\n"
        "def boom():\n"
        "    raise ValueError('kaboom')\n"
        "\n"
        "server.run()\n" % IMPORT_ROOTS
    )
    lines, _ = _run(
        [INIT_REQ, _req(2, "tools/call", {"name": "boom", "arguments": {}})],
        script=str(script),
    )
    result = _by_id(lines)[2]["result"]
    assert result["isError"] is True
    assert "kaboom" in result["content"][0]["text"]


def test_tools_call_dict_with_content_str_key_is_json_encoded(tmp_path):
    # A handler's natural return value may itself be a dict that happens to
    # have a `content` key (e.g. a file-read result, a message frame) whose
    # value is a plain string, not a content-block array. `tool_result` must
    # not mistake that for an already-shaped MCP result -- it has to fall
    # through to JSON-encoding, and a registered result middleware appending
    # to `result["content"]` (a list) must keep working against it.
    script = tmp_path / "raw_frame_server.py"
    script.write_text(
        "import sys\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "import asyncio\n"
        "from mpyfastmcp import MCPServer\n"
        "server = MCPServer('raw-frame-demo', '0.0.1')\n"
        "\n"
        "@server.tool('raw_frame', 'returns a dict with a content str key', params=[])\n"
        "def raw_frame():\n"
        "    return {'content': 'raw text body', 'path': '/x'}\n"
        "\n"
        "@server.on_tool_result\n"
        "def _tag(_tool_name, result):\n"
        "    result['content'].append({'type': 'text', 'text': 'tagged'})\n"
        "    return result\n"
        "\n"
        "server.run()\n" % IMPORT_ROOTS
    )
    lines, _ = _run(
        [INIT_REQ, _req(2, "tools/call", {"name": "raw_frame", "arguments": {}})],
        script=str(script),
    )
    result = _by_id(lines)[2]["result"]
    assert "isError" not in result
    assert isinstance(result["content"], list)
    texts = [c["text"] for c in result["content"]]
    assert json.dumps({"content": "raw text body", "path": "/x"}) in texts
    assert "tagged" in texts


def test_result_middleware_appends_content_block():
    # demo_server's middleware appends a one-shot notice queued by its
    # on_initialized hook onto the *next* tool result's content array.
    lines, _ = _run(
        [
            INIT_REQ,
            INITIALIZED_NOTIF,
            _req(2, "tools/call", {"name": "echo", "arguments": {"message": "hi"}}),
        ]
    )
    result = _by_id(lines)[2]["result"]
    texts = [c["text"] for c in result["content"]]
    assert "hi" in texts
    assert "demo server says hello" in texts


# ── prompts/list, prompts/get ────────────────────────────────────────────


def test_prompts_list_golden_schema():
    lines, _ = _run([INIT_REQ, _req(2, "prompts/list")])
    prompts = _by_id(lines)[2]["result"]["prompts"]
    assert prompts == [
        {
            "name": "greet",
            "description": "Produce a greeting message for `name`.",
            "arguments": [{"name": "name", "description": "Who to greet", "required": True}],
        }
    ]


def test_prompts_get_success_returns_messages_shape():
    lines, _ = _run(
        [INIT_REQ, _req(2, "prompts/get", {"name": "greet", "arguments": {"name": "Andrew"}})]
    )
    result = _by_id(lines)[2]["result"]
    assert result["description"] == "Greet Andrew"
    assert result["messages"][0]["role"] == "user"
    assert "Andrew" in result["messages"][0]["content"]["text"]


def test_prompts_get_unknown_prompt_is_invalid_params_rpc_error():
    lines, _ = _run([INIT_REQ, _req(2, "prompts/get", {"name": "nope"})])
    error = _by_id(lines)[2]["error"]
    assert error["code"] == -32602


# ── resources/list, resources/read ───────────────────────────────────────


def test_resources_list_golden_schema():
    lines, _ = _run([INIT_REQ, _req(2, "resources/list")])
    resources = _by_id(lines)[2]["result"]["resources"]
    assert resources == [
        {
            "uri": "resource://demo/notes",
            "name": "Demo Notes",
            "description": "A short, static, read-only note resource.",
            "mimeType": "text/plain",
        }
    ]


def test_resources_read_success_returns_contents_shape():
    lines, _ = _run([INIT_REQ, _req(2, "resources/read", {"uri": "resource://demo/notes"})])
    result = _by_id(lines)[2]["result"]
    assert result["contents"] == [
        {
            "uri": "resource://demo/notes",
            "mimeType": "text/plain",
            "text": "This is a static demo resource exposed by mpyfastmcp.",
        }
    ]


def test_resources_read_unknown_uri_is_invalid_params_rpc_error():
    lines, _ = _run([INIT_REQ, _req(2, "resources/read", {"uri": "resource://demo/nope"})])
    error = _by_id(lines)[2]["error"]
    assert error["code"] == -32602


def test_resources_list_before_initialize_is_gated():
    lines, _ = _run([_req(1, "resources/list")])
    error = _by_id(lines)[1]["error"]
    assert error["code"] == -32600

    lines, _ = _run([_req(1, "resources/read", {"uri": "resource://demo/notes"})])
    assert _by_id(lines)[1]["error"]["code"] == -32600


def test_resources_capability_only_advertised_when_a_resource_exists(tmp_path):
    """`{"resources": {}}` must appear in `initialize`'s `capabilities` only
    once at least one `@server.resource` is registered -- a server with no
    resources must not advertise the capability, matching how `tools`/
    `prompts` are only advertised once at least one is registered."""
    script = tmp_path / "no_resources_server.py"
    script.write_text(
        "import sys\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "from mpyfastmcp import MCPServer\n"
        "server = MCPServer('no-resources-demo', '0.0.1')\n"
        "\n"
        "@server.tool('noop', 'does nothing', params=[])\n"
        "def noop():\n"
        "    return 'ok'\n"
        "\n"
        "server.run()\n" % IMPORT_ROOTS
    )
    lines, _ = _run([INIT_REQ], script=str(script))
    result = _by_id(lines)[1]["result"]
    assert result["capabilities"] == {"tools": {}}
    assert "resources" not in result["capabilities"]


def test_client_capabilities_and_info_are_none_before_initialize(tmp_path):
    """Q6 sentinel: `get_client_capabilities()` and `get_client_info()` must
    both return `None` before `initialize` has been handled -- a shared
    "not yet known" sentinel distinct from an empty `capabilities: {}` sent
    by the client."""
    script = tmp_path / "q6_sentinel_server.py"
    script.write_text(
        "import sys\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "from mpyfastmcp import MCPServer\n"
        "server = MCPServer('q6-demo', '0.0.1')\n"
        "server.peer.log('pre-init caps=%%r info=%%r' %% "
        "(server.get_client_capabilities(), server.get_client_info()))\n"
        "\n"
        "@server.on_initialized\n"
        "def _check():\n"
        "    server.peer.log('post-init caps=%%r info=%%r' %% "
        "(server.get_client_capabilities(), server.get_client_info()))\n"
        "\n"
        "server.run()\n" % IMPORT_ROOTS
    )
    _, stderr = _run([INIT_REQ, INITIALIZED_NOTIF, _req(2, "tools/list")], script=str(script))
    stderr_text = stderr.decode()
    assert "pre-init caps=None info=None" in stderr_text
    post_init_line = next(line for line in stderr_text.splitlines() if "post-init" in line)
    assert "caps={'experimental': {'probe': True}}" in post_init_line
    assert "info=None" not in post_init_line, post_init_line
    assert "conformance-client" in post_init_line


# ── Unknown JSON-RPC method ───────────────────────────────────────────────


def test_unknown_method_is_method_not_found():
    lines, _ = _run([INIT_REQ, _req(2, "totally/unknown")])
    error = _by_id(lines)[2]["error"]
    assert error["code"] == -32601


# ── Notification framing under concurrent load ───────────────────────────


def test_notify_mid_session_does_not_corrupt_framing_around_tool_calls():
    # notifications/demo/ready fires as soon as notifications/initialized
    # is handled, concurrently with the tools/call requests queued right
    # behind it in the same input burst (mpyjsonrpc dispatches each line
    # as its own task) -- every line must still round-trip through
    # json.loads (already enforced by `_run`) and every request must still
    # get its own correctly-correlated response.
    lines, _ = _run(
        [
            INIT_REQ,
            INITIALIZED_NOTIF,
            _req(2, "tools/call", {"name": "add", "arguments": {"a": 1, "b": 1}}),
            _req(3, "tools/call", {"name": "add", "arguments": {"a": 2, "b": 2}}),
            _req(4, "tools/call", {"name": "add", "arguments": {"a": 3, "b": 3}}),
        ]
    )
    by_id = _by_id(lines)
    assert json.loads(by_id[2]["result"]["content"][0]["text"]) == {"sum": 2}
    assert json.loads(by_id[3]["result"]["content"][0]["text"]) == {"sum": 4}
    assert json.loads(by_id[4]["result"]["content"][0]["text"]) == {"sum": 6}
    notifications = [l for l in lines if "id" not in l]
    assert any(n["method"] == "notifications/demo/ready" for n in notifications)


# ── PRE-FREEZE fixes: run()/serve(), on_initialized isolation, prompts/get
# error code, lifecycle gating, on_tool_result rename, log_prefix ─────────


def test_serve_is_awaitable_and_run_drives_it(tmp_path):
    """`serve()` is the coroutine form (must be awaited to do anything);
    `run()` is the blocking wrapper that drives it without a caller-side
    `asyncio.run()`. A bare `server.serve()` call with no `await` and no
    surrounding `asyncio.run()` produces an un-awaited coroutine that never
    runs -- the server never touches stdin and emits nothing."""
    unawaited_script = tmp_path / "unawaited_serve_server.py"
    unawaited_script.write_text(
        "import sys\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "from mpyfastmcp import MCPServer\n"
        "server = MCPServer('unawaited-demo', '0.0.1')\n"
        "server.serve()\n" % IMPORT_ROOTS  # not awaited, no asyncio.run() -- must be a no-op
    )
    lines, _ = _run([INIT_REQ], script=str(unawaited_script))
    assert lines == [], "bare server.serve() (unawaited) must not serve requests"

    awaited_script = tmp_path / "awaited_serve_server.py"
    awaited_script.write_text(
        "import sys\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "import asyncio\n"
        "from mpyfastmcp import MCPServer\n"
        "server = MCPServer('awaited-demo', '0.0.1')\n"
        "asyncio.run(server.serve())\n" % IMPORT_ROOTS  # awaited via asyncio.run -- must serve
    )
    lines, _ = _run([INIT_REQ], script=str(awaited_script))
    result = _by_id(lines)[1]["result"]
    assert result["serverInfo"]["name"] == "awaited-demo"

    run_script = tmp_path / "run_server.py"
    run_script.write_text(
        "import sys\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "from mpyfastmcp import MCPServer\n"
        "server = MCPServer('run-demo', '0.0.1')\n"
        "server.run()\n" % IMPORT_ROOTS  # blocking wrapper -- must serve without asyncio.run()
    )
    lines, _ = _run([INIT_REQ], script=str(run_script))
    result = _by_id(lines)[1]["result"]
    assert result["serverInfo"]["name"] == "run-demo"


def test_on_initialized_callbacks_are_exception_isolated(tmp_path):
    """A raising `on_initialized` callback must not stop later callbacks
    from running, matching `on_shutdown`'s isolation."""
    script = tmp_path / "oninit_isolation_server.py"
    script.write_text(
        "import sys\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "from mpyfastmcp import MCPServer\n"
        "server = MCPServer('oninit-isolation-demo', '0.0.1')\n"
        "\n"
        "@server.on_initialized\n"
        "def _first():\n"
        "    raise ValueError('first callback blew up')\n"
        "\n"
        "@server.on_initialized\n"
        "async def _second():\n"
        "    await server.notify('notifications/second/ran', {'ok': True})\n"
        "\n"
        "server.run()\n" % IMPORT_ROOTS
    )
    lines, _ = _run([INIT_REQ, INITIALIZED_NOTIF, _req(2, "tools/list")], script=str(script))
    notifications = [l for l in lines if "id" not in l]
    assert any(n["method"] == "notifications/second/ran" for n in notifications), (
        "second on_initialized callback must still run after the first raises"
    )


def test_prompts_get_bad_argument_is_invalid_params_rpc_error():
    """A `prompts/get` argument that fails `mpyschema.validate()` (here: the
    required `name` argument is missing) must surface as JSON-RPC
    `-32602` (`InvalidParams`), not `-32603` (`InternalError`)."""
    lines, _ = _run([INIT_REQ, _req(2, "prompts/get", {"name": "greet", "arguments": {}})])
    error = _by_id(lines)[2]["error"]
    assert error["code"] == -32602


def test_lifecycle_gating_rejects_pre_initialize_requests():
    """`tools/list`, `tools/call`, `prompts/list`, `prompts/get` must be
    rejected before `initialize` completes, and must work once it has."""
    lines, _ = _run([_req(1, "tools/list")])
    error = _by_id(lines)[1]["error"]
    assert error["code"] == -32600

    lines, _ = _run([_req(1, "tools/call", {"name": "add", "arguments": {"a": 1, "b": 1}})])
    assert _by_id(lines)[1]["error"]["code"] == -32600

    lines, _ = _run([_req(1, "prompts/list")])
    assert _by_id(lines)[1]["error"]["code"] == -32600

    lines, _ = _run([_req(1, "prompts/get", {"name": "greet", "arguments": {"name": "x"}})])
    assert _by_id(lines)[1]["error"]["code"] == -32600

    # Once initialize has completed, all four are served normally.
    lines, _ = _run([INIT_REQ, _req(2, "tools/list")])
    assert "error" not in _by_id(lines)[2]
    assert _by_id(lines)[2]["result"]["tools"]


def test_on_tool_result_fires_on_isError_results(tmp_path):
    """An `on_tool_result` callback runs for `isError` results too, not
    only successes -- which is why a callback that only cares about
    successful calls has to check `result.get("isError")` and skip."""
    script = tmp_path / "on_tool_result_server.py"
    script.write_text(
        "import sys\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "from mpyfastmcp import MCPServer\n"
        "server = MCPServer('on-tool-result-demo', '0.0.1')\n"
        "\n"
        "@server.tool('boom', 'always raises', params=[])\n"
        "def boom():\n"
        "    raise ValueError('kaboom')\n"
        "\n"
        "@server.on_tool_result\n"
        "def _tag(_tool_name, result):\n"
        "    result['content'].append({'type': 'text', 'text': 'tagged:%%s' %% result.get('isError', False)})\n"
        "    return result\n"
        "\n"
        "server.run()\n" % IMPORT_ROOTS
    )
    lines, _ = _run(
        [INIT_REQ, _req(2, "tools/call", {"name": "boom", "arguments": {}})],
        script=str(script),
    )
    result = _by_id(lines)[2]["result"]
    assert result["isError"] is True
    texts = [c["text"] for c in result["content"]]
    assert "tagged:True" in texts, "on_tool_result must fire on isError results too"


def _hook_server(tmp_path, name, body):
    """Write a one-tool server whose `on_tool_result` hook is `body`."""
    script = tmp_path / ("%s.py" % name)
    script.write_text(
        "import sys\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "import asyncio\n"
        "from mpyfastmcp import MCPServer\n"
        "server = MCPServer(%r, '0.0.1')\n"
        "\n"
        "@server.tool('echo', 'echoes', params=[])\n"
        "def echo():\n"
        "    return 'original'\n"
        "\n"
        "%s\n"
        "server.run()\n" % (IMPORT_ROOTS, name, body)
    )
    return str(script)


CALL_ECHO = _req(2, "tools/call", {"name": "echo", "arguments": {}})


def test_on_tool_result_callback_that_raises_is_isolated(tmp_path):
    """A raising `on_tool_result` callback must be logged and skipped, with
    the later callbacks still running and the result still delivered --
    the isolation `on_initialized` and `on_shutdown` already had. Before
    this, one bad callback turned every `tools/call` into a -32603."""
    script = _hook_server(
        tmp_path,
        "hook-raises",
        "@server.on_tool_result\n"
        "def _boom(_name, _result):\n"
        "    raise ValueError('hook exploded')\n"
        "\n"
        "@server.on_tool_result\n"
        "def _tag(_name, result):\n"
        "    result['content'].append({'type': 'text', 'text': 'second-ran'})\n"
        "    return result\n",
    )
    lines, stderr = _run([INIT_REQ, CALL_ECHO], script=script)
    response = _by_id(lines)[2]
    assert "error" not in response, "a raising hook must not fail the tool call"
    texts = [c["text"] for c in response["result"]["content"]]
    assert "original" in texts
    assert "second-ran" in texts, "later callbacks must still run"
    assert "hook exploded" in stderr.decode()


def test_on_tool_result_async_callback_is_awaited(tmp_path):
    """An `async def` callback must be awaited. Before this, the coroutine
    object itself became the result and went out on the wire as `null`."""
    script = _hook_server(
        tmp_path,
        "hook-async",
        "@server.on_tool_result\n"
        "async def _tag(_name, result):\n"
        "    await asyncio.sleep(0)\n"
        "    result['content'].append({'type': 'text', 'text': 'awaited'})\n"
        "    return result\n",
    )
    lines, _ = _run([INIT_REQ, CALL_ECHO], script=script)
    result = _by_id(lines)[2]["result"]
    assert result is not None, "an async hook must not collapse the result to null"
    texts = [c["text"] for c in result["content"]]
    assert texts == ["original", "awaited"]


def test_on_tool_result_none_return_keeps_the_result(tmp_path):
    """A callback that mutates in place and forgets `return result` must
    leave the result intact rather than sending `null`."""
    script = _hook_server(
        tmp_path,
        "hook-none",
        "@server.on_tool_result\n"
        "def _mutate(_name, result):\n"
        "    result['content'].append({'type': 'text', 'text': 'mutated'})\n",
    )
    lines, _ = _run([INIT_REQ, CALL_ECHO], script=script)
    result = _by_id(lines)[2]["result"]
    assert result is not None
    texts = [c["text"] for c in result["content"]]
    assert texts == ["original", "mutated"]


def test_on_tool_result_callbacks_run_in_registration_order(tmp_path):
    script = _hook_server(
        tmp_path,
        "hook-order",
        "@server.on_tool_result\n"
        "def _first(_name, result):\n"
        "    result['content'].append({'type': 'text', 'text': 'first'})\n"
        "    return result\n"
        "\n"
        "@server.on_tool_result\n"
        "def _second(_name, result):\n"
        "    result['content'].append({'type': 'text', 'text': 'second'})\n"
        "    return result\n",
    )
    lines, _ = _run([INIT_REQ, CALL_ECHO], script=script)
    texts = [c["text"] for c in _by_id(lines)[2]["result"]["content"]]
    assert texts == ["original", "first", "second"]


def _probe(tmp_path, name, body):
    """Run `body` on the MicroPython binary and return the JSON object it
    prints. Used for assertions about in-process API behaviour, which
    cannot be made from CPython: `mpyfastmcp` is MicroPython-only, and
    constructing an `MCPServer` reaches `asyncio.StreamReader(obj)`, whose
    signature CPython does not share."""
    script = tmp_path / ("%s.py" % name)
    script.write_text(
        "import sys, json\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "out = {}\n"
        "%s\n"
        "print(json.dumps(out))\n" % (IMPORT_ROOTS, body)
    )
    lines, stderr = _run([], script=str(script))
    assert lines, "probe produced no output; stderr: %s" % stderr.decode()
    return lines[0]


def test_duplicate_registration_raises(tmp_path):
    """A second registration under an existing tool name, prompt name, or
    resource URI must raise rather than silently replacing the first. For
    one in-repo consumer a duplicate name is a typo found immediately; for
    a consumer composing tool modules from several sources it is a tool
    that silently vanishes."""
    got = _probe(
        tmp_path,
        "dup-probe",
        "from mpyfastmcp import MCPServer\n"
        "server = MCPServer('dup-demo', '0.0.1')\n"
        "\n"
        "def raises(fn):\n"
        "    try:\n"
        "        fn()\n"
        "    except ValueError:\n"
        "        return True\n"
        "    return False\n"
        "\n"
        "server.add_tool('t', 'first', lambda: 'a')\n"
        "out['tool'] = raises(lambda: server.add_tool('t', 'second', lambda: 'b'))\n"
        "server.add_prompt('p', 'first', lambda: {})\n"
        "out['prompt'] = raises(lambda: server.add_prompt('p', 'second', lambda: {}))\n"
        "server.add_resource('res://x', 'first', lambda: 'a')\n"
        "out['resource'] = raises(lambda: server.add_resource('res://x', 'second', lambda: 'b'))\n"
        "out['decorator'] = raises(lambda: server.tool('t', 'third')(lambda: 'c'))\n"
        "out['first_survives'] = server._tools['t'].description\n",
    )
    assert got["tool"], "duplicate tool name must raise"
    assert got["prompt"], "duplicate prompt name must raise"
    assert got["resource"], "duplicate resource URI must raise"
    assert got["decorator"], "the decorator form shares the guard"
    assert got["first_survives"] == "first", "the first registration must win"


def test_imperative_and_decorator_registration_agree(tmp_path):
    """`add_tool` and `@tool` must produce the same `tools/list` entry, and
    registration order must follow call order across a mix of both."""
    script = tmp_path / "imperative_server.py"
    script.write_text(
        "import sys\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "from mpyfastmcp import MCPServer\n"
        "from mpyschema import Str\n"
        "server = MCPServer('imperative-demo', '0.0.1')\n"
        "server.add_tool('added', 'via add_tool', lambda who: 'hi ' + who,\n"
        "                params=[Str('who', desc='name', required=True)])\n"
        "\n"
        "@server.tool('decorated', 'via decorator', params=[Str('who', desc='name', required=True)])\n"
        "def _decorated(who):\n"
        "    return 'hi ' + who\n"
        "\n"
        "server.run()\n" % IMPORT_ROOTS
    )
    lines, _ = _run([INIT_REQ, _req(2, "tools/list")], script=str(script))
    tools = _by_id(lines)[2]["result"]["tools"]
    assert [t["name"] for t in tools] == ["added", "decorated"]
    assert tools[0]["inputSchema"] == tools[1]["inputSchema"]


def test_ping_is_answered_and_ungated():
    """MCP defines `ping` for both parties, and permits a client to send it
    before `initialize`. An unanswered ping is observable as a fault, not
    as an unsupported optional, so it must not be -32601 or -32600."""
    lines, _ = _run([_req(90, "ping"), INIT_REQ, _req(91, "ping")])
    by_id = _by_id(lines)
    assert "error" not in by_id[90], "ping must be answered before initialize"
    assert by_id[90]["result"] == {}
    assert by_id[91]["result"] == {}


def test_protocol_versions_override(tmp_path):
    """`protocol_versions=` must replace the module tuple for negotiation,
    so a consumer on a revision this package predates does not have to edit
    the installed package or mutate a module global."""
    script = tmp_path / "protocol_override_server.py"
    script.write_text(
        "import sys\n"
        "for _p in %r: sys.path.insert(0, _p)\n"
        "from mpyfastmcp import MCPServer\n"
        "server = MCPServer('proto-demo', '0.0.1',\n"
        "                   protocol_versions=('2099-01-01', '2025-06-18'))\n"
        "\n"
        "@server.tool('noop', 'does nothing', params=[])\n"
        "def noop():\n"
        "    return 'ok'\n"
        "\n"
        "server.run()\n" % IMPORT_ROOTS
    )
    # A version in the override is echoed back verbatim.
    lines, _ = _run(
        [
            _req(
                1,
                "initialize",
                {
                    "protocolVersion": "2099-01-01",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            )
        ],
        script=str(script),
    )
    assert _by_id(lines)[1]["result"]["protocolVersion"] == "2099-01-01"
    # An unknown one falls back to the override's newest, not the module's.
    lines, _ = _run(
        [
            _req(
                1,
                "initialize",
                {
                    "protocolVersion": "1999-01-01",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            )
        ],
        script=str(script),
    )
    assert _by_id(lines)[1]["result"]["protocolVersion"] == "2099-01-01"


def test_resources_read_unknown_uri_carries_the_uri_in_data():
    """The MCP spec's resource-not-found example carries the requested URI
    in `error.data`, which is the only thing distinguishing this -32602
    from an ordinary bad-argument one."""
    lines, _ = _run([INIT_REQ, _req(2, "resources/read", {"uri": "resource://demo/nope"})])
    error = _by_id(lines)[2]["error"]
    assert error["code"] == -32602
    assert error["message"] == "Resource not found"
    assert error["data"] == {"uri": "resource://demo/nope"}


def test_tool_result_indent_renders_indented_json(tmp_path):
    """`tool_result(data, indent=N)` must produce what `json.dumps(data,
    indent=N)` would on a runtime that supported the keyword -- which
    MicroPython's does not, which is why `json_pretty` exists at all. The
    assertion is made against CPython's own `json.dumps(indent=)` output,
    so the reference is the real thing rather than a restatement of the
    implementation."""
    # Key order is preserved through the probe by rendering a single-key
    # nesting: MicroPython dicts do not iterate in insertion order, so a
    # multi-key literal would not round-trip comparably.
    data = {"outer": {"inner": [1, 2, {"deep": "x"}]}}
    got = _probe(
        tmp_path,
        "indent-probe",
        "from mpyfastmcp import json_pretty, tool_result\n"
        "data = %r\n"
        "out['pretty2'] = json_pretty(data, 2)\n"
        "out['pretty4'] = json_pretty(data, 4)\n"
        "out['empty'] = json_pretty({'a': {}, 'b': []}, 2)\n"
        "out['compact_result'] = tool_result(data)['content'][0]['text']\n"
        "out['indented_result'] = tool_result(data, indent=2)['content'][0]['text']\n"
        "out['string_passthrough'] = tool_result('plain', indent=2)['content'][0]['text']\n"
        % (data,),
    )
    assert got["pretty2"] == json.dumps(data, indent=2)
    assert got["pretty4"] == json.dumps(data, indent=4)
    assert got["empty"] == json.dumps({"a": {}, "b": []}, indent=2)
    assert got["compact_result"] == json.dumps(data)
    assert got["indented_result"] == json.dumps(data, indent=2)
    assert got["string_passthrough"] == "plain"


def test_log_prefix_derives_from_server_name():
    """`peer.log()` output is tagged with the server's own `name`, not a
    hard-coded `[mpyfastmcp]` prefix shared by every server."""
    _, stderr = _run([INIT_REQ, INITIALIZED_NOTIF, _req(2, "tools/list")])
    stderr_text = stderr.decode()
    assert "[mpyfastmcp-demo] " in stderr_text
    assert "[mpyfastmcp] " not in stderr_text
