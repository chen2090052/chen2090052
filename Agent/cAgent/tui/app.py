"""cAgent TUI：深色双栏、多会话隔离的 Agent 聊天终端界面。"""

from dataclasses import dataclass, field
from typing import Dict, List

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, ListItem, ListView, RichLog

from cAgent.core.agent.agent import Agent
from cAgent.tui.events import TUIEventHandler
from cAgent.tui.messages import AgentError, AssistantDone, StreamDelta, ToolResult, ToolStart

# 三类消息的颜色约定
USER_STYLE = "bold green"
ASSISTANT_STYLE = "bold yellow"
MUTED_STYLE = "grey62"


def _preview(text: str, limit: int = 200) -> str:
    """工具结果压成单行并截断，避免刷屏。"""
    flat = text.replace("\n", " ⏎ ")
    return flat if len(flat) <= limit else flat[:limit] + "…"


@dataclass
class Session:
    """一个独立会话：独立 Agent（独立上下文）与独立的界面历史。"""

    sid: str
    title: str
    agent: Agent
    history: List[Text] = field(default_factory=list)
    pending: str = ""  # 本轮正在流式输出的内容
    busy: bool = False


class SessionItem(ListItem):
    """侧边栏中的单个会话条目。"""

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session_id = session.sid
        self._label = Label(session.title)

    def compose(self) -> ComposeResult:
        yield self._label


class ChatLog(RichLog):
    """RichLog 扩展：可原地重写末尾一段，用于流式打字机输出。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._stream_start = 0

    def begin_stream_block(self) -> None:
        self._stream_start = len(self.lines)

    def update_stream_block(self, content: Text) -> None:
        if self._stream_start < len(self.lines):
            del self.lines[self._stream_start:]
            self._line_cache.clear()
        self.write(content)


class CAgentApp(App):
    TITLE = "cAgent"
    CSS = """
    Screen {
        layout: horizontal;
        background: #101216;
        color: #d8dbe2;
    }

    /* ---------- 左侧会话栏 ---------- */
    #sidebar {
        width: 24;
        height: 100%;
        background: #171a21;
        border-right: solid #2b303b;
        padding: 0;
    }

    #new-session {
        width: 100%;
        margin: 0 0 1 0;
    }

    #sessions {
        height: 1fr;
        background: transparent;
        border: none;
    }

    ListView > ListItem {
        height: auto;
        padding: 0 1;
        background: transparent;
        color: #d8dbe2;
    }

    ListView > ListItem.-hovered {
        background: #222733;
    }

    ListView > ListItem.-highlight {
        background: #2a3550;
        color: #eef1f7;
        text-style: bold;
    }

    /* ---------- 右侧聊天区 ---------- */
    #main {
        width: 1fr;
        height: 100%;
        layout: vertical;
    }

    #log {
        height: 1fr;
        padding: 0 1;
        background: #101216;
        color: #d8dbe2;
        border: none;
    }

    #input {
        dock: bottom;
        background: #171a21;
        color: #eef1f7;
        border: tall #2b303b;
    }

    #input:focus {
        border: tall #4c8bf5;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.sessions: Dict[str, Session] = {}
        self.active_sid: str | None = None
        self._counter = 0

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Button("＋ 新建会话", id="new-session", variant="primary")
                yield ListView(id="sessions")
            with Vertical(id="main"):
                yield ChatLog(id="log", markup=True, wrap=True, min_width=20)
                yield Input(placeholder="输入消息，回车发送…", id="input")

    async def on_mount(self) -> None:
        self.theme = "textual-dark"
        self._log = self.query_one("#log", ChatLog)
        self._list = self.query_one("#sessions", ListView)
        self._input = self.query_one("#input", Input)
        await self._create_session()

    # ---------- 会话管理 ----------

    async def _create_session(self) -> None:
        self._counter += 1
        sid = f"session-{self._counter}"
        agent = Agent(event_handler=TUIEventHandler(self, sid))
        session = Session(sid, f"会话 {self._counter}", agent)
        self.sessions[sid] = session
        await self._list.append(SessionItem(session))
        self._list.index = len(self.sessions) - 1
        self._switch_session(sid)

    def _switch_session(self, sid: str) -> None:
        self.active_sid = sid
        self._render_session()

    def _render_session(self) -> None:
        """切换会话时清空日志，重绘该会话的历史与进行中的流式内容。"""
        session = self.sessions.get(self.active_sid)
        self._log.clear()
        if session is None:
            return
        for line in session.history:
            self._log.write(line)
        if not session.history and not session.pending:
            self._log.write(Text("输入问题开始新对话。", style=MUTED_STYLE))
        if session.pending:
            self._log.write(
                Text.assemble(("Assistant: ", ASSISTANT_STYLE), session.pending)
            )
        self._log.begin_stream_block()
        self._sync_input()

    def _sync_input(self) -> None:
        session = self.sessions.get(self.active_sid)
        busy = bool(session and session.busy)
        self._input.disabled = busy
        if not busy:
            self._input.focus()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new-session":
            await self._create_session()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SessionItem):
            self._switch_session(event.item.session_id)

    # ---------- 发送与执行 ----------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        session = self.sessions.get(self.active_sid)
        if not text or session is None or session.busy:
            return

        session.busy = True
        session.pending = ""
        user_line = Text.assemble(("You: ", USER_STYLE), text)
        session.history.append(user_line)
        self._log.write(user_line)
        self._log.begin_stream_block()
        self._sync_input()

        # LLM 请求与工具调度全在 core.Agent 内完成，UI 只消费回调
        self.run_worker(
            lambda: self._run_turn(session, text),
            thread=True,
            name=session.sid,
        )

    def _run_turn(self, session: Session, text: str) -> None:
        try:
            session.agent.chat(text)
        except Exception:
            pass  # 错误已由 on_error 回调上报
        finally:
            try:
                self.call_from_thread(self._turn_finished, session.sid)
            except Exception:
                pass

    def _turn_finished(self, sid: str) -> None:
        session = self.sessions.get(sid)
        if session is not None:
            session.busy = False
        if sid == self.active_sid:
            self._sync_input()

    def _flush_pending(self, session: Session) -> None:
        """把已流出的内容定稿为一条历史记录（工具调用/错误打断时使用）。"""
        if not session.pending:
            return
        line = Text.assemble(("Assistant: ", ASSISTANT_STYLE), session.pending)
        session.history.append(line)
        session.pending = ""
        if session.sid == self.active_sid:
            self._log.update_stream_block(line)
            self._log.begin_stream_block()

    # ---------- core 回调 -> 界面 ----------

    def on_stream_delta(self, message: StreamDelta) -> None:
        session = self.sessions.get(message.session_id)
        if session is None:
            return
        session.pending += message.content
        if session.sid == self.active_sid:
            self._log.update_stream_block(
                Text.assemble(("Assistant: ", ASSISTANT_STYLE), session.pending)
            )

    def on_assistant_done(self, message: AssistantDone) -> None:
        session = self.sessions.get(message.session_id)
        if session is None:
            return
        session.pending = ""
        if not message.content.strip():
            if session.sid == self.active_sid:
                self._log.begin_stream_block()
            return
        line = Text.assemble(("Assistant: ", ASSISTANT_STYLE), message.content)
        session.history.append(line)
        if session.sid == self.active_sid:
            self._log.update_stream_block(line)
            self._log.begin_stream_block()

    def on_tool_start(self, message: ToolStart) -> None:
        session = self.sessions.get(message.session_id)
        if session is None:
            return
        self._flush_pending(session)
        if session.sid == self.active_sid:
            self._log.write(Text(f"[tool:{message.name}] 执行中…", style=MUTED_STYLE))
            self._log.begin_stream_block()

    def on_tool_result(self, message: ToolResult) -> None:
        session = self.sessions.get(message.session_id)
        if session is None:
            return
        self._flush_pending(session)
        line = Text.assemble(
            (f"[tool:{message.name}] ", MUTED_STYLE), _preview(message.result)
        )
        session.history.append(line)
        if session.sid == self.active_sid:
            self._log.write(line)
            self._log.begin_stream_block()

    def on_agent_error(self, message: AgentError) -> None:
        session = self.sessions.get(message.session_id)
        if session is None:
            return
        self._flush_pending(session)
        line = Text.assemble(("Error: ", MUTED_STYLE), message.error)
        session.history.append(line)
        if session.sid == self.active_sid:
            self._log.write(line)
            self._log.begin_stream_block()


def run() -> None:
    CAgentApp().run()


if __name__ == "__main__":
    run()
