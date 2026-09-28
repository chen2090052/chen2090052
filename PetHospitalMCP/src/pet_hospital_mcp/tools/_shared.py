"""Shared building blocks reused by every tool.

One module owns the backend vocabulary and the shared data models so the ten
tools can never drift apart: the enumerations the Go service accepts (from
``GET /api/v1/meta``), the ``Annotated`` constraint aliases backed by both the
tool signatures (→ JSON Schema) and the strict input models (→ runtime
validation), and the pet / record / charge output models.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# --- Values the Go backend accepts (from GET /api/v1/meta) ------------------ #

SpeciesName = Literal["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
StatusName = Literal["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"]
Gender = Literal["公", "母"]
ChargeCategory = Literal["检查", "药品", "手术", "住院", "疫苗", "护理", "其他"]
SortField = Literal[
    "id",
    "name",
    "ownerName",
    "species",
    "doctor",
    "disease",
    "status",
    "totalCost",
    "visitCount",
    "createdAt",
    "updatedAt",
]
SortOrder = Literal["asc", "desc"]

MAX_PAGE_SIZE = 500

# --- Shared constraint aliases --------------------------------------------- #
# Used by BOTH the tool signature (→ JSON Schema) and the strict input model
# (→ runtime validation), so the schema and the validator can never drift apart.
# Defaults live on the parameter/field assignment, not inside Field(...).

TextField = Annotated[
    Optional[str],
    Field(description="模糊匹配的文本条件；留空或省略表示不限制该条件。"),
]
QField = Annotated[
    Optional[str],
    Field(description="跨字段全文检索关键词，空格分词按 AND 组合，覆盖病历正文。"),
]
SpeciesField = Annotated[
    Optional[SpeciesName],
    Field(description="宠物种类，取值必须是后端枚举之一。"),
]
StatusField = Annotated[
    Optional[StatusName],
    Field(description="就诊状态，取值必须是后端枚举之一。"),
]
SortByField = Annotated[
    Optional[SortField],
    Field(description="排序字段；省略时使用后端默认顺序。"),
]
OrderField = Annotated[
    Optional[SortOrder],
    Field(description="排序方向，asc 升序 / desc 降序；通常与 sortBy 搭配使用。"),
]
MinField = Annotated[
    Optional[float],
    Field(ge=0, description="总花费下限（元，含）；必须 <= max。"),
]
MaxField = Annotated[
    Optional[float],
    Field(ge=0, description="总花费上限（元，含）；必须 >= min。"),
]
PageField = Annotated[
    int,
    Field(ge=1, description="页码，从 1 开始。"),
]
PageSizeField = Annotated[
    int,
    Field(ge=1, le=MAX_PAGE_SIZE, description="每页条数，1 到 500。"),
]

# --- Aliases for the pet-ID tools ------------------------------------------ #

IdField = Annotated[
    str,
    Field(min_length=1, pattern=r".*\S.*", description="宠物档案编号（必填），如 PET-000001。"),
]
TopField = Annotated[
    Optional[int],
    Field(ge=1, le=100, description="统计返回的消费排行榜条数，1 到 100；省略时使用后端默认条数。"),
]
MoneyField = Annotated[
    Optional[float],
    Field(ge=0, description="金额（元，非负）。"),
]
WeightField = Annotated[
    Optional[float],
    Field(gt=0, le=300, description="体重（公斤，0 到 300）。"),
]
TemperatureField = Annotated[
    Optional[float],
    Field(ge=30, le=45, description="体温（摄氏度，30 到 45）。"),
]
PrescriptionField = Annotated[
    Optional[list[str]],
    Field(description="处方药品列表，每项为药名。"),
]

# --- Output models ---------------------------------------------------------- #
# Field types mirror the Go service exactly. `records` and `charges` are
# Optional because a freshly created pet serialises them as `null`; unknown
# extra fields are ignored so backend additions cannot break a tool.


class PetRecord(BaseModel):
    """One entry of a pet's 历史病历 (medical history)."""

    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    visitDate: Optional[str] = None
    doctor: Optional[str] = None
    diagnosis: Optional[str] = None
    symptoms: Optional[str] = None
    treatment: Optional[str] = None
    prescription: Optional[list[str]] = None
    weightKg: Optional[float] = None
    temperature: Optional[float] = None
    followUp: Optional[str] = None
    charge: Optional[float] = None
    createdAt: Optional[str] = None


class PetCharge(BaseModel):
    """One entry of a pet's 消费明细 (billing line)."""

    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    item: Optional[str] = None
    category: Optional[str] = None
    amount: Optional[float] = None
    doctor: Optional[str] = None
    date: Optional[str] = None


class Pet(BaseModel):
    """A pet档案 as returned by the backend."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: Optional[str] = None
    species: Optional[str] = None
    breed: Optional[str] = None
    gender: Optional[str] = None
    ageMonths: Optional[int] = None
    color: Optional[str] = None
    chipNo: Optional[str] = None
    ownerName: Optional[str] = None
    ownerPhone: Optional[str] = None
    ownerAddr: Optional[str] = None
    doctor: Optional[str] = None
    disease: Optional[str] = None
    status: Optional[str] = None
    allergy: Optional[str] = None
    note: Optional[str] = None
    records: Optional[list[PetRecord]] = None
    charges: Optional[list[PetCharge]] = None
    totalCost: Optional[float] = None
    visitCount: Optional[int] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class OpaqueData(BaseModel):
    """Backend-owned response data passed through untouched.

    Write operations return whatever the backend decided to put in ``data``
    (typically the freshly created record / charge). Its shape is not modelled
    here because the backend owns it; unknown fields are preserved verbatim so
    nothing is dropped and no field is guessed.
    """

    model_config = ConfigDict(extra="allow")


def render_error_fields(exc: Any) -> list[str]:
    """Extract offending field paths from a Pydantic ``ValidationError``."""
    return sorted({".".join(str(part) for part in err["loc"]) for err in exc.errors()})