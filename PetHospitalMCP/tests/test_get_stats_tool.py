"""`get_stats`: input model, output model, JSON Schema, and tool body."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from pet_hospital_mcp.tools import GetStatsInput, GetStatsOutput
from pet_hospital_mcp.tools.get_stats import GET_STATS_TOOL_NAME
from tests.conftest import StubBackend, make_stats, ok_envelope, tool_by_name, tool_schema


# --------------------------------------------------------------------------- #
# Input model
# --------------------------------------------------------------------------- #


def test_no_arguments_is_valid() -> None:
    parsed = GetStatsInput.model_validate({})
    assert parsed.top is None


@pytest.mark.parametrize("top", [1, 5, 100])
def test_top_within_bounds_is_accepted(top: int) -> None:
    assert GetStatsInput.model_validate({"top": top}).top == top


@pytest.mark.parametrize("top", [0, -1, 101, 10_000, "5", 5.0])
def test_top_outside_bounds_is_rejected(top: Any) -> None:
    with pytest.raises(ValidationError):
        GetStatsInput.model_validate({"top": top})


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        GetStatsInput.model_validate({"top": 5, "pages": 2})


# --------------------------------------------------------------------------- #
# Output model
# --------------------------------------------------------------------------- #


def test_output_accepts_the_documented_stats_shape() -> None:
    output = GetStatsOutput.model_validate(make_stats())
    assert output.totalPets == 3
    assert output.totalRevenue == 4680.0
    assert output.bySpecies == {"犬": 2, "猫": 1}
    assert output.revenueByDoctor == {"李医生": 4680.0}
    assert output.topSpenders[0].id == "PET-000001"
    assert output.topSpenders[0].records[0].diagnosis == "急性肠胃炎"


def test_counts_keep_their_integer_type() -> None:
    data = GetStatsOutput.model_validate(make_stats()).model_dump(mode="json")
    assert data["bySpecies"]["犬"] == 2 and isinstance(data["bySpecies"]["犬"], int)


def test_optional_fields_may_be_absent() -> None:
    output = GetStatsOutput.model_validate(
        {"totalPets": 1, "totalRecords": 1, "totalCharges": 1, "totalRevenue": 0.0}
    )
    assert output.topSpenders is None


def test_totals_are_required() -> None:
    for missing in ("totalPets", "totalRecords", "totalCharges", "totalRevenue"):
        data = make_stats()
        del data[missing]
        with pytest.raises(ValidationError):
            GetStatsOutput.model_validate(data)


def test_unknown_backend_fields_are_ignored() -> None:
    data = make_stats()
    data["future"] = True
    assert GetStatsOutput.model_validate(data).totalPets == 3


# --------------------------------------------------------------------------- #
# Registration and JSON Schema
# --------------------------------------------------------------------------- #


async def test_tool_is_registered(mcp_server: MCPServer) -> None:
    assert GET_STATS_TOOL_NAME in [t.name for t in await mcp_server.list_tools()]


async def test_json_schema_declares_the_top_edge(mcp_server: MCPServer) -> None:
    schema = tool_schema(await tool_by_name(mcp_server, GET_STATS_TOOL_NAME))
    properties = schema["properties"]
    assert set(properties) == {"top"}
    assert properties["top"]["anyOf"][0]["minimum"] == 1
    assert properties["top"]["anyOf"][0]["maximum"] == 100


async def test_tool_description_covers_purpose_and_error_codes(mcp_server: MCPServer) -> None:
    description = (await tool_by_name(mcp_server, GET_STATS_TOOL_NAME)).description or ""
    assert "GET /api/v1/stats" in description
    assert "适用场景" in description and "返回值" in description
    assert "topSpenders" in description
    assert "BACKEND_INVALID_RESPONSE" in description


# --------------------------------------------------------------------------- #
# Tool body
# --------------------------------------------------------------------------- #


async def test_valid_call_returns_structured_content(mcp_server: MCPServer) -> None:
    result = await mcp_server.call_tool(GET_STATS_TOOL_NAME, {})

    assert result.is_error is False
    assert result.structured_content["totalPets"] == 3
    assert json.loads(result.content[0].text) == result.structured_content


async def test_top_parameter_is_forwarded_as_a_query(mcp_server: MCPServer, stub: StubBackend) -> None:
    await mcp_server.call_tool(GET_STATS_TOOL_NAME, {"top": 7})
    assert stub.last_request.method == "GET"
    assert stub.last_request.url.path == "/api/v1/stats"
    assert dict(httpx.URL(stub.last_request.url).params) == {"top": "7"}


async def test_no_top_means_no_query_param(mcp_server: MCPServer, stub: StubBackend) -> None:
    await mcp_server.call_tool(GET_STATS_TOOL_NAME, {})
    assert dict(httpx.URL(stub.last_request.url).params) == {}


async def test_unmatched_backend_shape_returns_an_envelope(
    mcp_server: MCPServer, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope({"totalPets": "nope"}))

    result = await mcp_server.call_tool(GET_STATS_TOOL_NAME, {})

    error = result.structured_content["error"]
    assert error["code"] == "BACKEND_INVALID_RESPONSE"
    assert "totalPets" in error["details"]["fields"]