"""The MCP surface: /health, the 2026-07-28 stateless flow, and the SDK 2.x client.

Every test here drives the real ASGI application over real HTTP (uvicorn on an
ephemeral loopback port) against a stubbed Go backend.
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any

import httpx
import httpx2
import pytest
import pytest_asyncio
import uvicorn

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.server import build_app
from tests.conftest import (
    FAKE_BASE_URL,
    StubBackend,
    error_envelope,
    mcp_body,
    mcp_headers,
    ok_envelope,
)

MCP_PATH = "/mcp"


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


class RecordingASGI:
    """ASGI wrapper that records the JSON-RPC methods clients send."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.methods: list[str] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []

        async def recording_receive() -> Any:
            message = await receive()
            if message["type"] == "http.request":
                chunks.append(message.get("body", b""))
            return message

        await self.app(scope, recording_receive, send)

        body = b"".join(chunks)
        if not body:
            return
        try:
            payload = json.loads(body)
        except ValueError:
            return
        for item in payload if isinstance(payload, list) else [payload]:
            if isinstance(item, dict) and item.get("method"):
                self.methods.append(item["method"])


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _serve(app: Any) -> Any:
    config = uvicorn.Config(app, host="127.0.0.1", port=_free_port(), log_level="error", access_log=False)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(500):
        if server.started:
            return server, task
        await asyncio.sleep(0.01)
    raise RuntimeError("uvicorn did not start in time")  # pragma: no cover


@pytest_asyncio.fixture
async def recording_server(settings: Settings, stub: StubBackend) -> Any:
    """Serve the app behind :class:`RecordingASGI`; yields ``(base_url, recorder)``."""
    recorder = RecordingASGI(build_app(settings, transport=stub.transport()))
    server, task = await _serve(recorder)
    try:
        yield f"http://127.0.0.1:{server.config.port}", recorder
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


@pytest_asyncio.fixture
async def http(live_server: str) -> Any:
    async with httpx.AsyncClient(base_url=live_server, timeout=10) as client:
        yield client


async def _rpc(
    http: httpx.AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    name: str | None = None,
    request_id: int = 1,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return await http.post(
        MCP_PATH,
        json=mcp_body(method, params, request_id=request_id),
        headers=mcp_headers(method, name=name) if headers is None else headers,
    )


def _result(response: httpx.Response) -> dict[str, Any]:
    return json.loads(response.text)["result"]


def _error_payload(response: httpx.Response) -> dict[str, Any]:
    """The unified error envelope carried in a tools/call result."""
    result = _result(response)
    assert result["isError"] is True
    return result["structuredContent"]["error"]


def _call_args(response: httpx.Response) -> dict[str, Any]:
    result = _result(response)
    return result.get("structuredContent") or {}


# --------------------------------------------------------------------------- #
# /health
# --------------------------------------------------------------------------- #


async def test_health_returns_200(http: httpx.AsyncClient, live_server: str) -> None:
    response = await http.get("/health")
    assert response.status_code == 200


async def test_health_reports_service_metadata(http: httpx.AsyncClient) -> None:
    payload = (await http.get("/health")).json()

    assert payload["status"] == "ok"
    assert payload["service"] == "pet-hospital-mcp"
    assert payload["version"]
    assert payload["mcpEndpoint"].endswith(MCP_PATH)


async def test_health_declares_the_protocol_and_transport(http: httpx.AsyncClient) -> None:
    """The health document is where a reviewer confirms the protocol generation."""
    payload = (await http.get("/health")).json()

    assert payload["protocolVersion"] == "2026-07-28"
    assert payload["transport"] == "streamable-http"
    assert payload["stateless"] is True


async def test_health_reports_upstream_reachability(http: httpx.AsyncClient) -> None:
    upstream = (await http.get("/health")).json()["upstream"]

    assert upstream["url"] == FAKE_BASE_URL
    assert upstream["reachable"] is True  # the stub backend answers /health
    assert upstream["status"] == "healthy"


async def test_health_stays_200_when_the_backend_is_down(
    http: httpx.AsyncClient, stub: StubBackend
) -> None:
    """Liveness must not depend on the Go service being up."""

    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    stub.responder = responder
    response = await http.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["upstream"]["reachable"] is False


async def test_health_reports_the_upstream_error_code(
    http: httpx.AsyncClient, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(500, text="boom")
    upstream = (await http.get("/health")).json()["upstream"]

    assert upstream["reachable"] is False
    assert upstream["error_code"] == "BACKEND_API_ERROR"


async def test_health_does_not_leak_environment_details(http: httpx.AsyncClient) -> None:
    body = (await http.get("/health")).text.lower()
    for forbidden in ("python", "site-packages", "traceback", "uvicorn"):
        assert forbidden not in body


# --------------------------------------------------------------------------- #
# Discovery: server/discover, no initialize, no sessions
# --------------------------------------------------------------------------- #


async def test_discover_succeeds_without_any_handshake(http: httpx.AsyncClient) -> None:
    response = await _rpc(http, "server/discover")

    assert response.status_code == 200
    assert _result(response)["supportedVersions"] == ["2026-07-28"]


async def test_discover_advertises_capabilities_and_instructions(http: httpx.AsyncClient) -> None:
    result = _result(await _rpc(http, "server/discover"))

    assert result["capabilities"]["tools"]["listChanged"] is True
    assert result["instructions"]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "pet-hospital-mcp"


async def test_discover_returns_no_session_id_header(http: httpx.AsyncClient) -> None:
    response = await _rpc(http, "server/discover")

    for header in response.headers:
        assert header.lower() != "mcp-session-id", "the server must not open a session"


async def test_no_response_ever_carries_a_session_id(
    http: httpx.AsyncClient, stub: StubBackend
) -> None:
    responses = [
        await _rpc(http, "server/discover"),
        await _rpc(http, "tools/list"),
        await _rpc(http, "tools/call", {"name": "list_pets", "arguments": {}}, name="list_pets"),
    ]

    for response in responses:
        assert "mcp-session-id" not in {header.lower() for header in response.headers}


async def test_legacy_initialize_is_not_supported(http: httpx.AsyncClient) -> None:
    """The 2026-07-28 revision removed the handshake entirely (SEP-2575)."""
    response = await _rpc(
        http, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "old", "version": "1"}}
    )

    assert response.status_code == 404
    assert json.loads(response.text)["error"]["code"] == -32601  # Method not found


async def test_legacy_initialized_notification_is_not_supported(http: httpx.AsyncClient) -> None:
    response = await _rpc(http, "notifications/initialized")

    assert response.status_code == 404
    assert json.loads(response.text)["error"]["code"] == -32601


async def test_tools_list_needs_no_prior_handshake(http: httpx.AsyncClient) -> None:
    """Every request is self-contained: this is the first request this client makes."""
    response = await _rpc(http, "tools/list")

    assert response.status_code == 200
    assert _result(response)["tools"]


async def test_tools_call_needs_no_prior_handshake(http: httpx.AsyncClient) -> None:
    response = await _rpc(http, "tools/call", {"name": "list_pets", "arguments": {}}, name="list_pets")

    assert response.status_code == 200
    assert _result(response)["isError"] is False


async def test_concurrent_requests_do_not_share_state(http: httpx.AsyncClient) -> None:
    """There is no session to contend over, so parallel calls are independent."""
    responses = await asyncio.gather(
        *(
            _rpc(
                http,
                "tools/call",
                {"name": "list_pets", "arguments": {"page": page}},
                name="list_pets",
                request_id=page,
            )
            for page in range(1, 6)
        )
    )
    assert all(response.status_code == 200 for response in responses)
    assert all(_result(response)["isError"] is False for response in responses)


# --------------------------------------------------------------------------- #
# Routing headers (SEP-2243)
# --------------------------------------------------------------------------- #


def _header_mismatch(response: httpx.Response) -> bool:
    """SEP-2243: a routing-header mismatch is reported as ``HeaderMismatch``."""
    payload = json.loads(response.text)
    return response.status_code == 400 and payload.get("error", {}).get("code") == -32020


async def test_missing_method_header_is_rejected(http: httpx.AsyncClient) -> None:
    headers = mcp_headers("tools/list")
    del headers["Mcp-Method"]

    response = await http.post(MCP_PATH, json=mcp_body("tools/list"), headers=headers)
    assert _header_mismatch(response)


async def test_mismatched_method_header_is_rejected(http: httpx.AsyncClient) -> None:
    headers = mcp_headers("tools/list")
    headers["Mcp-Method"] = "tools/call"

    response = await _rpc(http, "tools/list", headers=headers)
    assert _header_mismatch(response)


async def test_missing_name_header_on_tools_call_is_rejected(http: httpx.AsyncClient) -> None:
    headers = mcp_headers("tools/call")  # deliberately no Mcp-Name

    response = await _rpc(http, "tools/call", {"name": "list_pets", "arguments": {}}, headers=headers)
    assert _header_mismatch(response)


async def test_a_full_modern_header_set_selects_the_modern_envelope(
    http: httpx.AsyncClient,
) -> None:
    """The 2026-07-28 path is what a compliant client gets.

    Its results carry the modern result envelope (``resultType``), which the
    SDK omits when the protocol-version header is absent.
    """
    result = _result(await _rpc(http, "tools/list"))

    assert result["resultType"] == "complete"


async def test_unknown_method_is_rejected(http: httpx.AsyncClient) -> None:
    response = await _rpc(http, "tools/teleport")

    assert response.status_code == 404
    payload = json.loads(response.text)
    assert payload["error"]["code"] == -32601
    assert payload["error"]["data"] == "tools/teleport"


# --------------------------------------------------------------------------- #
# tools/list over HTTP
# --------------------------------------------------------------------------- #


async def test_tools_list_exposes_all_ten_tools(http: httpx.AsyncClient) -> None:
    tools = _result(await _rpc(http, "tools/list"))["tools"]
    names = [tool["name"] for tool in tools]

    assert len(names) == 10
    assert len(set(names)) == 10, "tool names are unique"
    assert set(names) == {
        "add_pet_charge",
        "add_pet_record",
        "get_endpoints",
        "get_meta",
        "get_pet",
        "get_pet_summary",
        "get_stats",
        "list_pet_charges",
        "list_pet_records",
        "list_pets",
    }


def _list_pets_tool(tools: list[dict[str, Any]]) -> dict[str, Any]:
    for tool in tools:
        if tool["name"] == "list_pets":
            return tool
    raise AssertionError("list_pets is not advertised")


async def test_tools_list_schema_forbids_unknown_fields(http: httpx.AsyncClient) -> None:
    """Hardening middleware must make the advertised schema match what we enforce."""
    schema = _list_pets_tool(_result(await _rpc(http, "tools/list"))["tools"])["inputSchema"]

    assert schema["additionalProperties"] is False


async def test_tools_list_schema_is_complete(http: httpx.AsyncClient) -> None:
    schema = _list_pets_tool(_result(await _rpc(http, "tools/list"))["tools"])["inputSchema"]
    properties = schema["properties"]

    assert len(properties) == 14
    assert properties["species"]["anyOf"][0]["enum"] == ["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
    assert properties["pageSize"]["maximum"] == 500


async def test_tools_list_is_stable_across_calls(http: httpx.AsyncClient) -> None:
    first = _result(await _rpc(http, "tools/list", request_id=1))
    second = _result(await _rpc(http, "tools/list", request_id=2))
    assert first == second


async def test_tools_list_carries_no_output_schema(http: httpx.AsyncClient) -> None:
    """Failure envelopes are returned through the same channel, so no success
    schema is advertised."""
    tool = _list_pets_tool(_result(await _rpc(http, "tools/list"))["tools"])
    assert not tool.get("outputSchema")


# --------------------------------------------------------------------------- #
# tools/call over HTTP
# --------------------------------------------------------------------------- #


async def test_call_returns_structured_content(http: httpx.AsyncClient) -> None:
    response = await _rpc(
        http, "tools/call", {"name": "list_pets", "arguments": {"species": "犬"}}, name="list_pets"
    )
    result = _result(response)

    assert result["isError"] is False
    assert set(result["structuredContent"]) == {
        "items",
        "total",
        "page",
        "pageSize",
        "totalPages",
        "totalCost",
    }


async def test_call_result_also_carries_text(http: httpx.AsyncClient) -> None:
    result = _result(
        await _rpc(http, "tools/call", {"name": "list_pets", "arguments": {}}, name="list_pets")
    )

    assert result["content"][0]["type"] == "text"
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


async def test_call_forwards_every_filter(http: httpx.AsyncClient, stub: StubBackend) -> None:
    arguments = {
        "q": "肠胃炎",
        "name": "旺财",
        "ownerName": "张三",
        "ownerPhone": "13800001111",
        "species": "犬",
        "doctor": "李医生",
        "disease": "急性肠胃炎",
        "status": "待就诊",
        "min": 100,
        "max": 5000,
        "sortBy": "totalCost",
        "order": "desc",
        "page": 2,
        "pageSize": 10,
    }
    await _rpc(http, "tools/call", {"name": "list_pets", "arguments": arguments}, name="list_pets")

    sent = dict(httpx.URL(stub.last_request.url).params)
    assert stub.last_request.url.path == "/api/v1/pets"
    assert sent["species"] == "犬"
    assert sent["sortBy"] == "totalCost"
    assert sent["order"] == "desc"
    assert sent["page"] == "2"
    assert sent["pageSize"] == "10"
    assert len(sent) == 14


async def test_call_redacts_sensitive_arguments_in_logs(
    http: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tools"):
        await _rpc(
            http,
            "tools/call",
            {"name": "list_pets", "arguments": {"ownerPhone": "13800001111"}},
            name="list_pets",
        )

    assert "13800001111" not in caplog.text


async def test_sensitive_query_parameters_never_reach_any_handler(
    http: httpx.AsyncClient, stub: StubBackend
) -> None:
    """The transport logger must not echo the outbound query string.

    Our own `tool_call` line redacts `ownerPhone`, but HTTPX logs the request URL
    at INFO independently — that was a real leak until the transport loggers were
    raised to WARNING.
    """
    import logging

    from pet_hospital_mcp.logging_config import configure_logging

    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    seen: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    try:
        configure_logging("INFO")
        capture = Capture()
        transport_logger = logging.getLogger("httpx")
        transport_logger.addHandler(capture)
        try:
            await _rpc(
                http,
                "tools/call",
                {"name": "list_pets", "arguments": {"ownerPhone": "13800001111"}},
                name="list_pets",
            )
        finally:
            transport_logger.removeHandler(capture)
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)

    # The call really happened, and carried the phone number upstream.
    assert dict(httpx.URL(stub.last_request.url).params)["ownerPhone"] == "13800001111"
    assert seen == []
    assert "13800001111" not in "".join(seen)


# --------------------------------------------------------------------------- #
# Error envelopes over the wire
# --------------------------------------------------------------------------- #


async def test_invalid_species_returns_a_validation_error(http: httpx.AsyncClient) -> None:
    response = await _rpc(
        http, "tools/call", {"name": "list_pets", "arguments": {"species": "恐龙"}}, name="list_pets"
    )
    error = _error_payload(response)

    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fields"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"page": 0},
        {"page": "2"},
        {"pageSize": 501},
        {"status": "已出院"},
        {"sortBy": "ownerAddr"},
        {"order": "up"},
        {"min": -1},
        {"min": 10, "max": 1},
        {"ownerAddr": "北京市朝阳区"},
        {"limit": 5},
    ],
)
async def test_every_invalid_argument_shape_returns_validation_error(
    http: httpx.AsyncClient, arguments: dict[str, Any]
) -> None:
    """Invalid input is a tool result, never a JSON-RPC protocol error."""
    response = await _rpc(
        http, "tools/call", {"name": "list_pets", "arguments": arguments}, name="list_pets"
    )

    assert response.status_code == 200
    assert _error_payload(response)["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("arguments", [{"page": "abc"}, {"bogus": 1}, {"min": -5}])
async def test_validation_errors_never_leak_framework_wording(
    http: httpx.AsyncClient, arguments: dict[str, Any]
) -> None:
    response = await _rpc(
        http, "tools/call", {"name": "list_pets", "arguments": arguments}, name="list_pets"
    )
    rendered = json.dumps(_result(response), ensure_ascii=False).lower()

    for forbidden in ("pydantic", "validationerror", "type=", "traceback", "int_parsing", "value_error"):
        assert forbidden not in rendered, f"{forbidden!r} leaked to the client"


async def test_unknown_tool_returns_an_error_result(http: httpx.AsyncClient) -> None:
    response = await _rpc(
        http, "tools/call", {"name": "delete_all_pets", "arguments": {}}, name="delete_all_pets"
    )
    result = _result(response)

    assert result["isError"] is True


async def test_backend_timeout_returns_backend_timeout(
    http: httpx.AsyncClient, stub: StubBackend
) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    stub.responder = responder
    error = _error_payload(
        await _rpc(http, "tools/call", {"name": "list_pets", "arguments": {}}, name="list_pets")
    )

    assert error["code"] == "BACKEND_TIMEOUT"


async def test_backend_connection_failure_returns_backend_unavailable(
    http: httpx.AsyncClient, stub: StubBackend
) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    stub.responder = responder
    error = _error_payload(
        await _rpc(http, "tools/call", {"name": "list_pets", "arguments": {}}, name="list_pets")
    )

    assert error["code"] == "BACKEND_UNAVAILABLE"


async def test_backend_http_error_returns_backend_api_error(
    http: httpx.AsyncClient, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(500, json=error_envelope(500, "boom"))
    error = _error_payload(
        await _rpc(http, "tools/call", {"name": "list_pets", "arguments": {}}, name="list_pets")
    )

    assert error["code"] == "BACKEND_API_ERROR"
    assert error["details"]["status_code"] == 500


async def test_backend_business_error_returns_backend_api_error(
    http: httpx.AsyncClient, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(
        200, json={"code": 400, "message": "非法参数", "time": "t"}
    )
    error = _error_payload(
        await _rpc(http, "tools/call", {"name": "list_pets", "arguments": {}}, name="list_pets")
    )

    assert error["code"] == "BACKEND_API_ERROR"
    assert error["details"]["upstream_message"] == "非法参数"


async def test_invalid_json_returns_backend_invalid_response(
    http: httpx.AsyncClient, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, text="<html>oops</html>")
    error = _error_payload(
        await _rpc(http, "tools/call", {"name": "list_pets", "arguments": {}}, name="list_pets")
    )

    assert error["code"] == "BACKEND_INVALID_RESPONSE"


async def test_unexpected_payload_shape_returns_backend_invalid_response(
    http: httpx.AsyncClient, stub: StubBackend
) -> None:
    """Valid JSON, wrong shape — e.g. `items` is not an array."""
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope({"items": "nope"}))
    error = _error_payload(
        await _rpc(http, "tools/call", {"name": "list_pets", "arguments": {}}, name="list_pets")
    )

    assert error["code"] == "BACKEND_INVALID_RESPONSE"
    assert "items" in error["details"]["fields"]
    assert "pydantic" not in json.dumps(error).lower()


@pytest.mark.parametrize(
    "responder",
    [
        lambda _request: httpx.Response(200, text="<html>nginx</html>"),
        lambda _request: httpx.Response(200, json={"code": 200, "message": "ok"}),
        lambda _request: httpx.Response(503, json=error_envelope(503, "down")),
    ],
)
async def test_no_internal_detail_escapes_on_backend_failure(
    http: httpx.AsyncClient, stub: StubBackend, responder: Any
) -> None:
    stub.responder = responder
    response = await _rpc(
        http, "tools/call", {"name": "list_pets", "arguments": {}}, name="list_pets"
    )
    rendered = json.dumps(_result(response), ensure_ascii=False).lower()

    for forbidden in ("traceback", "site-packages", "httpx.", "pydantic", ".py", "nginx", "<html>"):
        assert forbidden not in rendered, f"{forbidden!r} leaked to the client"


async def test_error_envelope_shape_is_uniform(http: httpx.AsyncClient, stub: StubBackend) -> None:
    """Both a validation failure and a backend failure produce the same shape."""
    stub.responder = lambda _request: httpx.Response(200, text="not json")
    backend_error = _error_payload(
        await _rpc(http, "tools/call", {"name": "list_pets", "arguments": {}}, name="list_pets")
    )
    validation_error = _error_payload(
        await _rpc(
            http, "tools/call", {"name": "list_pets", "arguments": {"page": 0}}, name="list_pets"
        )
    )

    for error in (backend_error, validation_error):
        assert set(error) == {"code", "message", "details"}
        assert isinstance(error["message"], str) and error["message"]
        assert isinstance(error["details"], dict)


# --------------------------------------------------------------------------- #
# End-to-end with the official SDK 2.x client
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def sdk_client(recording_server: Any) -> Any:
    """A factory for SDK clients.

    The transport is entered and left inside the test body: the SDK's
    Streamable HTTP client opens an anyio cancel scope, which cannot be entered
    in a fixture's task and exited in the test's.
    """
    from contextlib import asynccontextmanager

    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client

    base_url, recorder = recording_server

    @asynccontextmanager
    async def connect() -> Any:
        async with httpx2.AsyncClient(timeout=10) as http_client:
            transport = streamable_http_client(f"{base_url}{MCP_PATH}", http_client=http_client)
            async with Client(transport) as client:
                client.recorder = recorder  # type: ignore[attr-defined]
                yield client

    return connect


async def test_sdk_client_discovers_and_calls_the_tool(sdk_client: Any, stub: StubBackend) -> None:
    async with sdk_client() as client:
        tools = (await client.list_tools()).tools
        assert "list_pets" in [tool.name for tool in tools]

        result = await client.call_tool("list_pets", {"species": "犬", "page": 1, "pageSize": 5})

    assert result.is_error is False
    assert result.structured_content["items"]
    assert dict(httpx.URL(stub.last_request.url).params)["species"] == "犬"


async def test_sdk_client_never_sends_the_legacy_handshake(sdk_client: Any) -> None:
    async with sdk_client() as client:
        await client.list_tools()

    methods = client.recorder.methods
    assert "initialize" not in methods
    assert "notifications/initialized" not in methods
    assert "server/discover" in methods


async def test_sdk_client_error_round_trip(sdk_client: Any) -> None:
    async with sdk_client() as client:
        result = await client.call_tool("list_pets", {"page": 0})

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "VALIDATION_ERROR"


async def test_sdk_client_reports_backend_failures_structurally(
    sdk_client: Any, stub: StubBackend
) -> None:
    """A downstream outage must not degrade into an opaque text block."""

    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    stub.responder = responder
    async with sdk_client() as client:
        result = await client.call_tool("list_pets", {})

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "BACKEND_UNAVAILABLE"


async def test_sdk_client_can_be_constructed_from_a_bare_url(recording_server: Any) -> None:
    """`Client(str)` is the SDK's one-liner form of the Streamable HTTP transport."""
    from mcp.client.client import Client

    base_url, recorder = recording_server
    async with Client(f"{base_url}{MCP_PATH}") as client:
        tools = (await client.list_tools()).tools

    assert "list_pets" in [tool.name for tool in tools]
    assert "initialize" not in recorder.methods
