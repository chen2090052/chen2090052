"""REST client: request shaping, retries, and error classification."""

from __future__ import annotations

import json

import httpx
import pytest

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.errors import (
    BackendAPIError,
    BackendInvalidResponseError,
    BackendTimeoutError,
    BackendUnavailableError,
    ErrorCode,
)
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from tests.conftest import FAKE_BASE_URL, StubBackend, error_envelope, make_pet, make_pets_data, ok_envelope


@pytest.fixture
def fast_settings() -> Settings:
    """No backoff, so retry behaviour is observable without slow tests."""
    return Settings(base_url=FAKE_BASE_URL, max_retries=2, backoff_seconds=0)


@pytest.fixture
def client(fast_settings: Settings, stub: StubBackend) -> PetHospitalRestClient:
    return PetHospitalRestClient(fast_settings, transport=stub.transport())


# --------------------------------------------------------------------------- #
# Request shaping
# --------------------------------------------------------------------------- #


async def test_list_pets_hits_the_expected_path(client: PetHospitalRestClient, stub: StubBackend) -> None:
    await client.list_pets({})
    assert stub.last_request.method == "GET"
    assert stub.last_request.url.path == "/api/v1/pets"


async def test_every_supported_query_parameter_is_forwarded(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    params = {
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
    await client.list_pets(params)

    sent = dict(httpx.URL(stub.last_request.url).params)
    assert sent == {key: str(value) for key, value in params.items()}
    assert len(sent) == 14


async def test_no_unrequested_parameters_are_sent(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    await client.list_pets({"page": 1})
    assert dict(httpx.URL(stub.last_request.url).params) == {"page": "1"}


async def test_chinese_values_are_url_encoded_round_trip(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    await client.list_pets({"species": "爬宠"})
    assert dict(httpx.URL(stub.last_request.url).params)["species"] == "爬宠"


async def test_successful_response_returns_the_data_object(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    expected = make_pets_data(total=42, page=2, page_size=10)
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope(expected))

    data = await client.list_pets({})

    assert data == expected
    assert set(data) == {"items", "total", "page", "pageSize", "totalPages", "totalCost"}


async def test_health_hits_the_health_path(client: PetHospitalRestClient, stub: StubBackend) -> None:
    data = await client.health()
    assert stub.last_request.url.path == "/health"
    assert data["status"] == "healthy"


# --------------------------------------------------------------------------- #
# Pet sub-resources
# --------------------------------------------------------------------------- #


async def test_get_pet_hits_the_path_with_an_encoded_id(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    await client.get_pet("PET-000001")
    assert stub.last_request.method == "GET"
    assert stub.last_request.url.path == "/api/v1/pets/PET-000001"


async def test_get_pet_url_encodes_exotic_ids(client: PetHospitalRestClient, stub: StubBackend) -> None:
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope(make_pet(pet_id="A/B C")))
    await client.get_pet("A/B C")
    assert b"/api/v1/pets/A%2FB%20C" in stub.last_request.url.raw_path


@pytest.mark.parametrize(
    ("method", "suffix"),
    [("get_pet_summary", "/summary"), ("list_pet_records", "/records"), ("list_pet_charges", "/charges")],
)
async def test_pet_subresources_hit_the_expected_paths(
    client: PetHospitalRestClient, stub: StubBackend, method: str, suffix: str
) -> None:
    await getattr(client, method)("PET-000001")
    assert stub.last_request.method == "GET"
    assert stub.last_request.url.path == f"/api/v1/pets/PET-000001{suffix}"


async def test_add_pet_record_posts_json_without_retry(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(503, json=error_envelope(503, "busy"))

    with pytest.raises(BackendAPIError):
        await client.add_pet_record("PET-000001", {"doctor": "李医生", "charge": 380})

    assert stub.last_request.method == "POST"
    assert stub.last_request.url.path == "/api/v1/pets/PET-000001/records"
    assert json.loads(stub.last_request.content) == {"doctor": "李医生", "charge": 380}
    assert len(stub.requests) == 1, "a write must not be retried even on a 5xx"


async def test_add_pet_charge_posts_json_without_retry(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(503, json=error_envelope(503, "busy"))

    with pytest.raises(BackendAPIError):
        await client.add_pet_charge("PET-000001", {"item": "血常规", "amount": 180})

    assert stub.last_request.method == "POST"
    assert stub.last_request.url.path == "/api/v1/pets/PET-000001/charges"
    assert json.loads(stub.last_request.content) == {"item": "血常规", "amount": 180}
    assert len(stub.requests) == 1


async def test_stats_and_system_endpoints_hit_the_expected_paths(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    await client.get_stats({"top": 3})
    assert stub.last_request.method == "GET"
    assert stub.last_request.url.path == "/api/v1/stats"
    assert dict(httpx.URL(stub.last_request.url).params) == {"top": "3"}

    await client.get_meta()
    assert stub.last_request.url.path == "/api/v1/meta"

    await client.get_endpoints()
    assert stub.last_request.url.path == "/api/v1/endpoints"


# --------------------------------------------------------------------------- #
# HTTP error classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_client_errors_become_backend_api_error(
    client: PetHospitalRestClient, stub: StubBackend, status: int
) -> None:
    stub.responder = lambda _request: httpx.Response(status, json=error_envelope(status, "bad request"))

    with pytest.raises(BackendAPIError) as excinfo:
        await client.list_pets({})

    assert excinfo.value.code is ErrorCode.BACKEND_API_ERROR
    assert excinfo.value.details["status_code"] == status


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_server_errors_become_backend_api_error_after_retries(
    client: PetHospitalRestClient, stub: StubBackend, status: int
) -> None:
    stub.responder = lambda _request: httpx.Response(status, json=error_envelope(status, "upstream down"))

    with pytest.raises(BackendAPIError):
        await client.list_pets({})

    assert len(stub.requests) == 3  # 1 initial + 2 retries


async def test_client_errors_are_not_retried(client: PetHospitalRestClient, stub: StubBackend) -> None:
    stub.responder = lambda _request: httpx.Response(400, json=error_envelope(400, "bad"))

    with pytest.raises(BackendAPIError):
        await client.list_pets({})

    assert len(stub.requests) == 1


async def test_retry_succeeds_after_a_transient_server_error(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    attempts = {"n": 0}

    def responder(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, json=error_envelope(503, "try again"))
        return httpx.Response(200, json=ok_envelope(make_pets_data()))

    stub.responder = responder
    data = await client.list_pets({})

    assert attempts["n"] == 2
    assert "items" in data


async def test_non_200_envelope_code_becomes_backend_api_error(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    """HTTP 200 but the Go envelope reports a business failure."""
    stub.responder = lambda _request: httpx.Response(
        200, json={"code": 400, "message": "species 必须是 犬/猫/兔 之一", "time": "t"}
    )

    with pytest.raises(BackendAPIError) as excinfo:
        await client.list_pets({})

    assert excinfo.value.details["upstream_code"] == 400
    assert excinfo.value.details["upstream_message"] == "species 必须是 犬/猫/兔 之一"


# --------------------------------------------------------------------------- #
# Transport failures
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("too slow"),
        httpx.ConnectTimeout("too slow"),
        httpx.PoolTimeout("too slow"),
    ],
)
async def test_timeouts_become_backend_timeout(
    client: PetHospitalRestClient, stub: StubBackend, exc: Exception
) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        raise exc

    stub.responder = responder

    with pytest.raises(BackendTimeoutError) as excinfo:
        await client.list_pets({})

    assert excinfo.value.code is ErrorCode.BACKEND_TIMEOUT
    assert excinfo.value.details["attempts"] == 3
    assert len(stub.requests) == 3


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadError("connection reset"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
)
async def test_connection_failures_become_backend_unavailable(
    client: PetHospitalRestClient, stub: StubBackend, exc: Exception
) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        raise exc

    stub.responder = responder

    with pytest.raises(BackendUnavailableError) as excinfo:
        await client.list_pets({})

    assert excinfo.value.code is ErrorCode.BACKEND_UNAVAILABLE
    assert len(stub.requests) == 3


async def test_zero_retries_means_a_single_attempt(settings: Settings, stub: StubBackend) -> None:
    no_retry = settings.model_copy(update={"max_retries": 0, "backoff_seconds": 0})
    client = PetHospitalRestClient(no_retry, transport=stub.transport())

    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    stub.responder = responder

    with pytest.raises(BackendUnavailableError):
        await client.list_pets({})
    assert len(stub.requests) == 1


# --------------------------------------------------------------------------- #
# Malformed responses
# --------------------------------------------------------------------------- #


async def test_non_json_body_becomes_invalid_response(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, text="<html>oops</html>")

    with pytest.raises(BackendInvalidResponseError) as excinfo:
        await client.list_pets({})

    assert excinfo.value.details["reason"] == "response body is not valid JSON"


async def test_json_array_body_becomes_invalid_response(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, json=[1, 2, 3])

    with pytest.raises(BackendInvalidResponseError):
        await client.list_pets({})


async def test_envelope_without_data_becomes_invalid_response(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, json={"code": 200, "message": "ok"})

    with pytest.raises(BackendInvalidResponseError) as excinfo:
        await client.list_pets({})

    assert "no 'data' object" in excinfo.value.details["reason"]


async def test_data_of_the_wrong_type_becomes_invalid_response(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, json=ok_envelope(["not", "an", "object"]))

    with pytest.raises(BackendInvalidResponseError):
        await client.list_pets({})


async def test_truncated_json_becomes_invalid_response(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(200, text='{"code":200,"data":{"items":[')

    with pytest.raises(BackendInvalidResponseError):
        await client.list_pets({})


# --------------------------------------------------------------------------- #
# Leak prevention
# --------------------------------------------------------------------------- #


async def test_backend_error_text_does_not_leak_http_specifics(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    stub.responder = lambda _request: httpx.Response(500, text="<html>nginx internal error</html>")

    with pytest.raises(BackendAPIError) as excinfo:
        await client.list_pets({})

    rendered = excinfo.value.to_json()
    assert "<html>" not in rendered
    assert "nginx" not in rendered
    assert excinfo.value.details["upstream_message"] is None


async def test_transport_error_message_does_not_leak(
    client: PetHospitalRestClient, stub: StubBackend
) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed to connect to /secret/socket/path.sock")

    stub.responder = responder

    with pytest.raises(BackendUnavailableError) as excinfo:
        await client.list_pets({})

    assert "secret" not in excinfo.value.to_json()
    assert "socket" not in excinfo.value.to_json()


async def test_client_is_usable_as_an_async_context_manager(fast_settings: Settings) -> None:
    async with PetHospitalRestClient(fast_settings, transport=httpx.MockTransport(StubBackend._default)) as client:
        assert await client.health()
