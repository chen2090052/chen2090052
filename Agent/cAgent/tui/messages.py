from textual.message import Message


class AgentMessage(Message):
    """带会话标识的消息基类，UI 据此路由到对应会话。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__()


class StreamDelta(AgentMessage):
    def __init__(self, session_id: str, content: str) -> None:
        super().__init__(session_id)
        self.content = content


class AssistantDone(AgentMessage):
    def __init__(self, session_id: str, content: str) -> None:
        super().__init__(session_id)
        self.content = content


class ToolStart(AgentMessage):
    def __init__(self, session_id: str, name: str, call_id: str) -> None:
        super().__init__(session_id)
        self.name = name
        self.call_id = call_id


class ToolResult(AgentMessage):
    def __init__(self, session_id: str, name: str, call_id: str, result: str) -> None:
        super().__init__(session_id)
        self.name = name
        self.call_id = call_id
        self.result = result


class AgentError(AgentMessage):
    def __init__(self, session_id: str, error: str) -> None:
        super().__init__(session_id)
        self.error = error
