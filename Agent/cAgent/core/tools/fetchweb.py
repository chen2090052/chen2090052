"""fetchWeb 工具：用 curl 抓取网页，再用 trafilatura 提取正文并转为 Markdown。"""

import re
import socket
import subprocess
import urllib.parse

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_CHARS = 10000
MIN_CONTENT_CHARS = 80  # 低于此长度通常只是导航/广告等噪声
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


class _CurlError(Exception):
    """curl 调用失败，消息可直接作为工具结果返回。"""


def fetch_web(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    max_chars: int = DEFAULT_MAX_CHARS,
    proxy: str = "",
) -> str:
    """获取网页正文，返回包含原始 URL 的 Markdown 文本。

    所有异常都转换成可读的错误文本，不向调用方抛出。
    """
    url = (url or "").strip()
    if not url:
        return "获取失败：url 参数不能为空。"

    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return f"获取失败：无效的 URL（仅支持 http/https）：{url}"
    try:
        parts.port
    except ValueError:
        return f"获取失败：URL 端口不合法：{url}"

    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = DEFAULT_MAX_CHARS
    max_chars = max(1000, min(max_chars, 50000))

    try:
        raw = _curl_fetch(url, timeout, proxy)
    except _CurlError as exc:
        return str(exc)

    if not raw.strip():
        return f"获取失败：服务器返回了空响应：{url}"
    if len(raw) > MAX_DOWNLOAD_BYTES:
        return f"获取失败：网页内容超过 {MAX_DOWNLOAD_BYTES // 1024 // 1024} MB，已放弃解析：{url}"

    try:
        import trafilatura
    except ImportError:
        return "获取失败：未安装 trafilatura，请先执行 pip install trafilatura。"

    html_text = _decode_html(raw)
    try:
        markdown = trafilatura.extract(
            html_text,
            url=url,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            include_formatting=True,
            include_comments=False,
            deduplicate=True,
        )
    except Exception as exc:
        return f"获取失败：HTML 解析出错（{type(exc).__name__}: {exc}）：{url}"

    if not markdown or len(markdown.strip()) < MIN_CONTENT_CHARS:
        return (
            "获取失败：未能从该网页提取到正文，可能是图片/视频页、需要登录、"
            f"受反爬限制或正文过短：{url}"
        )

    markdown = _tidy_markdown(markdown)
    title = _extract_title(trafilatura, html_text, url)

    if len(markdown) > max_chars:
        markdown = markdown[:max_chars].rstrip() + "\n\n…（正文过长，已截断）"

    header = [f"URL: {url}"]
    if title:
        header.append(f"标题: {title}")
    return "\n".join(header) + "\n\n" + markdown


def _curl_fetch(url: str, timeout: int, proxy: str) -> bytes:
    """以参数列表方式调用 curl（不使用 shell=True），返回原始响应字节。"""
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = max(5, min(timeout, 120))
    command = [
        "curl",
        "--url", url,
        "--location",
        "--proto", "=http,https",
        "--proto-redir", "=http,https",
        "--silent",
        "--show-error",
        "--fail",
        "--compressed",
        "--max-time", str(timeout),
        "--connect-timeout", str(min(15, timeout)),
        "--max-filesize", str(MAX_DOWNLOAD_BYTES),
        "--user-agent", USER_AGENT,
        "--header", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "--header", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
    ]
    if proxy:
        command += ["--proxy", proxy]
    # 本机 IPv6 路由常很慢；站点有 A 记录时优先走 IPv4，纯 IPv6 站点仍可访问
    if _has_ipv4(url):
        command.append("--ipv4")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout + 10,
            check=False,
        )
    except FileNotFoundError:
        raise _CurlError("获取失败：未找到 curl，请确认系统已安装 curl 并加入 PATH。")
    except subprocess.TimeoutExpired:
        raise _CurlError(f"获取失败：请求超时（超过 {timeout + 10} 秒）：{url}")
    except OSError as exc:
        raise _CurlError(f"获取失败：无法启动 curl（{exc}）：{url}")

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()[:300]
        hint = _curl_error_hint(result.returncode)
        detail = stderr or f"curl 退出码 {result.returncode}"
        raise _CurlError(f"获取失败：{hint}（{detail}）：{url}")
    return result.stdout


def _curl_error_hint(code: int) -> str:
    return {
        6: "域名解析失败",
        7: "无法连接到服务器",
        22: "网页返回 HTTP 错误状态",
        28: "请求超时",
        35: "TLS/SSL 握手失败",
        52: "服务器未返回任何数据",
        56: "接收数据时连接中断",
        63: "网页内容超过大小限制",
    }.get(code, "网页访问失败")


def _has_ipv4(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        socket.getaddrinfo(parts.hostname, port, socket.AF_INET, socket.SOCK_STREAM)
        return True
    except Exception:
        return False


def _decode_html(raw: bytes) -> str:
    """按 BOM / meta charset 或常见编码解码 HTML。"""
    for bom, encoding in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            return raw.decode(encoding, "replace")

    head = raw[:4096].decode("ascii", "ignore")
    match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", head, re.IGNORECASE)
    candidates = []
    if match:
        candidates.append(match.group(1).lower())
    candidates += ["utf-8", "gb18030"]
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _extract_title(trafilatura, html_text: str, url: str) -> str:
    try:
        metadata = trafilatura.extract_metadata(html_text, default_url=url)
        title = (getattr(metadata, "title", "") or "").strip()
        return re.sub(r"\s+", " ", title)[:200]
    except Exception:
        return ""


def _tidy_markdown(markdown: str) -> str:
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip()
