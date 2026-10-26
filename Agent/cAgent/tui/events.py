from ..core.events.base import AgentEventHandler
from .messages import AgentError, AssistantDone, StreamDelta, ToolResult, ToolStart


class TUIEventHandler(AgentEventHandler):
    """core 回调 -> Textual 消息的适配层，不含任何业务逻辑。"""

    def __init__(self, app, session_id: str):
        self.app = app
        self.session_id = session_id

    def _post(self, message) -> None:
        # Agent.chat 在工作线程里执行时走 call_from_thread；
        # 在主线程调用时直接投递，避免 RuntimeError 导致界面无输出。
        try:
            self.app.call_from_thread(self.app.post_message, message)
        except Exception:
            self.app.post_message(message)

    def on_stream_delta(self, content: str) -> None:
        if content:
            self._post(StreamDelta(self.session_id, content))

    def on_tool_call_start(self, tool_name: str, tool_call_id: str) -> None:
        self._post(ToolStart(self.session_id, tool_name, tool_call_id))

    def on_tool_call_result(self, tool_name: str, tool_call_id: str, result: str) -> None:
        self._post(ToolResult(self.session_id, tool_name, tool_call_id, result))

    def on_assistant_message(self, content: str) -> None:
        self._post(AssistantDone(self.session_id, content))

    def on_error(self, error: Exception) -> None:
        self._post(AgentError(self.session_id, str(error)))
