"""JSON logging with recursive redaction of sensitive pet-owner data.

Every tool call emits exactly one JSON line containing at least
``timestamp``, ``tool_name``, ``params``, ``status`` and ``duration_ms``.

``params`` is scrubbed before it is serialised. Redaction is *recursive* — it
walks nested objects and arrays — and matches both camelCase and snake_case
spellings of each sensitive key, ignoring case and separators, so
``ownerPhone``, ``owner_phone``, ``OWNERPHONE`` and ``owner-phone`` are all
covered.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

#: Keys whose values must never reach a log sink.
SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {
        "ownerPhone".lower(),
        "ownerAddr".lower(),
        "chipNo".lower(),
    }
)

#: Replacement written in place of a sensitive value.
REDACTED = "***"

TOOL_LOGGER_NAME = "pet_hospital_mcp.tools"


def _normalize_key(key: str) -> str:
    """Fold a key to a comparison form: lowercase, separators removed.

    ``owner_phone`` / ``owner-phone`` / ``ownerPhone`` all become ``ownerphone``.
    """
    return "".join(ch for ch in key.lower() if ch.isalnum())


_SENSITIVE_NORMALIZED: frozenset[str] = frozenset(_normalize_key(k) for k in SENSITIVE_FIELDS)


def is_sensitive_key(key: str) -> bool:
    """True when ``key`` names a field that must be redacted."""
    return _normalize_key(key) in _SENSITIVE_NORMALIZED


def redact(value: Any) -> Any:
    """Return a copy of ``value`` with every sensitive field masked.

    Recurses through mappings and sequences. Unknown objects are stringified so
    they can never smuggle a repr containing raw data into the log line.
    """
    if isinstance(value, dict):
        return {
            key: (REDACTED if isinstance(key, str) and is_sensitive_key(key) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class JsonFormatter(logging.Formatter):
    """Render a log record as a single-line JSON object.

    ``record.extra_fields`` (if present) is merged in, and any ``params`` value
    found there is redacted before serialisation.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                payload[key] = redact(value) if key == "params" else redact(value)

        if record.exc_info and record.exc_info[0] is not None:
            # Only the exception class name: a formatted traceback could carry
            # payload fragments and local variable values.
            payload["exception"] = record.exc_info[0].__name__

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own handlers; let them propagate to the JSON root
    # instead of duplicating output.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # HTTPX logs the full outbound URL at INFO. Query strings carry sensitive
    # values (ownerPhone / ownerAddr / chipNo), which our own redaction cannot
    # reach — the message is a pre-formatted string, not structured data. Raise
    # the floor so request lines are never emitted; failures still surface
    # through our own structured error handling.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def log_tool_call(
    *,
    tool_name: str,
    params: dict[str, Any] | None,
    status: str,
    duration_ms: float,
    error_code: str | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Emit one structured JSON log line for a tool invocation.

    Args:
        tool_name: MCP tool name, e.g. ``list_pets``.
        params: Arguments the client supplied. Redacted before serialisation.
        status: ``"ok"`` or ``"error"``.
        duration_ms: Wall-clock duration of the call in milliseconds.
        error_code: The :class:`~pet_hospital_mcp.errors.ErrorCode` value when
            ``status`` is ``"error"``.
    """
    target = logger or logging.getLogger(TOOL_LOGGER_NAME)
    fields: dict[str, Any] = {
        "tool_name": tool_name,
        "params": redact(params or {}),
        "status": status,
        "duration_ms": round(duration_ms, 3),
    }
    if error_code is not None:
        fields["error_code"] = error_code

    level = logging.INFO if status == "ok" else logging.WARNING
    target.log(level, "tool_call", extra={"extra_fields": fields})
