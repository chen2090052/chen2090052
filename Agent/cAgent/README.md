# README.md
cAgent

## 目录结构
```
cAgent/
├── core/                # 稳定核心层（无UI感知）
│   ├── agent/           # Agent 主逻辑、工具调度、上下文
│   ├── config/          # 配置加载（env/toml/json/yaml）
│   ├── events/          # 事件抽象（AgentEventHandler）
│   ├── llm/             # LLM 客户端抽象（BaseLLMClient、ollama v1/原生api）
│   ├── schema/          # 数据模型
│   ├── tools/           # 工具注册与执行（复用 chat_stream_files.py 工具逻辑）
│   └── utils/           # 工具函数
├── tui/                 # Textual TUI 前端
└── main_tui.py          # TUI 入口
```

## 启动方式
```bash
python -m cAgent.tui.main_tui
# 或
python cAgent/tui/main_tui.py
```

## 配置
支持环境变量：
- CAGENT_LLM_BASE_URL / OLLAMA_BASE_URL
- CAGENT_LLM_API_KEY / OLLAMA_API_KEY
- CAGENT_MODEL / OLLAMA_MODEL
- CAGENT_LLM_API_TYPE: openai_v1 或 ollama_native
- QIANFAN_API_KEY / QIANFAN_SEARCH_URL / QIANFAN_SEARCH_TOP_K（联网搜索）

也可放置 config.toml/config.ini/config.json/config.yaml 到项目根目录或通过 CAGENT_CONFIG 指定。

联网搜索（`searchWeb` 工具）使用百度千帆 AI 搜索。API Key 放在 config.ini 中：

```ini
[search]
baidu_qianfan_api_key = bce-v3/...
```

可选参数可放在 config.toml（或 config.ini）的 `[search]` 节：

```toml
[search]
base_url = "https://qianfan.baidubce.com/v2/ai_search/web_search"
top_k = 5      # 返回结果条数，1~20
timeout = 30   # 超时秒数
```

网页正文获取（`fetchWeb` 工具）通过本地 `curl` 抓取 HTML，再由 trafilatura 提取正文并转为
Markdown。需安装 trafilatura（`pip install trafilatura`）并确保 `curl` 在 PATH 中。可选参数：

```toml
[fetch]
timeout = 30       # 抓取超时秒数，5~120
max_chars = 10000  # 返回给 LLM 的正文字符上限
# proxy = "http://127.0.0.1:7897"  # 需要代理时配置；curl 也会读取 HTTPS_PROXY
```

本地命令执行（`RunCli` 工具）以 argv 列表方式调用任意已安装的 CLI（`shell=False`），返回
stdout、stderr 与退出码，并对超时和过长输出进行处理。该工具仅用于受控教学实验环境，
不设命令白名单/黑名单或审批，命令以运行 cAgent 的当前用户权限执行。可选参数：

```toml
[cli]
timeout = 30             # 命令执行超时秒数，1~300
max_output_chars = 6000  # stdout / stderr 各自返回的最大字符数
```

同时存在 config.toml / config.ini 时会合并加载，同名配置项以 ini 为准。`config.toml`、`config.ini`
均含敏感信息，已在 .gitignore 中忽略，请勿提交。

## Skills
cAgent 启动时会把程序启动时的工作目录作为项目根目录，扫描 `.agents/skills/` 下的各个
Skill 子目录。每个子目录中的 `SKILL.md` 必须以下列 YAML Frontmatter 开头：

```markdown
---
name: my-skill
description: 一句话说明这个 Skill 适用于什么场景
---

Skill 正文……
```

初始化阶段只登记 `name` 与 `description`，正文保持惰性加载；需要时可通过
`Agent.skills.get("my-skill").load_body()` 读取。缺少 Frontmatter、必填字段或同名 Skill
会被跳过，原因记录在 `Agent.skills.warnings` 中。

发现 Skill 后，cAgent 会把名称和描述整理进 System Prompt，并注册 `loadSkill` 工具：

- `loadSkill(name)`：返回该 Skill 的 `SKILL.md` 全文；
- `loadSkill(name, filename)`：返回 Skill 目录内引用的附加文件。`filename` 支持
  Skill 相对路径、项目相对路径和绝对路径；只给文件名时会递归搜索，唯一匹配才读取，
  多个同名文件则返回候选路径。读取范围严格限制在该 Skill 目录内。

Skill 正文不会在初始化阶段进入对话上下文，只有 LLM 实际调用 `loadSkill` 时才会作为
工具结果加入消息列表，并与 `RunCli`、文件操作等已有工具共用同一套 Function Calling 流程。

## 扩展方式
1. 新增前端：在顶层新增 web/desktop 等目录，实现 `core.events.base.AgentEventHandler` 并在前端调用 `core.agent.agent.Agent(chat(...), event_handler=...)`。core 无需改动。
2. 新增 LLM 后端：继承 `core.llm.base.BaseLLMClient` 实现 `stream_chat`，在 `core/agent/agent.py` 中按配置选择即可。
3. 新增工具：在 `core.tools.executor.Tools` 注册 schema 并在 `execute` 增加分支，或后续改为插件注册模式。
4. 复用逻辑：流式请求、chunk 解析逻辑已从 `Practice02/chat_stream_files.py` 提取至 `core.llm.client`，工具逻辑提取至 `core.tools.executor`，业务编排在 `core.agent.agent`。
