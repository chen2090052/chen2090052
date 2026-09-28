"""`add_pet_charge`: input model, JSON body shaping, and the no-retry write path."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from pet_hospital_mcp.tools import AddPetChargeInput
from pet_hospital_mcp.tools.add_pet_charge import ADD_PET_CHARGE_TOOL_NAME
from tests.conftest import StubBackend, make_charge, ok_envelope, tool_by_name, tool_schema, tool_ctx

PETH = "PET-000001"

VALID_ARGS: dict[str, Any] = {
    "id": PETH,
    "item": "血常规检查",
    "category": "检查",
    "amount": 180,
    "doctor": "李医生",
    "date": "2026-09-20",
}


# --------------------------------------------------------------------------- #
# Input model and body shaping
# --------------------------------------------------------------------------- #


def test_all_documented_parameters_are_accepted() -> None:
    parsed = AddPetChargeInput.model_validate(VALID_ARGS)
    assert set(parsed.model_dump()) == set(VALID_ARGS)


def test_id_is_required() -> None:
    for bad in ({}, {"item": "检查"}, {"id": ""}, {"id": None}):
        with pytest.raises(ValidationError):
            AddPetChargeInput.model_validate(bad)


@pytest.mark.parametrize("extra", ["petId", "timestamp", "unknown"])
def test_unknown_fields_are_rejected(extra: str) -> None:
    with pytest.raises(ValidationError):
        AddPetChargeInput.model_validate({**VALID_ARGS, extra: 1})


def test_json_body_omits_the_pet_id() -> None:
    body = AddPetChargeInput.model_validate(VALID_ARGS).to_json_body()
    assert body == {key: value for key, value in VALID_ARGS.items() if key != "id"}
    assert "id" not in body


def test_blank_text_fields_are_normalised_to_none() -> None:
    parsed = AddPetChargeInput.model_validate({**VALID_ARGS, "item": "  ", "doctor": ""})
    assert parsed.item is None
    assert parsed.doctor is None


@pytest.mark.parametrize("category", ["检查", "药品", "手术", "住院", "疫苗", "护理", "其他"])
def test_all_backend_charge_categories_are_accepted(category: str) -> None:
    assert AddPetChargeInput.model_validate({**VALID_ARGS, "category": category}).category == category


@pytest.mark.parametrize("category", ["体检", "unknown", ""])
def test_unknown_charge_category_is_rejected(category: str) -> None:
    with pytest.raises(ValidationError):
        AddPetChargeInput.model_validate({**VALID_ARGS, "category": category})


def test_negative_amount_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AddPetChargeInput.model_validate({**VALID_ARGS, "amount": -1})


@pytest.mark.parametrize("field, value", [("amount", "180"), ("category", ["检查"]), ("item", 123)])
def test_type_incorrect_input_is_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        AddPetChargeInput.model_validate({**VALID_ARGS, field: value})


# --------------------------------------------------------------------------- #
# Registration and JSON Schema
# --------------------------------------------------------------------------- #


async def test_tool_is_registered(mcp_server: MCPServer) -> None:
    assert ADD_PET_CHARGE_TOOL_NAME in [t.name for t in await mcp_server.list_tools()]


async def test_json_schema_declares_the_charge_fields(mcp_server: MCPServer) -> None:
    schema = tool_schema(await tool_by_name(mcp_server, ADD_PET_CHARGE_TOOL_NAME))
    assert set(schema["properties"]) == set(VALID_ARGS)
    category_enum = schema["properties"]["category"]["anyOf"][0]["enum"]
    assert category_enum == ["检查", "药品", "手术", "住院", "疫苗", "护理", "其他"]


async def test_tool_description_notes_that_writes_are_not_retried(mcp_server: MCPServer) -> None:
    description = (await tool_by_name(mcp_server, ADD_PET_CHARGE_TOOL_NAME)).description or ""
    assert "POST /api/v1/pets/{id}/charges" in description
    assert "重复写入" in description


# --------------------------------------------------------------------------- #
# Tool body
# --------------------------------------------------------------------------- #


async def test_valid_call_sends_a_json_post_once(mcp_server: MCPServer, stub: StubBackend) -> None:
    result = await mcp_server.call_tool(ADD_PET_CHARGE_TOOL_NAME, VALID_ARGS)

    assert result.is_error is False
    assert stub.last_request.method == "POST"
    assert stub.last_request.url.path == "/api/v1/pets/PET-000001/charges"
    body = json.loads(stub.last_request.content)
    assert body["item"] == "血常规检查"
    assert body["category"] == "检查"
    assert body["amount"] == 180
    assert "id" not in body


async def test_backend_data_is_passed_through_untouched(mcp_server: MCPServer, stub: StubBackend) -> None:
    created = {**make_charge(charge_id="CH-2026-0002"), "date": "2026-09-20"}
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope(created))

    result = await mcp_server.call_tool(ADD_PET_CHARGE_TOOL_NAME, VALID_ARGS)

    assert result.structured_content == created


async def test_writes_are_never_retried(mcp_server: MCPServer, stub: StubBackend) -> None:
    stub.responder = lambda _request: httpx.Response(503, json={"code": 503, "message": "busy"})

    result = await mcp_server.call_tool(ADD_PET_CHARGE_TOOL_NAME, VALID_ARGS)

    assert result.structured_content["error"]["code"] == "BACKEND_API_ERROR"
    assert len(stub.requests) == 1


async def test_invalid_arguments_are_refused_in_middleware(guard: Any) -> None:
    from mcp.types import CallToolResult, TextContent

    async def _next_ok(_ctx: Any) -> Any:
        return CallToolResult(content=[TextContent(type="text", text="ok")], structured_content={"fine": True})

    result = await guard(
        tool_ctx("tools/call", {"name": ADD_PET_CHARGE_TOOL_NAME, "arguments": {"id": PETH, "category": "坏分类"}}),
        _next_ok,
    )
    assert result.structured_content["error"]["code"] == "VALIDATION_ERROR"