"""The ``add_pet_charge`` tool.

Adapts ``POST /api/v1/pets/{id}/charges``. Appends one 消费明细 line to a
pet's billing history. The write is **not idempotent**, so it is sent exactly
once (no retry). The returned data is whatever the backend chose to put in the
envelope; it is passed through untouched.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, model_validator

from pet_hospital_mcp.errors import PetHospitalToolError
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.tools._shared import (
    ChargeCategory,
    IdField,
    MoneyField,
    OpaqueData,
    TextField,
)

ADD_PET_CHARGE_TOOL_NAME = "add_pet_charge"


class AddPetChargeInput(BaseModel):
    """Strictly validated arguments for :func:`add_pet_charge`.

    Every field is optional at the adapter layer except the pet ``id``: the
    backend owns which fields are required and which categories are legal.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    id: IdField
    item: TextField = None
    category: Optional[ChargeCategory] = None
    amount: MoneyField = None
    doctor: TextField = None
    date: TextField = None

    @model_validator(mode="after")
    def _normalise_blanks(self) -> AddPetChargeInput:
        for field_name in ("item", "doctor", "date"):
            value = getattr(self, field_name)
            if isinstance(value, str) and not value.strip():
                setattr(self, field_name, None)
        return self

    def to_json_body(self) -> dict:
        """Serialise to the JSON body the Go API expects, None values omitted."""
        return {key: value for key, value in self.model_dump(exclude_none=True).items() if key != "id"}


class AddPetChargeOutput(OpaqueData):
    """The backend's ``data`` after the write; passed through verbatim."""


#: Exposed so the middleware can validate raw arguments against the same model.
ADD_PET_CHARGE_INPUT_MODEL = AddPetChargeInput


ADD_PET_CHARGE_DESCRIPTION = """\
为指定宠物追加一条消费明细（对应后端 POST /api/v1/pets/{id}/charges）。

用途：写入一笔新的收费记录。除宠物编号 id 外，其余字段都是可选的——后端
掌握业务规则（哪些必填、分类枚举、金额约束），缺失或非法时由后端自己的
校验信息回应。

适用场景：
- 完成检查/用药/手术/住院等项目后把费用登记进档案；
- 补录历史收费、或登记按次护理/疫苗费用。

参数：
- id：宠物档案编号（必填），如 PET-000001。
- item：收费项目名称（如 血常规检查）。
- category：收费分类，必须是 检查/药品/手术/住院/疫苗/护理/其他 之一。
- amount：金额（元，非负）。
- doctor：收费医生。
- date：收费日期（文本，如 2026-09-20）。

返回值：本次写入后后端返回的 data 对象，字段由后端定义（通常是新建的
收费条目），原样透传、不做猜测性校验。

示例：{"id": "PET-000001", "item": "血常规检查", "category": "检查",
"amount": 180, "doctor": "李医生"}

注意：写入操作不可幂等，请求只发送一次、不做自动重试，以避免重复写入。

失败时返回统一错误结构 {"error": {"code", "message", "details"}}，code 取
VALIDATION_ERROR / BACKEND_TIMEOUT / BACKEND_UNAVAILABLE / BACKEND_API_ERROR /
BACKEND_INVALID_RESPONSE / INTERNAL_ERROR 之一。
"""


def register_add_pet_charge(mcp: MCPServer, client: PetHospitalRestClient) -> None:
    """Register ``add_pet_charge`` on ``mcp``, bound to ``client``."""

    @mcp.tool(name=ADD_PET_CHARGE_TOOL_NAME, description=ADD_PET_CHARGE_DESCRIPTION)
    async def add_pet_charge(
        id: IdField,
        item: TextField = None,
        category: Optional[ChargeCategory] = None,
        amount: MoneyField = None,
        doctor: TextField = None,
        date: TextField = None,
    ) -> CallToolResult:
        try:
            validated = AddPetChargeInput(id=id, item=item, category=category, amount=amount, doctor=doctor, date=date)
            data = await client.add_pet_charge(validated.id, validated.to_json_body())

            # The backend owns the post-write response shape; pass it through
            # verbatim instead of failing on an un-modelled field.
            output = AddPetChargeOutput.model_validate(data)
        except PetHospitalToolError as exc:
            return exc.to_call_tool_result()

        return CallToolResult(
            content=[TextContent(type="text", text=output.model_dump_json())],
            structured_content=output.model_dump(mode="json"),
        )