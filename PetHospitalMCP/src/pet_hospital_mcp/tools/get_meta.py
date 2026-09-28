"""The ``get_meta`` tool.

Adapts ``GET /api/v1/meta``. Returns the backend's own vocabulary: the exact
enum values it accepts for species, status, gender and charge categories, the
sortable fields, and every query/body field it knows about.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, ValidationError

from pet_hospital_mcp.errors import BackendInvalidResponseError, PetHospitalToolError
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.tools._shared import render_error_fields

GET_META_TOOL_NAME = "get_meta"


class GetMetaInput(BaseModel):
    """``get_meta`` takes no arguments; this model exists for the middleware hook."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class MetaFieldDoc(BaseModel):
    """One (name, desc) field documentation entry from the backend."""

    model_config = ConfigDict(extra="ignore")

    name: str
    desc: Optional[str] = None


class GetMetaOutput(BaseModel):
    """The ``data`` object of ``GET /api/v1/meta``."""

    model_config = ConfigDict(extra="ignore")

    species: list[str]
    status: list[str]
    gender: list[str]
    chargeCategories: list[str]
    sortFields: list[str]
    fields: list[MetaFieldDoc]


#: Exposed so the middleware can validate raw arguments against the same model.
GET_META_INPUT_MODEL = GetMetaInput


GET_META_DESCRIPTION = """\
获取后端自身的"元信息"（对应后端 GET /api/v1/meta）：后端当前接受的枚举值
和字段说明，不访问任何宠物数据。

用途：拿到后端当前权威的种类、就诊状态、性别、收费分类和排序字段枚举，
以及每个查询/写入字段的说明。由于枚举来自后端官方自描述，可以放心用来
校验其它工具的取值。

适用场景：
- 想知道"到底有哪些可用的 species/status/category"时；
- 动态构建查询界面或校验上游枚举是否已经变化时。

参数：无。

返回值：一个对象，字段与后端 data 一致 ——
- species/status/gender/chargeCategories/sortFields：对应枚举值数组；
- fields：字段说明数组，每项含 name 和 desc。

示例：{}（无需任何参数）

失败时返回统一错误结构 {"error": {"code", "message", "details"}}，code 取
VALIDATION_ERROR / BACKEND_TIMEOUT / BACKEND_UNAVAILABLE / BACKEND_API_ERROR /
BACKEND_INVALID_RESPONSE / INTERNAL_ERROR 之一。
"""


def register_get_meta(mcp: MCPServer, client: PetHospitalRestClient) -> None:
    """Register ``get_meta`` on ``mcp``, bound to ``client``."""

    @mcp.tool(name=GET_META_TOOL_NAME, description=GET_META_DESCRIPTION)
    async def get_meta() -> CallToolResult:
        try:
            data = await client.get_meta()

            try:
                output = GetMetaOutput.model_validate(data)
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