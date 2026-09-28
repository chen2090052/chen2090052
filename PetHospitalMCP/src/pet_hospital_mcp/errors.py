"""Unified structured error contract for every MCP tool response.

Every failure — invalid tool input, upstream HTTP problem, malformed backend
payload, or an unexpected internal fault — is normalised into a single envelope::

    {"error": {"code": "ERROR_CODE", "message": "human readable", "details": {}}}

The envelope is delivered to MCP clients as the ``structuredContent`` of a
``CallToolResult`` whose ``is_error`` flag is set. That is the SDK 2.x convention
(``CallToolResult.is_error``, serialised on the wire as ``isError``) — v1's
``CallToolResult.isError`` Python attribute no longer exists.

Two rules drive the design:

* **Never leak internals.** Messages are fixed, human-readable strings. Raw
  exception text from HTTPX, Pydantic, the SDK, or a Python traceback is never
  placed in ``message``; structured, non-sensitive context goes in ``details``.
* **Never fail silently.** Any exception escaping a tool call is converted into
  ``INTERNAL_ERROR`` rather than propagating a stack trace to the client.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(str, Enum):
    """Error codes shared with MCP clients. Values are part of the public contract."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    """Tool input failed validation. The backend was never called."""

    BACKEND_TIMEOUT = "BACKEND_TIMEOUT"
    """The Go REST API did not answer within the configured timeout."""

    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    """The Go REST API could not be reached (connection refused, DNS, reset)."""

    BACKEND_API_ERROR = "BACKEND_API_ERROR"
    """The Go REST API answered with an HTTP error status or a non-200 envelope."""

    BACKEND_INVALID_RESPONSE = "BACKEND_INVALID_RESPONSE"
    """The Go REST API answered with something that is not valid JSON or does not
    match the modelled response shape."""

    INTERNAL_ERROR = "INTERNAL_ERROR"
    """An unexpected fault inside the MCP server."""


class ErrorEnvelope(BaseModel):
    """The ``error`` object itself."""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Top-level error payload, i.e. the ``structuredContent`` of a failed call."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorEnvelope


# --------------------------------------------------------------------------- #
# Exception hierarchy
# --------------------------------------------------------------------------- #


class PetHospitalToolError(Exception):
    """Base class for every error surfaced to an MCP client.

    Subclasses carry a stable :class:`ErrorCode` plus a safe, fixed message.
    ``details`` must only ever hold non-sensitive, structured context.
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    default_message: str = "An unexpected internal error occurred."

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(error=ErrorEnvelope(code=self.code, message=self.message, details=self.details))

    def to_json(self) -> str:
        """Envelope as a compact JSON string (used for the text content block)."""
        return json.dumps(self.to_response().model_dump(mode="json"), ensure_ascii=False)

    def to_call_tool_result(self) -> CallToolResult:
        """Render as a failed ``CallToolResult``.

        ``is_error=True`` is the SDK 2.x way of marking a tool call as failed.
        ``structured_content`` is set so machine clients get the typed envelope,
        and the same JSON is mirrored into a text block for clients that only
        read ``content``.
        """
        return CallToolResult(
            content=[TextContent(type="text", text=self.to_json())],
            structured_content=self.to_response().model_dump(mode="json"),
            is_error=True,
        )


class InputValidationError(PetHospitalToolError):
    """Tool arguments were rejected before any backend call was attempted."""

    code = ErrorCode.VALIDATION_ERROR
    default_message = "The tool input is invalid."


class BackendTimeoutError(PetHospitalToolError):
    code = ErrorCode.BACKEND_TIMEOUT
    default_message = "The Pet Hospital REST API did not respond in time."


class BackendUnavailableError(PetHospitalToolError):
    code = ErrorCode.BACKEND_UNAVAILABLE
    default_message = "The Pet Hospital REST API is unreachable."


class BackendAPIError(PetHospitalToolError):
    code = ErrorCode.BACKEND_API_ERROR
    default_message = "The Pet Hospital REST API returned an error."


class BackendInvalidResponseError(PetHospitalToolError):
    code = ErrorCode.BACKEND_INVALID_RESPONSE
    default_message = "The Pet Hospital REST API returned an unusable response."


class InternalError(PetHospitalToolError):
    code = ErrorCode.INTERNAL_ERROR
    default_message = "An unexpected internal error occurred."


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

ENVELOPE_ERROR_KEY = "error"


def is_error_envelope(value: Any) -> bool:
    """True when ``value`` already is one of our error envelopes.

    Used to tell our own structured failures apart from generic failures the SDK
    framework produces on its own (e.g. signature-level argument validation).
    """
    if not isinstance(value, dict):
        return False
    error = value.get(ENVELOPE_ERROR_KEY)
    if not isinstance(error, dict):
        return False
    return isinstance(error.get("code"), str) and isinstance(error.get("message"), str)


def normalize_exception(exc: BaseException) -> PetHospitalToolError:
    """Map an arbitrary exception onto the unified error contract.

    Anything already part of the contract is passed through untouched; everything
    else becomes ``INTERNAL_ERROR`` with a fixed message, so no exception text,
    module path, or traceback can reach the client.
    """
    if isinstance(exc, PetHospitalToolError):
        return exc
    return InternalError(details={"type": type(exc).__name__})
