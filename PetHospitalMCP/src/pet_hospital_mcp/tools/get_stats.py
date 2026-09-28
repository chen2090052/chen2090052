"""The ``get_stats`` tool.

Adapts ``GET /api/v1/stats``. Returns whole-hospital aggregates (totals,
averages, maximum) plus distributions by species / status / doctor and a
消费排行榜 (``topSpenders``) — the top-spending pets.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, ValidationError

from pet_hospital_mcp.errors import BackendInvalidResponseError, PetHospitalToolError
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.tools._shared import Pet, TopField, render_error_fields

GET_STATS_TOOL_NAME = "get_stats"


class GetStatsInput(BaseModel):
    """Strictly validated arguments for :func:`get_stats`."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    top: TopField = None


class GetStatsOutput(BaseModel):
    """The ``data`` object of ``GET /api/v1/stats``."""

    model_config = ConfigDict(extra="ignore")

    totalPets: int
    totalRecords: int
    totalCharges: int
    totalRevenue: float
    averageCost: Optional[float] = None
    maxCost: Optional[float] = None
    bySpecies: Optional[dict[str, int]] = None
    byStatus: Optional[dict[str, int]] = None
    byDoctor: Optional[dict[str, int]] = None
    revenueByDoctor: Optional[dict[str, float]] = None
    topSpenders: Optional[list[Pet]] = None


#: Exposed so the middleware can validate raw arguments against the same model.
GET_STATS_INPUT_MODEL = GetStatsInput


GET_STATS_DESCRIPTION = """\
获取全院统计与消费排行榜（对应后端 GET /api/v1/stats）。

用途：一栏子概览整个宠物医院——总量（档案/病历/收费笔数/总收入）、单次
平均花费与最高单笔花费，以及按种类、按状态、按医生的人数分布和按医生的
收入分布，还有一个消费最高的宠物排行榜 topSpenders。

适用场景：
- 给业主/管理者做经营概览时；
- 了解哪类宠物、哪个医生贡献最多，发现高价值客户时。

参数（全部可选）：
- top：消费排行榜返回的宠物条数，1 到 100；省略时使用后端默认条数。

返回值：一个对象，字段与后端 data 一致 ——
- totalPets/totalRecords/totalCharges：档案、病历、收费总量；
- totalRevenue：总收入（元）；averageCost/maxCost：单次平均/最高花费；
- bySpecies/byStatus/byDoctor：按种类/状态/医生的人数分布；
- revenueByDoctor：按医生的收入分布；
- topSpenders：消费最高的宠物数组（每项为完整档案，含 records/charges）。

示例：{"top": 5}

失败时返回统一错误结构 {"error": {"code", "message", "details"}}，code 取
VALIDATION_ERROR / BACKEND_TIMEOUT / BACKEND_UNAVAILABLE / BACKEND_API_ERROR /
BACKEND_INVALID_RESPONSE / INTERNAL_ERROR 之一。
"""


def register_get_stats(mcp: MCPServer, client: PetHospitalRestClient) -> None:
    """Register ``get_stats`` on ``mcp``, bound to ``client``."""

    @mcp.tool(name=GET_STATS_TOOL_NAME, description=GET_STATS_DESCRIPTION)
    async def get_stats(top: TopField = None) -> CallToolResult:
        try:
            validated = GetStatsInput(top=top)
            params = {} if validated.top is None else {"top": validated.top}
            data = await client.get_stats(params)

            try:
                output = GetStatsOutput.model_validate(data)
            except ValidationError as exc:
                raise BackendInvalidResponseError(
                    details={
                        "reason": "backend data does not match the expected schema",
                        "fields": render_error_fields(exc),
                    }
                ) from None
        except PetHospitalToolError as exc:
            return exc.to_call_tool_result()

        return CallToolResult(
            content=[TextContent(type="text", text=output.model_dump_json())],
            structured_content=output.model_dump(mode="json"),
        )