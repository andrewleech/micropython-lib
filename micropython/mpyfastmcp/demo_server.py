"""Minimal example `mpyfastmcp` server: two tools, a prompt, and a resource.

Runnable directly on any MicroPython build with `json` and `asyncio`:

    micropython demo_server.py

Speaks MCP over stdio (real `sys.stdin`/`sys.stdout`) — feed it JSON-RPC
lines (`initialize`, `notifications/initialized`, `tools/list`,
`tools/call`, `prompts/list`, `prompts/get`, `resources/list`,
`resources/read`) and read the responses back. See `test_mpyfastmcp.py`
in this directory for a scripted client driving this exact server.

Demonstrates: two tools (`echo`, `add`) exercising `mpyschema.Str`/`Num`
params and validation-error/handler-exception paths; one prompt (`greet`);
one resource (`notes`); `on_initialized` reading the client's declared
capabilities; an `on_tool_result` hook that appends a one-shot notice to
the next tool result; and a `notify()` custom notification fired once
initialization completes.
"""

# ruff: noqa: E402

import os
import sys


def _up(path):
    """Directory containing `path`.

    `os.path` is a micropython-lib package rather than a built-in, so
    this example does without it and runs on a bare interpreter.
    """
    if "/" in path:
        return path.rsplit("/", 1)[0]
    return ".." if path == "." else "."


# Run from a plain checkout, with this file either inside the package
# directory or beside it. An installed or frozen build imports all three
# already and falls through.
_HERE = _up(__file__)
try:
    os.stat(_HERE + "/mpyfastmcp")
except OSError:
    _ROOTS = [_up(_HERE)]
else:
    _ROOTS = [_HERE] + [_up(_HERE) + "/" + dep for dep in ("mpyjsonrpc", "mpyschema")]
for _root in _ROOTS:
    if _root not in sys.path:
        sys.path.insert(0, _root)

from mpyfastmcp import MCPServer
from mpyschema import Num, Str

server = MCPServer(
    name="mpyfastmcp-demo",
    version="0.1.0",
    instructions="A minimal demo server: `echo` and `add` tools, a `greet` prompt.",
)


@server.tool(
    "echo",
    "Echo `message` back, optionally uppercased.",
    params=[
        Str("message", desc="Text to echo back", required=True),
        Num("shout", desc="Non-zero to uppercase the echoed text"),
    ],
)
def echo(message, shout=0):
    return message.upper() if shout else message


@server.tool(
    "add",
    "Add two numbers.",
    params=[
        Num("a", desc="First addend", required=True),
        Num("b", desc="Second addend", required=True),
    ],
)
def add(a, b):
    return {"sum": a + b}


@server.prompt(
    "greet",
    "Produce a greeting message for `name`.",
    arguments=[Str("name", desc="Who to greet", required=True)],
)
def greet(name):
    return {
        "description": "Greet %s" % name,
        "messages": [
            {
                "role": "user",
                "content": {"type": "text", "text": "Say hello to %s." % name},
            }
        ],
    }


@server.resource(
    "resource://demo/notes",
    "Demo Notes",
    description="A short, static, read-only note resource.",
    mime_type="text/plain",
)
def notes():
    return "This is a static demo resource exposed by mpyfastmcp."


# One-shot "pending notice" queue, drained into the next tool result by the
# on_tool_result hook below -- the generic hook the P7 app's nudge-drain
# behaviour is built on.
_pending_notices = []


@server.on_tool_result
def _drain_pending_notices(_tool_name, result):
    for text in _pending_notices:
        result["content"].append({"type": "text", "text": text})
    _pending_notices.clear()
    return result


@server.on_initialized
async def _on_initialized():
    # `get_client_capabilities()` / `get_client_info()` are only readable
    # once the handshake has completed, which is what this hook is for.
    # Echoing them back demonstrates that, and lets a client see what the
    # server understood it to have declared.
    capabilities = server.get_client_capabilities()
    client_info = server.get_client_info()
    server.peer.log("client capabilities: %r" % (capabilities,))
    _pending_notices.append("demo server says hello")
    await server.notify(
        "notifications/demo/ready",
        {"ok": True, "capabilities": capabilities, "clientInfo": client_info},
    )


if __name__ == "__main__":
    server.run()
