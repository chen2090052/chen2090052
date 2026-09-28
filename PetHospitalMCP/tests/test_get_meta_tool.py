"""`get_meta`: output model, JSON Schema, and tool body (no arguments)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from pet_hospital_mcp.tools import GetMetaOutput
from pet_hospital_mcp.tools.get_meta import GET_META_TOOL_NAME
from tests.conftest import StubBackend, make_meta, ok_envelope, tool_by_name, tool_schema


# --------------------------------------------------------------------------- #
# Output model
# --------------------------------------------------------------------------- #


def test_output_accepts_the_documented_meta_shape() -> None:
    output = GetMetaOutput.model_validate(make_meta())
    assert output.species == ["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
    assert output.chargeCategories == ["检查", "药品", "手术", "住院", "疫苗", "护理", "其他"]
    assert output.fields[0].name == "q"
    assert output.fields[0].desc == "全文检索"


def test_enum_arrays_are_required() -> None:
    for missing in ("species", "status", "gender", "chargeCategories", "sortFields", "fields"):
        data = make_meta()
        del data[missing]
        with pytest.raises(ValidationError):
            GetMetaOutput.model_validate(data)


def test_unknown_backend_fields_are_ignored() -> None:
    data = make_meta()
    data["extra"] = 1
    assert GetMetaOutput.model_validate(data).fields[0].name == "q"


# --------------------------------------------------------------------------- #
# Registration and JSON Schema
# --------------------------------------------------------------------------- #


async def test_tool_is_registered(mcp_server: MCPServer) -> None:
    assert GET_META_TOOL_NAME in [t.name for t in await mcp_server.list_tools()]


async def test_json_schema_takes_no_parameters(mcp_server: MCPServer) -> None:
    schema = tool_schema(await tool_by_name(mcp_server, GET_META_TOOL_NAME))
    assert "properties" not in schema or not schema["properties"]


async def test_tool_description_covers_purpose_and_error_codes(mcp_server: MCPServer) -> None:
    description = (await tool_by_name(mcp_server, GET_META_TOOL_NAME)).description or ""
    assert "GET /api/v1/meta" in description
    assert "适用场景" in description and "返回值" in description
    assert "chargeCategories" in description
    assert "BACKEND_INVALID_RESPONSE" in description


# --------------------------------------------------------------------------- #
# Tool body
# --------------------------------------------------------------------------- #


async def test_valid_call_returns_structured_content(mcp_server: MCPServer) -> None:
    result = await mcp_server.call_tool(GET_META_TOOL_NAME, {})

    assert result.is_error is False
    assert result.structured_content["species"][0] == "犬"
    assert json.loads(result.content[0].text) == result.structured_content


async def test_hits_the_meta_path(mcp_server: MCPServer, stub: StubBackend) -> None:
    await mcp_server.call_tool(GET_META_TOOL_NAME, {})
    assert stub.last_request.url.path == "/api/v1/meta"


def test_any_parameter_is_rejected() -> None:
    from pet_hospital_mcp.tools.get_meta import GetMetaInput

    with pytest.raises(ValidationError):
        GetMetaInput.model_validate({"species": "犬"})


async def test_unmatched_backend_shape_returns_an_envelope(
    mcp_server: MCPServer, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope({"species": 42}))

    result = await mcp_server.call_tool(GET_META_TOOL_NAME, {})

    error = result.structured_content["error"]
    assert error["code"] == "BACKEND_INVALID_RESPONSE"
    assert "species" in error["details"]["fields"]


async def test_backend_failure_returns_an_envelope(
    mcp_server: MCPServer, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, text="<html>")
    result = await mcp_server.call_tool(GET_META_TOOL_NAME, {})
    assert result.structured_content["error"]["code"] == "BACKEND_INVALID_RESPONSE"