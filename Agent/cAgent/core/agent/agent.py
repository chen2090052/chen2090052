import json
from typing import Any, Dict, List, Optional
from ..llm.base import StreamDelta
from ..events.base import AgentEventHandler
from ..tools.executor import Tools
from ..skills.registry import SkillRegistry
from ..config.config import get_config


class Agent:
    def __init__(self, llm_client=None, event_handler: Optional[AgentEventHandler] = None):
        self.config = get_config()
        self.event_handler = event_handler
        self.skills = SkillRegistry().discover()
        self.tools = Tools(self.skills)
        self.system_prompt = self.skills.system_prompt()
        if llm_client is None:
            cfg = self.config.get("llm", default={})
            base_url = cfg.get("base_url") or "http://localhost:11434/v1"
            api_key = cfg.get("api_key") or "ollama"
            model = cfg.get("model") or "llama3.1"
            api_type = cfg.get("api_type") or "openai_v1"
            if api_type == "ollama_native":
                from ..llm.client import OllamaNativeClient
                self.llm = OllamaNativeClient(base_url=base_url, api_key=api_key, model=model)
            else:
                from ..llm.client import OllamaOpenAICompatClient
                self.llm = OllamaOpenAICompatClient(base_url=base_url, api_key=api_key, model=model)
        else:
            self.llm = llm_client
        self.messages: List[Dict[str, Any]] = []
        if self.system_prompt:
            self.messages.append({"role": "system", "content": self.system_prompt})

    def set_handler(self, event_handler: AgentEventHandler):
        self.event_handler = event_handler

    def chat(self, question: str):
        try:
            self.messages.append({"role": "user", "content": question})
            while True:
                answer = ""
                tool_calls_accum: List[Dict[str, Any]] = []
                for delta in self.llm.stream_chat(self.messages, tools=self.tools.get_schemas()):
                    if delta.content:
                        answer += delta.content
                        if self.event_handler:
                            self.event_handler.on_stream_delta(delta.content)
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index if hasattr(tc, "index") else 0
                            while len(tool_calls_accum) <= idx:
                                tool_calls_accum.append({"id": "", "function": {"name": "", "arguments": ""}})
                            call = tool_calls_accum[idx]
                            if tc.id:
                                call["id"] = tc.id
                            if hasattr(tc, "name") and tc.name:
                                call["function"]["name"] += tc.name
                            if hasattr(tc, "arguments") and tc.arguments:
                                call["function"]["arguments"] += tc.arguments
                if not tool_calls_accum:
                    self.messages.append({"role": "assistant", "content": answer})
                    if self.event_handler:
                        self.event_handler.on_assistant_message(answer)
                    return answer
                self.messages.append({"role": "assistant", "content": answer, "tool_calls": tool_calls_accum})
                for call in tool_calls_accum:
                    name = call["function"]["name"]
                    try:
                        arguments = json.loads(call["function"]["arguments"] or "{}")
                    except Exception:
                        arguments = {}
                    if self.event_handler:
                        self.event_handler.on_tool_call_start(name, call.get("id", ""))
                    result = self.tools.execute(name, arguments)
                    if self.event_handler:
                        self.event_handler.on_tool_call_result(name, call.get("id", ""), result)
                    self.messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "name": name, "content": result})
        except Exception as e:
            if self.event_handler:
                self.event_handler.on_error(e)
            raise
