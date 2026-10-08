import configparser
import json
import sys
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

config = configparser.ConfigParser()
config.read("config.ini", encoding="utf-8")

url = config["llm"]["url"]
api_key = config["llm"]["api_key"]
model = config["llm"]["model"]

messages = []

while True:
    try:
        question = input("请输入问题：")
        messages.append({"role": "user", "content": question})
        answer = ""

        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": messages,
                "stream": True,
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

        print()
        messages.append({"role": "assistant", "content": answer})
    except KeyboardInterrupt:
        break
