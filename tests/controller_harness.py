"""控制器测试的共享假件。

放在单独模块里，让"业务流程"与"窗口定位"两组测试各自保持在 300 行以内，
同时共用同一套假件（避免两份定义慢慢漂移）。
"""

from __future__ import annotations

from src.core.agents import AgentSpec, InjectSpec, WindowSpec
from src.core.controller import Controller
from src.core.window import WindowInfo
from src.injectors.base import InjectionOutcome, InjectionStatus

WINDOW = WindowInfo(hwnd=1001, title="ChatGPT", pid=42, process="ChatGPT.exe")
TERMINAL_WINDOW = WindowInfo(hwnd=1002, title="终端", pid=43, process="WindowsTerminal.exe")

#: 测试用 Agent 集合（不改产品配置；产品配置见 config/agents/*.yaml）
TEST_AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        id="chatgpt-desktop",
        name="ChatGPT",
        type="gui",
        process="ChatGPT.exe",
        window=WindowSpec(title_pattern="ChatGPT*"),
    ),
    AgentSpec(
        id="cursor",
        name="Cursor",
        type="gui",
        process="Cursor.exe",
        window=WindowSpec(title_pattern="Cursor*"),
    ),
    AgentSpec(
        id="windows-terminal",
        name="Windows Terminal",
        type="cli",
        process="WindowsTerminal.exe",
        supported_payloads=["text"],
        inject=InjectSpec(paste_delay=200, render_delay=300),
    ),
)


class FakeClipboard:
    def __init__(self, image=None, text=None):
        self.image = image
        self.text = text

    def read_image(self):
        return self.image

    def read_text(self):
        return self.text


class FakeLocator:
    def __init__(self, window=WINDOW):
        self.window = window
        self.asks: list[tuple] = []

    def find(self, *, process=None, title_pattern=None):
        self.asks.append((process, title_pattern))
        return self.window

    def find_host_window(self, cli_match, *, terminal_process=None):
        self.asks.append((cli_match, terminal_process))
        return self.window

    def describe_candidates(self, process: str, *, limit: int = 6) -> str:
        return f"{process} 当前没有任何可见窗口"


class FakeMenu:
    def __init__(self):
        self.shown: list = []
        self.visible = False

    def show_at(self, pos):
        self.shown.append(pos)
        self.visible = True

    def isVisible(self) -> bool:  # noqa: N802 - 对齐 Qt 命名
        return self.visible


class FakeInjector:
    def __init__(self, outcome=None):
        self.outcome = outcome or InjectionOutcome(InjectionStatus.SUCCESS)
        self.calls: list[tuple] = []

    def inject(self, payload, window):
        self.calls.append((payload, window))
        return self.outcome


class Harness:
    def __init__(self, *, image=None, text=None, window=WINDOW, outcome=None):
        self.clipboard = FakeClipboard(image, text)
        self.locator = FakeLocator(window)
        self.menu = FakeMenu()
        self.injector = FakeInjector(outcome)
        self.notifications: list[tuple] = []

    def notify(self, title, message, **kwargs):
        self.notifications.append((title, message, kwargs))

    def build(self, **kwargs) -> Controller:
        kwargs.setdefault("cursor_pos", lambda: "cursor")
        kwargs.setdefault("agents", TEST_AGENTS)
        return Controller(
            clipboard=self.clipboard,
            locator=self.locator,
            menu=self.menu,
            notifier=self.notify,
            injector_factory=lambda agent: self.injector,
            **kwargs,
        )

    @property
    def warnings(self):
        return [n for n in self.notifications if n[2].get("warning")]
