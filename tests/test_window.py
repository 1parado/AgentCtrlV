"""T1.4：窗口查找与 force_foreground 测试（Win32 全部 mock）。"""

from __future__ import annotations

import pytest

from src.core import window as window_module
from src.core.window import WindowInfo, WindowLocator, WindowNotFoundError

NOTEPAD = WindowInfo(hwnd=1001, title="记事本", pid=42, process="Notepad.exe")
CHATGPT = WindowInfo(hwnd=1002, title="ChatGPT", pid=43, process="ChatGPT.exe")
CURSOR = WindowInfo(hwnd=1003, title="Cursor - main.py", pid=44, process="Cursor.exe")


@pytest.fixture
def windows(monkeypatch):
    """可替换的顶层窗口列表（顺序即 Z 序，最前优先）。"""
    state = {"items": [NOTEPAD, CHATGPT, CURSOR]}
    monkeypatch.setattr(window_module, "_enum_top_level_windows", lambda: list(state["items"]))
    return state


def test_find_filters_by_process(windows) -> None:
    assert WindowLocator().find(process="chatgpt.exe") == CHATGPT


def test_find_is_case_insensitive_on_process(windows) -> None:
    assert WindowLocator().find(process="NOTEPAD.EXE") == NOTEPAD


def test_find_filters_by_title_pattern(windows) -> None:
    assert WindowLocator().find(title_pattern="Cursor*") == CURSOR
    assert WindowLocator().find(title_pattern="*main.py") == CURSOR


def test_find_combines_process_and_title(windows) -> None:
    assert WindowLocator().find(process="Cursor.exe", title_pattern="Cursor*") == CURSOR
    assert WindowLocator().find(process="Cursor.exe", title_pattern="ChatGPT*") is None


def test_find_returns_topmost_first(windows) -> None:
    second = WindowInfo(2002, "记事本 - 2", 56, "Notepad.exe")
    windows["items"] = [NOTEPAD, second]  # Z 序：NOTEPAD 更靠前
    assert WindowLocator().find(process="notepad.exe") == NOTEPAD

    windows["items"] = [second, NOTEPAD]
    assert WindowLocator().find(process="notepad.exe") == second


def test_find_returns_none_when_no_match(windows) -> None:
    assert WindowLocator().find(process="firefox.exe") is None


def test_find_all_returns_every_match(windows) -> None:
    windows["items"] = [NOTEPAD, WindowInfo(2001, "记事本 - 2", 55, "Notepad.exe")]
    assert len(WindowLocator().find_all(process="notepad.exe")) == 2


def test_find_all_without_filters_returns_all(windows) -> None:
    assert WindowLocator().find_all() == [NOTEPAD, CHATGPT, CURSOR]


# ---------- force_foreground ----------


class _GuiSpy:
    """记录 win32gui / win32process 调用的替身。"""

    def __init__(self, monkeypatch, foreground_after_set: int):
        self.foreground = 999
        self.foreground_after_set = foreground_after_set
        self.calls: list[tuple] = []

    def install(self, monkeypatch) -> None:
        calls = self.calls

        def record(name, result=None):
            def fn(*args):
                calls.append((name, *args))
                return result

            return fn

        monkeypatch.setattr("win32gui.IsWindow", record("IsWindow", True))
        monkeypatch.setattr("win32gui.IsIconic", record("IsIconic", False))
        monkeypatch.setattr(
            "win32gui.GetForegroundWindow", lambda: self.foreground
        )
        monkeypatch.setattr("win32gui.ShowWindow", record("ShowWindow"))
        monkeypatch.setattr("win32gui.BringWindowToTop", record("BringWindowToTop"))
        monkeypatch.setattr(
            "win32gui.SetForegroundWindow", self._set_foreground
        )
        monkeypatch.setattr("win32gui.SetFocus", record("SetFocus"))
        monkeypatch.setattr("win32process.GetWindowThreadProcessId", lambda hwnd: (7, 42))
        monkeypatch.setattr("win32api.GetCurrentThreadId", lambda: 9)
        monkeypatch.setattr("win32process.AttachThreadInput", record("AttachThreadInput", True))

    def _set_foreground(self, hwnd):
        self.calls.append(("SetForegroundWindow", hwnd))
        self.foreground = self.foreground_after_set


def test_force_foreground_fast_path_when_already_front(monkeypatch) -> None:
    spy = _GuiSpy(monkeypatch, foreground_after_set=1001)
    spy.foreground = 1001
    spy.install(monkeypatch)

    assert WindowLocator.force_foreground(1001, timeout=0.0) is True
    assert not any(call[0] == "SetForegroundWindow" for call in spy.calls)


def test_force_foreground_attaches_thread_input(monkeypatch) -> None:
    spy = _GuiSpy(monkeypatch, foreground_after_set=1001)
    spy.install(monkeypatch)

    assert WindowLocator.force_foreground(1001, timeout=0.5) is True
    names = [call[0] for call in spy.calls]
    assert "AttachThreadInput" in names
    assert "BringWindowToTop" in names
    assert names.count("AttachThreadInput") == 2  # 并入 + 解除


def test_force_foreground_returns_false_on_timeout(monkeypatch) -> None:
    spy = _GuiSpy(monkeypatch, foreground_after_set=555)  # 抢不到前台
    spy.install(monkeypatch)
    monkeypatch.setattr("src.utils.sendinput.send_hotkey", lambda *a: None)

    assert WindowLocator.force_foreground(1001, timeout=0.0) is False


def test_force_foreground_restores_minimized_window(monkeypatch) -> None:
    spy = _GuiSpy(monkeypatch, foreground_after_set=1001)
    spy.install(monkeypatch)
    monkeypatch.setattr("win32gui.IsIconic", lambda hwnd: True)

    assert WindowLocator.force_foreground(1001, timeout=0.5) is True
    show = [call for call in spy.calls if call[0] == "ShowWindow"]
    assert show and show[0][2] == 9  # SW_RESTORE


def test_force_foreground_rejects_invalid_hwnd(monkeypatch) -> None:
    monkeypatch.setattr("win32gui.IsWindow", lambda hwnd: False)
    with pytest.raises(WindowNotFoundError):
        WindowLocator.force_foreground(0, timeout=0.0)


def test_force_foreground_survives_rejected_set_foreground(monkeypatch) -> None:
    import pywintypes

    spy = _GuiSpy(monkeypatch, foreground_after_set=1001)
    spy.install(monkeypatch)

    def reject(hwnd):
        spy.calls.append(("SetForegroundWindow", hwnd))
        raise pywintypes.error(5, "SetForegroundWindow", "Access is denied")

    monkeypatch.setattr("win32gui.SetForegroundWindow", reject)
    # 第一次被拒 -> Alt 技巧 -> 第二次仍被拒 -> 超时返回 False（但不应抛异常）
    monkeypatch.setattr("src.utils.sendinput.send_hotkey", lambda *a: None)
    assert WindowLocator.force_foreground(1001, timeout=0.0) is False
