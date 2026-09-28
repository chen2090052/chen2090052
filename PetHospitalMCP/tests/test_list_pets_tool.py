"""`list_pets`: input model, output model, JSON Schema, and tool body."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from pet_hospital_mcp.logging_config import REDACTED
from pet_hospital_mcp.server import ToolCallGuard
from pet_hospital_mcp.tools import TOOL_INPUT_MODELS, ListPetsInput, ListPetsOutput
from pet_hospital_mcp.tools.list_pets import LIST_PETS_TOOL_NAME
from tests.conftest import StubBackend, make_pet, make_pets_data, ok_envelope

VALID_ARGS: dict[str, Any] = {
    "q": "肠胃炎",
    "name": "旺财",
    "ownerName": "张三",
    "ownerPhone": "13800001111",
    "species": "犬",
    "doctor": "李医生",
    "disease": "急性肠胃炎",
    "status": "待就诊",
    "min": 100,
    "max": 5000,
    "sortBy": "totalCost",
    "order": "desc",
    "page": 3,
    "pageSize": 50,
}

ALL_PARAM_NAMES = set(VALID_ARGS)


# --------------------------------------------------------------------------- #
# Input model
# --------------------------------------------------------------------------- #


def test_all_documented_parameters_are_accepted() -> None:
    parsed = ListPetsInput.model_validate(VALID_ARGS)
    assert set(parsed.model_dump()) == ALL_PARAM_NAMES


def test_no_adapter_private_parameters_exist() -> None:
    """The input model mirrors the backend's query parameters exactly."""
    assert set(ListPetsInput.model_fields) == ALL_PARAM_NAMES


def test_every_parameter_is_optional() -> None:
    parsed = ListPetsInput.model_validate({})
    assert parsed.page == 1
    assert parsed.pageSize == 20
    assert parsed.species is None
    assert parsed.sortBy is None


def test_defaults_are_not_sent_upstream() -> None:
    """Only caller-supplied parameters are forwarded."""
    assert ListPetsInput.model_validate({}).to_query_params() == {"page": 1, "pageSize": 20}


def test_absent_optional_parameters_are_omitted() -> None:
    params = ListPetsInput.model_validate({"species": "犬"}).to_query_params()
    assert params == {"species": "犬", "page": 1, "pageSize": 20}
    assert "doctor" not in params


@pytest.mark.parametrize("species", ["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"])
def test_all_backend_species_are_accepted(species: str) -> None:
    assert ListPetsInput.model_validate({"species": species}).species == species


@pytest.mark.parametrize("status", ["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"])
def test_all_backend_statuses_are_accepted(status: str) -> None:
    assert ListPetsInput.model_validate({"status": status}).status == status


@pytest.mark.parametrize(
    "sort_by",
    [
        "id",
        "name",
        "ownerName",
        "species",
        "doctor",
        "disease",
        "status",
        "totalCost",
        "visitCount",
        "createdAt",
        "updatedAt",
    ],
)
def test_all_backend_sort_fields_are_accepted(sort_by: str) -> None:
    assert ListPetsInput.model_validate({"sortBy": sort_by}).sortBy == sort_by


@pytest.mark.parametrize("order", ["asc", "desc"])
def test_all_orders_are_accepted(order: str) -> None:
    assert ListPetsInput.model_validate({"order": order}).order == order


@pytest.mark.parametrize("species", ["恐龙", "dog", "犬类", ""])
def test_unknown_species_is_rejected(species: str) -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"species": species})


@pytest.mark.parametrize("status", ["已出院", "unknown", ""])
def test_unknown_status_is_rejected(status: str) -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"status": status})


@pytest.mark.parametrize("sort_by", ["ownerAddr", "phone", "total_cost", ""])
def test_unknown_sort_field_is_rejected(sort_by: str) -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"sortBy": sort_by})


@pytest.mark.parametrize("order", ["up", "down", "ASC", ""])
def test_unknown_order_is_rejected(order: str) -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"order": order})


@pytest.mark.parametrize("page", [0, -1, -100])
def test_page_below_one_is_rejected(page: int) -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"page": page})


@pytest.mark.parametrize("page", [1, 2, 9999])
def test_page_from_one_upwards_is_accepted(page: int) -> None:
    assert ListPetsInput.model_validate({"page": page}).page == page


@pytest.mark.parametrize("page_size", [0, -1, 501, 10_000])
def test_page_size_outside_one_to_500_is_rejected(page_size: int) -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({"pageSize": page_size})


@pytest.mark.parametrize("page_size", [1, 20, 500])
def test_page_size_within_bounds_is_accepted(page_size: int) -> None:
    assert ListPetsInput.model_validate({"pageSize": page_size}).pageSize == page_size


@pytest.mark.parametrize("field", ["min", "max"])
@pytest.mark.parametrize("value", [-1, -0.5, -1000])
def test_negative_cost_bounds_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({field: value})


@pytest.mark.parametrize("field", ["min", "max"])
def test_zero_is_a_valid_cost_bound(field: str) -> None:
    assert getattr(ListPetsInput.model_validate({field: 0}), field) == 0


def test_min_greater_than_max_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ListPetsInput.model_validate({"min": 5000, "max": 100})
    assert "min must be less than or equal to max" in str(excinfo.value)


@pytest.mark.parametrize(("minimum", "maximum"), [(100, 100), (0, 0), (100, 5000)])
def test_min_not_greater_than_max_is_accepted(minimum: float, maximum: float) -> None:
    parsed = ListPetsInput.model_validate({"min": minimum, "max": maximum})
    assert (parsed.min, parsed.max) == (minimum, maximum)


def test_equal_bounds_are_allowed() -> None:
    assert ListPetsInput.model_validate({"min": 42, "max": 42}).min == 42


@pytest.mark.parametrize("extra", ["limit", "offset", "Page", "PAGESIZE", "sortby", "unknown"])
def test_unknown_fields_are_rejected(extra: str) -> None:
    """Unknown fields must be refused, not silently ignored."""
    with pytest.raises(ValidationError) as excinfo:
        ListPetsInput.model_validate({**VALID_ARGS, extra: 1})
    assert any(err["type"] == "extra_forbidden" for err in excinfo.value.errors())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["min", "max"])
def test_nan_and_infinity_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("page", "1"),
        ("page", "abc"),
        ("page", 1.0),
        ("page", 1.5),
        ("page", True),
        ("page", None),
        ("pageSize", "20"),
        ("pageSize", 20.0),
        ("species", 1),
        ("species", ["犬"]),
        ("min", "100"),
        ("min", True),
        ("q", 123),
        ("q", ["a"]),
    ],
)
def test_type_incorrect_input_is_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        ListPetsInput.model_validate({field: value})


def test_integer_is_accepted_where_a_float_is_expected() -> None:
    """`min: 100` is a fine way to express 100.0."""
    assert ListPetsInput.model_validate({"min": 100}).min == 100.0


@pytest.mark.parametrize("field", ["q", "name", "ownerName", "ownerPhone", "doctor", "disease"])
@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_text_filters_are_normalised_to_none(field: str, blank: str) -> None:
    parsed = ListPetsInput.model_validate({field: blank})
    assert getattr(parsed, field) is None
    assert field not in parsed.to_query_params()


def test_meaningful_text_is_preserved() -> None:
    assert ListPetsInput.model_validate({"q": " 肠胃炎 "}).q == " 肠胃炎 "


def test_to_query_params_drops_every_none() -> None:
    params = ListPetsInput.model_validate({"species": "犬", "page": 2, "pageSize": 10}).to_query_params()
    assert all(value is not None for value in params.values())
    assert params == {"species": "犬", "page": 2, "pageSize": 10}


# --------------------------------------------------------------------------- #
# Output model
# --------------------------------------------------------------------------- #


def test_output_accepts_the_documented_data_fields() -> None:
    output = ListPetsOutput.model_validate(make_pets_data())
    assert set(output.model_dump()) == {"items", "total", "page", "pageSize", "totalPages", "totalCost"}


@pytest.mark.parametrize("empty_value", [None, []])
def test_records_and_charges_accept_null_and_empty_array(empty_value: Any) -> None:
    """The backend serialises a fresh pet's arrays as `null`, not `[]`."""
    data = make_pets_data([make_pet(records=empty_value, charges=empty_value)])
    output = ListPetsOutput.model_validate(data)

    assert output.items[0].records == empty_value
    assert output.items[0].charges == empty_value


def test_records_and_charges_accept_populated_arrays() -> None:
    data = make_pets_data(
        [
            make_pet(
                records=[{"id": "MR-1", "diagnosis": "肠胃炎", "prescription": ["阿莫西林"], "charge": 380}],
                charges=[{"id": "CH-1", "item": "血常规", "category": "检查", "amount": 180}],
                totalCost=560.0,
                visitCount=1,
            )
        ]
    )
    output = ListPetsOutput.model_validate(data)

    assert output.items[0].records[0].diagnosis == "肠胃炎"
    assert output.items[0].records[0].prescription == ["阿莫西林"]
    assert output.items[0].charges[0].amount == 180
    assert output.items[0].totalCost == 560.0


def test_unknown_backend_fields_are_ignored() -> None:
    """A future backend field must not break the tool."""
    data = make_pets_data([make_pet(brandNewField="surprise")], total=1)
    data["anotherNewField"] = [1, 2, 3]

    output = ListPetsOutput.model_validate(data)
    assert output.total == 1


def test_optional_pet_fields_may_be_absent() -> None:
    output = ListPetsOutput.model_validate(
        {"items": [{"id": "PET-000002"}], "total": 1, "page": 1, "pageSize": 20, "totalPages": 1, "totalCost": 0}
    )
    assert output.items[0].name is None
    assert output.items[0].records is None


@pytest.mark.parametrize("missing", ["total", "page", "pageSize", "totalPages", "totalCost", "items"])
def test_missing_required_envelope_field_is_rejected(missing: str) -> None:
    data = make_pets_data()
    del data[missing]
    with pytest.raises(ValidationError):
        ListPetsOutput.model_validate(data)


def test_items_must_be_an_array() -> None:
    data = make_pets_data()
    data["items"] = {"not": "an array"}
    with pytest.raises(ValidationError):
        ListPetsOutput.model_validate(data)


def test_pet_without_an_id_is_rejected() -> None:
    data = make_pets_data([{"name": "无名"}])
    with pytest.raises(ValidationError):
        ListPetsOutput.model_validate(data)


# --------------------------------------------------------------------------- #
# Registration and JSON Schema
# --------------------------------------------------------------------------- #


async def _find_tool(mcp_server: MCPServer, name: str) -> Any:
    for tool in await mcp_server.list_tools():
        if tool.name == name:
            return tool
    raise AssertionError(f"tool {name!r} is not registered")


async def test_tool_is_registered_under_a_snake_case_name(mcp_server: MCPServer) -> None:
    tools = await mcp_server.list_tools()
    tool_names = [tool.name for tool in tools]
    assert LIST_PETS_TOOL_NAME in tool_names
    assert LIST_PETS_TOOL_NAME.islower()


async def test_all_ten_tools_are_registered(mcp_server: MCPServer) -> None:
    """The full toolset of the 宠爱AI病历管理系统 is exposed."""
    tool_names = {tool.name for tool in await mcp_server.list_tools()}
    assert tool_names == {
        "list_pets",
        "get_pet",
        "get_pet_summary",
        "list_pet_records",
        "list_pet_charges",
        "add_pet_record",
        "add_pet_charge",
        "get_stats",
        "get_meta",
        "get_endpoints",
    }


def _schema(tool: Any) -> dict[str, Any]:
    return tool.inputSchema if hasattr(tool, "inputSchema") else tool.input_schema


async def test_json_schema_lists_every_parameter(mcp_server: MCPServer) -> None:
    schema = _schema(await _find_tool(mcp_server, LIST_PETS_TOOL_NAME))
    assert set(schema["properties"]) == ALL_PARAM_NAMES


async def test_json_schema_declares_the_backend_enums(mcp_server: MCPServer) -> None:
    schema = _schema(await _find_tool(mcp_server, LIST_PETS_TOOL_NAME))
    properties = schema["properties"]

    assert properties["species"]["anyOf"][0]["enum"] == ["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
    assert properties["status"]["anyOf"][0]["enum"] == [
        "待就诊",
        "就诊中",
        "住院中",
        "已康复",
        "慢性病随访",
    ]
    assert properties["order"]["anyOf"][0]["enum"] == ["asc", "desc"]
    assert properties["sortBy"]["anyOf"][0]["enum"] == [
        "id",
        "name",
        "ownerName",
        "species",
        "doctor",
        "disease",
        "status",
        "totalCost",
        "visitCount",
        "createdAt",
        "updatedAt",
    ]


async def test_json_schema_declares_numeric_bounds(mcp_server: MCPServer) -> None:
    properties = _schema(await _find_tool(mcp_server, LIST_PETS_TOOL_NAME))["properties"]

    assert properties["page"]["minimum"] == 1
    assert properties["pageSize"]["minimum"] == 1
    assert properties["pageSize"]["maximum"] == 500
    assert properties["min"]["anyOf"][0]["minimum"] == 0
    assert properties["max"]["anyOf"][0]["minimum"] == 0


async def test_json_schema_defaults_match_the_model(mcp_server: MCPServer) -> None:
    properties = _schema(await _find_tool(mcp_server, LIST_PETS_TOOL_NAME))["properties"]
    assert properties["page"]["default"] == 1
    assert properties["pageSize"]["default"] == 20


async def test_every_parameter_is_documented_in_the_schema(mcp_server: MCPServer) -> None:
    properties = _schema(await _find_tool(mcp_server, LIST_PETS_TOOL_NAME))["properties"]
    for name, spec in properties.items():
        assert spec.get("description"), f"{name} has no description"


async def test_tool_description_covers_purpose_scenarios_and_returns(mcp_server: MCPServer) -> None:
    description = (await _find_tool(mcp_server, LIST_PETS_TOOL_NAME)).description or ""

    assert "GET /api/v1/pets" in description  # purpose: which endpoint
    assert "适用场景" in description  # when to use it
    assert "返回值" in description  # what comes back
    for name in ALL_PARAM_NAMES:
        assert name in description, f"{name} is not documented in the tool description"
    for code in ("VALIDATION_ERROR", "BACKEND_TIMEOUT", "BACKEND_UNAVAILABLE", "BACKEND_INVALID_RESPONSE"):
        assert code in description


# --------------------------------------------------------------------------- #
# Tool body
# --------------------------------------------------------------------------- #


async def test_valid_call_returns_structured_content(mcp_server: MCPServer, stub: StubBackend) -> None:
    result = await mcp_server.call_tool(LIST_PETS_TOOL_NAME, {"species": "犬", "page": 1, "pageSize": 5})

    assert result.is_error is False
    assert set(result.structured_content) == {
        "items",
        "total",
        "page",
        "pageSize",
        "totalPages",
        "totalCost",
    }
    assert json.loads(result.content[0].text) == result.structured_content


def _expected_query(args: dict[str, Any]) -> dict[str, str]:
    """`min`/`max` are floats, so an integer argument is sent as e.g. ``100.0``."""
    return {
        key: str(float(value)) if key in ("min", "max") else str(value)
        for key, value in args.items()
    }


async def test_all_arguments_reach_the_backend(mcp_server: MCPServer, stub: StubBackend) -> None:
    await mcp_server.call_tool(LIST_PETS_TOOL_NAME, dict(VALID_ARGS))

    sent = dict(httpx.URL(stub.last_request.url).params)
    assert sent == _expected_query(VALID_ARGS)
    assert len(sent) == len(VALID_ARGS)


async def test_unmatched_backend_shape_returns_an_envelope(
    mcp_server: MCPServer, stub: StubBackend
) -> None:
    """Valid JSON of the wrong shape is an error *result*, not a raised error.

    Returning it (rather than raising) is what keeps the structured envelope
    intact — the SDK flattens an escaping exception into a text block.
    """
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope({"items": "not-an-array"}))

    result = await mcp_server.call_tool(LIST_PETS_TOOL_NAME, {})

    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["code"] == "BACKEND_INVALID_RESPONSE"
    assert "items" in error["details"]["fields"]

    rendered = json.dumps(result.structured_content).lower()
    for forbidden in ("pydantic", "validationerror", "errors.pydantic.dev"):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    "failure",
    ["timeout", "unavailable", "api_error", "invalid_response"],
)
async def test_every_backend_failure_returns_an_envelope(
    mcp_server: MCPServer, stub: StubBackend, failure: str
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
    result = await mcp_server.call_tool(LIST_PETS_TOOL_NAME, {})

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == {
        "timeout": "BACKEND_TIMEOUT",
        "unavailable": "BACKEND_UNAVAILABLE",
        "api_error": "BACKEND_API_ERROR",
        "invalid_response": "BACKEND_INVALID_RESPONSE",
    }[failure]


# --------------------------------------------------------------------------- #
# ToolCallGuard (the middleware that enforces the contract)
# --------------------------------------------------------------------------- #


def _ctx(method: str, params: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(method=method, params=params)


@pytest.fixture
def guard() -> ToolCallGuard:
    return ToolCallGuard(TOOL_INPUT_MODELS, frozenset(TOOL_INPUT_MODELS))


async def _next_ok(_ctx: Any) -> Any:
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(content=[TextContent(type="text", text="ok")], structured_content={"fine": True})


@pytest.mark.parametrize(
    "bad_args",
    [
        {"page": 0},
        {"pageSize": 501},
        {"species": "恐龙"},
        {"status": "已出院"},
        {"sortBy": "ownerAddr"},
        {"order": "up"},
        {"min": -1},
        {"max": float("inf")},
        {"min": 100, "max": 1},
        {"unknown": 1},
        {"page": "2"},
    ],
)
async def test_guard_rejects_invalid_arguments(guard: ToolCallGuard, bad_args: dict[str, Any]) -> None:
    result = await guard(_ctx("tools/call", {"name": "list_pets", "arguments": bad_args}), _next_ok)

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "VALIDATION_ERROR"
    assert result.structured_content["error"]["details"]["fields"]


async def test_guard_does_not_call_through_on_invalid_arguments(guard: ToolCallGuard) -> None:
    called = False

    async def next_that_must_not_run(_ctx: Any) -> Any:
        nonlocal called
        called = True
        return await _next_ok(_ctx)

    await guard(_ctx("tools/call", {"name": "list_pets", "arguments": {"page": 0}}), next_that_must_not_run)
    assert called is False


async def test_guard_reports_unknown_tools(guard: ToolCallGuard) -> None:
    result = await guard(_ctx("tools/call", {"name": "delete_everything", "arguments": {}}), _next_ok)

    assert result.is_error is True
    assert result.structured_content["error"]["details"]["reason"] == "unknown_tool"


async def test_guard_passes_valid_arguments_through(guard: ToolCallGuard) -> None:
    result = await guard(_ctx("tools/call", {"name": "list_pets", "arguments": {"page": 2}}), _next_ok)

    assert result.is_error is False
    assert result.structured_content == {"fine": True}


async def test_guard_replaces_framework_failures(guard: ToolCallGuard) -> None:
    """Failures the SDK produces itself carry Pydantic prose; they must be replaced.

    Handlers return a raw result mapping, so that is what the guard inspects.
    """

    async def failing(_ctx: Any) -> Any:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Error executing tool list_pets: 1 validation error for list_petsArguments\n"
                        "page\n  Input should be a valid integer "
                        "[type=int_parsing, input_value='abc', input_url=https://errors.pydantic.dev/]"
                    ),
                }
            ],
            "isError": True,
        }

    result = await guard(_ctx("tools/call", {"name": "list_pets", "arguments": {"page": 1}}), failing)

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "VALIDATION_ERROR"
    rendered = json.dumps(result.structured_content, ensure_ascii=False)
    for forbidden in ("pydantic", "int_parsing", "list_petsArguments", "Error executing tool"):
        assert forbidden not in rendered


async def test_guard_preserves_our_own_error_envelopes(guard: ToolCallGuard) -> None:
    """A tool that returns an envelope gets it passed through untouched."""
    from pet_hospital_mcp.errors import BackendTimeoutError

    envelope = BackendTimeoutError(details={"attempts": 3}).to_response().model_dump(mode="json")

    async def failing(_ctx: Any) -> Any:
        return {
            "content": [{"type": "text", "text": json.dumps(envelope)}],
            "structuredContent": envelope,
            "isError": True,
        }

    result = await guard(_ctx("tools/call", {"name": "list_pets", "arguments": {}}), failing)

    # Passed through as the same mapping — not rebuilt.
    assert result is not None
    assert result["structuredContent"]["error"]["code"] == "BACKEND_TIMEOUT"
    assert result["structuredContent"]["error"]["details"] == {"attempts": 3}


async def test_guard_logs_our_error_code(guard: ToolCallGuard, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from pet_hospital_mcp.errors import BackendTimeoutError

    envelope = BackendTimeoutError().to_response().model_dump(mode="json")

    async def failing(_ctx: Any) -> Any:
        return {"content": [], "structuredContent": envelope, "isError": True}

    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tools"):
        await guard(_ctx("tools/call", {"name": "list_pets", "arguments": {}}), failing)

    payload = caplog.records[-1].__dict__["extra_fields"]
    assert payload["status"] == "error"
    assert payload["error_code"] == "BACKEND_TIMEOUT"


async def test_guard_converts_unexpected_exceptions(guard: ToolCallGuard) -> None:
    async def exploding(_ctx: Any) -> Any:
        raise RuntimeError("internal detail that must not escape")

    result = await guard(_ctx("tools/call", {"name": "list_pets", "arguments": {}}), exploding)

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "INTERNAL_ERROR"
    assert "internal detail" not in json.dumps(result.structured_content)


async def test_guard_ignores_other_methods(guard: ToolCallGuard) -> None:
    result = await guard(_ctx("tools/list", {}), _next_ok)
    assert result.structured_content == {"fine": True}


async def test_guard_rejects_non_object_arguments(guard: ToolCallGuard) -> None:
    result = await guard(_ctx("tools/call", {"name": "list_pets", "arguments": "not-an-object"}), _next_ok)

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "VALIDATION_ERROR"


async def test_guard_redacts_sensitive_arguments_in_logs(
    guard: ToolCallGuard, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tools"):
        await guard(
            _ctx("tools/call", {"name": "list_pets", "arguments": {"ownerPhone": "13800001111"}}),
            _next_ok,
        )

    payload = caplog.records[-1].__dict__["extra_fields"]
    assert payload["tool_name"] == "list_pets"
    assert payload["params"]["ownerPhone"] == REDACTED
    assert "13800001111" not in caplog.text
    assert "13800001111" not in json.dumps(payload, ensure_ascii=False)


async def test_guard_logs_duration_and_status(guard: ToolCallGuard, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tools"):
        await guard(_ctx("tools/call", {"name": "list_pets", "arguments": {"species": "犬"}}), _next_ok)

    payload = caplog.records[-1].__dict__["extra_fields"]
    assert payload["tool_name"] == "list_pets"
    assert payload["status"] == "ok"
    assert payload["params"] == {"species": "犬"}
    assert payload["duration_ms"] >= 0


async def test_guard_logs_validation_failures_with_the_error_code(
    guard: ToolCallGuard, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tools"):
        await guard(_ctx("tools/call", {"name": "list_pets", "arguments": {"page": 0}}), _next_ok)

    payload = caplog.records[-1].__dict__["extra_fields"]
    assert payload["status"] == "error"
    assert payload["error_code"] == "VALIDATION_ERROR"
