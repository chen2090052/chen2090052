"""The ``list_pet_records`` tool.

Adapts ``GET /api/v1/pets/{id}/records``. Returns the pet's 历史病历 as an
array plus the backend-rendered ``historyText`` summary.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, ValidationError

from pet_hospital_mcp.errors import BackendInvalidResponseError, PetHospitalToolError
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.tools._shared import IdField, PetRecord, render_error_fields

LIST_PET_RECORDS_TOOL_NAME = "list_pet_records"


class ListPetRecordsInput(BaseModel):
    """Strictly validated arguments for :func:`list_pet_records`."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    id: IdField


class ListPetRecordsOutput(BaseModel):
    """The ``data`` object of ``GET /api/v1/pets/{id}/records``."""

    model_config = ConfigDict(extra="ignore")

    petId: str
    count: int
    petName: Optional[str] = None
    ownerName: Optional[str] = None
    historyText: Optional[str] = None
    records: Optional[list[PetRecord]] = None


#: Exposed so the middleware can validate raw arguments against the same model.
LIST_PET_RECORDS_INPUT_MODEL = ListPetRecordsInput


LIST_PET_RECORDS_DESCRIPTION = """\
按档案编号列出某个宠物的全部历史病历（对应后端 GET /api/v1/pets/{id}/records）。

用途：逐条查看诊断记录（就诊日期、医生、诊断、症状、处置、处方、体重、
体温、复诊安排、单次费用），并附带一段后端生成的文字病史 historyText。

适用场景：
- 需要单只宠物"全部病历明细"而不是整份档案时；
- 看某次就诊的处方与处置细节、或查找就医时间线。

参数：
- id：宠物档案编号（必填），如 PET-000001。

返回值：一个对象，字段与后端 data 一致 ——
- petId/petName/ownerName：宠物与主人的基本信息；
- count：病历条数；
- records：病历数组，每项含 id/visitDate/doctor/diagnosis/symptoms/
  treatment/prescription/weightKg/temperature/followUp/charge/createdAt；
- historyText：后端根据全部病历生成的自然语言病史。

示例：{"id": "PET-000001"}

失败时返回统一错误结构 {"error": {"code", "message", "details"}}，code 取
VALIDATION_ERROR / BACKEND_TIMEOUT / BACKEND_UNAVAILABLE / BACKEND_API_ERROR /
BACKEND_INVALID_RESPONSE / INTERNAL_ERROR 之一。
"""


def register_list_pet_records(mcp: MCPServer, client: PetHospitalRestClient) -> None:
    """Register ``list_pet_records`` on ``mcp``, bound to ``client``."""

    @mcp.tool(name=LIST_PET_RECORDS_TOOL_NAME, description=LIST_PET_RECORDS_DESCRIPTION)
    async def list_pet_records(id: IdField) -> CallToolResult:
        try:
            validated = ListPetRecordsInput(id=id)
            data = await client.list_pet_records(validated.id)

            try:
                output = ListPetRecordsOutput.model_validate(data)
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