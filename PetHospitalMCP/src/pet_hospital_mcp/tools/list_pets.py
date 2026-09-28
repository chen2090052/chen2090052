"""The ``list_pets`` tool.

Adapts ``GET /api/v1/pets`` on the Go Pet Hospital REST API. The tool exposes
exactly the query parameters the backend accepts — no adapter-private extras —
and validates them strictly before anything is sent upstream.

Two layers cooperate:

* The **flat tool signature** below drives the JSON Schema advertised to MCP
  clients. Its ``Annotated`` aliases are the single source of truth for the
  per-field constraints, and the same aliases are reused by
  :class:`ListPetsInput`.
* :class:`ListPetsInput` adds what a signature cannot express — rejection of
  unknown fields, NaN/Infinity, and the cross-field ``min <= max`` rule — and is
  enforced by the server middleware before the tool body runs.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from pet_hospital_mcp.errors import BackendInvalidResponseError, PetHospitalToolError
from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.tools._shared import (
    MAX_PAGE_SIZE,
    MaxField,
    MinField,
    OrderField,
    PageField,
    PageSizeField,
    Pet,
    PetCharge,
    PetRecord,
    QField,
    SortByField,
    SpeciesField,
    StatusField,
    TextField,
)

LIST_PETS_TOOL_NAME = "list_pets"


# --- Input model ------------------------------------------------------------ #


class ListPetsInput(BaseModel):
    """Strictly validated arguments for :func:`list_pets`.

    Beyond the per-field constraints carried by the shared aliases this model:

    * rejects unknown fields (``extra="forbid"``),
    * rejects ``NaN`` / ``Infinity`` (``allow_inf_nan=False``),
    * rejects type-incorrect values such as ``"1"`` or ``1.0`` for ``page``
      (``strict=True``),
    * normalises blank/whitespace-only strings to ``None`` so an accidental
      empty filter does not turn into a filter that matches nothing, and
    * enforces ``min <= max``.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    q: TextField = None
    name: TextField = None
    ownerName: TextField = None
    ownerPhone: TextField = None
    species: SpeciesField = None
    doctor: TextField = None
    disease: TextField = None
    status: StatusField = None
    min: MinField = None
    max: MaxField = None
    sortBy: SortByField = None
    order: OrderField = None
    page: PageField = 1
    pageSize: PageSizeField = 20

    @model_validator(mode="after")
    def _check(self) -> ListPetsInput:
        for field_name in (
            "q",
            "name",
            "ownerName",
            "ownerPhone",
            "doctor",
            "disease",
        ):
            value = getattr(self, field_name)
            if isinstance(value, str) and not value.strip():
                setattr(self, field_name, None)

        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must be less than or equal to max")
        return self

    def to_query_params(self) -> dict[str, Any]:
        """Serialise to the query string the Go API expects.

        Only parameters the caller actually supplied are forwarded, so the
        backend keeps ownership of its own defaults.
        """
        return {key: value for key, value in self.model_dump(exclude_none=True).items()}


# --- Output models ---------------------------------------------------------- #
# Shared pet / record / charge models live in ``_shared`` (imported above) and
# are re-exported through this module so existing imports keep working.


class ListPetsOutput(BaseModel):
    """The ``data`` object of a successful ``GET /api/v1/pets`` response."""

    model_config = ConfigDict(extra="ignore")

    items: list[Pet]
    total: int
    page: int
    pageSize: int
    totalPages: int
    totalCost: float


#: Exposed so the middleware can validate raw arguments against the same model.
LIST_PETS_INPUT_MODEL = ListPetsInput


LIST_PETS_DESCRIPTION = """\
按条件分页查询宠物档案列表（对应后端 GET /api/v1/pets）。

用途：检索、筛选、排序和翻页浏览宠物档案。返回的是档案主档信息，
包含主人、医生、疾病、就诊状态，以及每个档案的历史病历与消费明细。

适用场景：
- 想知道"有哪些宠物/谁的主人/某个医生负责哪些病例"时；
- 按种类、医生、疾病、就诊状态做筛选统计；
- 找花费最高或最低的一批宠物（配合 sortBy=totalCost 与 order）；
- 分页浏览全部档案。

参数（全部可选；只传需要生效的条件，未传的条件不会下发给后端）：
- q：跨字段全文检索，空格分词按 AND 组合，覆盖病历正文。
- name / ownerName / ownerPhone / doctor / disease：按对应字段模糊匹配。
- species：种类，必须是 犬/猫/兔/鸟/仓鼠/爬宠/其他 之一。
- status：就诊状态，必须是 待就诊/就诊中/住院中/已康复/慢性病随访 之一。
- min / max：按"总花费"区间过滤，单位为元，均为非负且 min 不能大于 max。
- sortBy：排序字段，取 id/name/ownerName/species/doctor/disease/status/
  totalCost/visitCount/createdAt/updatedAt 之一。
- order：asc 升序或 desc 降序，配合 sortBy 使用。
- page：页码，从 1 开始，默认 1。
- pageSize：每页条数，1..500，默认 20；超过 500 会被拒绝。

返回值：一个对象，字段与后端 data 一致 ——
- items：宠物档案数组（含 records、charges，二者可能为 null）；
- total：符合条件的总条数；
- page / pageSize：本次请求的页码与每页条数；
- totalPages：总页数；
- totalCost：符合条件的全部档案的总花费合计。

示例：{"species": "犬", "min": 1000, "sortBy": "totalCost", "order": "desc", "page": 1, "pageSize": 20}

失败时返回统一错误结构 {"error": {"code", "message", "details"}}，code 取
VALIDATION_ERROR / BACKEND_TIMEOUT / BACKEND_UNAVAILABLE / BACKEND_API_ERROR /
BACKEND_INVALID_RESPONSE / INTERNAL_ERROR 之一。
"""


def register_list_pets(mcp: MCPServer, client: PetHospitalRestClient) -> None:
    """Register ``list_pets`` on ``mcp``, bound to ``client``."""

    @mcp.tool(name=LIST_PETS_TOOL_NAME, description=LIST_PETS_DESCRIPTION)
    async def list_pets(
        q: QField = None,
        name: TextField = None,
        ownerName: TextField = None,
        ownerPhone: TextField = None,
        species: SpeciesField = None,
        doctor: TextField = None,
        disease: TextField = None,
        status: StatusField = None,  # type: ignore[valid-type]
        min: MinField = None,
        max: MaxField = None,
        sortBy: SortByField = None,
        order: OrderField = None,
        page: PageField = 1,
        pageSize: PageSizeField = 20,
    ) -> CallToolResult:
        # Arguments arriving here have already been validated twice: once by the
        # server middleware against ListPetsInput, once by the SDK's own
        # signature model. Failures never reach this body.
        #
        # Failures are *returned* as the unified envelope rather than raised:
        # the SDK turns an escaping exception into a `ToolError`, which reaches
        # the client as a plain text block with the structured error lost.
        try:
            validated = ListPetsInput(
                q=q,
                name=name,
                ownerName=ownerName,
                ownerPhone=ownerPhone,
                species=species,
                doctor=doctor,
                disease=disease,
                status=status,
                min=min,
                max=max,
                sortBy=sortBy,
                order=order,
                page=page,
                pageSize=pageSize,
            )

            data = await client.list_pets(validated.to_query_params())

            try:
                output = ListPetsOutput.model_validate(data)
            except ValidationError as exc:
                # The backend answered, but not in the shape we model. Surface
                # the offending field names only — never Pydantic's message text.
                raise BackendInvalidResponseError(
                    details={
                        "reason": "backend data does not match the expected schema",
                        "fields": sorted({".".join(str(p) for p in err["loc"]) for err in exc.errors()}),
                    }
                ) from None
        except PetHospitalToolError as exc:
            return exc.to_call_tool_result()

        return CallToolResult(
            content=[TextContent(type="text", text=output.model_dump_json())],
            structured_content=output.model_dump(mode="json"),
        )
