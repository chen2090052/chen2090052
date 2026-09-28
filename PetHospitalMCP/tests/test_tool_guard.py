"""ToolCallGuard uniformly rejects invalid arguments for every registered tool.

The SDK's own signature validation runs first when a tool is called *in
process*, so constraint violations are exercised here through the middleware
itself — the same object that guards the wire path.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.conftest import tool_ctx

#: Tool name -> (invalid arguments, reason) pairs; every case must be refused
#: before any backend call.
INVALID_BY_TOOL: dict[str, list[tuple[dict[str, Any], str]]] = {
    "add_pet_charge": [
        ({}, "missing id"),
        ({"id": " "}, "blank id"),
        ({"id": "PET-1", "category": "体检"}, "unknown category"),
        ({"id": "PET-1", "amount": -5}, "negative amount"),
        ({"id": "PET-1", "amount": "free"}, "wrong type"),
        ({"id": "PET-1", "bogus": 1}, "unknown field"),
    ],
    "add_pet_record": [
        ({}, "missing id"),
        ({"id": " "}, "blank id"),
        ({"id": "PET-1", "charge": -1}, "negative charge"),
        ({"id": "PET-1", "weightKg": 0}, "zero weight"),
        ({"id": "PET-1", "temperature": 60}, "temperature out of range"),
        ({"id": "PET-1", "diagnosis": 3}, "wrong type"),
        ({"id": "PET-1", "bogus": 1}, "unknown field"),
    ],
    "get_endpoints": [
        ({"any": 1}, "no arguments are accepted"),
    ],
    "get_meta": [
        ({"any": 1}, "no arguments are accepted"),
    ],
    "get_pet": [
        ({}, "missing id"),
        ({"id": " "}, "blank id"),
        ({"id": 123}, "wrong type"),
        ({"id": "PET-1", "bogus": 1}, "unknown field"),
    ],
    "get_pet_summary": [
        ({}, "missing id"),
        ({"id": None}, "null id"),
        ({"id": "PET-1", "bogus": 1}, "unknown field"),
    ],
    "get_stats": [
        ({"top": 0}, "top below one"),
        ({"top": 101}, "top above one hundred"),
        ({"top": "5"}, "top as a string"),
        ({"bogus": 1}, "unknown field"),
    ],
    "list_pet_charges": [
        ({}, "missing id"),
        ({"id": None}, "null id"),
        ({"id": "PET-1", "bogus": 1}, "unknown field"),
    ],
    "list_pet_records": [
        ({}, "missing id"),
        ({"id": None}, "null id"),
        ({"id": "PET-1", "bogus": 1}, "unknown field"),
    ],
    "list_pets": [
        ({"page": 0}, "page below one"),
        ({"species": "恐龙"}, "unknown species"),
        ({"status": "已出院"}, "unknown status"),
        ({"min": -1}, "negative bound"),
        ({"min": 100, "max": 1}, "min greater than max"),
        ({"bogus": 1}, "unknown field"),
    ],
}

VALID_BY_TOOL: dict[str, list[dict[str, Any]]] = {
    "add_pet_charge": [{"id": "PET-000001"}, {"id": "PET-000001", "item": "血常规", "category": "检查", "amount": 180}],
    "add_pet_record": [{"id": "PET-000001", "doctor": "李医生"}, {"id": "PET-000001", "prescription": ["阿莫西林"], "charge": 380}],
    "get_endpoints": [{}],
    "get_meta": [{}],
    "get_pet": [{"id": "PET-000001"}],
    "get_pet_summary": [{"id": "PET-000001"}],
    "get_stats": [{}, {"top": 5}],
    "list_pet_charges": [{"id": "PET-000001"}],
    "list_pet_records": [{"id": "PET-000001"}],
    "list_pets": [{"page": 1}, {"species": "犬", "sortBy": "totalCost", "order": "desc"}],
}


async def _next_ok(_ctx: Any) -> Any:
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(content=[TextContent(type="text", text="ok")], structured_content={"fine": True})


@pytest.mark.parametrize("tool_name", sorted(INVALID_BY_TOOL))
async def test_guard_rejects_every_invalid_argument_set(guard: Any, tool_name: str) -> None:
    for bad_args, reason in INVALID_BY_TOOL[tool_name]:
        result = await guard(tool_ctx("tools/call", {"name": tool_name, "arguments": bad_args}), _next_ok)

        assert result.is_error is True, f"{tool_name} should reject {reason}: {bad_args}"
        assert result.structured_content["error"]["code"] == "VALIDATION_ERROR", f"{tool_name} {reason}"
        assert result.structured_content["error"]["details"]["fields"]
        rendered = json.dumps(result.structured_content, ensure_ascii=False).lower()
        for forbidden in ("pydantic", "validationerror", "errors.pydantic.dev"):
            assert forbidden not in rendered, f"{forbidden!r} leaked for {tool_name} {reason}"


@pytest.mark.parametrize("tool_name", sorted(VALID_BY_TOOL))
async def test_guard_accepts_every_valid_argument_set(guard: Any, tool_name: str) -> None:
    for good_args in VALID_BY_TOOL[tool_name]:
        result = await guard(tool_ctx("tools/call", {"name": tool_name, "arguments": good_args}), _next_ok)

        assert result.is_error is False
        assert result.structured_content == {"fine": True}


async def test_guard_reports_unknown_tools(guard: Any) -> None:
    result = await guard(tool_ctx("tools/call", {"name": "delete_all", "arguments": {}}), _next_ok)
    assert result.structured_content["error"]["details"]["reason"] == "unknown_tool"