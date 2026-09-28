"""Async HTTP client for the Go Pet Hospital REST API.

This is the *only* place in the project that talks to the upstream service. It
owns transport concerns — timeout, retry, status mapping, envelope unwrapping —
so tool modules only deal with validated Python data.

Failures are translated into the project's error contract; nothing from HTTPX or
from JSON decoding is allowed to escape as-is.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import httpx

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.errors import (
    BackendAPIError,
    BackendInvalidResponseError,
    BackendTimeoutError,
    BackendUnavailableError,
)

logger = logging.getLogger(__name__)

#: The Go service wraps every response in `{"code", "message", "data", "time"}`
#: and reports success with HTTP 200 plus `code == 200`.
SUCCESS_CODE = 200

RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})


class PetHospitalRestClient:
    """Thin, typed wrapper over the Go REST API.

    Args:
        settings: Supplies the base URL, timeout and retry budget.
        transport: Optional ``httpx`` transport. Tests pass an
            ``httpx.MockTransport``; production leaves it as ``None`` so HTTPX
            uses its default network transport.
    """

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx.Timeout(settings.request_timeout),
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        """Release the underlying connection pool."""
        await self._client.aclose()

    async def __aenter__(self) -> PetHospitalRestClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ #
    # Endpoints
    # ------------------------------------------------------------------ #

    async def health(self) -> dict[str, Any]:
        """``GET /health`` — liveness probe for the upstream service."""
        return await self._request("GET", "/health")

    async def list_pets(self, params: dict[str, Any]) -> dict[str, Any]:
        """``GET /api/v1/pets`` with already-validated query parameters.

        Args:
            params: Query parameters. ``None`` values must be removed by the
                caller; only present keys are sent on the wire.

        Returns:
            The unwrapped ``data`` object: ``items``, ``total``, ``page``,
            ``pageSize``, ``totalPages``, ``totalCost``.
        """
        return await self._request("GET", "/api/v1/pets", params=params)

    async def get_pet(self, pet_id: str) -> dict[str, Any]:
        """``GET /api/v1/pets/{id}`` — one pet档案 including its records and charges."""
        return await self._request("GET", self._pet_path(pet_id))

    async def get_pet_summary(self, pet_id: str) -> dict[str, Any]:
        """``GET /api/v1/pets/{id}/summary`` — the AI-facing care summary."""
        return await self._request("GET", f"{self._pet_path(pet_id)}/summary")

    async def list_pet_records(self, pet_id: str) -> dict[str, Any]:
        """``GET /api/v1/pets/{id}/records`` — one pet's 历史病历."""
        return await self._request("GET", f"{self._pet_path(pet_id)}/records")

    async def list_pet_charges(self, pet_id: str) -> dict[str, Any]:
        """``GET /api/v1/pets/{id}/charges`` — one pet's 消费明细."""
        return await self._request("GET", f"{self._pet_path(pet_id)}/charges")

    async def add_pet_record(self, pet_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/v1/pets/{id}/records`` — append one 病历 entry.

        Never retried: the operation is not idempotent, so a second attempt
        could write the same record twice.
        """
        return await self._request(
            "POST", f"{self._pet_path(pet_id)}/records", json_body=payload, retry=False
        )

    async def add_pet_charge(self, pet_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/v1/pets/{id}/charges`` — append one 消费明细 line.

        Never retried: same non-idempotency reasoning as :meth:`add_pet_record`.
        """
        return await self._request(
            "POST", f"{self._pet_path(pet_id)}/charges", json_body=payload, retry=False
        )

    async def get_stats(self, params: dict[str, Any]) -> dict[str, Any]:
        """``GET /api/v1/stats`` — aggregate and leaderboard statistics."""
        return await self._request("GET", "/api/v1/stats", params=params)

    async def get_meta(self) -> dict[str, Any]:
        """``GET /api/v1/meta`` — the backend's accepted enum values and fields."""
        return await self._request("GET", "/api/v1/meta")

    async def get_endpoints(self) -> dict[str, Any]:
        """``GET /api/v1/endpoints`` — the backend's self-description."""
        return await self._request("GET", "/api/v1/endpoints")

    # ------------------------------------------------------------------ #
    # Transport
    # ------------------------------------------------------------------ #

    @staticmethod
    def _pet_path(pet_id: str) -> str:
        """Build ``/api/v1/pets/{id}`` with the id safely URL-encoded."""
        return f"/api/v1/pets/{quote(pet_id, safe='')}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retry: bool = True,
    ) -> dict[str, Any]:
        """Perform one logical request, retrying transient failures.

        Retries are limited to idempotent-safe conditions: timeouts, connection
        failures and 5xx responses. A 4xx is returned to the caller immediately
        because retrying cannot change the outcome.

        Raised writes pass ``retry=False``: the Go service owns the write, so a
        duplicate attempt could apply the same mutation twice. Those requests go
        out exactly once.
        """
        attempts = (self._settings.max_retries + 1) if retry else 1
        last_error: Exception | None = None
        request_kwargs: dict[str, Any] = {}
        if params:
            request_kwargs["params"] = params
        if json_body is not None:
            request_kwargs["json"] = json_body

        for attempt in range(attempts):
            try:
                response = await self._client.request(method, path, **request_kwargs)
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.debug("backend timeout on %s %s (attempt %d/%d)", method, path, attempt + 1, attempts)
            except httpx.TransportError as exc:
                last_error = exc
                logger.debug("backend transport error on %s %s (attempt %d/%d)", method, path, attempt + 1, attempts)
            else:
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < attempts - 1:
                    logger.debug("backend %d on %s %s, retrying", response.status_code, method, path)
                    last_error = None
                else:
                    return self._parse(response, path)

            if attempt < attempts - 1:
                await asyncio.sleep(self._settings.backoff_seconds * (2**attempt))

        # Retries exhausted: classify by what went wrong last.
        if isinstance(last_error, httpx.TimeoutException):
            raise BackendTimeoutError(
                details={"path": path, "timeout_seconds": self._settings.request_timeout, "attempts": attempts}
            )
        raise BackendUnavailableError(details={"path": path, "attempts": attempts})

    def _parse(self, response: httpx.Response, path: str) -> dict[str, Any]:
        """Validate status, decode JSON, unwrap the envelope, return ``data``."""
        if response.status_code >= 400:
            raise BackendAPIError(
                details={
                    "path": path,
                    "status_code": response.status_code,
                    "upstream_message": _safe_upstream_message(response),
                }
            )

        try:
            payload = response.json()
        except ValueError:
            raise BackendInvalidResponseError(
                details={"path": path, "reason": "response body is not valid JSON"}
            ) from None

        if not isinstance(payload, dict):
            raise BackendInvalidResponseError(
                details={"path": path, "reason": "response body is not a JSON object"}
            )

        code = payload.get("code")
        if code != SUCCESS_CODE:
            raise BackendAPIError(
                details={
                    "path": path,
                    "status_code": response.status_code,
                    "upstream_code": code if isinstance(code, int) else None,
                    "upstream_message": _safe_upstream_message(response, payload),
                }
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise BackendInvalidResponseError(
                details={"path": path, "reason": "response envelope has no 'data' object"}
            )

        return data


def _safe_upstream_message(response: httpx.Response, payload: dict[str, Any] | None = None) -> str | None:
    """Extract the backend's own ``message`` field, if it has one.

    The Go service writes business-level messages (e.g. "species must be one of
    ..."). These are its own strings, not HTTPX/Pydantic/SDK internals, so they
    are safe to forward as structured detail. Anything unexpected is dropped and
    the raw body is never logged or returned.
    """
    if payload is None:
        try:
            candidate = response.json()
        except ValueError:
            return None
        if not isinstance(candidate, dict):
            return None
        payload = candidate

    message = payload.get("message")
    if isinstance(message, str) and message:
        return message[:500]
    return None
