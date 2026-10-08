# LLM 对话示例

一个极简的 Python 命令行工具，通过 OpenAI 兼容接口与大语言模型对话，支持一次性输出和打字机流式输出两种模式。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `chat.py` | 一次性对话，等待完整结果后输出 |
| `chat_stream.py` | 流式对话，逐字打字机效果输出 |
| `config.ini` | 配置文件：接口地址、api-key、模型名称 |

## 环境依赖

- Python 3.8+
- requests

```bash
pip install requests
```

## 配置

编辑 `config.ini`：

```ini
[llm]
url = https://api.openai.com/v1/chat/completions
api_key = sk-xxxxxxxxxxxxxxxx
model = gpt-4o-mini
```

- `url`：LLM 接口地址（任意 OpenAI 兼容接口均可）
- `api_key`：你的 API 密钥，不要提交到仓库
- `model`：模型名称

本地 Ollama 示例：

```ini
[llm]
url = http://localhost:11434/v1/chat/completions
api_key = ollama
model = qwen3-vl:4b-instruct
```

> AnythingLLM 桌面版（`http://localhost:3001`）是客户端，其自带 API 不兼容 OpenAI 格式；请填它所连接的模型服务地址（如 Ollama、vLLM 等）。

建议将 `config.ini` 加入 `.gitignore`，或使用 `config.ini.example` 作为模板。

## 运行

```bash
# 一次性输出
python chat.py

# 打字机流式输出
python chat_stream.py
```

运行后按提示输入问题，回车即可看到模型回复。
