"""`list_pet_charges`: input model, output model, JSON Schema, and tool body."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from pet_hospital_mcp.tools import ListPetChargesInput, ListPetChargesOutput
from pet_hospital_mcp.tools.list_pet_charges import LIST_PET_CHARGES_TOOL_NAME
from tests.conftest import make_charges_data, tool_by_name, tool_schema

PETH = "PET-000001"
VALID_ARGS = {"id": PETH}


# --------------------------------------------------------------------------- #
# Input model
# --------------------------------------------------------------------------- #


def test_accepts_a_pet_id() -> None:
    assert ListPetChargesInput.model_validate(VALID_ARGS).id == PETH


@pytest.mark.parametrize("bad", [{}, {"id": ""}, {"id": None}, {"id": ["x"]}])
def test_invalid_id_is_rejected(bad: Any) -> None:
    with pytest.raises(ValidationError):
        ListPetChargesInput.model_validate(bad)


@pytest.mark.parametrize("extra", ["page", "from", "unknown"])
def test_unknown_fields_are_rejected(extra: str) -> None:
    with pytest.raises(ValidationError):
        ListPetChargesInput.model_validate({**VALID_ARGS, extra: 1})


# --------------------------------------------------------------------------- #
# Output model
# --------------------------------------------------------------------------- #


def test_output_accepts_the_documented_data_shape() -> None:
    data = make_charges_data()
    output = ListPetChargesOutput.model_validate(data)
    assert output.petId == PETH
    assert output.count == 1
    assert output.totalCost == 180.0
    assert output.costByCategory == {"检查": 180.0}
    assert output.charges[0].item == "血常规检查"
    assert output.charges[0].category == "检查"
    assert output.charges[0].amount == 180.0


def test_charges_accept_null_and_empty_array() -> None:
    for value in (None, []):
        data = make_charges_data()
        data["charges"] = value
        data["count"] = 0
        output = ListPetChargesOutput.model_validate(data)
        assert output.charges == value
        assert output.count == 0


def test_unknown_backend_fields_are_ignored() -> None:
    data = make_charges_data()
    data["extra"] = 1
    assert ListPetChargesOutput.model_validate(data).petId == PETH


def test_missing_pet_id_is_rejected() -> None:
    data = make_charges_data()
    del data["petId"]
    with pytest.raises(ValidationError):
        ListPetChargesOutput.model_validate(data)


# --------------------------------------------------------------------------- #
# Registration and JSON Schema
# --------------------------------------------------------------------------- #


async def test_tool_is_registered(mcp_server: MCPServer) -> None:
    assert LIST_PET_CHARGES_TOOL_NAME in [t.name for t in await mcp_server.list_tools()]


async def test_json_schema_declares_only_the_id(mcp_server: MCPServer) -> None:
    schema = tool_schema(await tool_by_name(mcp_server, LIST_PET_CHARGES_TOOL_NAME))
    assert set(schema["properties"]) == {"id"}


async def test_tool_description_covers_purpose_and_error_codes(mcp_server: MCPServer) -> None:
    description = (await tool_by_name(mcp_server, LIST_PET_CHARGES_TOOL_NAME)).description or ""
    assert "GET /api/v1/pets/{id}/charges" in description
    assert "适用场景" in description and "返回值" in description
    assert "BACKEND_INVALID_RESPONSE" in description


# --------------------------------------------------------------------------- #
# Tool body
# --------------------------------------------------------------------------- #


async def test_valid_call_returns_structured_content(mcp_server: MCPServer) -> None:
    result = await mcp_server.call_tool(LIST_PET_CHARGES_TOOL_NAME, VALID_ARGS)

    assert result.is_error is False
    assert result.structured_content["petId"] == PETH
    assert result.structured_content["charges"][0]["item"] == "血常规检查"
    assert json.loads(result.content[0].text) == result.structured_content


async def test_hits_the_charges_path(mcp_server: MCPServer, stub) -> None:
    from tests.conftest import StubBackend

    assert isinstance(stub, StubBackend)
    await mcp_server.call_tool(LIST_PET_CHARGES_TOOL_NAME, VALID_ARGS)
    assert stub.last_request.method == "GET"
    assert stub.last_request.url.path == "/api/v1/pets/PET-000001/charges"