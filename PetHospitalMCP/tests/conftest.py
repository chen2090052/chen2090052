"""Shared test fixtures.

**No test in this suite touches a real Pet Hospital service.** Every backend
call is served by an :class:`httpx.MockTransport` stub, including the end-to-end
cases, which run the real ASGI app against that stub.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
import uvicorn
from mcp.server.mcpserver import MCPServer

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.server import build_app, build_server

#: Never actually contacted — every request goes through a mock transport.
FAKE_BASE_URL = "http://127.0.0.1:18080"


def ok_envelope(data: Any) -> dict[str, Any]:
    """Wrap ``data`` in the Go service's success envelope."""
    return {"code": 200, "message": "ok", "data": data, "time": "2026-09-17T00:00:00+08:00"}


def error_envelope(code: int, message: str) -> dict[str, Any]:
    """The Go service's error envelope — note there is no `data` key."""
    return {"code": code, "message": message, "time": "2026-09-17T00:00:00+08:00"}


def make_pet(pet_id: str = "PET-000001", **overrides: Any) -> dict[str, Any]:
    """A pet档案 shaped like the real backend's, with `records`/`charges` null."""
    pet: dict[str, Any] = {
        "id": pet_id,
        "name": "旺财",
        "species": "犬",
        "breed": "金毛",
        "gender": "公",
        "ageMonths": 36,
        "color": "金黄",
        "chipNo": "CHIP-000001",
        "ownerName": "张三",
        "ownerPhone": "13800001111",
        "ownerAddr": "北京市朝阳区建国路1号",
        "doctor": "李医生",
        "disease": "急性肠胃炎",
        "status": "待就诊",
        "allergy": "无",
        "records": None,
        "charges": None,
        "totalCost": 0,
        "visitCount": 0,
        "createdAt": "2026-09-15T13:50:11+08:00",
        "updatedAt": "2026-09-15T13:50:11+08:00",
    }
    pet.update(overrides)
    return pet


def make_pets_data(
    items: list[dict[str, Any]] | None = None,
    *,
    total: int | None = None,
    page: int = 1,
    page_size: int = 20,
    total_cost: float | None = None,
) -> dict[str, Any]:
    """The `data` object of `GET /api/v1/pets`."""
    resolved_items = [make_pet()] if items is None else items
    resolved_total = len(resolved_items) if total is None else total
    return {
        "items": resolved_items,
        "total": resolved_total,
        "page": page,
        "pageSize": page_size,
        "totalPages": (resolved_total + page_size - 1) // page_size if page_size else 0,
        "totalCost": sum(item.get("totalCost", 0) or 0 for item in resolved_items)
        if total_cost is None
        else total_cost,
    }


def make_record(record_id: str = "MR-2026-0001", **overrides: Any) -> dict[str, Any]:
    """One 历史病历 entry, shaped like the Go backend's PetRecord."""
    record: dict[str, Any] = {
        "id": record_id,
        "visitDate": "2026-09-12",
        "doctor": "李医生",
        "diagnosis": "急性肠胃炎",
        "symptoms": "呕吐、腹泻、精神不佳",
        "treatment": "补液、消炎、控制饮食",
        "prescription": ["阿莫西林", "益生菌"],
        "weightKg": 24.5,
        "temperature": 38.6,
        "followUp": "一周后复查",
        "charge": 380.0,
        "createdAt": "2026-09-12T10:30:00+08:00",
    }
    record.update(overrides)
    return record


def make_charge(charge_id: str = "CH-2026-0001", **overrides: Any) -> dict[str, Any]:
    """One 消费明细 line, shaped like the Go backend's PetCharge."""
    charge: dict[str, Any] = {
        "id": charge_id,
        "item": "血常规检查",
        "category": "检查",
        "amount": 180.0,
        "doctor": "李医生",
        "date": "2026-09-12",
    }
    charge.update(overrides)
    return charge


def make_records_data(
    pet_id: str = "PET-000001",
    records: list[dict[str, Any]] | None = None,
    *,
    pet_name: str = "旺财",
) -> dict[str, Any]:
    """The `data` object of `GET /api/v1/pets/{id}/records`."""
    resolved = [make_record()] if records is None else records
    return {
        "petId": pet_id,
        "petName": pet_name,
        "ownerName": "张三",
        "count": len(resolved),
        "records": resolved,
        "historyText": "2026-09-12 急性肠胃炎，补液消炎，费用380元。",
    }


def make_charges_data(
    pet_id: str = "PET-000001",
    charges: list[dict[str, Any]] | None = None,
    *,
    pet_name: str = "旺财",
) -> dict[str, Any]:
    """The `data` object of `GET /api/v1/pets/{id}/charges`."""
    resolved = [make_charge()] if charges is None else charges
    return {
        "petId": pet_id,
        "petName": pet_name,
        "count": len(resolved),
        "totalCost": sum(item.get("amount", 0) or 0 for item in resolved),
        "costByCategory": {"检查": 180.0},
        "charges": resolved,
    }


def make_summary(**overrides: Any) -> dict[str, Any]:
    """The `data` object of `GET /api/v1/pets/{id}/summary`."""
    summary: dict[str, Any] = {
        "id": "PET-000001",
        "name": "旺财",
        "species": "犬",
        "breed": "金毛",
        "gender": "公",
        "ownerName": "张三",
        "ownerPhone": "13800001111",
        "doctor": "李医生",
        "disease": "急性肠胃炎",
        "status": "就诊中",
        "visitCount": 3,
        "chargeCount": 5,
        "totalCost": 1560.0,
        "avgCostPerVisit": 520.0,
        "maxSingleCharge": 800.0,
        "costByCategory": {"检查": 560.0, "药品": 1000.0},
        "costByDoctor": {"李医生": 1560.0},
        "firstVisit": "2026-08-01",
        "lastVisit": "2026-09-12",
        "historyText": "旺财因急性肠胃炎在 2026-08-01 首次就诊…",
    }
    summary.update(overrides)
    return summary


def make_stats(**overrides: Any) -> dict[str, Any]:
    """The `data` object of `GET /api/v1/stats` (counts are ints)."""
    pet = make_pet()
    pet["totalCost"] = 1560.0
    pet["visitCount"] = 3
    pet["records"] = [make_record()]
    pet["charges"] = [make_charge()]
    stats: dict[str, Any] = {
        "totalPets": 3,
        "totalRecords": 9,
        "totalCharges": 15,
        "totalRevenue": 4680.0,
        "averageCost": 520.0,
        "maxCost": 1560.0,
        "bySpecies": {"犬": 2, "猫": 1},
        "byStatus": {"就诊中": 2, "已康复": 1},
        "byDoctor": {"李医生": 3},
        "revenueByDoctor": {"李医生": 4680.0},
        "topSpenders": [pet],
    }
    stats.update(overrides)
    return stats


def make_meta(**overrides: Any) -> dict[str, Any]:
    """The `data` object of `GET /api/v1/meta`."""
    meta: dict[str, Any] = {
        "species": ["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"],
        "status": ["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"],
        "gender": ["公", "母"],
        "chargeCategories": ["检查", "药品", "手术", "住院", "疫苗", "护理", "其他"],
        "sortFields": ["id", "name", "ownerName", "species", "doctor", "disease", "status", "totalCost", "visitCount", "createdAt", "updatedAt"],
        "fields": [{"name": "q", "desc": "全文检索"}, {"name": "species", "desc": "宠物种类"}],
    }
    meta.update(overrides)
    return meta


def make_endpoints(**overrides: Any) -> dict[str, Any]:
    """The `data` object of `GET /api/v1/endpoints`."""
    endpoints: dict[str, Any] = {
        "count": 2,
        "endpoints": [
            {"Method": "GET", "Path": "/api/v1/pets", "Desc": "分页查询宠物", "Example": "/api/v1/pets?page=1"},
            {"Method": "GET", "Path": "/api/v1/meta", "Desc": "元信息", "Example": "/api/v1/meta"},
        ],
    }
    endpoints.update(overrides)
    return endpoints


class StubBackend:
    """Records outbound requests and answers them with scripted responses.

    By default it answers `GET /api/v1/pets` and `GET /health` successfully.
    Assign :attr:`responder` to change that; it receives the request and either
    returns a response or raises (to simulate transport failures).
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responder: Callable[[httpx.Request], httpx.Response] | None = None

    @property
    def last_request(self) -> httpx.Request:
        assert self.requests, "no request was made"
        return self.requests[-1]

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.responder is not None:
                return self.responder(request)
            return self._default(request)

        return httpx.MockTransport(handle)

    @staticmethod
    def _default(request: httpx.Request) -> httpx.Response:
        def ok(data: Any) -> httpx.Response:
            return httpx.Response(200, json=ok_envelope(data))

        path = request.url.path
        if path == "/health":
            return ok({"status": "healthy", "petCount": 1008})
        if path == "/api/v1/pets":
            return ok(make_pets_data())
        if path == "/api/v1/stats":
            return ok(make_stats())
        if path == "/api/v1/meta":
            return ok(make_meta())
        if path == "/api/v1/endpoints":
            return ok(make_endpoints())

        pet_prefix = "/api/v1/pets/"
        if path.startswith(pet_prefix):
            remainder = path[len(pet_prefix):]
            if remainder.endswith("/summary"):
                return ok(make_summary())
            if remainder.endswith("/records"):
                if request.method == "POST":
                    return ok({**make_record(record_id="MR-2026-0002"), **json.loads(request.content or b"{}")})
                return ok(make_records_data())
            if remainder.endswith("/charges"):
                if request.method == "POST":
                    return ok({**make_charge(charge_id="CH-2026-0002"), **json.loads(request.content or b"{}")})
                return ok(make_charges_data())
            if not remainder.startswith("PET-"):
                return httpx.Response(404, json=error_envelope(404, "pet not found"))
            return ok(make_pet(pet_id=remainder))
        return httpx.Response(404, json=error_envelope(404, "not found"))


@pytest.fixture
def stub() -> StubBackend:
    return StubBackend()


@pytest.fixture
def settings() -> Settings:
    return Settings(base_url=FAKE_BASE_URL, mcp_host="127.0.0.1", mcp_port=8765)


@pytest.fixture
def rest_client(settings: Settings, stub: StubBackend) -> Iterator[PetHospitalRestClient]:
    client = PetHospitalRestClient(settings, transport=stub.transport())
    yield client


@pytest_asyncio.fixture
async def mcp_server(settings: Settings, stub: StubBackend) -> AsyncIterator[MCPServer]:
    """An in-process MCPServer wired to the stub backend."""
    server, client = build_server(settings, transport=stub.transport())
    try:
        yield server
    finally:
        await client.aclose()


async def tool_by_name(mcp_server: MCPServer, name: str) -> Any:
    """Look up one advertised tool (works whether entries are objects or dicts)."""
    for tool in await mcp_server.list_tools():
        if getattr(tool, "name", None) == name or (isinstance(tool, dict) and tool.get("name") == name):
            return tool
    raise AssertionError(f"tool {name!r} is not registered")


def tool_schema(tool: Any) -> dict[str, Any]:
    """The input JSON Schema of an advertised tool, tolerant of SDK versions."""
    if isinstance(tool, dict):
        return tool["inputSchema"]
    return tool.inputSchema if hasattr(tool, "inputSchema") else tool.input_schema


def tool_ctx(method: str, params: dict[str, Any]) -> Any:
    """A minimal middleware context carrying a JSON-RPC method and its params."""
    from types import SimpleNamespace

    return SimpleNamespace(method=method, params=params)


@pytest.fixture
def guard() -> Any:
    """ToolCallGuard wired to the real TOOL_INPUT_MODELS registry."""
    from pet_hospital_mcp.server import ToolCallGuard
    from pet_hospital_mcp.tools import TOOL_INPUT_MODELS

    return ToolCallGuard(TOOL_INPUT_MODELS, frozenset(TOOL_INPUT_MODELS))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest_asyncio.fixture
async def live_server(settings: Settings, stub: StubBackend) -> AsyncIterator[str]:
    """Serve the real ASGI app over HTTP on an ephemeral loopback port.

    Returns the base URL, e.g. ``http://127.0.0.1:54321``. The app is built with
    the stub transport, so the "backend" is entirely in-process.
    """
    app = build_app(settings, transport=stub.transport())
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    server = uvicorn.Server(config)

    task = asyncio.create_task(server.serve())
    try:
        for _ in range(500):
            if server.started:
                break
            await asyncio.sleep(0.01)
        else:  # pragma: no cover - startup failure surfaces as a test error
            raise RuntimeError("uvicorn did not start in time")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


def mcp_body(method: str, params: dict[str, Any] | None = None, *, request_id: int = 1) -> dict[str, Any]:
    """A 2026-07-28 JSON-RPC request body with the required `_meta` envelope."""
    merged = dict(params or {})
    merged["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "1.0.0"},
    }
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": merged}


def mcp_headers(method: str, *, name: str | None = None, version: str = "2026-07-28") -> dict[str, str]:
    """Routing headers required by the 2026-07-28 Streamable HTTP transport."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": version,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


@pytest.fixture
def json_of() -> Callable[[httpx.Response], dict[str, Any]]:
    return lambda response: json.loads(response.text)
