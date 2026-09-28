"""Structured logging: required fields and recursive redaction of owner data."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest

from pet_hospital_mcp.logging_config import (
    REDACTED,
    JsonFormatter,
    configure_logging,
    is_sensitive_key,
    log_tool_call,
    redact,
)

SECRET_PHONE = "13800001111"
SECRET_ADDR = "北京市朝阳区建国路1号"
SECRET_CHIP = "CHIP-000001"


@pytest.mark.parametrize(
    "key",
    [
        "ownerPhone",
        "owner_phone",
        "OWNERPHONE",
        "owner-phone",
        "ownerAddr",
        "owner_addr",
        "chipNo",
        "chip_no",
        "OWNERADDR",
    ],
)
def test_sensitive_keys_are_recognised_in_both_spellings(key: str) -> None:
    assert is_sensitive_key(key) is True


@pytest.mark.parametrize("key", ["ownerName", "name", "phone", "addr", "chip", "species"])
def test_non_sensitive_keys_are_untouched(key: str) -> None:
    assert is_sensitive_key(key) is False


def test_redaction_masks_camel_and_snake_case() -> None:
    result = redact(
        {
            "ownerPhone": SECRET_PHONE,
            "owner_phone": SECRET_PHONE,
            "ownerAddr": SECRET_ADDR,
            "owner_addr": SECRET_ADDR,
            "chipNo": SECRET_CHIP,
            "chip_no": SECRET_CHIP,
        }
    )
    assert set(result.values()) == {REDACTED}


def test_redaction_is_recursive_through_nested_objects_and_arrays() -> None:
    payload = {
        "items": [
            {"id": "PET-000001", "ownerPhone": SECRET_PHONE, "owner": {"ownerAddr": SECRET_ADDR}},
            {"id": "PET-000002", "charges": [{"item": "检查", "chipNo": SECRET_CHIP}]},
        ],
        "meta": {"nested": {"deeper": [{"Owner_Phone": SECRET_PHONE}]}},
    }

    serialised = json.dumps(redact(payload), ensure_ascii=False)

    for secret in (SECRET_PHONE, SECRET_ADDR, SECRET_CHIP):
        assert secret not in serialised
    # Non-sensitive values survive.
    assert "PET-000001" in serialised
    assert "检查" in serialised


def test_redaction_leaves_scalars_alone() -> None:
    assert redact("plain") == "plain"
    assert redact(7) == 7
    assert redact(None) is None
    assert redact(True) is True


def test_redaction_stringifies_unknown_objects() -> None:
    class Opaque:
        def __repr__(self) -> str:  # pragma: no cover - only used if called
            return "Opaque()"

    assert isinstance(redact(Opaque()), str)


def _captured(caplog: pytest.LogCaptureFixture, record: logging.LogRecord) -> dict:
    assert record.message == "tool_call" or record.getMessage() == "tool_call"
    return json.loads(JsonFormatter().format(record))


def test_log_line_contains_the_required_fields(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tools"):
        log_tool_call(
            tool_name="list_pets",
            params={"species": "犬", "page": 2},
            status="ok",
            duration_ms=12.3456,
        )

    payload = _captured(caplog, caplog.records[-1])
    assert payload["tool_name"] == "list_pets"
    assert payload["status"] == "ok"
    assert payload["duration_ms"] == 12.346
    assert payload["params"] == {"species": "犬", "page": 2}
    assert isinstance(payload["timestamp"], str) and "T" in payload["timestamp"]
    assert isinstance(payload["level"], str)


def test_sensitive_params_never_reach_the_log_line(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tools"):
        log_tool_call(
            tool_name="list_pets",
            params={
                "ownerPhone": SECRET_PHONE,
                "owner_phone": SECRET_PHONE,
                "ownerAddr": SECRET_ADDR,
                "chipNo": SECRET_CHIP,
                "species": "犬",
            },
            status="ok",
            duration_ms=1.0,
        )

    record = caplog.records[-1]
    rendered = JsonFormatter().format(record)
    raw_message = record.getMessage()

    for secret in (SECRET_PHONE, SECRET_ADDR, SECRET_CHIP):
        assert secret not in rendered
        assert secret not in raw_message
        assert secret not in str(record.__dict__)

    payload = json.loads(rendered)
    assert payload["params"]["ownerPhone"] == REDACTED
    assert payload["params"]["owner_phone"] == REDACTED
    assert payload["params"]["ownerAddr"] == REDACTED
    assert payload["params"]["chipNo"] == REDACTED
    assert payload["params"]["species"] == "犬"


def test_error_log_records_the_error_code(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tools"):
        log_tool_call(
            tool_name="list_pets",
            params={},
            status="error",
            duration_ms=3.0,
            error_code="BACKEND_TIMEOUT",
        )

    payload = _captured(caplog, caplog.records[-1])
    assert payload["status"] == "error"
    assert payload["error_code"] == "BACKEND_TIMEOUT"
    assert caplog.records[-1].levelno == logging.WARNING


def test_formatter_emits_a_single_json_line(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tools"):
        log_tool_call(tool_name="list_pets", params={"q": "多行\n关键词"}, status="ok", duration_ms=0.5)

    rendered = JsonFormatter().format(caplog.records[-1])
    assert "\n" not in rendered
    assert json.loads(rendered)["params"]["q"] == "多行\n关键词"


@pytest.fixture
def restoring_root_handlers() -> Iterator[None]:
    """Let a test call `configure_logging` without disturbing pytest's capture."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        yield
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)


@pytest.mark.parametrize("logger_name", ["httpx", "httpcore"])
def test_configure_logging_silences_http_transport_logging(
    logger_name: str, restoring_root_handlers: None
) -> None:
    """HTTPX logs the full outbound URL at INFO — query string included.

    Our redaction cannot reach those lines (they are pre-formatted strings, not
    structured data), and the query string carries `ownerPhone`/`ownerAddr`/
    `chipNo` verbatim. The transport loggers are therefore raised to WARNING.
    """
    configure_logging("INFO")

    assert not logging.getLogger(logger_name).isEnabledFor(logging.INFO)


def test_sensitive_query_parameters_are_never_logged_by_http(
    restoring_root_handlers: None,
) -> None:
    """Attach a handler to the transport logger and confirm nothing is emitted."""
    configure_logging("INFO")
    seen: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    transport_logger = logging.getLogger("httpx")
    handler = Capture()
    transport_logger.addHandler(handler)
    try:
        transport_logger.info(
            "HTTP Request: GET http://127.0.0.1:8080/api/v1/pets?ownerPhone=%s", SECRET_PHONE
        )
    finally:
        transport_logger.removeHandler(handler)

    assert seen == []
    assert SECRET_PHONE not in "".join(seen)


def test_configure_logging_keeps_our_own_logger_at_info(
    restoring_root_handlers: None, caplog: pytest.LogCaptureFixture
) -> None:
    configure_logging("INFO")

    assert logging.getLogger("pet_hospital_mcp.tools").isEnabledFor(logging.INFO)


def test_formatter_records_only_the_exception_class_name() -> None:
    try:
        raise ValueError(f"carrying {SECRET_PHONE}")
    except ValueError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())

    rendered = JsonFormatter().format(record)
    assert SECRET_PHONE not in rendered
    assert json.loads(rendered)["exception"] == "ValueError"
