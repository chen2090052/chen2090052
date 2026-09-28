"""`add_pet_record`: input model, JSON body shaping, and the no-retry write path."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from pet_hospital_mcp.tools import AddPetRecordInput
from pet_hospital_mcp.tools.add_pet_record import ADD_PET_RECORD_TOOL_NAME
from tests.conftest import StubBackend, make_record, ok_envelope, tool_by_name, tool_schema, tool_ctx

PETH = "PET-000001"

VALID_ARGS: dict[str, Any] = {
    "id": PETH,
    "visitDate": "2026-09-20",
    "doctor": "李医生",
    "diagnosis": "急性肠胃炎",
    "symptoms": "呕吐腹泻",
    "treatment": "补液消炎",
    "prescription": ["阿莫西林", "益生菌"],
    "weightKg": 24.5,
    "temperature": 38.6,
    "followUp": "一周后复查",
    "charge": 380,
}

ARGS_MINUS_ID = set(VALID_ARGS) - {"id"}


# --------------------------------------------------------------------------- #
# Input model and body shaping
# --------------------------------------------------------------------------- #


def test_all_documented_parameters_are_accepted() -> None:
    parsed = AddPetRecordInput.model_validate(VALID_ARGS)
    assert set(parsed.model_dump()) == ARGS_MINUS_ID | {"id"}


def test_id_is_required() -> None:
    for bad in ({}, {"doctor": "李医生"}, {"id": ""}, {"id": None}):
        with pytest.raises(ValidationError):
            AddPetRecordInput.model_validate(bad)


@pytest.mark.parametrize("extra", ["petId", "createdAt", "unknown"])
def test_unknown_fields_are_rejected(extra: str) -> None:
    with pytest.raises(ValidationError):
        AddPetRecordInput.model_validate({**VALID_ARGS, extra: 1})


def test_json_body_omits_the_pet_id() -> None:
    body = AddPetRecordInput.model_validate(VALID_ARGS).to_json_body()
    assert body == {
        key: value for key, value in VALID_ARGS.items() if key != "id" and value is not None
    }


def test_blank_text_fields_are_normalised_to_none() -> None:
    arguments = {**VALID_ARGS, "visitDate": "   ", "doctor": "", "followUp": "\t"}
    parsed = AddPetRecordInput.model_validate(arguments)
    assert parsed.visitDate is None
    assert parsed.doctor is None
    assert parsed.followUp is None
    assert "visitDate" not in parsed.to_json_body()


def test_prescription_blanks_are_dropped() -> None:
    parsed = AddPetRecordInput.model_validate({**VALID_ARGS, "prescription": ["阿莫西林", "", "  "]})
    assert parsed.prescription == ["阿莫西林"]
    assert parsed.to_json_body()["prescription"] == ["阿莫西林"]


@pytest.mark.parametrize("field, value", [("weightKg", 0), ("weightKg", 301), ("temperature", 29), ("temperature", 46), ("charge", -1)])
def test_out_of_range_numerics_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        AddPetRecordInput.model_validate({**VALID_ARGS, field: value})


@pytest.mark.parametrize("field, value", [("weightKg", "24"), ("charge", "380"), ("prescription", "阿莫西林"), ("temperature", True)])
def test_type_incorrect_input_is_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        AddPetRecordInput.model_validate({**VALID_ARGS, field: value})


# --------------------------------------------------------------------------- #
# Registration and JSON Schema
# --------------------------------------------------------------------------- #


async def test_tool_is_registered(mcp_server: MCPServer) -> None:
    assert ADD_PET_RECORD_TOOL_NAME in [t.name for t in await mcp_server.list_tools()]


async def test_json_schema_declares_the_record_fields(mcp_server: MCPServer) -> None:
    schema = tool_schema(await tool_by_name(mcp_server, ADD_PET_RECORD_TOOL_NAME))
    assert set(schema["properties"]) == ARGS_MINUS_ID | {"id"}


async def test_tool_description_notes_that_writes_are_not_retried(mcp_server: MCPServer) -> None:
    description = (await tool_by_name(mcp_server, ADD_PET_RECORD_TOOL_NAME)).description or ""
    assert "POST /api/v1/pets/{id}/records" in description
    assert "重复写入" in description
    assert "BACKEND_INVALID_RESPONSE" in description


# --------------------------------------------------------------------------- #
# Tool body
# --------------------------------------------------------------------------- #


async def test_valid_call_sends_a_json_post_once(mcp_server: MCPServer, stub: StubBackend) -> None:
    result = await mcp_server.call_tool(ADD_PET_RECORD_TOOL_NAME, VALID_ARGS)

    assert result.is_error is False
    assert stub.last_request.method == "POST"
    assert stub.last_request.url.path == "/api/v1/pets/PET-000001/records"
    body = json.loads(stub.last_request.content)
    assert body["diagnosis"] == "急性肠胃炎"
    assert body["prescription"] == ["阿莫西林", "益生菌"]
    assert body["charge"] == 380
    assert "id" not in body


async def test_backend_data_is_passed_through_untouched(mcp_server: MCPServer, stub: StubBackend) -> None:
    created = {**make_record(record_id="MR-2026-0002"), "visitDate": "2026-09-20"}
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope(created))

    result = await mcp_server.call_tool(ADD_PET_RECORD_TOOL_NAME, VALID_ARGS)

    assert result.is_error is False
    assert result.structured_content == created


async def test_writes_are_never_retried(mcp_server: MCPServer, stub: StubBackend) -> None:
    """A 503 must not be followed by a second POST — that would duplicate a write."""
    stub.responder = lambda _request: httpx.Response(503, json={"code": 503, "message": "busy"})

    result = await mcp_server.call_tool(ADD_PET_RECORD_TOOL_NAME, VALID_ARGS)

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "BACKEND_API_ERROR"
    assert len(stub.requests) == 1


async def test_backend_business_error_is_forwarded(mcp_server: MCPServer, stub: StubBackend) -> None:
    stub.responder = lambda _request: httpx.Response(
        200, json={"code": 400, "message": "diagnosis 为必填项", "time": "t"}
    )

    result = await mcp_server.call_tool(ADD_PET_RECORD_TOOL_NAME, VALID_ARGS)

    error = result.structured_content["error"]
    assert error["code"] == "BACKEND_API_ERROR"
    assert error["details"]["upstream_message"] == "diagnosis 为必填项"


async def test_invalid_arguments_are_refused_in_middleware(guard: Any) -> None:
    from mcp.types import CallToolResult, TextContent

    async def _next_ok(_ctx: Any) -> Any:
        return CallToolResult(content=[TextContent(type="text", text="ok")], structured_content={"fine": True})

    result = await guard(
        tool_ctx("tools/call", {"name": ADD_PET_RECORD_TOOL_NAME, "arguments": {"id": PETH, "charge": "abc"}}),
        _next_ok,
    )
    assert result.structured_content["error"]["code"] == "VALIDATION_ERROR"