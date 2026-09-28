"""The unified error contract."""

from __future__ import annotations

import json

import pytest
from mcp.types import CallToolResult

from pet_hospital_mcp.errors import (
    BackendAPIError,
    BackendInvalidResponseError,
    BackendTimeoutError,
    BackendUnavailableError,
    ErrorCode,
    ErrorResponse,
    InputValidationError,
    InternalError,
    PetHospitalToolError,
    is_error_envelope,
    normalize_exception,
)

ALL_ERROR_CLASSES = [
    InputValidationError,
    BackendTimeoutError,
    BackendUnavailableError,
    BackendAPIError,
    BackendInvalidResponseError,
    InternalError,
]


def test_every_error_code_is_covered_by_a_class() -> None:
    declared = {cls.code for cls in ALL_ERROR_CLASSES}
    assert declared == set(ErrorCode)


@pytest.mark.parametrize("error_class", ALL_ERROR_CLASSES)
def test_envelope_shape(error_class: type[PetHospitalToolError]) -> None:
    error = error_class(details={"k": "v"})
    payload = error.to_response().model_dump(mode="json")

    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message", "details"}
    assert payload["error"]["code"] == error_class.code.value
    assert isinstance(payload["error"]["message"], str) and payload["error"]["message"]
    assert payload["error"]["details"] == {"k": "v"}

    # Round-trips through the declared response model.
    assert ErrorResponse.model_validate(payload).error.code is error_class.code


@pytest.mark.parametrize("error_class", ALL_ERROR_CLASSES)
def test_call_tool_result_is_marked_as_error(error_class: type[PetHospitalToolError]) -> None:
    """SDK 2.x marks failure with `is_error` (wire name `isError`)."""
    result = error_class().to_call_tool_result()

    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert result.result_type == "complete"

    # The envelope is available both structurally and as text.
    assert is_error_envelope(result.structured_content)
    assert result.structured_content["error"]["code"] == error_class.code.value
    assert json.loads(result.content[0].text) == result.structured_content


def test_serialised_wire_field_is_iserror() -> None:
    payload = json.loads(BackendTimeoutError().to_call_tool_result().model_dump_json(by_alias=True))
    assert payload["isError"] is True


def test_details_default_to_an_empty_object() -> None:
    assert InternalError().to_response().error.details == {}


def test_error_details_are_json_serialisable() -> None:
    error = BackendAPIError(details={"status_code": 500, "upstream_message": "boom", "nested": {"a": [1, 2]}})
    assert json.loads(error.to_json())["error"]["details"]["status_code"] == 500


# --------------------------------------------------------------------------- #
# Leak prevention
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("error_class", ALL_ERROR_CLASSES)
def test_default_messages_never_contain_internals(error_class: type[PetHospitalToolError]) -> None:
    message = error_class().to_response().error.message
    lowered = message.lower()
    for forbidden in ("traceback", "httpx", "pydantic", "mcp.", ".py", "exception"):
        assert forbidden not in lowered


def test_normalize_exception_passes_through_contract_errors() -> None:
    original = BackendTimeoutError(details={"path": "/api/v1/pets"})
    assert normalize_exception(original) is original


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("internal detail that must not escape"),
        ValueError("secret-ish"),
        KeyError("ownerPhone"),
        TimeoutError("httpx.ReadTimeout"),
    ],
)
def test_normalize_exception_hides_unexpected_text(exc: BaseException) -> None:
    normalized = normalize_exception(exc)

    assert isinstance(normalized, InternalError)
    assert normalized.code is ErrorCode.INTERNAL_ERROR
    # The original message must not survive anywhere in the envelope.
    assert str(exc) not in normalized.to_json()
    assert "Traceback" not in normalized.to_json()


def test_normalize_exception_records_only_the_class_name() -> None:
    normalized = normalize_exception(RuntimeError("boom"))
    assert normalized.details == {"type": "RuntimeError"}


# --------------------------------------------------------------------------- #
# Envelope detection
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "value",
    [
        None,
        "string",
        [],
        {},
        {"error": "not-a-dict"},
        {"error": {"code": 1, "message": "x"}},
        {"error": {"code": "X"}},
        {"other": {"code": "X", "message": "y"}},
    ],
)
def test_is_error_envelope_rejects_non_envelopes(value: object) -> None:
    assert is_error_envelope(value) is False


def test_is_error_envelope_accepts_our_envelope() -> None:
    assert is_error_envelope(InputValidationError().to_response().model_dump(mode="json")) is True
