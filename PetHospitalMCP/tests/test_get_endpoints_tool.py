"""`get_endpoints`: output model, JSON Schema, and tool body (no arguments)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from pet_hospital_mcp.tools import GetEndpointsOutput
from pet_hospital_mcp.tools.get_endpoints import GET_ENDPOINTS_TOOL_NAME
from tests.conftest import StubBackend, make_endpoints, ok_envelope, tool_by_name, tool_schema


# --------------------------------------------------------------------------- #
# Output model
# --------------------------------------------------------------------------- #


def test_output_accepts_the_documented_endpoints_shape() -> None:
    output = GetEndpointsOutput.model_validate(make_endpoints())
    assert output.count == 2
    assert output.endpoints[1].Method == "GET"
    assert output.endpoints[1].Path == "/api/v1/meta"
    assert output.endpoints[0].Example


def test_count_and_endpoints_are_required() -> None:
    for missing in ("count", "endpoints"):
        data = make_endpoints()
        del data[missing]
        with pytest.raises(ValidationError):
            GetEndpointsOutput.model_validate(data)


def test_endpoint_fields_are_optional() -> None:
    output = GetEndpointsOutput.model_validate({"count": 0, "endpoints": [{"Method": "GET"}]})
    assert output.endpoints[0].Path is None


def test_unknown_backend_fields_are_ignored() -> None:
    data = make_endpoints()
    data["extra"] = True
    assert GetEndpointsOutput.model_validate(data).count == 2


# --------------------------------------------------------------------------- #
# Registration and JSON Schema
# --------------------------------------------------------------------------- #


async def test_tool_is_registered(mcp_server: MCPServer) -> None:
    assert GET_ENDPOINTS_TOOL_NAME in [t.name for t in await mcp_server.list_tools()]


async def test_json_schema_takes_no_parameters(mcp_server: MCPServer) -> None:
    schema = tool_schema(await tool_by_name(mcp_server, GET_ENDPOINTS_TOOL_NAME))
    assert "properties" not in schema or not schema["properties"]


async def test_tool_description_covers_purpose_and_error_codes(mcp_server: MCPServer) -> None:
    description = (await tool_by_name(mcp_server, GET_ENDPOINTS_TOOL_NAME)).description or ""
    assert "GET /api/v1/endpoints" in description
    assert "适用场景" in description and "返回值" in description
    assert "Method" in description
    assert "BACKEND_INVALID_RESPONSE" in description


# --------------------------------------------------------------------------- #
# Tool body
# --------------------------------------------------------------------------- #


async def test_valid_call_returns_structured_content(mcp_server: MCPServer) -> None:
    result = await mcp_server.call_tool(GET_ENDPOINTS_TOOL_NAME, {})

    assert result.is_error is False
    assert result.structured_content["count"] == 2
    assert result.structured_content["endpoints"][0]["Path"] == "/api/v1/pets"
    assert json.loads(result.content[0].text) == result.structured_content


async def test_hits_the_endpoints_path(mcp_server: MCPServer, stub: StubBackend) -> None:
    await mcp_server.call_tool(GET_ENDPOINTS_TOOL_NAME, {})
    assert stub.last_request.method == "GET"
    assert stub.last_request.url.path == "/api/v1/endpoints"


async def test_unmatched_backend_shape_returns_an_envelope(
    mcp_server: MCPServer, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope({"count": "many"}))

    result = await mcp_server.call_tool(GET_ENDPOINTS_TOOL_NAME, {})

    error = result.structured_content["error"]
    assert error["code"] == "BACKEND_INVALID_RESPONSE"
    assert "count" in error["details"]["fields"]