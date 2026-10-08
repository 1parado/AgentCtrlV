"""M3 验收用的共用零件：探针 Agent、状态读取、真实点击。

与 m3_demo.py 分开，一是各自保持在 300 行以内，二是这些零件本身
可以在别的地方复用（例如以后做可靠性采样）。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.pop("QT_QPA_PLATFORM", None)

import psutil  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402

from src.core.agents import AgentSpec, InjectSpec, WindowSpec  # noqa: E402
from src.core.clipboard import ClipboardManager  # noqa: E402
from src.core.window import WindowLocator  # noqa: E402
from src.injectors.clipboard_injector import ClipboardInjector  # noqa: E402
from src.ui.radial_menu import RadialMenu  # noqa: E402

PROBE = ROOT / "scripts" / "paste_target.py"
WINDOW_TIMEOUT = 20.0


def probe_agent(agent_id: str, state_path: Path, title: str) -> AgentSpec:
    """一个把 launch 指向自有探针的 Agent——冷启动因此可以真跑而不碰用户的应用。"""
    return AgentSpec(
        id=agent_id,
        name=f"探针 {agent_id}",
        process=Path(sys.executable).name,
        launch=f'"{sys.executable}" "{PROBE}" "{state_path}" "{title}"',
        window=WindowSpec(title_pattern=f"{title}*"),
        inject=InjectSpec(paste_delay=200, render_delay=300),
    )


def read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def wait_for_text(path: Path, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        mime = read_state(path).get("last_mime") or {}
        if mime.get("has_text"):
            return True
        time.sleep(0.1)
    return False


def find_window(locator: WindowLocator, title: str):
    return locator.find(process=Path(sys.executable).name, title_pattern=f"{title}*")


def wait_for_window(locator: WindowLocator, title: str, timeout: float = WINDOW_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if find_window(locator, title) is not None:
            return True
        time.sleep(0.2)
    return False


def kill_by_title(locator: WindowLocator, title: str) -> None:
    window = find_window(locator, title)
    if window is None:
        return
    try:
        proc = psutil.Process(window.pid)
        proc.kill()
        proc.wait(timeout=5)
    except (psutil.Error, OSError):
        pass


class StubMenu:
    """只关心链路时用的空菜单。"""

    def __init__(self) -> None:
        self.shown = False

    def show_at(self, _pos) -> None:
        self.shown = True

    def isVisible(self) -> bool:  # noqa: N802 - 对齐 Qt
        return False


class TimingInjector:
    """用**真实** ClipboardInjector，只额外记下每次开火的时刻与结果。"""

    def __init__(
        self,
        agent: AgentSpec,
        manager: ClipboardManager,
        locator: WindowLocator,
        stamps: list,
        outcomes: list,
    ) -> None:
        self._agent = agent
        self._inner = ClipboardInjector(
            manager,
            locator,
            paste_delay_ms=agent.paste_delay_ms,
            render_delay_ms=agent.render_delay_ms,
        )
        self._stamps = stamps
        self._outcomes = outcomes

    def inject(self, payload, window):
        self._stamps.append(time.monotonic())
        outcome = self._inner.inject(payload, window)
        self._outcomes.append((self._agent.id, outcome.status.name))
        return outcome


def _press(menu: RadialMenu, point: QPointF, modifiers) -> None:
    menu.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            point,
            menu.mapToGlobal(point),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            modifiers,
        )
    )


def click_item(menu: RadialMenu, index: int, *, ctrl: bool) -> None:
    modifiers = Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier
    _press(menu, menu._item_center(index), modifiers)


def click_center(menu: RadialMenu) -> None:
    _press(menu, QPointF(menu._center, menu._center), Qt.KeyboardModifier.NoModifier)
