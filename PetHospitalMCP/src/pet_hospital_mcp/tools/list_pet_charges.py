"""The ``list_pet_charges`` tool.

Adapts ``GET /api/v1/pets/{id}/charges``. Returns the pet's 消费明细 as an
array plus the aggregate ``totalCost`` and ``costByCategory``.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, ValidationError

from pet_hospital_mcp.errors import BackendInvalidResponseError, PetHospitalToolError
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.tools._shared import IdField, PetCharge, render_error_fields

LIST_PET_CHARGES_TOOL_NAME = "list_pet_charges"


class ListPetChargesInput(BaseModel):
    """Strictly validated arguments for :func:`list_pet_charges`."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    id: IdField


class ListPetChargesOutput(BaseModel):
    """The ``data`` object of ``GET /api/v1/pets/{id}/charges``."""

    model_config = ConfigDict(extra="ignore")

    petId: str
    count: int
    petName: Optional[str] = None
    totalCost: Optional[float] = None
    costByCategory: Optional[dict[str, float]] = None
    charges: Optional[list[PetCharge]] = None


#: Exposed so the middleware can validate raw arguments against the same model.
LIST_PET_CHARGES_INPUT_MODEL = ListPetChargesInput


LIST_PET_CHARGES_DESCRIPTION = """\
按档案编号列出某个宠物的全部消费明细（对应后端 GET /api/v1/pets/{id}/charges）。

用途：逐条查看收费记录（日期、项目、分类、收费医生、金额），并附带该宠物的
累计花费总额 totalCost 和按分类汇总 costByCategory。

适用场景：
- 需要单只宠物"全部收费明细"而不是整份档案时；
- 核对某笔费用、看消费结构（检查/药品/手术/住院/疫苗/护理/其他）。

参数：
- id：宠物档案编号（必填），如 PET-000001。

返回值：一个对象，字段与后端 data 一致 ——
- petId/petName：宠物基本信息；
- count：收费笔数；
- totalCost：累计消费总额（元）；
- costByCategory：按收费分类汇总的金额映射；
- charges：消费明细数组，每项含 id/item/category/amount/doctor/date。

示例：{"id": "PET-000001"}

失败时返回统一错误结构 {"error": {"code", "message", "details"}}，code 取
VALIDATION_ERROR / BACKEND_TIMEOUT / BACKEND_UNAVAILABLE / BACKEND_API_ERROR /
BACKEND_INVALID_RESPONSE / INTERNAL_ERROR 之一。
"""


def register_list_pet_charges(mcp: MCPServer, client: PetHospitalRestClient) -> None:
    """Register ``list_pet_charges`` on ``mcp``, bound to ``client``."""

    @mcp.tool(name=LIST_PET_CHARGES_TOOL_NAME, description=LIST_PET_CHARGES_DESCRIPTION)
    async def list_pet_charges(id: IdField) -> CallToolResult:
        try:
            validated = ListPetChargesInput(id=id)
            data = await client.list_pet_charges(validated.id)

            try:
                output = ListPetChargesOutput.model_validate(data)
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