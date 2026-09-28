"""The ``get_pet_summary`` tool.

Adapts ``GET /api/v1/pets/{id}/summary``. The Go backend renders a
pet档案 into an AI-facing 诊疗摘要: the remembered disease and doctor, the
visit timeline, cost aggregation by category and by doctor, and a plain-text
``historyText`` derived from every record.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, ValidationError

from pet_hospital_mcp.errors import BackendInvalidResponseError, PetHospitalToolError
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.tools._shared import IdField, render_error_fields

GET_PET_SUMMARY_TOOL_NAME = "get_pet_summary"


class GetPetSummaryInput(BaseModel):
    """Strictly validated arguments for :func:`get_pet_summary`."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    id: IdField


class PetSummary(BaseModel):
    """The AI-facing 诊疗摘要 as returned by ``GET /api/v1/pets/{id}/summary``."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: Optional[str] = None
    species: Optional[str] = None
    breed: Optional[str] = None
    gender: Optional[str] = None
    ownerName: Optional[str] = None
    ownerPhone: Optional[str] = None
    doctor: Optional[str] = None
    disease: Optional[str] = None
    status: Optional[str] = None
    visitCount: Optional[int] = None
    chargeCount: Optional[int] = None
    totalCost: Optional[float] = None
    avgCostPerVisit: Optional[float] = None
    maxSingleCharge: Optional[float] = None
    costByCategory: Optional[dict[str, float]] = None
    costByDoctor: Optional[dict[str, float]] = None
    firstVisit: Optional[str] = None
    lastVisit: Optional[str] = None
    historyText: Optional[str] = None


#: Exposed so the middleware can validate raw arguments against the same model.
GET_PET_SUMMARY_INPUT_MODEL = GetPetSummaryInput


GET_PET_SUMMARY_DESCRIPTION = """\
按档案编号读取单个宠物的AI诊疗摘要（对应后端 GET /api/v1/pets/{id}/summary）。

用途：生成一段面向AI/医生的宠物诊疗概览，把长期病历浓缩成一份摘要，包含
疾病与主治医生、首次/最近就诊、费用统计和一段自然语言病史（historyText）。

适用场景：
- 需要快速概括"这只宠物啥病、治了多久、花了多少钱"时；
- 向二次会诊或AI助手投喂精简上下文时（比整份 records+charges 更省token）；
- 查看按收费分类（costByCategory）和按医生（costByDoctor）的花费分布。

参数：
- id：宠物档案编号（必填），如 PET-000001。

返回值：一个对象，字段与后端 data 一致 ——
- id/name/species/breed/gender/ownerName/ownerPhone：基础档案信息；
- doctor/disease/status：主治医生、疾病与当前状态；
- visitCount/chargeCount：就诊次数与收费笔数；
- totalCost/avgCostPerVisit/maxSingleCharge：花费汇总；
- costByCategory/costByDoctor：按分类、按医生的花费分布；
- firstVisit/lastVisit：首次与最近就诊日期；
- historyText：由后端根据全部病历生成的自然语言病史。

示例：{"id": "PET-000001"}

失败时返回统一错误结构 {"error": {"code", "message", "details"}}，code 取
VALIDATION_ERROR / BACKEND_TIMEOUT / BACKEND_UNAVAILABLE / BACKEND_API_ERROR /
BACKEND_INVALID_RESPONSE / INTERNAL_ERROR 之一。
"""


def register_get_pet_summary(mcp: MCPServer, client: PetHospitalRestClient) -> None:
    """Register ``get_pet_summary`` on ``mcp``, bound to ``client``."""

    @mcp.tool(name=GET_PET_SUMMARY_TOOL_NAME, description=GET_PET_SUMMARY_DESCRIPTION)
    async def get_pet_summary(id: IdField) -> CallToolResult:
        try:
            validated = GetPetSummaryInput(id=id)
            data = await client.get_pet_summary(validated.id)

            try:
                output = PetSummary.model_validate(data)
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