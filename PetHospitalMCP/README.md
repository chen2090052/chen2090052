# 🐾 宠物医院 · Pet Hospital MCP Server

把现有 **Go 宠物医院 REST API** 的能力，以 **MCP（Model Context Protocol）** 的形式
暴露给 AI Agent 的独立 Python 服务。

- **独立服务**：只通过 HTTP 调用 Go REST API，不修改、不嵌入、不依赖其源码
- **无状态（stateless）**：每个请求自带上下文，没有握手、没有会话、没有会话过期
- **十个工具**：档案检索、单档案与诊疗摘要、病历与消费明细的查询和写入，以及统计、元信息与接口清单（详见 [UPGRADE_PROMPT.md](UPGRADE_PROMPT.md)）

| 项目 | 实际值 |
| --- | --- |
| Python | **3.11+**（开发环境实测 3.13.7） |
| MCP Python SDK | **`mcp==2.0.0`**（官方 SDK v2，锁版本） |
| MCP 协议版本 | **`2026-07-28`**（无状态修订版） |
| 服务端类 | **`mcp.server.mcpserver.MCPServer`**（**不是** `FastMCP`） |
| 传输 | **Streamable HTTP，`stateless_http=True`** |
| 上游业务后端 | Go Pet Hospital REST API（默认 `http://127.0.0.1:8080`） |

> **不使用 `mcp.server.fastmcp.FastMCP`。** SDK v2 已把 `FastMCP` 更名为 `MCPServer`
> 并迁移到 `mcp.server.mcpserver.*`；本项目直接使用新 API。
>
> **不实现** 旧协议的 `initialize` 握手、`Mcp-Session-Id`、会话存储、会话过期、
> `max_sessions`、SSE 恢复（event store / resumability）。

---

## 一、前置条件

### 1. 必须先启动 Go 宠物医院服务

MCP 服务是**纯适配器**，自身不存数据；Go 服务没起来，`list_pets` 会返回
`BACKEND_UNAVAILABLE`。

```bat
:: 进入发行包目录（本仓库内为 pet-hospital-windows-amd64\windows）
pethospital.exe
```

看到下面这行即表示就绪：

```text
✅ 服务已启动，请访问：  http://127.0.0.1:8080/
```

> 换端口时请同步设置 `PET_HOSPITAL_BASE_URL`，例如
> `pethospital.exe -addr 127.0.0.1:9090` 对应
> `PET_HOSPITAL_BASE_URL=http://127.0.0.1:9090`。

### 2. 安装本 MCP 服务

```bash
cd pet_hospital_mcp
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -e ".[dev]"
```

---

## 二、启动

```bash
cd pet_hospital_mcp
python -m pet_hospital_mcp
```

启动后终端会打印：

```text
  pet-hospital-mcp v0.1.0  (stateless MCP, protocol 2026-07-28)

  MCP endpoint   http://127.0.0.1:8765/mcp
  Health check   http://127.0.0.1:8765/health
  Upstream API   http://127.0.0.1:8080

  The MCP host must be running. Ctrl+C to stop.
```

也可以直接用控制台脚本：`pet-hospital-mcp`。

### 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PET_HOSPITAL_BASE_URL` | `http://127.0.0.1:8080` | Go REST API 地址 |
| `MCP_HOST` | `127.0.0.1` | MCP 监听地址（默认仅本机） |
| `MCP_PORT` | `8765` | MCP 监听端口（避开 Go 服务的 8080） |
| `MCP_PATH` | `/mcp` | MCP 端点路径 |
| `PET_HOSPITAL_TIMEOUT` | `10.0` | 单次后端请求超时（秒） |
| `PET_HOSPITAL_MAX_RETRIES` | `2` | 首次之外的重试次数 |
| `PET_HOSPITAL_BACKOFF` | `0.25` | 重试退避基数（秒） |
| `MCP_LOG_LEVEL` | `INFO` | 日志级别 |

示例：

```bash
MCP_PORT=9000 PET_HOSPITAL_BASE_URL=http://127.0.0.1:9090 python -m pet_hospital_mcp
```

### MCP 端点

```
POST http://127.0.0.1:8765/mcp     ← MCP（Streamable HTTP，无状态）
GET  http://127.0.0.1:8765/health  ← 健康检查
```

---

## 三、工具一览

所有工具名采用 `snake_case`，参数与后端 REST 参数**逐字对应**，不新增适配器私有
参数；调用失败统一返回「错误结构」一节描述的信封，且**写操作不会自动重试**
（非幂等，防止重复写入）。

| 工具 | 对应后端接口 | 说明 |
| --- | --- | --- |
| `list_pets` | `GET /api/v1/pets` | 档案检索（查询参数最全，见下表） |
| `get_pet` | `GET /api/v1/pets/{id}` | 单档案（含内嵌 records / charges） |
| `get_pet_summary` | `GET /api/v1/pets/{id}/summary` | 诊疗摘要（统计、花费分布、历史文本） |
| `list_pet_records` | `GET /api/v1/pets/{id}/records` | 历史病历明细 |
| `add_pet_record` | `POST /api/v1/pets/{id}/records` | 新增病历（写操作，不重试） |
| `list_pet_charges` | `GET /api/v1/pets/{id}/charges` | 消费明细 |
| `add_pet_charge` | `POST /api/v1/pets/{id}/charges` | 新增消费（写操作，不重试） |
| `get_stats` | `GET /api/v1/stats` | 统计（可选 `top`: 1–100） |
| `get_meta` | `GET /api/v1/meta` | 元信息（枚举、检索字段） |
| `get_endpoints` | `GET /api/v1/endpoints` | 接口清单 |

以 `list_pets`（严格对应后端 `GET /api/v1/pets`）为例，它**只支持且完整支持**
后端已有的查询参数：

| 参数 | 类型 | 约束 |
| --- | --- | --- |
| `q` | string | 跨字段全文检索，空格分词 AND，含病历正文 |
| `name` | string | 宠物姓名模糊匹配 |
| `ownerName` | string | 主人姓名 |
| `ownerPhone` | string | 主人电话 |
| `species` | string | `犬/猫/兔/鸟/仓鼠/爬宠/其他` |
| `doctor` | string | 主治医生 |
| `disease` | string | 疾病 |
| `status` | string | `待就诊/就诊中/住院中/已康复/慢性病随访` |
| `min` | number | 总花费下限，≥ 0，且 ≤ `max` |
| `max` | number | 总花费上限，≥ 0，且 ≥ `min` |
| `sortBy` | string | `id/name/ownerName/species/doctor/disease/status/totalCost/visitCount/createdAt/updatedAt` |
| `order` | string | `asc` / `desc` |
| `page` | integer | ≥ 1，默认 1 |
| `pageSize` | integer | 1–500，默认 20 |

**输入校验**（在调用后端之前完成）：枚举值必须合法、`page >= 1`、
`1 <= pageSize <= 500`、`min`/`max` 非负且 `min <= max`；拒绝未知字段、
`NaN`、`Infinity`、以及类型不正确的输入（如 `"1"`、`1.0`、`true` 作为 `page`）。
仅包含空白的字符串会被视为“未提供”。

**返回值**：与后端 `data` 一一对应。

```json
{
  "items": [ { "id": "PET-000937", "name": "摩卡", "species": "猫",
               "records": [ ... ], "charges": [ ... ], "totalCost": 1532.48 } ],
  "total": 1008,
  "page": 1,
  "pageSize": 20,
  "totalPages": 51,
  "totalCost": 3680172.85
}
```

> `records` 与 `charges` 在新建档案上会返回 `null`（而不是 `[]`），输出模型两种
> 形态都接受。

### 调用示例（SDK 2.x 客户端）

```python
import asyncio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
import httpx2

async def main():
    http_client = httpx2.AsyncClient()
    async with streamable_http_client("http://127.0.0.1:8765/mcp", http_client=http_client) as transport:
        async with Client(transport) as client:
            tools = await client.list_tools()
            print([t.name for t in tools.tools])
            # ['get_endpoints', 'get_meta', 'get_pet', 'get_pet_summary',
            #  'get_stats', 'list_pet_charges', 'list_pet_records', 'list_pets',
            #  'add_pet_charge', 'add_pet_record']（顺序即注册顺序）

            result = await client.call_tool("list_pets", {
                "species": "犬",
                "min": 1000,
                "sortBy": "totalCost",
                "order": "desc",
                "page": 1,
                "pageSize": 5,
            })
            print(result.is_error)                        # False
            print(result.structured_content["items"][0]["name"])

asyncio.run(main())
```

### 错误结构

所有失败（无效输入、后端异常、内部错误）都返回统一结构，并通过
`CallToolResult.is_error = true` 标记（SDK 2.x 的写法，线上字段为 `isError`）：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The tool input is invalid.",
    "details": { "fields": [ { "field": "page", "reason": "out_of_range" } ] }
  }
}
```

| `code` | 含义 |
| --- | --- |
| `VALIDATION_ERROR` | 工具入参不合法，**未调用后端** |
| `BACKEND_TIMEOUT` | 后端超时（含重试后仍超时；写操作不重试） |
| `BACKEND_UNAVAILABLE` | 后端连不上（拒绝连接 / DNS / 连接重置） |
| `BACKEND_API_ERROR` | 后端返回 4xx/5xx，或响应信封 `code != 200` |
| `BACKEND_INVALID_RESPONSE` | 后端返回的不是合法 JSON，或不符合建模的数据结构 |
| `INTERNAL_ERROR` | MCP 服务内部异常 |

**不会**把 HTTPX / Pydantic / SDK 的异常文本或 Python 堆栈返回给客户端：
`message` 是固定的可读文案，结构化上下文放在 `details`。

失败以**工具结果**（`isError: true` + `structuredContent`）返回，而不是 JSON-RPC
协议错误，也不是抛给 SDK 的异常 —— SDK 会把逃逸的异常压成一段纯文本，结构化信封
就丢失了。因此工具内部 `return exc.to_call_tool_result()`，
`ToolCallGuard` 中间件只做“放行 / 兜底替换”两件事。

---

## 四、`/health`

```bash
curl -s http://127.0.0.1:8765/health
```

```json
{
  "status": "ok",
  "service": "pet-hospital-mcp",
  "version": "0.1.0",
  "protocolVersion": "2026-07-28",
  "transport": "streamable-http",
  "stateless": true,
  "mcpEndpoint": "http://127.0.0.1:8765/mcp",
  "upstream": { "url": "http://127.0.0.1:8080", "reachable": true, "status": "healthy" }
}
```

存活语义：只要本进程在服务就返回 `200`；上游是否可达写在响应体里，
不会因为 Go 服务短暂不可用而让 MCP 服务本身显示为“已死”。Go 服务未启动时
`upstream` 形如：

```json
{ "url": "http://127.0.0.1:8080", "reachable": false, "error_code": "BACKEND_UNAVAILABLE" }
```

---

## 五、验证无状态 MCP 连接与调用

### 方式一：MCP Inspector

```bash
npx @modelcontextprotocol/inspector
```

1. Transport 选择 **Streamable HTTP**
2. URL 填 `http://127.0.0.1:8765/mcp`
3. Connect → 在 Tools 页签应看到 **`list_pets`**，并带完整 inputSchema
4. 填入参数（如 `{"species":"犬","page":1,"pageSize":5}`）→ Run

> 该连接**不会**发送 `initialize`，**不会**出现 `Mcp-Session-Id` 请求/响应头，
> 能力发现走 `server/discover`。

### 方式二：SDK 2.x 客户端

见上方「调用示例」，或直接运行本仓库的端到端测试：

```bash
cd pet_hospital_mcp
pytest -q tests/test_mcp_stateless.py
```

### 方式三：裸 HTTP（观察协议细节）

```bash
curl -s -X POST http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: server/discover' \
  -d '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{
        "io.modelcontextprotocol/protocolVersion":"2026-07-28",
        "io.modelcontextprotocol/clientCapabilities":{}}}}'
```

响应中 `result.supportedVersions` 应为 `["2026-07-28"]`，且响应头**没有**
`Mcp-Session-Id`。

路由头（SEP-2243）由 SDK 强制：

| 情况 | 结果 |
| --- | --- |
| `Mcp-Method` 缺失或与请求体 `method` 不一致 | `400`，JSON-RPC `-32020` |
| `Mcp-Name` 缺失或与 `tools/call` 的 `name` 不一致 | `400`，JSON-RPC `-32020` |
| `initialize` / `notifications/initialized` | `404`，JSON-RPC `-32601`（新协议已删除握手） |
| 未知方法 | `404`，JSON-RPC `-32601` |

### 方式四：接到 Claude Code

本服务是 **HTTP** MCP 服务，由 Claude Code 连接现成的 URL，**不由客户端拉起进程** ——
所以每次要先启动 Go 服务与 MCP 服务（见「一、前置」「二、启动」）。

在项目根的 `.mcp.json` 里加一条：

```json
{
  "mcpServers": {
    "pet-hospital-mcp": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

改完**重启 Claude Code**（或重载窗口）工具才会出现。验证：

```bash
claude mcp list
# pet-hospital-mcp: http://127.0.0.1:8765/mcp (HTTP) - ✓ Connected
```

> 连上本身就说明走的是新协议：服务端对 `initialize` 返回 `404`，
> 若客户端发的是旧握手，这里只会显示连接失败。

---

## 六、测试

```bash
cd pet_hospital_mcp
.venv\Scripts\activate          # 先激活虚拟环境（见「安装」）
pytest -q
```

**预期结果：全部通过，无 skip / xfail。**

```text
553 passed in ~40s
```

测试**不会访问真实 Go 服务**：所有后端调用都由 `httpx.MockTransport` 桩接，
端到端用例也在同一进程内用 stub 传输启动 MCP 服务。

| 文件 | 覆盖内容 |
| --- | --- |
| `tests/test_config.py` | 默认值、环境变量覆盖、非法配置报错 |
| `tests/test_errors.py` | 错误信封结构、`is_error` 标记、不泄漏内部文本 |
| `tests/test_logging_redaction.py` | `ownerPhone`/`ownerAddr`/`chipNo` 及 snake_case 的递归脱敏 |
| `tests/test_rest_client.py` | 请求路径与全部查询参数转发、路径编码、4xx/5xx、超时、连接异常、非法 JSON、重试、写操作不重试 |
| `tests/test_list_pets_tool.py` | `list_pets` 输入校验各条规则、输出模型兼容 `null`、JSON Schema |
| `tests/test_<tool>_tool.py` | 其余 9 个工具各自的输入/输出模型、JSON Schema、工具体、错误信封、写操作无重试 |
| `tests/test_tool_guard.py` | `ToolCallGuard` 中间件对所有 10 个工具的参数拒绝 / 放行，含未知工具 |
| `tests/test_mcp_stateless.py` | 工具注册与工具名、`/health`、无 `initialize`、无 `Mcp-Session-Id`、`server/discover`、HTTP 端点发现与调用、SDK 2.x 客户端全链路 |

---

## 七、目录结构

```text
pet_hospital_mcp/
├── pyproject.toml                 依赖与构建（mcp==2.0.0 锁版本）
├── README.md                      本文档
├── UPGRADE_PROMPT.md              阶段二新增工具的交接说明
├── src/pet_hospital_mcp/
│   ├── __init__.py
│   ├── __main__.py                入口：配置 → 日志 → uvicorn
│   ├── config.py                  环境变量配置与校验
│   ├── server.py                  MCPServer 装配、中间件、/health
│   ├── rest_client.py             Go REST API 客户端（超时/重试/错误映射）
│   ├── errors.py                  统一错误契约
│   ├── logging_config.py          JSON 日志与递归脱敏
│   └── tools/
│       ├── __init__.py            工具注册表（TOOL_INPUT_MODELS / register_tools）
│       ├── _shared.py             共享枚举、字段约束、输出模型
│       └── 10 个工具模块          见「三、工具一览」
└── tests/
```

---

## 八、说明与限制

- **无认证、无鉴权、无 CORS / Origin 校验**，且默认只监听 `127.0.0.1` —— 教学用途，
  请勿暴露到公网。
- 无状态意味着**任何请求都可以被任意副本处理**，适合 serverless / 多副本部署，
  代价是不支持服务端主动发起的请求（sampling / elicitation / roots）。
- 日志中的 `ownerPhone`、`ownerAddr`、`chipNo`（含 snake_case 写法）会被**递归**
  替换为 `***`；完整敏感数据不会落盘。
- 后端本身对非法查询值比较宽容（例如未知 `sortBy` 会被忽略、`pageSize` 超过 500
  会被悄悄截断），因此**严格校验由本适配器负责**，非法枚举在到达后端之前即被拒绝。
