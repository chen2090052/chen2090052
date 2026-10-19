import configparser
import json
import locale
import os
import subprocess
import sys
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

config = configparser.ConfigParser()
config.read("config.ini", encoding="utf-8")

url = config["llm"]["url"]
api_key = config["llm"]["api_key"]
model = config["llm"]["model"]

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


def execute_tool(name, arguments):
    if name == "getDate":
        return get_date()
    if name == "isleapyear":
        return is_leap_year(arguments.get("year"))
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
