"""Entry point: ``python -m pet_hospital_mcp`` / ``pet-hospital-mcp``.

Starts a stateless Streamable HTTP MCP server on ``MCP_HOST:MCP_PORT`` and
serves the MCP endpoint at ``MCP_PATH`` (default ``/mcp``) plus ``/health``.

The Go Pet Hospital REST API must already be running; see ``PET_HOSPITAL_BASE_URL``.
"""

from __future__ import annotations

import sys

import uvicorn

from pet_hospital_mcp import PROTOCOL_VERSION, __version__
from pet_hospital_mcp.config import ConfigError, Settings, load_settings
from pet_hospital_mcp.logging_config import configure_logging
from pet_hospital_mcp.server import SERVER_NAME, build_app


def _banner(settings: Settings) -> str:
    return "\n".join(
        [
            "",
            f"  {SERVER_NAME} v{__version__}  (stateless MCP, protocol {PROTOCOL_VERSION})",
            "",
            f"  MCP endpoint   http://{settings.mcp_host}:{settings.mcp_port}{settings.mcp_path}",
            f"  Health check   http://{settings.mcp_host}:{settings.mcp_port}/health",
            f"  Upstream API   {settings.base_url}",
            "",
            "  The MCP host must be running. Ctrl+C to stop.",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    """Run the server. Returns a process exit code."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings.log_level)

    app = build_app(settings)
    print(_banner(settings), flush=True)
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
