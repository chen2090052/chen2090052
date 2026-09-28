"""Runtime configuration, read from the environment.

| Variable                    | Default                  | Meaning                              |
| --------------------------- | ------------------------ | ------------------------------------ |
| ``PET_HOSPITAL_BASE_URL``   | ``http://127.0.0.1:8080``| Upstream Go REST API base URL         |
| ``MCP_HOST``                | ``127.0.0.1``            | Interface the MCP server binds to     |
| ``MCP_PORT``                | ``8765``                 | Port the MCP server binds to          |
| ``MCP_PATH``                | ``/mcp``                 | Path of the Streamable HTTP endpoint  |
| ``PET_HOSPITAL_TIMEOUT``    | ``10.0``                 | Per-attempt backend timeout, seconds  |
| ``PET_HOSPITAL_MAX_RETRIES``| ``2``                    | Extra attempts after the first        |
| ``PET_HOSPITAL_BACKOFF``    | ``0.25``                 | Base backoff between attempts, seconds|
| ``MCP_LOG_LEVEL``           | ``INFO``                 | Root log level                        |

The MCP server binds to loopback by default; it is a teaching service with no
authentication, so do not expose it to an untrusted network.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_MCP_HOST = "127.0.0.1"
#: Distinct from the Go service's 8080 so both can run side by side.
DEFAULT_MCP_PORT = 8765
DEFAULT_MCP_PATH = "/mcp"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 0.25
DEFAULT_LOG_LEVEL = "INFO"

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(Exception):
    """Raised when an environment variable is present but unusable."""


class Settings(BaseModel):
    """Validated, immutable configuration for one MCP server process."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = DEFAULT_BASE_URL
    mcp_host: str = DEFAULT_MCP_HOST
    mcp_port: int = Field(default=DEFAULT_MCP_PORT, ge=1, le=65535)
    mcp_path: str = DEFAULT_MCP_PATH
    request_timeout: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0, le=600)
    max_retries: int = Field(default=DEFAULT_MAX_RETRIES, ge=0, le=10)
    backoff_seconds: float = Field(default=DEFAULT_BACKOFF_SECONDS, ge=0, le=60)
    log_level: str = DEFAULT_LOG_LEVEL

    @property
    def endpoint_url(self) -> str:
        """Full URL an MCP client should POST to."""
        path = self.mcp_path if self.mcp_path.startswith("/") else f"/{self.mcp_path}"
        return f"http://{self.mcp_host}:{self.mcp_port}{path}"


def _read(env: Mapping[str, str], name: str, default: str) -> str:
    raw = env.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    # Treat an empty variable as "unset" so `MCP_PORT=` in a shell profile
    # does not blow up startup.
    return raw or default


def _read_number(env: Mapping[str, str], name: str, default: float, *, integral: bool) -> float | int:
    raw = _read(env, name, str(default))
    try:
        return int(raw) if integral else float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be {'an integer' if integral else 'a number'}, got {raw!r}") from None


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from ``environ`` (defaults to ``os.environ``).

    Raises:
        ConfigError: if a variable is set to a value that is not usable. The
            message names the offending variable; Pydantic's own error text is
            never surfaced.
    """
    env = os.environ if environ is None else environ

    base_url = _read(env, "PET_HOSPITAL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ConfigError(f"PET_HOSPITAL_BASE_URL must start with http:// or https://, got {base_url!r}")

    log_level = _read(env, "MCP_LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigError(f"MCP_LOG_LEVEL must be one of {', '.join(_LOG_LEVELS)}, got {log_level!r}")

    mcp_path = _read(env, "MCP_PATH", DEFAULT_MCP_PATH)
    if not mcp_path.startswith("/"):
        mcp_path = f"/{mcp_path}"

    payload: dict[str, Any] = {
        "base_url": base_url,
        "mcp_host": _read(env, "MCP_HOST", DEFAULT_MCP_HOST),
        "mcp_port": _read_number(env, "MCP_PORT", DEFAULT_MCP_PORT, integral=True),
        "mcp_path": mcp_path,
        "request_timeout": _read_number(env, "PET_HOSPITAL_TIMEOUT", DEFAULT_TIMEOUT_SECONDS, integral=False),
        "max_retries": _read_number(env, "PET_HOSPITAL_MAX_RETRIES", DEFAULT_MAX_RETRIES, integral=True),
        "backoff_seconds": _read_number(env, "PET_HOSPITAL_BACKOFF", DEFAULT_BACKOFF_SECONDS, integral=False),
        "log_level": log_level,
    }

    try:
        return Settings.model_validate(payload)
    except ValidationError as exc:
        # Re-raise as a readable, field-named error rather than a Pydantic dump.
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"invalid configuration: {problems}") from None
