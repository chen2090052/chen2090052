"""RunCli 工具：以 argv 列表方式执行本地 CLI，返回 stdout、stderr 和退出码。

仅用于受控教学环境：不设命令白名单/黑名单/审批，命令以运行 cAgent 的当前用户权限执行。
"""

import json
import locale
import os
import subprocess
import threading
import time
from typing import Any, List, Tuple

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_OUTPUT_CHARS = 6000
MAX_TIMEOUT = 300
MAX_CAPTURE_BYTES = 2 * 1024 * 1024  # 每个输出流最多缓存的字节数，防止失控输出耗尽内存


def run_cli(
    command: str,
    args: Any = None,
    timeout: Any = None,
    max_output_chars: Any = DEFAULT_MAX_OUTPUT_CHARS,
) -> str:
    """执行本地命令行程序，返回包含标准输出、标准错误和退出码的文本。

    command 与 args 会拼装成列表传给 subprocess，并显式使用 shell=False。
    所有异常都转换成可读的错误文本，不向调用方抛出。
    """
    command = command.strip() if isinstance(command, str) else ""
    if not command:
        return "执行失败：command 参数不能为空。"
    if "\x00" in command:
        return "执行失败：command 中不能包含空字符。"

    argv, error = _normalize_args(args)
    if error:
        return error
    if any("\x00" in item for item in argv):
        return "执行失败：args 中不能包含空字符。"

    timeout = max(1, min(_coerce_int(timeout, DEFAULT_TIMEOUT), MAX_TIMEOUT))
    max_chars = max(1000, min(_coerce_int(max_output_chars, DEFAULT_MAX_OUTPUT_CHARS), 30000))

    full_command = [command] + argv
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            full_command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except FileNotFoundError:
        return f"执行失败：找不到命令 {command!r}，请确认程序已安装并加入 PATH。"
    except PermissionError:
        return f"执行失败：没有权限执行命令 {command!r}。"
    except (OSError, ValueError) as exc:
        return f"执行失败：无法执行命令 {command!r}（{type(exc).__name__}: {exc}）。"

    # 用独立线程读取输出并限制缓存大小，避免程序失控输出时占用大量内存
    capture = _OutputCapture()
    readers = [
        threading.Thread(target=capture.read, args=(process.stdout, "stdout", process), daemon=True),
        threading.Thread(target=capture.read, args=(process.stderr, "stderr", process), daemon=True),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate(process)
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            returncode = -1

    for reader in readers:
        reader.join(timeout=2)

    elapsed = time.perf_counter() - started
    stdout_raw, stderr_raw, overflow = capture.snapshot()
    stdout = _decode_output(stdout_raw)
    stderr = _decode_output(stderr_raw)
    overflow_note = _overflow_note(overflow)

    if timed_out:
        lines = [
            f"执行超时：命令在 {timeout} 秒内未结束，进程已被终止（耗时 {elapsed:.2f} 秒）。",
            f"命令: {_format_command(full_command)}",
            f"工作目录: {os.getcwd()}",
        ]
        if overflow_note:
            lines.append(overflow_note)
        lines += [
            "",
            _format_stream("超时前的标准输出", stdout, max_chars),
            "",
            _format_stream("超时前的标准错误", stderr, max_chars),
        ]
        return "\n".join(lines)

    if returncode == 0:
        status = "成功"
    elif returncode < 0:
        status = f"被信号 {-returncode} 终止"
    else:
        status = "失败"

    lines = [
        f"命令: {_format_command(full_command)}",
        f"工作目录: {os.getcwd()}",
        f"退出码: {returncode}（{status}）",
        f"耗时: {elapsed:.2f} 秒",
    ]
    if overflow_note:
        lines.append(overflow_note)
    lines += [
        "",
        _format_stream("标准输出", stdout, max_chars),
        "",
        _format_stream("标准错误", stderr, max_chars),
    ]
    return "\n".join(lines)


class _OutputCapture:
    """并发读取 stdout/stderr，限制每个流缓存的字节数。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {"stdout": bytearray(), "stderr": bytearray()}
        self._overflow = set()

    def read(self, stream, key: str, process: subprocess.Popen) -> None:
        if stream is None:
            return
        read = getattr(stream, "read1", stream.read)
        try:
            while True:
                chunk = read(65536)
                if not chunk:
                    break
                overflowed = False
                with self._lock:
                    remaining = MAX_CAPTURE_BYTES - len(self._data[key])
                    if remaining > 0:
                        self._data[key].extend(chunk[:remaining])
                    if len(chunk) > max(remaining, 0):
                        self._overflow.add(key)
                        overflowed = True
                if overflowed:
                    _terminate(process)
                    break
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def snapshot(self) -> Tuple[bytes, bytes, set]:
        with self._lock:
            return (
                bytes(self._data["stdout"]),
                bytes(self._data["stderr"]),
                set(self._overflow),
            )


def _terminate(process: subprocess.Popen) -> None:
    try:
        if process.poll() is None:
            process.kill()
    except OSError:
        pass


def _overflow_note(overflow: set) -> str:
    if not overflow:
        return ""
    names = {"stdout": "标准输出", "stderr": "标准错误"}
    streams = "、".join(names.get(key, key) for key in ("stdout", "stderr") if key in overflow)
    return (
        f"注意：{streams}超过 {MAX_CAPTURE_BYTES // 1024 // 1024} MB 捕获上限，"
        "进程已被终止，仅保留已捕获内容。"
    )


def _normalize_args(args: Any) -> Tuple[List[str], str]:
    """把 args 规范化为字符串列表；失败时返回 (空列表, 错误信息)。"""
    if args is None:
        return [], ""
    if isinstance(args, str):
        return [], '执行失败：args 必须是字符串数组（例如 ["--version"]），不能是单个字符串。'
    if not isinstance(args, (list, tuple)):
        return [], "执行失败：args 必须是字符串数组。"

    values: List[str] = []
    for item in args:
        if isinstance(item, (dict, list, tuple)):
            return [], f"执行失败：args 中的每一项都必须是字符串，收到 {type(item).__name__}。"
        if item is None:
            return [], "执行失败：args 中不能包含 null。"
        if isinstance(item, bool):
            values.append("true" if item else "false")
        else:
            values.append(str(item))
    return values, ""


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _format_command(full_command: List[str]) -> str:
    return json.dumps(full_command, ensure_ascii=False) + "（shell=False）"


def _decode_output(raw: Any) -> str:
    """解码子进程输出：优先 UTF-8，其次本地编码/Windows ANSI 代码页（中文通常为 GBK）。"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return _normalize_newlines(raw)

    for bom, encoding in (
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
    ):
        if raw.startswith(bom):
            return _normalize_newlines(raw.decode(encoding, errors="replace"))

    for encoding in _candidate_encodings():
        try:
            return _normalize_newlines(raw.decode(encoding))
        except (LookupError, UnicodeDecodeError):
            continue
    return _normalize_newlines(raw.decode("utf-8", errors="replace"))


def _candidate_encodings() -> List[str]:
    candidates = ["utf-8"]
    seen = {"utf-8"}
    for encoding in (locale.getpreferredencoding(False), _windows_acp()):
        if encoding and encoding.lower() not in seen:
            seen.add(encoding.lower())
            candidates.append(encoding)
    if "gb18030" not in seen:
        candidates.append("gb18030")
    return candidates


def _windows_acp() -> str:
    """Windows ANSI 代码页（中文系统通常为 936/GBK）。"""
    if os.name != "nt":
        return ""
    try:
        import ctypes

        code_page = ctypes.windll.kernel32.GetACP()
        return f"cp{code_page}" if code_page else ""
    except Exception:
        return ""


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _format_stream(label: str, text: str, limit: int) -> str:
    text = _normalize_newlines(text).rstrip("\n")
    if not text.strip():
        return f"{label}: (无输出)"
    if len(text) <= limit:
        return f"{label}:\n{text}"

    head_size = int(limit * 0.75)
    tail_size = limit - head_size
    omitted = len(text) - limit
    return (
        f"{label}（已截断）:\n{text[:head_size]}\n"
        f"…（中间省略 {omitted} 个字符）…\n{text[-tail_size:]}"
    )
