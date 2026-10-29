import html
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
from typing import Any, Dict, Optional, Tuple

from ..config.config import get_config
from ..skills.registry import SkillRegistry
from .fetchweb import fetch_web
from .runcli import DEFAULT_MAX_OUTPUT_CHARS, DEFAULT_TIMEOUT, run_cli

MAX_READ_BYTES = 200000

DEFAULT_SEARCH_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"
MAX_SUMMARY_CHARS = 400
MAX_RESULT_CHARS = 6000


def _clean_text(raw: Any) -> str:
    """去掉 HTML 标签、反转义实体并压缩空白。"""
    text = re.sub(r"<[^>]+>", " ", str(raw or ""))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int) -> Tuple[int, str]:
    """最小化的 HTTPS POST，返回 (状态码, 响应文本)。

    不用 requests：部分网络下该接口会重置 requests/urllib3 的 TLS 会话；
    同时该域名 IPv6 路由很慢，这里显式优先用 IPv4 建立连接。
    """
    import http.client
    import ssl
    import urllib.parse

    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if not host:
        raise ValueError(f"无效的搜索服务地址：{url}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    body = json.dumps(payload).encode("utf-8")

    request_headers = {"Content-Type": "application/json; charset=utf-8", "Connection": "close"}
    request_headers.update(headers)

    if parts.scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
        try:
            ipv4 = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        except OSError:
            ipv4 = None
        if ipv4:
            # TLS 仍按域名做 SNI 与证书校验，只是底层连到 IPv4 地址
            conn._create_connection = lambda addr, _timeout=None, source_address=None: socket.create_connection(
                (ipv4, port), timeout, source_address
            )
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)

    try:
        conn.request("POST", path, body=body, headers=request_headers)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", "replace")
    finally:
        conn.close()


def _describe_api_error(status: int, raw: str) -> str:
    """把 HTTP 错误响应整理成一句给 LLM 看的话。"""
    message = code = request_id = ""
    body: Any = None
    try:
        body = json.loads(raw)
    except Exception:
        pass
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("msg") or "")
            code = str(error.get("code") or error.get("type") or "")
        elif error:
            message = str(error)
        message = message or str(body.get("message") or body.get("msg") or "")
        code = code or str(body.get("code") or body.get("error_code") or "")
        request_id = str(body.get("request_id") or "")
    if not message:
        message = _clean_text(raw)[:300] or "服务端未返回错误详情"

    hint = {
        400: "请求参数有误",
        401: "API Key 无效或未通过鉴权，请检查 config.ini 中的 baidu_qianfan_api_key",
        403: "API Key 无权访问该接口，请确认已开通百度千帆 AI 搜索服务",
        429: "请求过于频繁，请稍后重试",
    }.get(status)
    if hint is None and status >= 500:
        hint = "搜索服务暂时不可用，请稍后重试"

    parts = [f"API 返回 HTTP {status}"]
    if code:
        parts.append(f"错误码 {code}")
    if message:
        parts.append(message)
    if hint:
        parts.append(hint)
    if request_id:
        parts.append(f"request_id={request_id}")
    return "，".join(parts)


def _decode_output(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except Exception:
        try:
            import locale
            return raw.decode(locale.getpreferredencoding(False) or "gbk", errors="replace")
        except Exception:
            return raw.decode("utf-8", errors="replace")


def _decode_bytes(raw: bytes):
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return None


def _is_text_bytes(raw: bytes) -> bool:
    if b"\x00" in raw:
        return False
    return _decode_bytes(raw[:8192]) is not None


class Tools:
    def __init__(self, skills: Optional[SkillRegistry] = None):
        self.skills = skills
        self._tools = [
            {
                "type": "function",
                "function": {
                    "name": "getDate",
                    "description": "获取当前电脑的日期和时间",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "isleapyear",
                    "description": "计算指定年份是否是闰年",
                    "parameters": {
                        "type": "object",
                        "properties": {"year": {"type": "integer", "description": "年份"}},
                        "required": ["year"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "searchWeb",
                    "description": (
                        "联网搜索互联网上的最新信息，返回搜索结果摘要及参考来源标题和 URL。"
                        "当问题涉及实时新闻、天气、价格、近期事件、事实核查，"
                        "或模型自身知识可能过时时，应优先调用本工具。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索关键词或自然语言问题",
                            }
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fetchWeb",
                    "description": (
                        "获取指定网页的正文内容并转换为 Markdown（保留标题、段落、列表、表格和链接）。"
                        "当用户提供 URL，或需要阅读 searchWeb 返回链接的完整内容时调用。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "要获取的网页地址，必须以 http:// 或 https:// 开头",
                            }
                        },
                        "required": ["url"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "RunCli",
                    "description": (
                        "在本机执行已安装的命令行程序（CLI），返回标准输出、标准错误和退出码。"
                        "command 为程序名或路径，args 为参数数组；命令以参数列表方式执行（shell=False），"
                        "因此管道、重定向、通配符等 Shell 语法不会生效。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "要执行的程序名称或路径，例如 python、git、C:\\Windows\\System32\\cmd.exe",
                            },
                            "args": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "命令行参数列表，每个参数单独一项；无参数时传 []",
                            },
                            "timeout": {
                                "type": "integer",
                                "description": "可选，执行超时秒数（1~300），默认 30",
                            },
                        },
                        "required": ["command", "args"],
                    },
                },
            },
            {"type": "function", "function": {"name": "getcwd", "description": "获取当前工作目录", "parameters": {"type": "object", "properties": {}, "required": []}}},
            {"type": "function", "function": {"name": "listdir", "description": "列出指定目录", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
            {"type": "function", "function": {"name": "getfiletype", "description": "获取文件类型", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
            {"type": "function", "function": {"name": "readfile", "description": "读取纯文本文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
            {"type": "function", "function": {"name": "createfile", "description": "创建文件", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["directory", "filename"]}}},
            {"type": "function", "function": {"name": "editfile", "description": "编辑文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "new_text"]}}},
            {"type": "function", "function": {"name": "createdir", "description": "创建目录", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
            {"type": "function", "function": {"name": "deletedir", "description": "删除目录", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
            {"type": "function", "function": {"name": "deletefile", "description": "删除文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
            {
                "type": "function",
                "function": {
                    "name": "loadSkill",
                    "description": (
                        "加载指定 Skill 的完整说明，或读取该 Skill 目录中引用的附加文件。"
                        "当任务与某个 Skill 的描述匹配，或用户要求使用/加载某个 Skill 时，"
                        "必须先调用本工具，不要只根据描述回答。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "要加载的 Skill 名称",
                            },
                            "filename": {
                                "type": "string",
                                "description": (
                                    "可选，需要加载的 Skill 文件路径或名称。"
                                    "支持相对于 Skill 目录的路径、项目相对路径及绝对路径；"
                                    "省略时返回 SKILL.md 全文"
                                ),
                            },
                        },
                        "required": ["name"],
                    },
                },
            },
        ]

    def get_schemas(self):
        return self._tools

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        if name == "loadSkill":
            if self.skills is None:
                return "当前未加载任何 Skill。"
            filename = (
                arguments.get("filename")
                or arguments.get("file")
                or arguments.get("path")
            )
            skill_name = (
                arguments.get("name")
                or arguments.get("skill")
                or arguments.get("skill_name")
            )
            return self.skills.load_skill(
                str(skill_name or "").strip(),
                str(filename) if filename else None,
            )
        if name == "searchWeb":
            return self._search_web(str(arguments.get("query") or ""))
        if name == "fetchWeb":
            return self._fetch_web(str(arguments.get("url") or ""))
        if name == "RunCli":
            return self._run_cli(
                str(arguments.get("command") or ""),
                arguments.get("args"),
                arguments.get("timeout"),
            )
        if name == "getDate":
            if os.name == "nt":
                command = ["cmd", "/c", "date /t & time /t"]
            else:
                command = ["date"]
            result = subprocess.run(command, capture_output=True)
            if result.returncode != 0:
                error = _decode_output(result.stderr).strip()
                return f"执行 date 命令失败：{error or result.returncode}"
            return _decode_output(result.stdout).strip()
        if name == "isleapyear":
            try:
                year = int(arguments.get("year"))
            except Exception:
                return f"年份参数不合法：{arguments.get('year')}"
            if year < 1:
                return f"年份参数不合法：{year}"
            leap = (year % 4 == 0 and year % 100 != 0) or year % 400 == 0
            return f"{year}年{'是' if leap else '不是'}闰年"
        if name == "getcwd":
            return os.getcwd()
        if name == "listdir":
            path = arguments.get("path") or "."
            path = os.path.abspath(os.path.expanduser(path))
            if not os.path.isdir(path):
                return f"目录不存在：{path}"
            entries = sorted(os.listdir(path))
            if not entries:
                return f"目录为空：{path}"
            lines = [f"{name}/" if os.path.isdir(os.path.join(path, name)) else name for name in entries]
            return f"目录 {path} 共 {len(entries)} 项：\n" + "\n".join(lines)
        if name == "getfiletype":
            path = os.path.abspath(os.path.expanduser(arguments.get("path") or ""))
            if not os.path.exists(path):
                return f"路径不存在：{path}"
            if os.path.isdir(path):
                return f"{path} 是一个目录"
            import mimetypes
            extension = os.path.splitext(path)[1]
            mime, _ = mimetypes.guess_type(path)
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                head = f.read(8192)
            kind = "文本文件" if _is_text_bytes(head) else "二进制文件"
            return f"{path} 是文件（{kind}），扩展名：{extension or '无'}，MIME 类型：{mime or '未知'}，大小：{size} 字节"
        if name == "readfile":
            path = os.path.abspath(os.path.expanduser(arguments.get("path") or ""))
            if not os.path.exists(path):
                return f"文件不存在：{path}"
            if os.path.isdir(path):
                return f"这是一个目录，不是文件：{path}"
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                raw = f.read(MAX_READ_BYTES + 1)
            if not _is_text_bytes(raw[:8192]):
                return f"只支持读取纯文本文件，该文件是二进制文件：{path}"
            text = _decode_bytes(raw[:MAX_READ_BYTES])
            if text is None:
                return f"无法按文本解码该文件：{path}"
            if size > MAX_READ_BYTES:
                return f"文件 {path} 过大（{size} 字节），仅返回前 {MAX_READ_BYTES} 字节：\n{text}"
            return text
        if name == "createfile":
            directory = os.path.abspath(os.path.expanduser(arguments.get("directory") or "."))
            filename = arguments.get("filename") or ""
            content = arguments.get("content") or ""
            target = os.path.join(directory, filename)
            if os.path.isdir(target):
                return f"同名目录已存在：{target}"
            if os.path.exists(target):
                return f"文件已存在：{target}"
            os.makedirs(directory, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            return f"已创建文件 {target}，写入 {len(content)} 个字符"
        if name == "editfile":
            path = os.path.abspath(os.path.expanduser(arguments.get("path") or ""))
            new_text = arguments.get("new_text") or ""
            old_text = arguments.get("old_text") or ""
            if not os.path.exists(path):
                return f"文件不存在：{path}"
            if os.path.isdir(path):
                return f"这是一个目录，不是文件：{path}"
            with open(path, "rb") as f:
                raw = f.read(MAX_READ_BYTES + 1)
            if len(raw) > MAX_READ_BYTES:
                return f"文件过大，不支持编辑：{path}"
            if not _is_text_bytes(raw[:8192]):
                return f"只支持编辑纯文本文件，该文件是二进制文件：{path}"
            content = _decode_bytes(raw)
            if content is None:
                return f"无法按文本解码该文件：{path}"
            if old_text:
                count = content.count(old_text)
                if count == 0:
                    return f"在 {path} 中未找到要替换的 old_text"
                content = content.replace(old_text, new_text)
                result = f"已编辑 {path}，替换 {count} 处"
            else:
                content += new_text
                result = f"已编辑 {path}，在末尾追加 {len(new_text)} 个字符"
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            return result
        if name == "createdir":
            path = os.path.abspath(os.path.expanduser(arguments.get("path") or "."))
            if os.path.exists(path):
                return f"路径已存在：{path}"
            os.makedirs(path, exist_ok=True)
            return f"已创建目录 {path}"
        if name == "deletedir":
            path = os.path.abspath(os.path.expanduser(arguments.get("path") or "."))
            if not os.path.exists(path):
                return f"路径不存在：{path}"
            if not os.path.isdir(path):
                return f"这不是目录：{path}"
            if path == os.path.abspath(os.sep) or path == os.getcwd():
                return f"出于安全考虑拒绝删除该目录：{path}"
            shutil.rmtree(path)
            return f"已删除目录（含全部内容）：{path}"
        if name == "deletefile":
            path = os.path.abspath(os.path.expanduser(arguments.get("path") or "."))
            if not os.path.exists(path):
                return f"路径不存在：{path}"
            if os.path.isdir(path):
                return f"这是一个目录，请使用 deletedir：{path}"
            os.remove(path)
            return f"已删除文件：{path}"
        return f"未知的工具：{name}"

    # ---------- 工具实现 ----------

    def _search_web(self, query: str) -> str:
        """调用百度千帆 AI 搜索（web_search），把摘要与参考来源整理成文本。"""
        query = query.strip()
        if not query:
            return "搜索失败：query 参数不能为空，请提供搜索关键词或问题。"

        cfg = get_config().get("search", default={}) or {}
        api_key = str(
            os.environ.get("QIANFAN_API_KEY")
            or cfg.get("baidu_qianfan_api_key")
            or cfg.get("api_key")
            or ""
        ).strip().strip('"').strip("'")
        if not api_key:
            return (
                "搜索失败：未配置百度千帆 API Key。请在 config.ini 的 [search] 节中设置 "
                "baidu_qianfan_api_key = bce-v3/...（或设置环境变量 QIANFAN_API_KEY）。"
            )

        url = str(
            os.environ.get("QIANFAN_SEARCH_URL") or cfg.get("base_url") or DEFAULT_SEARCH_URL
        ).strip()
        if not url.startswith(("http://", "https://")):
            return f"搜索失败：搜索服务地址无效：{url}"
        try:
            top_k = int(os.environ.get("QIANFAN_SEARCH_TOP_K") or cfg.get("top_k") or 5)
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(1, min(top_k, 20))
        try:
            timeout = int(cfg.get("timeout") or 30)
        except (TypeError, ValueError):
            timeout = 30
        timeout = max(5, min(timeout, 120))

        payload = {
            "messages": [{"role": "user", "content": query}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": top_k}],
        }
        try:
            status, text = _post_json(
                url, {"Authorization": f"Bearer {api_key}"}, payload, timeout
            )
        except (socket.timeout, TimeoutError):
            return f"搜索失败：请求超时（{timeout} 秒），请稍后重试。"
        except ssl.SSLError as exc:
            return f"搜索失败：TLS 连接失败（{exc}）。请检查网络、代理或证书配置。"
        except ConnectionError as exc:
            return f"搜索失败：无法连接搜索服务（{exc}）。请检查网络是否可用。"
        except OSError as exc:
            return f"搜索失败：网络错误（{exc}）。请检查 DNS 解析或网络连接。"
        except ValueError as exc:
            return f"搜索失败：{exc}"
        except Exception as exc:
            return f"搜索失败：{type(exc).__name__}: {exc}"

        if status != 200:
            return f"搜索失败：{_describe_api_error(status, text)}"
        try:
            data = json.loads(text)
        except Exception:
            return f"搜索失败：响应不是合法 JSON：{_clean_text(text)[:300]}"
        if not isinstance(data, dict):
            return "搜索失败：搜索服务返回了无法识别的响应格式。"
        if data.get("error"):
            return f"搜索失败：{_describe_api_error(status, text)}"
        if data.get("code") not in (None, "", 0, "0", 200, "200"):
            return f"搜索失败：{_describe_api_error(status, text)}"

        references = data.get("references") or []
        if not references:
            return f"未找到与“{query}”相关的结果，请尝试更换关键词。"

        lines = [f"搜索词：{query}", f"共 {len(references)} 条结果：", ""]
        for index, ref in enumerate(references[:top_k], 1):
            title = _clean_text(ref.get("title")) or "无标题"
            site = _clean_text(ref.get("website"))
            summary = _clean_text(ref.get("snippet") or ref.get("content"))
            if len(summary) > MAX_SUMMARY_CHARS:
                summary = summary[:MAX_SUMMARY_CHARS] + "…"

            heading = f"[{index}] {title}"
            if site:
                heading += f"（{site}）"
            lines.append(heading)
            if ref.get("url"):
                lines.append(f"URL: {ref['url']}")
            if ref.get("date"):
                lines.append(f"日期: {ref['date']}")
            if summary:
                lines.append(f"摘要: {summary}")
            lines.append("")

        text = "\n".join(lines).strip()
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n…（结果过长已截断）"
        return text

    def _fetch_web(self, url: str) -> str:
        """抓取网页并提取正文，超时、截断长度等参数可在 [fetch] 节配置。"""
        cfg = get_config().get("fetch", default={}) or {}
        try:
            timeout = int(cfg.get("timeout") or 30)
        except (TypeError, ValueError):
            timeout = 30
        try:
            max_chars = int(cfg.get("max_chars") or 10000)
        except (TypeError, ValueError):
            max_chars = 10000
        proxy = str(cfg.get("proxy") or "").strip()
        return fetch_web(
            url,
            timeout=max(5, min(timeout, 120)),
            max_chars=max(1000, min(max_chars, 50000)),
            proxy=proxy,
        )

    def _run_cli(self, command: str, args: Any = None, timeout: Any = None) -> str:
        """执行本地 CLI，超时与输出上限可在 [cli] 节配置。"""
        cfg = get_config().get("cli", default={}) or {}
        if timeout in (None, "", 0, "0"):
            timeout = cfg.get("timeout") or DEFAULT_TIMEOUT
        try:
            max_chars = int(cfg.get("max_output_chars") or DEFAULT_MAX_OUTPUT_CHARS)
        except (TypeError, ValueError):
            max_chars = DEFAULT_MAX_OUTPUT_CHARS
        return run_cli(command, args, timeout=timeout, max_output_chars=max_chars)
