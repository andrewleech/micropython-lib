metadata(
    version="0.1.0",
    description="Async RFC6455 WebSocket client for MicroPython asyncio.",
)

package(
    "mpyws",
    files=["__init__.py", "_client.py", "_errors.py", "_frame.py", "_url.py"],
)
