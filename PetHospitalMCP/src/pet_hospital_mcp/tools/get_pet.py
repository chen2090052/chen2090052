"""The ``get_pet`` tool.

Adapts ``GET /api/v1/pets/{id}``. Given a pet档案编号 it returns the full
档案, including the nested 历史病历 and 消费明细.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, ValidationError

from pet_hospital_mcp.errors import BackendInvalidResponseError, PetHospitalToolError
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.tools._shared import IdField, Pet, render_error_fields

GET_PET_TOOL_NAME = "get_pet"


class GetPetInput(BaseModel):
    """Strictly validated arguments for :func:`get_pet`."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    id: IdField


class GetPetOutput(Pet):
    """The whole pet档案 — ``data`` of ``GET /api/v1/pets/{id}`` is the pet itself."""


#: Exposed so the middleware can validate raw arguments against the same model.
GET_PET_INPUT_MODEL = GetPetInput


GET_PET_DESCRIPTION = """\
按档案编号读取单个宠物的完整档案（对应后端 GET /api/v1/pets/{id}）。

用途：拿到某个宠物档案的全部信息，包括主人、医生、疾病、就诊状态、
过敏史，以及嵌套在档案里的历史病历（records）和消费明细（charges）。

适用场景：
- 已经有一个 PET-xxxxxx 编号，想查看该宠物的完整档案时；
- 需要某个宠物最近一次就诊、全部历史病历或全部消费明细时；
- 写病历/收费之前，先读取现有档案确认宠物身份与状态。

参数：
- id：宠物档案编号（必填），如 PET-000001。

返回值：一个对象，字段与后端 data 一致，即完整的宠物档案 ——
- id：档案编号；name/species/breed/gender/ageMonths/color/chipNo；
- ownerName/ownerPhone/ownerAddr：主人信息；
- doctor/disease/status/allergy/note：医疗信息；
- records：历史病历数组（可能为 null）；charges：消费明细数组（可能为 null）；
- totalCost/visitCount：累计花费与就诊次数；createdAt/updatedAt：时间戳。

示例：{"id": "PET-000001"}

失败时返回统一错误结构 {"error": {"code", "message", "details"}}，code 取
VALIDATION_ERROR / BACKEND_TIMEOUT / BACKEND_UNAVAILABLE / BACKEND_API_ERROR /
BACKEND_INVALID_RESPONSE / INTERNAL_ERROR 之一。
"""


def register_get_pet(mcp: MCPServer, client: PetHospitalRestClient) -> None:
    """Register ``get_pet`` on ``mcp``, bound to ``client``."""

    @mcp.tool(name=GET_PET_TOOL_NAME, description=GET_PET_DESCRIPTION)
    async def get_pet(id: IdField) -> CallToolResult:
        try:
            validated = GetPetInput(id=id)
            data = await client.get_pet(validated.id)

            try:
                output = GetPetOutput.model_validate(data)
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