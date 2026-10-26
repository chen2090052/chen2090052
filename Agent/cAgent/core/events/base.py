from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from dataclasses import dataclass


@dataclass
class AgentEvent:
    type: str
    data: Dict[str, Any]


class AgentEventHandler(ABC):
    @abstractmethod
    def on_stream_delta(self, content: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def on_tool_call_start(self, tool_name: str, tool_call_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def on_tool_call_result(self, tool_name: str, tool_call_id: str, result: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def on_assistant_message(self, content: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def on_error(self, error: Exception) -> None:
        raise NotImplementedError
