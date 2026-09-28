"""`list_pet_records`: input model, output model, JSON Schema, and tool body."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from pet_hospital_mcp.tools import ListPetRecordsInput, ListPetRecordsOutput
from pet_hospital_mcp.tools.list_pet_records import LIST_PET_RECORDS_TOOL_NAME
from tests.conftest import StubBackend, make_records_data, ok_envelope, tool_by_name, tool_schema

PETH = "PET-000001"
VALID_ARGS = {"id": PETH}


# --------------------------------------------------------------------------- #
# Input model
# --------------------------------------------------------------------------- #


def test_accepts_a_pet_id() -> None:
    assert ListPetRecordsInput.model_validate(VALID_ARGS).id == PETH


@pytest.mark.parametrize("bad", [{}, {"id": ""}, {"id": None}, {"id": True}])
def test_invalid_id_is_rejected(bad: Any) -> None:
    with pytest.raises(ValidationError):
        ListPetRecordsInput.model_validate(bad)


@pytest.mark.parametrize("extra", ["page", "pageSize", "unknown"])
def test_unknown_fields_are_rejected(extra: str) -> None:
    with pytest.raises(ValidationError):
        ListPetRecordsInput.model_validate({**VALID_ARGS, extra: 1})


# --------------------------------------------------------------------------- #
# Output model
# --------------------------------------------------------------------------- #


def test_output_accepts_the_documented_data_shape() -> None:
    data = make_records_data()
    output = ListPetRecordsOutput.model_validate(data)
    assert output.petId == PETH
    assert output.count == 1
    assert output.records[0].diagnosis == "急性肠胃炎"
    assert output.records[0].prescription == ["阿莫西林", "益生菌"]
    assert output.records[0].weightKg == 24.5
    assert output.historyText


def test_records_accept_null_and_empty_array() -> None:
    for value in (None, []):
        data = make_records_data()
        data["records"] = value
        data["count"] = 0
        output = ListPetRecordsOutput.model_validate(data)
        assert output.records == value
        assert output.count == 0


def test_unknown_backend_fields_are_ignored() -> None:
    data = make_records_data()
    data["extra"] = True
    assert ListPetRecordsOutput.model_validate(data).petId == PETH


def test_missing_pet_id_is_rejected() -> None:
    data = make_records_data()
    del data["petId"]
    with pytest.raises(ValidationError):
        ListPetRecordsOutput.model_validate(data)


def test_record_without_an_id_is_still_accepted() -> None:
    """PetRecord fields are all optional; the record array itself is what counts."""
    output = ListPetRecordsOutput.model_validate(make_records_data(records=[{"diagnosis": "猫藓"}]))
    assert output.records[0].diagnosis == "猫藓"


# --------------------------------------------------------------------------- #
# Registration and JSON Schema
# --------------------------------------------------------------------------- #


async def test_tool_is_registered(mcp_server: MCPServer) -> None:
    assert LIST_PET_RECORDS_TOOL_NAME in [t.name for t in await mcp_server.list_tools()]


async def test_json_schema_declares_only_the_id(mcp_server: MCPServer) -> None:
    schema = tool_schema(await tool_by_name(mcp_server, LIST_PET_RECORDS_TOOL_NAME))
    assert set(schema["properties"]) == {"id"}


async def test_tool_description_covers_purpose_and_error_codes(mcp_server: MCPServer) -> None:
    description = (await tool_by_name(mcp_server, LIST_PET_RECORDS_TOOL_NAME)).description or ""
    assert "GET /api/v1/pets/{id}/records" in description
    assert "适用场景" in description and "返回值" in description
    assert "BACKEND_INVALID_RESPONSE" in description


# --------------------------------------------------------------------------- #
# Tool body
# --------------------------------------------------------------------------- #


async def test_valid_call_returns_structured_content(mcp_server: MCPServer) -> None:
    result = await mcp_server.call_tool(LIST_PET_RECORDS_TOOL_NAME, VALID_ARGS)

    assert result.is_error is False
    assert result.structured_content["petId"] == PETH
    assert result.structured_content["count"] == 1
    assert result.structured_content["records"][0]["diagnosis"] == "急性肠胃炎"
    assert json.loads(result.content[0].text) == result.structured_content


async def test_hits_the_records_path(mcp_server: MCPServer, stub: StubBackend) -> None:
    await mcp_server.call_tool(LIST_PET_RECORDS_TOOL_NAME, VALID_ARGS)
    assert stub.last_request.method == "GET"
    assert stub.last_request.url.path == "/api/v1/pets/PET-000001/records"


@pytest.mark.parametrize("error_code", ["BACKEND_TIMEOUT", "BACKEND_UNAVAILABLE"])
async def test_transport_failures_return_envelopes(
    mcp_server: MCPServer, stub: StubBackend, error_code: str
) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused") if error_code == "BACKEND_UNAVAILABLE" else httpx.ReadTimeout("slow")

    stub.responder = responder
    result = await mcp_server.call_tool(LIST_PET_RECORDS_TOOL_NAME, VALID_ARGS)
    assert result.structured_content["error"]["code"] == error_code


async def test_unmatched_backend_shape_returns_an_envelope(
    mcp_server: MCPServer, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope({"count": "nope"}))
    result = await mcp_server.call_tool(LIST_PET_RECORDS_TOOL_NAME, VALID_ARGS)
    error = result.structured_content["error"]
    assert error["code"] == "BACKEND_INVALID_RESPONSE"
    assert "petId" in error["details"]["fields"]
    assert "pydantic" not in json.dumps(error).lower()