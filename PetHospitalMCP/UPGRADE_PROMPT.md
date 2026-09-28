# 阶段二交接说明 / Upgrade Prompt

> **当前状态：阶段二已完成（10 个工具）。** 本文件同时记录新增工具时必须遵守的
> 既定约定，以便后续继续扩展（如 `/search`、`/export` 等剩余接口）。

本文件是给“下一位开发者（或下一个 AI 会话）”的交接说明：在新增工具时，需要遵守哪些
既定约定，才能不改动既有代码结构。

---

## 一、硬性约束（不可违反）

1. **不改动 Go 宠物医院服务。** 它是唯一业务后端，只能通过 HTTP 调用。
2. **保持无状态。** 不得引入 `initialize` 握手、`Mcp-Session-Id`、会话存储、
   会话过期、`max_sessions`、SSE 恢复 / event store。
3. **不使用 `mcp.server.fastmcp.FastMCP`。** 用 `mcp.server.mcpserver.MCPServer`。
4. **`mcp==2.0.0` 保持锁定。** 升级需同步复核 `MCPServer`、工具结果与错误语义。
5. **工具名用 snake_case**，参数名与后端 REST 参数**逐字对应**，
   不新增适配器私有业务参数。
6. **不把 HTTPX / Pydantic / SDK 的异常文本或 Python 堆栈暴露给客户端**，
   一律走统一错误结构。

---

## 二、新增一个工具的标准步骤

以新增 `get_pet`（`GET /api/v1/pets/{id}`）为例：

### 1. 新建 `src/pet_hospital_mcp/tools/get_pet.py`

按 `list_pets.py` 的结构组织：

```python
GET_PET_TOOL_NAME = "get_pet"

class GetPetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    id: Annotated[str, Field(min_length=1)]
    ...

class GetPetOutput(BaseModel):
    ...

GET_PET_INPUT_MODEL = GetPetInput

def register_get_pet(mcp: MCPServer, client: PetHospitalRestClient) -> None:
    @mcp.tool(name=GET_PET_TOOL_NAME, description=GET_PET_DESCRIPTION)
    async def get_pet(id: str) -> CallToolResult:
        ...
```

要点：

- **约束只写一份。** 用 `Annotated` 别名同时供“工具签名（→ JSON Schema）”和
  “输入模型（→ 运行时校验）”使用，二者不会漂移。
- **只转发调用方真正提供的参数**（`model_dump(exclude_none=True)`），
  让后端保留自己的默认值。
- **输出模型对 `null` 宽容。** Go 侧数组字段可能是 `null`（新建档案即如此），
  用 `Optional[list[T]] = None`；并设 `extra="ignore"`，后端加字段不会打断工具。
- **输出校验失败**抛 `BackendInvalidResponseError`，并把出错**字段名**（而非
  Pydantic 文案）放进 `details`。
- **错误必须 `return`，不要 `raise` 出去。** 用 `try/except PetHospitalToolError`
  包住整个工具体，`except` 里 `return exc.to_call_tool_result()`。
  SDK 会把逃逸的异常包成 `ToolError`，客户端只拿到一段纯文本，
  结构化错误信封就丢了；返回则原样保留 `structuredContent`。
  `tool_call` 中间件据此识别“我们自己的失败”并原样放行。
- **工具描述**必须写清：用途、每个参数、适用场景、返回值。

### 2. 在 `src/pet_hospital_mcp/tools/__init__.py` 注册

```python
TOOL_INPUT_MODELS: Mapping[str, type[BaseModel]] = {
    LIST_PETS_TOOL_NAME: LIST_PETS_INPUT_MODEL,
    GET_PET_TOOL_NAME: GET_PET_INPUT_MODEL,        # ← 新增
}

def register_tools(mcp, client) -> None:
    register_list_pets(mcp, client)
    register_get_pet(mcp, client)                  # ← 新增
```

中间件 `ToolCallGuard` 会自动读取 `TOOL_INPUT_MODELS`，无需改动 `server.py`：
严格入参校验、统一错误信封、JSON 日志（含脱敏）全部自动生效。

> `TOOL_INPUT_MODELS` 的键是**工具名常量**（如 `"get_pet"`），不是模型类的
> `__name__` —— `ToolCallGuard` 按工具名查表。
>
> SDK 按 `register_tools` 的**注册顺序**在 `tools/list` 里返回工具（字母序无关）。
> `test_mcp_stateless.py` 只断言 10 个工具名的集合，不锁顺序；`README.md` 展示了
> 当前注册顺序供参考。

> **SDK 2.x 中间件契约（踩过的坑）**：`HandlerResult = BaseModel | dict | None`，
> 内置 handler 返回的是**原始结果映射**，不是 `CallToolResult` 实例。
> 所以在中间件里判断失败要读 `result.get("isError")`、读信封要读
> `result.get("structuredContent")`（camelCase）；`tools/list` 同理，
> 要改 `result["tools"][i]["inputSchema"]`。按 Pydantic 模型去 `isinstance`
> 判断会静默失效。另外中间件运行在**参数校验之前**，`ctx.params` 是原始 dict。

### 3. 在 `src/pet_hospital_mcp/rest_client.py` 增加一个方法

```python
async def get_pet(self, pet_id: str) -> dict[str, Any]:
    return await self._request("GET", f"/api/v1/pets/{pet_id}")
```

超时、重试、状态码映射、信封解包都在 `_request` / `_parse` 里，无需重复实现。
路径中的 `{id}` 用静态方法 `_pet_path(pet_id)` 生成（`urllib.parse.quote(safe="")`
做 URL 编码）。

> 若新工具是**写操作**（POST/PUT/DELETE）：`_request` 会对 5xx 与传输异常重试，
> 对非幂等请求必须显式传 `retry=False`，否则可能重复写入 —— 已落地的
> `add_pet_record` / `add_pet_charge`，以及各自的工具测试都验证“写操作只发一次”。

### 4. 写工具的输出形状

`POST /records` 与 `POST /charges` 的返回 `data` 形状未在真实后端上探测过
（约束：只允许向真实后端发 GET）。因此写工具的输出模型使用 `OpaqueData`
（`extra="allow"`、零字段），把后端 `data` **原样透传**，不做形状猜测校验。

### 5. 补测试 `tests/test_<tool>.py`

至少覆盖：正常调用（路径 + 参数转发正确）、入参校验失败、后端 4xx/5xx、
超时与连接异常、非法 JSON / 不符合模型。后端一律用 `httpx.MockTransport` 桩接，
**禁止访问真实 Go 服务**。

---

## 三、当前尚未实现的后端能力（阶段三候选）

后端共有 29 个接口，已落地 10 个工具。其余可按需继续接入，例如：

| 方向 | 后端接口 | 状态 |
| --- | --- | --- |
| 单档案查询 | `GET /api/v1/pets/{id}` | ✅ 已实现（`get_pet`） |
| 诊疗摘要 | `GET /api/v1/pets/{id}/summary` | ✅ 已实现 |
| 病历 / 收费 | `GET\|POST /api/v1/pets/{id}/records`、`/charges` | ✅ 已实现 |
| 统计 / 字典 / 清单 | `/api/v1/stats`、`/meta`、`/endpoints` | ✅ 已实现 |
| 高级检索 | `/search`、`/by-owner`、`/by-doctor`、`/by-species`、`/by-disease`、`/by-status`、`/top-spenders`、`/cost-range` | ⬜ 候选 |
| 写操作 | `POST` / `PUT` / `PATCH` / `DELETE /api/v1/pets/{id}` | ⬜ 候选 |
| 导出 | `/api/v1/export` | ⬜ 候选 |

## 四、关于「入参校验」的测试路径

SDK 2.x 的进程内 `mcp_server.call_tool(name, args)` 会先用自己的签名模型校验参数，
**不合法时直接抛 `ToolError 异常`**，不会经过我们的中间件 —— 因此“非法参数 →
`VALIDATION_ERROR` 信封”这种断言**不要**写在基于 `call_tool` 的测例里。
统一放到 `tests/test_tool_guard.py`，在 `ToolCallGuard` 层验证
（`tool_ctx("tools/call", {"name": ..., "arguments": ...})` + 构造的 `next`）。
线上 HTTP 路径上，SDK 的签名校验失败会以 framework 结果返回，由中间件兜底替换成
我们的信封 —— `tests/test_mcp_stateless.py` 的 HTTP 用例覆盖了这条链路。
共有模型/帮助函数在 `tests/conftest.py`：`make_*` 构造器、`tool_by_name`、
`tool_schema`、`tool_ctx`、`guard` fixture。

## 五、验证清单

新增工具后，`README.md` 的工具表、参数表、工具数量描述需要同步更新，并跑通：

```bash
cd pet_hospital_mcp
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m pytest -q
```

> 注意：本机 venv 的可编辑安装路径是陈旧的
> （`.venv\Lib\site-packages\_editable_impl_pet_hospital_mcp.pth` 指向
> `E:\school\pet_hospital_mcp\src`，该目录不存在），因此务必带 `PYTHONPATH=src`
> 运行。修复：`pip install -e .` 重建可编辑安装。
