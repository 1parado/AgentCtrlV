"""M5：失败矩阵全部覆盖通知。

ARCHITECTURE.md「失败矩阵（摘要）」里的每一行，都必须能在真实链路上
（真实 Controller + 真实 ClipboardInjector）产生一条**用户看得见**的通知。
AGENTS.md 的「失败必须可见，不能静默」这句话，只有被测试钉住才算数。

每一条测试的名字都对应文档里的一行，改文档时请一起来改这里。
"""

from __future__ import annotations

import pytest
from PIL import Image

from src.core.clipboard import ClipboardError
from src.core.cold_start import ColdStartResult
from src.core.payload import Payload
from src.core.permissions import PermissionVerdict
from src.injectors.base import InjectionOutcome, InjectionStatus
from src.injectors.clipboard_injector import ClipboardInjector
from tests.controller_harness import TERMINAL_WINDOW, WINDOW, Harness


class StubClipboard:
    """真实注入器要的那几个方法，行为可注入。"""

    def __init__(self, *, fail_capture: bool = False) -> None:
        self.fail_capture = fail_capture
        self.written: list[str] = []
        self.restored = 0

    def capture(self):
        if self.fail_capture:
            raise ClipboardError("剪贴板被占用")
        from src.core.clipboard_snapshot import ClipboardSnapshot, TextEntry

        snapshot = ClipboardSnapshot()
        snapshot.entries[1] = TextEntry("原有的东西")
        return snapshot

    def write_text(self, text: str) -> None:
        self.written.append(text)

    def write_image(self, image) -> None:
        self.written.append("<image>")

    def restore(self, snapshot) -> bool:
        self.restored += 1
        return True


class StubLocator:
    def __init__(self, *, activate: bool = True) -> None:
        self._activate = activate

    def activate(self, hwnd, timeout=None) -> bool:
        return self._activate

    def find(self, **_kwargs):
        return WINDOW

    def find_host_window(self, *_args, **_kwargs):
        return TERMINAL_WINDOW

    def describe_candidates(self, process: str, *, limit: int = 6) -> str:
        return ""


def real_injector(clipboard=None, locator=None, **overrides) -> ClipboardInjector:
    """真注入器 + 假依赖：通知路径必须由产品代码产生，不能由测试伪造。"""
    kwargs = {
        "paste_delay_ms": 0,
        "render_delay_ms": 0,
        "paste_fn": lambda: None,
        "sleep_fn": lambda _s: None,
        "foreground_fn": lambda: WINDOW.hwnd,
    }
    kwargs.update(overrides)
    return ClipboardInjector(clipboard or StubClipboard(), locator or StubLocator(), **kwargs)


class ColdStarterFailing:
    def __init__(self, detail: str) -> None:
        self.detail = detail

    def ensure_window(self, _agent) -> ColdStartResult:
        return ColdStartResult(None, self.detail)


def dispatch_confirmed(harness: Harness, agent_id: str, controller=None):
    controller = controller or harness.build()
    controller.dispatch()
    controller.on_confirmed([agent_id])
    return controller


# ---------- 矩阵第 2 行：Agent 启动失败 ----------


def test_launch_failure_notifies() -> None:
    harness = Harness(text="hi", window=None)
    controller = harness.build(
        cold_starter=ColdStarterFailing("启动 Demo 失败：系统找不到指定的文件")
    )

    dispatch_confirmed(harness, "windows-terminal", controller)

    assert harness.warnings, "启动失败必须有通知"
    assert "启动" in harness.warnings[0][1]


# ---------- 矩阵第 3 行：窗口未出现（8s）----------


def test_window_timeout_notifies() -> None:
    harness = Harness(text="hi", window=None)
    controller = harness.build(
        cold_starter=ColdStarterFailing("已请求启动 ChatGPT，但 8.0s 内没等到它的窗口")
    )

    dispatch_confirmed(harness, "windows-terminal", controller)

    assert harness.warnings
    assert "没等到它的窗口" in harness.warnings[0][1]


# ---------- 矩阵第 4 行：UI 未就绪 / 降级（文档写"静默降级"是错的）----------


def test_degraded_is_not_silent() -> None:
    """文档原本写"静默降级"，但 AGENTS.md 禁止静默。

    实际的 DEGRADED 语义是「已派发，但可能没完全成功」——必须提示。
    """
    harness = Harness(text="hi")
    harness.injector.outcome = InjectionOutcome(InjectionStatus.DEGRADED, "输入框未确认就绪")

    dispatch_confirmed(harness, "windows-terminal")

    assert harness.warnings
    assert "可能未完全成功" in harness.warnings[0][0]


# ---------- 矩阵第 5 行：输入框/窗口找不到 ----------


def test_window_not_found_notifies_with_actionable_hint() -> None:
    harness = Harness(text="hi", window=None)
    controller = harness.build(
        cold_starter=ColdStarterFailing("没找到 ChatGPT 的窗口（进程 ChatGPT.exe）——请先启动它")
    )

    dispatch_confirmed(harness, "chatgpt-desktop", controller)

    assert harness.warnings
    assert "没找到" in harness.warnings[0][1]


def test_failure_with_empty_reason_still_says_something() -> None:
    """冷启动失败但没给原因时，也不能弹一条空白通知。

    空消息的提示等于没提示——用户只会看到"ChatGPT："然后一头雾水。
    """
    harness = Harness(text="hi", window=None)
    controller = harness.build(cold_starter=ColdStarterFailing(""))

    dispatch_confirmed(harness, "chatgpt-desktop", controller)

    assert harness.warnings
    assert harness.warnings[0][1].strip("： ") not in ("", "ChatGPT")
    assert "没有可用的窗口" in harness.warnings[0][1]


def test_activate_failure_notifies_instead_of_pasting_blindly() -> None:
    """激活失败必须放弃粘贴并说明，绝不能凑合粘到当前前台窗口。"""
    harness = Harness(text="hi")
    injector = real_injector(StubClipboard(), StubLocator(activate=False))

    dispatch_confirmed(harness, "windows-terminal", harness.build(injector_factory=lambda _a: injector))

    assert harness.warnings
    assert "窗口未能激活" in harness.warnings[0][1]


# ---------- 矩阵第 6 行：剪贴板被占用 ----------


def test_clipboard_busy_notifies() -> None:
    harness = Harness(text="hi")
    injector = real_injector(StubClipboard(fail_capture=True))

    dispatch_confirmed(
        harness, "windows-terminal", harness.build(injector_factory=lambda _a: injector)
    )

    assert harness.warnings, "剪贴板打不开必须通知，不能默默什么都不做"


# ---------- 矩阵第 7 行：管理员权限目标 ----------


def test_elevated_target_notifies() -> None:
    blocked = PermissionVerdict(
        False,
        "目标以更高权限运行，请以管理员身份重启 AgentCtrlV",
        None,
        None,
    )
    harness = Harness(text="hi")
    injector = real_injector(StubClipboard(), permission_fn=lambda _pid: blocked)

    dispatch_confirmed(
        harness, "windows-terminal", harness.build(injector_factory=lambda _a: injector)
    )

    assert harness.warnings
    assert "管理员" in harness.warnings[0][1]


# ---------- 矩阵第 8 行：多目标部分失败 ----------


def test_partial_multi_target_failure_is_summarised() -> None:
    harness = Harness(text="hi")
    failing = InjectionOutcome(InjectionStatus.FAILED, "没找到窗口")

    def factory(agent):
        from tests.test_controller_multi import RecordingInjector

        outcome = failing if agent.id == "cursor" else None
        return RecordingInjector(agent, [], outcome)

    controller = harness.build(injector_factory=factory)
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop", "cursor", "windows-terminal"])

    assert harness.warnings
    assert "1 个目标失败" in harness.warnings[0][0]
    assert "Cursor" in harness.warnings[0][1]


# ---------- 矩阵第 1 行：热键注册失败 ----------


def test_hotkey_conflict_notifies_and_keeps_tray_fallback(qt_app) -> None:
    from src.core.hotkey import HotkeyConflictError
    from src.main import Application

    application = Application(qt_app)
    notices: list[tuple] = []
    application.tray.notify = lambda title, message, **kw: notices.append((title, message, kw))

    def always_conflict(_text: str) -> int:
        raise HotkeyConflictError("已被其他程序占用")

    application.hotkeys.register = always_conflict
    bound = application.register_hotkeys()

    assert bound == {}
    assert notices, "热键冲突必须有托盘通知"
    assert any("冲突" in title for title, _m, _k in notices)
    assert any(_k.get("warning") for _t, _m, _k in notices)
