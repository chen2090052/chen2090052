from abc import ABC, abstractmethod
from typing import AsyncIterator, Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class ToolCallChunk:
    index: int = 0
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class StreamDelta:
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCallChunk]] = None


class BaseLLMClient(ABC):
    @abstractmethod
    def stream_chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, **kwargs) -> AsyncIterator[StreamDelta]:
        raise NotImplementedError
