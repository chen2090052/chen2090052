"""`get_pet_summary`: input model, output model, JSON Schema, and tool body."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from pet_hospital_mcp.tools import GetPetSummaryInput, PetSummary
from pet_hospital_mcp.tools.get_pet_summary import GET_PET_SUMMARY_TOOL_NAME
from tests.conftest import StubBackend, make_summary, ok_envelope, tool_by_name, tool_schema

PETH = "PET-000001"
VALID_ARGS = {"id": PETH}


# --------------------------------------------------------------------------- #
# Input model
# --------------------------------------------------------------------------- #


def test_accepts_a_pet_id() -> None:
    assert GetPetSummaryInput.model_validate(VALID_ARGS).id == PETH


@pytest.mark.parametrize("bad", [{}, {"id": ""}, {"id": None}, {"id": 12}])
def test_invalid_id_is_rejected(bad: Any) -> None:
    with pytest.raises(ValidationError):
        GetPetSummaryInput.model_validate(bad)


@pytest.mark.parametrize("extra", ["top", "summary", "unknown"])
def test_unknown_fields_are_rejected(extra: str) -> None:
    with pytest.raises(ValidationError):
        GetPetSummaryInput.model_validate({**VALID_ARGS, extra: 1})


# --------------------------------------------------------------------------- #
# Output model
# --------------------------------------------------------------------------- #


def test_output_accepts_the_documented_summary_shape() -> None:
    output = PetSummary.model_validate(make_summary())
    assert output.id == PETH
    assert output.visitCount == 3
    assert output.totalCost == 1560.0
    assert output.avgCostPerVisit == 520.0
    assert output.costByCategory == {"检查": 560.0, "药品": 1000.0}
    assert output.historyText


def test_optional_fields_may_be_absent() -> None:
    output = PetSummary.model_validate({"id": PETH})
    assert output.historyText is None


def test_summary_without_an_id_is_rejected() -> None:
    data = make_summary()
    del data["id"]
    with pytest.raises(ValidationError):
        PetSummary.model_validate(data)


def test_unknown_backend_fields_are_ignored() -> None:
    data = make_summary()
    data["newField"] = True
    assert PetSummary.model_validate(data).id == PETH


# --------------------------------------------------------------------------- #
# Registration and JSON Schema
# --------------------------------------------------------------------------- #


async def test_tool_is_registered(mcp_server: MCPServer) -> None:
    assert GET_PET_SUMMARY_TOOL_NAME in [t.name for t in await mcp_server.list_tools()]


async def test_json_schema_declares_only_the_id(mcp_server: MCPServer) -> None:
    schema = tool_schema(await tool_by_name(mcp_server, GET_PET_SUMMARY_TOOL_NAME))
    assert set(schema["properties"]) == {"id"}


async def test_tool_description_covers_purpose_and_error_codes(mcp_server: MCPServer) -> None:
    description = (await tool_by_name(mcp_server, GET_PET_SUMMARY_TOOL_NAME)).description or ""
    assert "GET /api/v1/pets/{id}/summary" in description
    assert "适用场景" in description and "返回值" in description
    assert "id" in description
    assert "BACKEND_INVALID_RESPONSE" in description


# --------------------------------------------------------------------------- #
# Tool body
# --------------------------------------------------------------------------- #


async def test_valid_call_returns_structured_content(mcp_server: MCPServer) -> None:
    result = await mcp_server.call_tool(GET_PET_SUMMARY_TOOL_NAME, VALID_ARGS)

    assert result.is_error is False
    assert result.structured_content["id"] == PETH
    assert set(result.structured_content) == set(make_summary())
    assert json.loads(result.content[0].text) == result.structured_content


async def test_hits_the_summary_path(mcp_server: MCPServer, stub: StubBackend) -> None:
    await mcp_server.call_tool(GET_PET_SUMMARY_TOOL_NAME, VALID_ARGS)
    assert stub.last_request.url.path == "/api/v1/pets/PET-000001/summary"


async def test_unmatched_backend_shape_returns_an_envelope(
    mcp_server: MCPServer, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope({"no": "id"}))

    result = await mcp_server.call_tool(GET_PET_SUMMARY_TOOL_NAME, VALID_ARGS)

    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["code"] == "BACKEND_INVALID_RESPONSE"
    assert "id" in error["details"]["fields"]