import configparser
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

        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": messages,
                "stream": False,
            },
        )
        content = resp.json()["choices"][0]["message"]["content"]
        print(content)
        messages.append({"role": "assistant", "content": content})
    except KeyboardInterrupt:
        break
