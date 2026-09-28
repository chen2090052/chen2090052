"""Server assembly: ``MCPServer`` construction, tool registration, HTTP wiring.

The server is **stateless**. It uses the SDK 2.x ``MCPServer`` class and serves
the Streamable HTTP transport with ``stateless_http=True``, which means:

* no ``initialize`` / ``notifications/initialized`` handshake on the modern path,
* no ``Mcp-Session-Id`` header, no session store, no session expiry,
* no ``max_sessions`` accounting and no SSE resumability / event store.

Capability discovery happens through ``server/discover``, handled by the SDK.

A single piece of middleware, :class:`ToolCallGuard`, sits in front of every
``tools/call``. It enforces the strict input models, guarantees the unified
error envelope, and emits the structured tool-call log line.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from pet_hospital_mcp import PROTOCOL_VERSION, __version__
from pet_hospital_mcp.config import Settings, load_settings
from pet_hospital_mcp.errors import (
    InputValidationError,
    InternalError,
    PetHospitalToolError,
    is_error_envelope,
    normalize_exception,
)
from pet_hospital_mcp.logging_config import log_tool_call
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.tools import TOOL_INPUT_MODELS, register_tools

logger = logging.getLogger(__name__)

SERVER_NAME = "pet-hospital-mcp"
TOOLS_CALL_METHOD = "tools/call"

#: Pydantic error codes mapped onto neutral, adapter-owned vocabulary so no
#: Pydantic wording or identifier reaches an MCP client.
_REASON_BY_PYDANTIC_TYPE: Mapping[str, str] = {
    "extra_forbidden": "unknown_field",
    "missing": "missing_field",
    "literal_error": "not_an_allowed_value",
    "finite_number": "not_a_finite_number",
    "greater_than_equal": "out_of_range",
    "less_than_equal": "out_of_range",
    "int_type": "wrong_type",
    "int_parsing": "wrong_type",
    "float_type": "wrong_type",
    "float_parsing": "wrong_type",
    "string_type": "wrong_type",
    "bool_type": "wrong_type",
    "list_type": "wrong_type",
    "value_error": "invalid_value",
}
_DEFAULT_REASON = "invalid_value"


def _describe_validation_error(exc: ValidationError) -> list[dict[str, str]]:
    """Field-level detail with neutral reasons and no Pydantic prose."""
    described: list[dict[str, str]] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ())) or "<body>"
        described.append(
            {
                "field": location,
                "reason": _REASON_BY_PYDANTIC_TYPE.get(str(error.get("type")), _DEFAULT_REASON),
            }
        )
    return described


class ToolCallGuard:
    """Middleware enforcing the input contract, error envelope and logging.

    Runs outermost of the application middleware. For every ``tools/call`` it:

    1. validates the raw arguments against the tool's strict input model and
       short-circuits with ``VALIDATION_ERROR`` if they are unusable — the
       backend is never contacted;
    2. converts any exception escaping the handler into the unified envelope;
    3. replaces framework-generated failures (which carry SDK/Pydantic prose)
       with the unified envelope;
    4. emits exactly one JSON log line with ``tool_name``, redacted ``params``,
       ``status`` and ``duration_ms``.

    Requests for other methods pass through untouched.
    """

    def __init__(self, input_models: Mapping[str, type[BaseModel]], known_tools: frozenset[str]) -> None:
        self._input_models = dict(input_models)
        self._known_tools = known_tools

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        if ctx.method != TOOLS_CALL_METHOD:
            return await call_next(ctx)

        started = time.perf_counter()
        params = ctx.params if isinstance(ctx.params, dict) else {}
        raw_name = params.get("name")
        tool_name = raw_name if isinstance(raw_name, str) else "<unnamed>"
        raw_args = params.get("arguments") or {}

        if not isinstance(raw_args, dict):
            return self._fail(
                tool_name,
                {"arguments": raw_args},
                started,
                InputValidationError(details={"reason": "arguments must be a JSON object"}),
            )

        failure = self._precheck(tool_name, raw_args)
        if failure is not None:
            return self._fail(tool_name, raw_args, started, failure)

        try:
            result = await call_next(ctx)
        except Exception as exc:  # noqa: BLE001 - nothing may escape un-normalised
            return self._fail(tool_name, raw_args, started, normalize_exception(exc))

        return self._settle(tool_name, raw_args, started, result)

    # ------------------------------------------------------------------ #

    def _precheck(self, tool_name: str, raw_args: dict[str, Any]) -> PetHospitalToolError | None:
        """Strict validation before the handler runs. ``None`` means "proceed"."""
        if tool_name not in self._known_tools:
            return InputValidationError(details={"reason": "unknown_tool", "tool_name": tool_name})

        model = self._input_models.get(tool_name)
        if model is None:
            return None

        try:
            model.model_validate(raw_args)
        except ValidationError as exc:
            return InputValidationError(details={"fields": _describe_validation_error(exc)})
        return None

    def _settle(self, tool_name: str, raw_args: dict[str, Any], started: float, result: Any) -> Any:
        """Inspect a handler result, normalising framework-level failures.

        The SDK's ``HandlerResult`` is ``BaseModel | dict | None`` and the
        built-in handlers return a raw result mapping, so the failure marker is
        read off the mapping rather than off a ``CallToolResult`` instance.
        """
        if not isinstance(result, dict) or not result.get("isError"):
            return self._log(tool_name, raw_args, started, None, result)

        envelope = result.get("structuredContent")
        if is_error_envelope(envelope):
            # One of our own structured failures, which the tool returned
            # rather than raised: pass it through and log its code.
            return self._log(tool_name, raw_args, started, _error_code(envelope), result)

        # A framework-generated failure — the SDK's own argument model refused
        # the call, so the text block carries SDK/Pydantic prose. Replace it.
        return self._fail(
            tool_name,
            raw_args,
            started,
            InputValidationError(
                details={} if tool_name in self._known_tools else {"reason": "unknown_tool", "tool_name": tool_name}
            ),
        )

    def _fail(
        self,
        tool_name: str,
        raw_args: dict[str, Any],
        started: float,
        error: PetHospitalToolError,
    ) -> CallToolResult:
        return self._log(tool_name, raw_args, started, error.code.value, error.to_call_tool_result())

    def _log(
        self,
        tool_name: str,
        raw_args: dict[str, Any],
        started: float,
        error_code: str | None,
        result: Any,
    ) -> Any:
        log_tool_call(
            tool_name=tool_name,
            params=raw_args,
            status="error" if error_code else "ok",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=error_code,
        )
        return result


class ToolSchemaHardening:
    """Advertise ``additionalProperties: false`` on our tools' input schemas.

    The SDK derives the input schema from the function signature, which describes
    each parameter but says nothing about *unlisted* ones. We do reject unknown
    fields (see :class:`ToolCallGuard`), so the advertised contract is tightened
    to match what is actually enforced — agents otherwise have no way to know
    that a typo'd parameter name will be refused rather than ignored.

    Only ``tools/list`` results are touched; every other method passes through.
    """

    def __init__(self, tool_names: frozenset[str]) -> None:
        self._tool_names = tool_names

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        result = await call_next(ctx)

        if ctx.method != "tools/list" or not isinstance(result, dict):
            return result

        tools = result.get("tools")
        if not isinstance(tools, list):
            return result

        for tool in tools:
            if not isinstance(tool, dict) or tool.get("name") not in self._tool_names:
                continue
            schema = tool.get("inputSchema")
            if not isinstance(schema, dict):
                continue
            # Copy before mutating: the SDK may hand back a cached result.
            hardened = dict(schema)
            hardened.setdefault("additionalProperties", False)
            tool["inputSchema"] = hardened
        return result


def _error_code(envelope: Any) -> str:
    if isinstance(envelope, dict):
        error = envelope.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return InternalError.code.value


def build_server(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[MCPServer, PetHospitalRestClient]:
    """Construct the ``MCPServer`` and its REST client.

    Args:
        settings: Configuration; loaded from the environment when omitted.
        transport: Optional HTTPX transport, used by tests to stub the Go API.
            When ``None``, real network transport is used.

    Returns:
        The server and the client it owns. The caller is responsible for the
        client's lifetime — :func:`build_app` wires that into the ASGI lifespan.
    """
    resolved = settings or load_settings()
    client = PetHospitalRestClient(resolved, transport=transport)

    tool_names = frozenset(TOOL_INPUT_MODELS)
    guard = ToolCallGuard(TOOL_INPUT_MODELS, tool_names)
    schema_hardening = ToolSchemaHardening(tool_names)

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
        logger.info(
            "pet-hospital-mcp starting",
            extra={
                "extra_fields": {
                    "upstream": resolved.base_url,
                    "protocol_version": PROTOCOL_VERSION,
                }
            },
        )
        try:
            yield None
        finally:
            await client.aclose()

    mcp: MCPServer = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=(
            "宠爱AI病历管理系统：订阅本地宠物医院 REST API，提供档案检索、"
            "单档案与诊疗摘要、病历与消费明细查询和写入，以及统计、元信息"
            "与接口清单。工具名采用 snake_case，参数与后端 REST 参数逐字"
            "对应；写操作不做自动重试，任何失败都返回统一错误信封"
            "{\"error\": {\"code\", \"message\", \"details\"}}。"
            "可用工具：get_pet / get_pet_summary / list_pet_records / "
            "list_pet_charges / add_pet_record / add_pet_charge / list_pets / "
            "get_stats / get_meta / get_endpoints。"
        ),
        lifespan=lifespan,
        middleware=[guard, schema_hardening],
    )

    register_tools(mcp, client)
    _register_health_route(mcp, resolved, client)

    return mcp, client


def _register_health_route(mcp: MCPServer, settings: Settings, client: PetHospitalRestClient) -> None:
    """Expose ``GET /health`` next to the MCP endpoint.

    Liveness semantics: the endpoint answers ``200`` whenever this process is
    serving. Upstream reachability is reported in the body rather than mapped
    onto the status code, so a briefly unavailable Go service does not make the
    MCP server itself look dead. The upstream probe is best-effort and never
    raises.
    """

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> Response:
        upstream: dict[str, Any] = {"url": settings.base_url, "reachable": False}
        try:
            data = await client.health()
            upstream["reachable"] = True
            upstream["status"] = data.get("status")
        except PetHospitalToolError as exc:
            upstream["error_code"] = exc.code.value
        except Exception:  # noqa: BLE001 - health must never propagate
            upstream["error_code"] = InternalError.code.value

        return JSONResponse(
            {
                "status": "ok",
                "service": SERVER_NAME,
                "version": __version__,
                "protocolVersion": PROTOCOL_VERSION,
                "transport": "streamable-http",
                "stateless": True,
                "mcpEndpoint": settings.endpoint_url,
                "upstream": upstream,
            }
        )


def build_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Starlette:
    """Build the ASGI application served by uvicorn.

    Returns a Starlette app mounting the MCP endpoint (default ``/mcp``) plus
    ``/health``. ``stateless_http=True`` selects the stateless Streamable HTTP
    model; ``json_response=True`` answers with plain JSON instead of an SSE
    stream, which suits a single-exchange server.
    """
    resolved = settings or load_settings()
    mcp, _client = build_server(resolved, transport=transport)

    return mcp.streamable_http_app(
        streamable_http_path=resolved.mcp_path,
        json_response=True,
        stateless_http=True,
        host=resolved.mcp_host,
    )
