"""`get_pet`: input model, output model, JSON Schema, and tool body."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from pet_hospital_mcp.tools import GetPetInput, GetPetOutput
from pet_hospital_mcp.tools.get_pet import GET_PET_TOOL_NAME
from tests.conftest import StubBackend, make_pet, ok_envelope, tool_by_name, tool_schema

PETH = "PET-000001"


# --------------------------------------------------------------------------- #
# Input model
# --------------------------------------------------------------------------- #


def test_requires_a_pet_id() -> None:
    parsed = GetPetInput.model_validate({"id": PETH})
    assert parsed.id == PETH


@pytest.mark.parametrize("missing", [{}, {"id": ""}, {"id": "   "}, {"id": None}])
def test_missing_or_blank_id_is_rejected(missing: Any) -> None:
    with pytest.raises(ValidationError):
        GetPetInput.model_validate(missing)


@pytest.mark.parametrize("extra", ["petId", "name", "species", "unknown"])
def test_unknown_fields_are_rejected(extra: str) -> None:
    with pytest.raises(ValidationError):
        GetPetInput.model_validate({"id": PETH, extra: 1})


def test_non_string_id_is_rejected() -> None:
    for bad in (123, ["PET-000001"], True):
        with pytest.raises(ValidationError):
            GetPetInput.model_validate({"id": bad})


# --------------------------------------------------------------------------- #
# Output model
# --------------------------------------------------------------------------- #


def test_output_accepts_the_full_pet_shape() -> None:
    output = GetPetOutput.model_validate(make_pet(records=[], charges=[]))
    assert output.id == PETH
    assert output.ownerName == "张三"
    assert output.records == []


def test_output_accepts_populated_records_and_charges() -> None:
    pet = make_pet(
        records=[{"id": "MR-1", "diagnosis": "肠胃炎", "charge": 380}],
        charges=[{"id": "CH-1", "item": "血常规", "category": "检查", "amount": 180}],
        totalCost=560.0,
        visitCount=1,
    )
    output = GetPetOutput.model_validate(pet)
    assert output.records[0].diagnosis == "肠胃炎"
    assert output.charges[0].amount == 180


def test_pet_without_an_id_is_rejected() -> None:
    data = make_pet()
    del data["id"]
    with pytest.raises(ValidationError):
        GetPetOutput.model_validate(data)


def test_unknown_backend_fields_are_ignored() -> None:
    pet = make_pet()
    pet["surprise"] = [1, 2, 3]
    output = GetPetOutput.model_validate(pet)
    assert output.id == PETH


# --------------------------------------------------------------------------- #
# Registration and JSON Schema
# --------------------------------------------------------------------------- #


async def test_tool_is_registered(mcp_server: MCPServer) -> None:
    names = [tool.name for tool in await mcp_server.list_tools()]
    assert GET_PET_TOOL_NAME in names


async def test_json_schema_declares_the_id(mcp_server: MCPServer) -> None:
    schema = tool_schema(await tool_by_name(mcp_server, GET_PET_TOOL_NAME))
    assert set(schema["properties"]) == {"id"}
    assert schema["properties"]["id"]["minLength"] == 1
    assert "pattern" in schema["properties"]["id"]


async def test_tool_description_covers_purpose_and_error_codes(mcp_server: MCPServer) -> None:
    for tool in await mcp_server.list_tools():
        if tool.name == GET_PET_TOOL_NAME:
            description = tool.description or ""
            break
    else:  # pragma: no cover
        pytest.fail("get_pet is not registered")
    assert "GET /api/v1/pets/{id}" in description
    assert "适用场景" in description
    assert "返回值" in description
    assert "id" in description
    for code in ("VALIDATION_ERROR", "BACKEND_TIMEOUT", "BACKEND_UNAVAILABLE", "BACKEND_INVALID_RESPONSE"):
        assert code in description


# --------------------------------------------------------------------------- #
# Tool body
# --------------------------------------------------------------------------- #


async def test_valid_call_returns_structured_content(mcp_server: MCPServer) -> None:
    result = await mcp_server.call_tool(GET_PET_TOOL_NAME, {"id": PETH})

    assert result.is_error is False
    assert result.structured_content["id"] == PETH
    assert result.structured_content["name"] == "旺财"
    assert json.loads(result.content[0].text) == result.structured_content


async def test_pet_id_is_url_encoded(mcp_server: MCPServer, stub: StubBackend) -> None:
    await mcp_server.call_tool(GET_PET_TOOL_NAME, {"id": PETH})
    assert stub.last_request.method == "GET"
    assert stub.last_request.url.path == "/api/v1/pets/PET-000001"


@pytest.mark.parametrize(
    "failure, code",
    [
        ("timeout", "BACKEND_TIMEOUT"),
        ("unavailable", "BACKEND_UNAVAILABLE"),
        ("api_error", "BACKEND_API_ERROR"),
        ("invalid_response", "BACKEND_INVALID_RESPONSE"),
    ],
)
async def test_every_backend_failure_returns_an_envelope(
    mcp_server: MCPServer, stub: StubBackend, failure: str, code: str
) -> None:
    if failure == "timeout":

        def responder(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

    elif failure == "unavailable":

        def responder(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

    elif failure == "api_error":
        responder = lambda _request: httpx.Response(500, json={"code": 500, "message": "boom"})
    else:
        responder = lambda _request: httpx.Response(200, text="<html>")

    stub.responder = responder
    result = await mcp_server.call_tool(GET_PET_TOOL_NAME, {"id": PETH})

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == code


async def test_unmatched_backend_shape_returns_an_envelope(
    mcp_server: MCPServer, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope({"not": "a pet"}))

    result = await mcp_server.call_tool(GET_PET_TOOL_NAME, {"id": PETH})

    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["code"] == "BACKEND_INVALID_RESPONSE"
    assert "id" in error["details"]["fields"]
    assert "pydantic" not in json.dumps(error).lower()