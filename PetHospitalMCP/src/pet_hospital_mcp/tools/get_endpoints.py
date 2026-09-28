"""The ``get_endpoints`` tool.

Adapts ``GET /api/v1/endpoints`` — the backend's self-description of every
route it exposes.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, ValidationError

from pet_hospital_mcp.errors import BackendInvalidResponseError, PetHospitalToolError
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.tools._shared import render_error_fields

GET_ENDPOINTS_TOOL_NAME = "get_endpoints"


class GetEndpointsInput(BaseModel):
    """``get_endpoints`` takes no arguments; this model exists for the middleware hook."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class EndpointInfo(BaseModel):
    """One route description from the backend's self-description."""

    model_config = ConfigDict(extra="ignore")

    Method: Optional[str] = None
    Path: Optional[str] = None
    Desc: Optional[str] = None
    Example: Optional[str] = None


class GetEndpointsOutput(BaseModel):
    """The ``data`` object of ``GET /api/v1/endpoints``."""

    model_config = ConfigDict(extra="ignore")

    count: int
    endpoints: list[EndpointInfo]


#: Exposed so the middleware can validate raw arguments against the same model.
GET_ENDPOINTS_INPUT_MODEL = GetEndpointsInput


GET_ENDPOINTS_DESCRIPTION = """\
获取后端接口清单（对应后端 GET /api/v1/endpoints）：后端自己声明的全部
REST 路由及其说明。

用途：列出后端当前暴露的接口（方法、路径、描述、示例），适合用来核实后端
能力、排查对接问题，或让 AI 了解可用数据面。

适用场景：
- 怀疑某个接口路径/方法不对、想和后端官方文档对账时；
- 需要了解后端完整数据面以规划新的查询时。

参数：无。

返回值：一个对象，字段与后端 data 一致 ——
- count：接口总数；
- endpoints：接口说明数组，每项含 Method/Path/Desc/Example。

示例：{}（无需任何参数）

失败时返回统一错误结构 {"error": {"code", "message", "details"}}，code 取
VALIDATION_ERROR / BACKEND_TIMEOUT / BACKEND_UNAVAILABLE / BACKEND_API_ERROR /
BACKEND_INVALID_RESPONSE / INTERNAL_ERROR 之一。
"""


def register_get_endpoints(mcp: MCPServer, client: PetHospitalRestClient) -> None:
    """Register ``get_endpoints`` on ``mcp``, bound to ``client``."""

    @mcp.tool(name=GET_ENDPOINTS_TOOL_NAME, description=GET_ENDPOINTS_DESCRIPTION)
    async def get_endpoints() -> CallToolResult:
        try:
            data = await client.get_endpoints()

            try:
                output = GetEndpointsOutput.model_validate(data)
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