metadata(
    version="0.1.0",
    description="FastMCP-style MCP server for MicroPython.",
)

require("mpyjsonrpc")
require("mpyschema")

package("mpyfastmcp", files=["__init__.py"])
