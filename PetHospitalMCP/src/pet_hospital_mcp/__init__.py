"""MCP server exposing the Go Pet Hospital REST API to AI agents.

This package targets the official MCP Python SDK v2 (``mcp==2.0.0``) and the
stateless ``2026-07-28`` protocol revision. It deliberately does **not** use
``mcp.server.fastmcp.FastMCP`` and does not implement the legacy handshake,
``Mcp-Session-Id`` sessions, or SSE resumability.
"""

__version__ = "0.1.0"

#: Protocol revision this server primarily speaks. The SDK still serves the
#: earlier, handshake-based revisions from the same ``MCPServer`` instance.
PROTOCOL_VERSION = "2026-07-28"

__all__ = ["__version__", "PROTOCOL_VERSION"]
