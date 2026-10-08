"""冷启动（T3.2）与新会话策略（T3.3）测试。

重点覆盖"失败必须可见"：没配 launch、CLI 寄生在终端、启动报错、
窗口迟迟不出现、新会话方式不支持——每种都要有一句人话，不能静默跳过。
"""

from __future__ import annotations

import pytest

from src.core import cold_start
from src.core.agents import AgentSpec, ColdStartSpec
from src.core.cold_start import (
    ColdStartError,
    ColdStarter,
    launch,
    modifiers_to_vks,
    start_new_session,
    wait_for_window,
)
from src.core.window import WindowInfo

WINDOW = WindowInfo(hwnd=900, title="Demo", pid=77, process="Demo.exe")


class SequenceLocator:
    """按顺序返回预设结果，用完之后一直返回最后一个。

    这样就能表达"一开始没在跑 -> 启动后窗口出现了"。
    """

    def __init__(self, results: list, *, activatable: bool = True) -> None:
        self._results = list(results)
        self._index = 0
        self.calls = 0
        self.activatable = activatable
        self.activate_calls = 0

    def _next(self):
        self.calls += 1
        return self._results[min(self._index, len(self._results) - 1)]

    def find(self, *, process=None, title_pattern=None):
        result = self._next()
        self._index += 1
        return result

    def find_host_window(self, cli_match, *, terminal_process=None):
        return self.find()

    def activate(self, hwnd, timeout=None) -> bool:
        self.activate_calls += 1
        return self.activatable

    def describe_candidates(self, process: str, *, limit: int = 6) -> str:
        return ""


class FakeClock:
    """假时钟：sleep 只推进时间，不真的等。"""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def gui_agent(**kwargs) -> AgentSpec:
    base = {"id": "demo", "name": "Demo", "process": "Demo.exe", "launch": "C:\\Demo\\Demo.exe"}
    base.update(kwargs)
    return AgentSpec(**base)


# ---------- 修饰键映射 ----------


def test_modifiers_map_to_virtual_keys() -> None:
    """RegisterHotKey 用 MOD_* 位标志，SendInput 要 VK，两者不能混。"""
    spec = cold_start.parse_hotkey("Ctrl+N")

    assert modifiers_to_vks(spec.modifiers) == [0x11]  # VK_CONTROL
    assert spec.vk == 0x4E  # 'N'


def test_combined_modifiers_keep_order() -> None:
    spec = cold_start.parse_hotkey("Ctrl+Alt+N")

    assert modifiers_to_vks(spec.modifiers) == [0x11, 0x12]  # CTRL, MENU


# ---------- 启动 ----------


def test_launch_without_command_is_explicit() -> None:
    with pytest.raises(ColdStartError, match="没有配置 launch"):
        launch(AgentSpec(id="x", name="X", process="X.exe"))


def test_launch_passes_command_to_launcher() -> None:
    seen: list[str] = []
    launch(gui_agent(), launcher=seen.append)

    assert seen == ["C:\\Demo\\Demo.exe"]


def test_shell_alias_goes_through_shell_execute(monkeypatch) -> None:
    """shell:AppsFolder 只有 ShellExecute 认得，Popen 不认。"""
    called: list[str] = []
    monkeypatch.setattr(cold_start.os, "startfile", lambda command: called.append(command))

    launch(gui_agent(launch="shell:AppsFolder\\Foo!App"))

    assert called == ["shell:AppsFolder\\Foo!App"]


def test_launch_wraps_oserror() -> None:
    def boom(_command: str) -> None:
        raise OSError(2, "系统找不到指定的文件")

    with pytest.raises(ColdStartError, match="启动 Demo 失败"):
        launch(gui_agent(), launcher=boom)


# ---------- 等窗口 ----------


def test_wait_for_window_returns_as_soon_as_it_appears() -> None:
    clock = FakeClock()
    locator = SequenceLocator([None, None, WINDOW])

    found = wait_for_window(locator, gui_agent(), 5.0, sleep=clock.sleep, clock=clock)

    assert found is WINDOW
    assert clock.now == pytest.approx(cold_start.POLL_INTERVAL_S * 2)


def test_wait_for_window_times_out_without_lying() -> None:
    clock = FakeClock()

    found = wait_for_window(SequenceLocator([None]), gui_agent(), 1.0, sleep=clock.sleep, clock=clock)

    assert found is None
    assert clock.now >= 1.0


# ---------- 新会话 ----------


def test_new_session_disabled_does_nothing() -> None:
    sent: list = []

    assert start_new_session(ColdStartSpec(new_session=False), sender=lambda v, m: sent.append(v)) == ""
    assert sent == []


def test_new_session_none_method_does_nothing() -> None:
    sent: list = []
    spec = ColdStartSpec(new_session=True, new_session_method="none")

    assert start_new_session(spec, sender=lambda v, m: sent.append(v)) == ""
    assert sent == []


def test_new_session_hotkey_is_sent() -> None:
    sent: list[tuple[int, list[int]]] = []

    detail = start_new_session(
        ColdStartSpec(new_session=True, new_session_hotkey="Ctrl+N"),
        sender=lambda vk, mods: sent.append((vk, mods)),
    )

    assert detail == ""
    assert sent == [(0x4E, [0x11])]


def test_unsupported_new_session_method_is_reported_not_skipped() -> None:
    """M3 只做 hotkey。没实现的方式必须说出来，不能假装做过。"""
    sent: list = []
    spec = ColdStartSpec(new_session=True, new_session_method="uia_button")

    detail = start_new_session(spec, sender=lambda v, m: sent.append(v))

    assert "uia_button" in detail and "尚未实现" in detail
    assert sent == []


def test_invalid_new_session_hotkey_is_reported() -> None:
    spec = ColdStartSpec(new_session=True, new_session_hotkey="Ctrl+这不是键")

    assert "不可用" in start_new_session(spec, sender=lambda v, m: None)
