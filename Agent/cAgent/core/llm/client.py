import json
from typing import Any, Dict, List, Optional, AsyncIterator
from ..llm.base import BaseLLMClient, StreamDelta, ToolCallChunk


class OllamaOpenAICompatClient(BaseLLMClient):
    def __init__(self, base_url: str, api_key: str, model: str, **kwargs):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = kwargs.get("timeout", 300)

    def stream_chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, **kwargs) -> AsyncIterator[StreamDelta]:
        import requests
        if self.base_url.endswith("/chat/completions"):
            url = self.base_url
        elif self.base_url.endswith("/v1"):
            url = f"{self.base_url}/chat/completions"
        else:
            url = f"{self.base_url}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=self.timeout)
        resp.raise_for_status()

        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith(b"data: "):
                data = line[6:].decode("utf-8")
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except Exception:
                    continue
                content = delta.get("content")
                tool_calls = []
                for item in delta.get("tool_calls") or []:
                    index = item.get("index", 0)
                    tc = ToolCallChunk(index=index)
                    if item.get("id"):
                        tc.id = item["id"]
                    function = item.get("function") or {}
                    if function.get("name"):
                        tc.name = function["name"]
                    if function.get("arguments"):
                        tc.arguments = function["arguments"]
                    tool_calls.append(tc)
                yield StreamDelta(content=content, tool_calls=tool_calls or None)
        return


class OllamaNativeClient(BaseLLMClient):
    def __init__(self, base_url: str, api_key: str, model: str, **kwargs):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = kwargs.get("timeout", 300)

    def stream_chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, **kwargs) -> AsyncIterator[StreamDelta]:
        import requests
        if self.base_url.endswith("/api/chat"):
            url = self.base_url
        elif self.base_url.endswith("/v1"):
            url = f"{self.base_url.replace('/v1', '')}/api/chat"
        else:
            url = f"{self.base_url}/api/chat"
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key != "ollama":
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=self.timeout)
        resp.raise_for_status()

        for line in resp.iter_lines():
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            if data.get("done"):
                break
            message = data.get("message") or {}
            content = message.get("content")
            tool_calls = []
            for item in message.get("tool_calls") or []:
                tc = ToolCallChunk()
                func = item.get("function") or {}
                tc.name = func.get("name", "")
                args = func.get("arguments", {})
                if isinstance(args, dict):
                    tc.arguments = json.dumps(args)
                else:
                    tc.arguments = str(args)
                tool_calls.append(tc)
            yield StreamDelta(content=content, tool_calls=tool_calls or None)
        return
