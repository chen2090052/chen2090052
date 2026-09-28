"""The ``add_pet_record`` tool.

Adapts ``POST /api/v1/pets/{id}/records``. Appends one 病历 entry to a pet's
history. The write is **not idempotent**, so it is sent exactly once (no retry).
The returned data is whatever the backend chose to put in the envelope; it is
passed through untouched rather than re-validated against a guessed shape.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, model_validator

from pet_hospital_mcp.errors import PetHospitalToolError
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.tools._shared import (
    IdField,
    MoneyField,
    OpaqueData,
    PrescriptionField,
    TemperatureField,
    TextField,
    WeightField,
)

ADD_PET_RECORD_TOOL_NAME = "add_pet_record"


class AddPetRecordInput(BaseModel):
    """Strictly validated arguments for :func:`add_pet_record`.

    The JSON body for ``POST /api/v1/pets/{id}/records`` is exactly the same
    fields minus ``id`` (the pet address lives in the path). Every record field
    is optional at the adapter layer: the backend owns the business rules (which
    fields are required, which values are legal) and answers with its own
    validation message. The adapter only enforces types and broad sanity bounds.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    id: IdField
    visitDate: TextField = None
    doctor: TextField = None
    diagnosis: TextField = None
    symptoms: TextField = None
    treatment: TextField = None
    prescription: PrescriptionField = None
    weightKg: WeightField = None
    temperature: TemperatureField = None
    followUp: TextField = None
    charge: MoneyField = None

    @model_validator(mode="after")
    def _normalise_blanks(self) -> AddPetRecordInput:
        for field_name in ("visitDate", "doctor", "diagnosis", "symptoms", "treatment", "followUp"):
            value = getattr(self, field_name)
            if isinstance(value, str) and not value.strip():
                setattr(self, field_name, None)
        if self.prescription is not None:
            self.prescription = [
                item for item in self.prescription if isinstance(item, str) and item.strip()
            ]
            if not self.prescription:
                self.prescription = None
        return self

    def to_json_body(self) -> dict:
        """Serialise to the JSON body the Go API expects, None values omitted."""
        return {key: value for key, value in self.model_dump(exclude_none=True).items() if key != "id"}


class AddPetRecordOutput(OpaqueData):
    """The backend's ``data`` after the write; passed through verbatim."""


#: Exposed so the middleware can validate raw arguments against the same model.
ADD_PET_RECORD_INPUT_MODEL = AddPetRecordInput


ADD_PET_RECORD_DESCRIPTION = """\
为指定宠物追加一条历史病历（对应后端 POST /api/v1/pets/{id}/records）。

用途：写入一条新的就诊记录。所有病历字段都是可选的——后端掌握业务规则
（哪些必填、枚举范围、金额约束），缺失或非法时由后端自己的校验信息回应。

适用场景：
- 宠物复诊/新诊后，把诊断、症状、处置、处方和本次费用补充到档案里；
- 需要更新体重、体温、复诊安排等体检数据时。

参数：
- id：宠物档案编号（必填），如 PET-000001。
- visitDate：就诊日期（文本，如 2026-09-20）。
- doctor：接诊医生。
- diagnosis：诊断。
- symptoms：症状描述。
- treatment：处置方案。
- prescription：处方药品列表（字符串数组）。
- weightKg：体重（公斤，0 到 300）。
- temperature：体温（摄氏度，30 到 45）。
- followUp：复诊安排。
- charge：本次费用（元，非负）。

返回值：本次写入后后端返回的 data 对象，字段由后端定义（通常是新建的病历
条目），原样透传、不做猜测性校验。

示例：{"id": "PET-000001", "doctor": "李医生", "diagnosis": "急性肠胃炎",
"symptoms": "呕吐腹泻", "treatment": "补液消炎", "prescription": ["阿莫西林"],
"charge": 380}

注意：写入操作不可幂等，请求只发送一次、不做自动重试，以避免重复写入。

失败时返回统一错误结构 {"error": {"code", "message", "details"}}，code 取
VALIDATION_ERROR / BACKEND_TIMEOUT / BACKEND_UNAVAILABLE / BACKEND_API_ERROR /
BACKEND_INVALID_RESPONSE / INTERNAL_ERROR 之一。
"""


def register_add_pet_record(mcp: MCPServer, client: PetHospitalRestClient) -> None:
    """Register ``add_pet_record`` on ``mcp``, bound to ``client``."""

    @mcp.tool(name=ADD_PET_RECORD_TOOL_NAME, description=ADD_PET_RECORD_DESCRIPTION)
    async def add_pet_record(
        id: IdField,
        visitDate: TextField = None,
        doctor: TextField = None,
        diagnosis: TextField = None,
        symptoms: TextField = None,
        treatment: TextField = None,
        prescription: PrescriptionField = None,
        weightKg: WeightField = None,
        temperature: TemperatureField = None,
        followUp: TextField = None,
        charge: MoneyField = None,
    ) -> CallToolResult:
        try:
            validated = AddPetRecordInput(
                id=id,
                visitDate=visitDate,
                doctor=doctor,
                diagnosis=diagnosis,
                symptoms=symptoms,
                treatment=treatment,
                prescription=prescription,
                weightKg=weightKg,
                temperature=temperature,
                followUp=followUp,
                charge=charge,
            )
            data = await client.add_pet_record(validated.id, validated.to_json_body())

            # The backend owns the post-write response shape; pass it through
            # verbatim instead of failing on an un-modelled field.
            output = AddPetRecordOutput.model_validate(data)
        except PetHospitalToolError as exc:
            return exc.to_call_tool_result()

        return CallToolResult(
            content=[TextContent(type="text", text=output.model_dump_json())],
            structured_content=output.model_dump(mode="json"),
        )