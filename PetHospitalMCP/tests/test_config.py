"""Configuration: defaults, environment overrides, and invalid input."""

from __future__ import annotations

import pytest

from pet_hospital_mcp.config import (
    DEFAULT_BASE_URL,
    DEFAULT_MCP_HOST,
    DEFAULT_MCP_PORT,
    ConfigError,
    load_settings,
)


def test_defaults_when_environment_is_empty() -> None:
    settings = load_settings({})
    assert settings.base_url == DEFAULT_BASE_URL == "http://127.0.0.1:8080"
    assert settings.mcp_host == DEFAULT_MCP_HOST == "127.0.0.1"
    assert settings.mcp_port == DEFAULT_MCP_PORT


def test_defaults_bind_to_loopback_only() -> None:
    """The service has no auth, so the default must not be a public interface."""
    settings = load_settings({})
    assert settings.mcp_host == "127.0.0.1"
    assert settings.base_url.startswith("http://127.0.0.1")


def test_environment_overrides() -> None:
    settings = load_settings(
        {
            "PET_HOSPITAL_BASE_URL": "http://127.0.0.1:9090",
            "MCP_HOST": "0.0.0.0",
            "MCP_PORT": "9000",
            "MCP_PATH": "/custom-mcp",
            "PET_HOSPITAL_TIMEOUT": "3.5",
            "PET_HOSPITAL_MAX_RETRIES": "0",
            "PET_HOSPITAL_BACKOFF": "0",
            "MCP_LOG_LEVEL": "debug",
        }
    )
    assert settings.base_url == "http://127.0.0.1:9090"
    assert settings.mcp_host == "0.0.0.0"
    assert settings.mcp_port == 9000
    assert settings.mcp_path == "/custom-mcp"
    assert settings.request_timeout == 3.5
    assert settings.max_retries == 0
    assert settings.backoff_seconds == 0
    assert settings.log_level == "DEBUG"


def test_trailing_slash_is_stripped_from_base_url() -> None:
    assert load_settings({"PET_HOSPITAL_BASE_URL": "http://127.0.0.1:8080/"}).base_url == (
        "http://127.0.0.1:8080"
    )


def test_mcp_path_gets_a_leading_slash() -> None:
    assert load_settings({"MCP_PATH": "mcp"}).mcp_path == "/mcp"


def test_endpoint_url_composition() -> None:
    settings = load_settings({"MCP_HOST": "127.0.0.1", "MCP_PORT": "8765", "MCP_PATH": "/mcp"})
    assert settings.endpoint_url == "http://127.0.0.1:8765/mcp"


def test_blank_values_fall_back_to_defaults() -> None:
    """An empty variable (common in shell profiles) must not break startup."""
    settings = load_settings({"MCP_PORT": "   ", "PET_HOSPITAL_BASE_URL": ""})
    assert settings.mcp_port == DEFAULT_MCP_PORT
    assert settings.base_url == DEFAULT_BASE_URL


@pytest.mark.parametrize(
    ("environ", "expected_fragment"),
    [
        ({"PET_HOSPITAL_BASE_URL": "127.0.0.1:8080"}, "PET_HOSPITAL_BASE_URL"),
        ({"PET_HOSPITAL_BASE_URL": "ftp://host"}, "PET_HOSPITAL_BASE_URL"),
        ({"MCP_PORT": "not-a-number"}, "MCP_PORT"),
        ({"MCP_PORT": "70000"}, "mcp_port"),
        ({"MCP_PORT": "0"}, "mcp_port"),
        ({"MCP_LOG_LEVEL": "chatty"}, "MCP_LOG_LEVEL"),
        ({"PET_HOSPITAL_TIMEOUT": "0"}, "request_timeout"),
        ({"PET_HOSPITAL_TIMEOUT": "-1"}, "request_timeout"),
        ({"PET_HOSPITAL_MAX_RETRIES": "99"}, "max_retries"),
        ({"PET_HOSPITAL_BACKOFF": "abc"}, "PET_HOSPITAL_BACKOFF"),
    ],
)
def test_invalid_values_raise_config_error(environ: dict[str, str], expected_fragment: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_settings(environ)
    assert expected_fragment in str(excinfo.value)


def test_config_error_does_not_leak_pydantic_wording() -> None:
    """Config errors must name the field, not dump a Pydantic trace."""
    with pytest.raises(ConfigError) as excinfo:
        load_settings({"MCP_PORT": "70000"})
    message = str(excinfo.value)
    assert "pydantic" not in message.lower()
    assert "traceback" not in message.lower()
    assert "mcp_port" in message
