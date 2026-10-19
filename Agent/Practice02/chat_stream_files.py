import configparser
import json
import locale
import mimetypes
import os
import shutil
import subprocess
import sys
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

config = configparser.ConfigParser()
config.read("config.ini", encoding="utf-8")

url = config["llm"]["url"]
api_key = config["llm"]["api_key"]
model = config["llm"]["model"]

MAX_READ_BYTES = 200000

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间，通过调用本地系统的 date 命令实现",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isleapyear",
            "description": "计算指定年份是否是闰年，通过 Python 代码完成计算",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "需要判断的年份，例如 2024",
                    }
                },
                "required": ["year"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getcwd",
            "description": "获取当前工作目录",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listdir",
            "description": "列出指定目录下的所有文件和子目录，子目录名以 / 结尾",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要列出内容的目录路径，例如 E:/school 或 .",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getfiletype",
            "description": "获取指定文件的文件类型（扩展名、MIME 类型、文本或二进制）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件的完整路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readfile",
            "description": "获取指定文件的内容，仅支持纯文本格式文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的纯文本文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createfile",
            "description": "根据指定的目录和文件名创建新文件，可以同时写入初始内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "新文件所在的目录路径，例如 E:/school/practice02",
                    },
                    "filename": {
                        "type": "string",
                        "description": "新文件名，例如 note.txt",
                    },
                    "content": {
                        "type": "string",
                        "description": "写入文件的初始内容，可省略，默认为空文件",
                    },
                },
                "required": ["directory", "filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "editfile",
            "description": "编辑指定文件的内容，仅支持纯文本格式文件；给 old_text 则替换，否则在末尾追加 new_text",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要编辑的纯文本文件路径",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "要被替换的原文本，省略或留空表示追加",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "替换后的新文本，或要追加的文本",
                    },
                },
                "required": ["path", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createdir",
            "description": "在指定路径创建新目录，父目录不存在时会一并创建",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要创建的目录路径，例如 E:/school/practice02/newdir",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deletedir",
            "description": "删除指定目录及其全部内容（支持非空目录，递归删除）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要删除的目录路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deletefile",
            "description": "删除指定的文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要删除的文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
]


def decode_output(raw):
    for encoding in (locale.getpreferredencoding(False), "gbk", "utf-8"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def decode_bytes(raw):
    for encoding in ("utf-8", locale.getpreferredencoding(False), "gbk"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def is_text_bytes(raw):
    if b"\x00" in raw:
        return False
    return decode_bytes(raw) is not None


def get_date():
    if os.name == "nt":
        command = ["cmd", "/c", "date /t & time /t"]
    else:
        command = ["date"]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        error = decode_output(result.stderr).strip()
        return f"执行 date 命令失败：{error or result.returncode}"
    return decode_output(result.stdout).strip()


def is_leap_year(year):
    try:
        year = int(year)
    except (TypeError, ValueError):
        return f"年份参数不合法：{year}"
    if year < 1:
        return f"年份参数不合法：{year}"
    leap = (year % 4 == 0 and year % 100 != 0) or year % 400 == 0
    return f"{year}年{'是' if leap else '不是'}闰年"


def get_cwd():
    return os.getcwd()


def list_dir(path):
    if not path:
        path = "."
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        return f"目录不存在：{path}"
    entries = sorted(os.listdir(path))
    if not entries:
        return f"目录为空：{path}"
    lines = [f"{name}/" if os.path.isdir(os.path.join(path, name)) else name for name in entries]
    return f"目录 {path} 共 {len(entries)} 项：\n" + "\n".join(lines)


def get_file_type(path):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        return f"路径不存在：{path}"
    if os.path.isdir(path):
        return f"{path} 是一个目录"
    extension = os.path.splitext(path)[1]
    mime, _ = mimetypes.guess_type(path)
    size = os.path.getsize(path)
    with open(path, "rb") as file:
        head = file.read(8192)
    kind = "文本文件" if is_text_bytes(head) else "二进制文件"
    return (
        f"{path} 是文件（{kind}），扩展名：{extension or '无'}，"
        f"MIME 类型：{mime or '未知'}，大小：{size} 字节"
    )


def read_file(path):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        return f"文件不存在：{path}"
    if os.path.isdir(path):
        return f"这是一个目录，不是文件：{path}"
    size = os.path.getsize(path)
    with open(path, "rb") as file:
        raw = file.read(MAX_READ_BYTES + 1)
    if not is_text_bytes(raw[:8192]):
        return f"只支持读取纯文本文件，该文件是二进制文件：{path}"
    text = decode_bytes(raw[:MAX_READ_BYTES])
    if text is None:
        return f"无法按文本解码该文件：{path}"
    if size > MAX_READ_BYTES:
        return f"文件 {path} 过大（{size} 字节），仅返回前 {MAX_READ_BYTES} 字节：\n{text}"
    return text


def create_file(directory, filename, content=""):
    directory = os.path.abspath(os.path.expanduser(directory or "."))
    target = os.path.join(directory, filename)
    if os.path.isdir(target):
        return f"同名目录已存在：{target}"
    if os.path.exists(target):
        return f"文件已存在：{target}"
    os.makedirs(directory, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as file:
        file.write(content or "")
    return f"已创建文件 {target}，写入 {len(content or '')} 个字符"


def edit_file(path, new_text, old_text=""):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        return f"文件不存在：{path}"
    if os.path.isdir(path):
        return f"这是一个目录，不是文件：{path}"
    with open(path, "rb") as file:
        raw = file.read(MAX_READ_BYTES + 1)
    if len(raw) > MAX_READ_BYTES:
        return f"文件过大，不支持编辑：{path}"
    if not is_text_bytes(raw[:8192]):
        return f"只支持编辑纯文本文件，该文件是二进制文件：{path}"
    content = decode_bytes(raw)
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
    with open(path, "w", encoding="utf-8", newline="") as file:
        file.write(content)
    return result


def create_dir(path):
    path = os.path.abspath(os.path.expanduser(path or "."))
    if os.path.exists(path):
        return f"路径已存在：{path}"
    os.makedirs(path, exist_ok=True)
    return f"已创建目录 {path}"


def delete_dir(path):
    path = os.path.abspath(os.path.expanduser(path or "."))
    if not os.path.exists(path):
        return f"路径不存在：{path}"
    if not os.path.isdir(path):
        return f"这不是目录：{path}"
    if path == os.path.abspath(os.sep) or path == os.getcwd():
        return f"出于安全考虑拒绝删除该目录：{path}"
    shutil.rmtree(path)
    return f"已删除目录（含全部内容）：{path}"


def delete_file(path):
    path = os.path.abspath(os.path.expanduser(path or "."))
    if not os.path.exists(path):
        return f"路径不存在：{path}"
    if os.path.isdir(path):
        return f"这是一个目录，请使用 deletedir：{path}"
    os.remove(path)
    return f"已删除文件：{path}"


def execute_tool(name, arguments):
    if name == "getDate":
        return get_date()
    if name == "isleapyear":
        return is_leap_year(arguments.get("year"))
    if name == "getcwd":
        return get_cwd()
    if name == "listdir":
        return list_dir(arguments.get("path"))
    if name == "getfiletype":
        return get_file_type(arguments.get("path"))
    if name == "readfile":
        return read_file(arguments.get("path"))
    if name == "createfile":
        return create_file(
            arguments.get("directory"),
            arguments.get("filename"),
            arguments.get("content", ""),
        )
    if name == "editfile":
        return edit_file(
            arguments.get("path"),
            arguments.get("new_text", ""),
            arguments.get("old_text", ""),
        )
    if name == "createdir":
        return create_dir(arguments.get("path"))
    if name == "deletedir":
        return delete_dir(arguments.get("path"))
    if name == "deletefile":
        return delete_file(arguments.get("path"))
    return f"未知的工具：{name}"


def chat(question, messages):
    messages.append({"role": "user", "content": question})

    while True:
        answer = ""
        tool_calls = []
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "tools": TOOLS,
            },
            stream=True,
        )

        for line in resp.iter_lines():
            if line and line.startswith(b"data: "):
                data = line[6:].decode("utf-8")
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0].get("delta", {})
                content = delta.get("content")
                if content:
                    answer += content
                    print(content, end="", flush=True)
                for item in delta.get("tool_calls") or []:
                    index = item.get("index", 0)
                    while len(tool_calls) <= index:
                        tool_calls.append({"id": "", "function": {"name": "", "arguments": ""}})
                    call = tool_calls[index]
                    if item.get("id"):
                        call["id"] = item["id"]
                    function = item.get("function") or {}
                    if function.get("name"):
                        call["function"]["name"] += function["name"]
                    if function.get("arguments"):
                        call["function"]["arguments"] += function["arguments"]

        print()

        if not tool_calls:
            messages.append({"role": "assistant", "content": answer})
            return answer

        messages.append({"role": "assistant", "content": answer, "tool_calls": tool_calls})
        for call in tool_calls:
            name = call["function"]["name"]
            try:
                arguments = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            result = execute_tool(name, arguments)
            print(f"[tool:{name}] {result}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": name,
                    "content": result,
                }
            )


messages = []

while True:
    try:
        question = input("请输入问题：")
        chat(question, messages)
    except (KeyboardInterrupt, EOFError):
        break
