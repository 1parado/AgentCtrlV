"""M2/T2.1：热键解析、保留组合、注册冲突与热更新测试。

注册走假 win32gui，不真的占用系统热键；真实注册由 `main.py --check` 覆盖。
"""

from __future__ import annotations

import pywintypes
import pytest

from src.core import hotkey as hotkey_module
from src.core.hotkey import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    WM_HOTKEY,
    HotkeyConflictError,
    HotkeyError,
    HotkeyManager,
    parse_hotkey,
)


class FakeGui:
    """记录注册调用的 win32gui 替身。"""

    def __init__(self, *, fail_with: int | None = None) -> None:
        self.calls: list[tuple] = []
        self.registered: set[int] = set()
        self.fail_with = fail_with

    def RegisterHotKey(self, hwnd, hotkey_id, modifiers, vk):  # noqa: N802
        self.calls.append(("register", hwnd, hotkey_id, modifiers, vk))
        if self.fail_with is not None:
            raise pywintypes.error(self.fail_with, "RegisterHotKey", "boom")
        self.registered.add(hotkey_id)

    def UnregisterHotKey(self, hwnd, hotkey_id):  # noqa: N802
        self.calls.append(("unregister", hwnd, hotkey_id))
        self.registered.discard(hotkey_id)


@pytest.fixture
def fake_gui(monkeypatch) -> FakeGui:
    fake = FakeGui()
    monkeypatch.setattr(hotkey_module.win32gui, "RegisterHotKey", fake.RegisterHotKey)
    monkeypatch.setattr(hotkey_module.win32gui, "UnregisterHotKey", fake.UnregisterHotKey)
    return fake


# ---------- 解析 ----------


def test_parse_simple_hotkey() -> None:
    spec = parse_hotkey("Alt+V")
    assert spec.text == "Alt+V"
    assert spec.modifiers == MOD_ALT
    assert spec.vk == ord("V")


def test_parse_is_case_insensitive_and_canonical() -> None:
    assert parse_hotkey("alt+shift+v").text == "Alt+Shift+V"
    assert parse_hotkey("CTRL+f5").text == "Ctrl+F5"


def test_parse_modifier_order_is_canonical() -> None:
    assert parse_hotkey("Shift+Ctrl+Alt+V").text == "Alt+Ctrl+Shift+V"


@pytest.mark.parametrize("text", ["V", "", "   ", "Alt+", "+V", "Alt+A+B"])
def test_parse_rejects_malformed(text: str) -> None:
    with pytest.raises(HotkeyError):
        parse_hotkey(text)


@pytest.mark.parametrize("text", ["Ctrl+Alt+Del", "Win+L", "Win+R", "Alt+Tab", "Ctrl+Shift+Esc"])
def test_parse_rejects_system_reserved(text: str) -> None:
    with pytest.raises(HotkeyError, match="系统保留组合"):
        parse_hotkey(text)


def test_parse_requires_modifier() -> None:
    with pytest.raises(HotkeyError, match="至少包含"):
        parse_hotkey("F5")


def test_parse_rejects_duplicate_modifier() -> None:
    with pytest.raises(HotkeyError, match="重复修饰键"):
        parse_hotkey("Alt+Alt+V")


# ---------- 注册 ----------


def test_register_uses_norepeat_and_reports_id(fake_gui: FakeGui) -> None:
    manager = HotkeyManager(hwnd=4242)
    hotkey_id = manager.register("Alt+V")

    assert hotkey_id in manager.registered
    _kind, hwnd, _id, modifiers, vk = fake_gui.calls[0]
    assert hwnd == 4242
    assert modifiers & MOD_NOREPEAT
    assert modifiers & MOD_ALT
    assert vk == ord("V")


def test_register_same_text_twice_is_idempotent(fake_gui: FakeGui) -> None:
    manager = HotkeyManager()
    first = manager.register("Alt+V")
    second = manager.register("Alt+V")

    assert first == second
    assert len(fake_gui.calls) == 1


def test_register_conflict_raises_with_reason(monkeypatch) -> None:
    fake = FakeGui(fail_with=1409)  # ERROR_HOTKEY_ALREADY_REGISTERED
    monkeypatch.setattr(hotkey_module.win32gui, "RegisterHotKey", fake.RegisterHotKey)

    with pytest.raises(HotkeyConflictError, match="已被其他程序注册"):
        HotkeyManager().register("Alt+V")


def test_register_bad_window_reports_offscreen_hint(monkeypatch) -> None:
    """回归：1400 是句柄无效（无界面平台），不能谎报成"被占用"。"""
    fake = FakeGui(fail_with=1400)
    monkeypatch.setattr(hotkey_module.win32gui, "RegisterHotKey", fake.RegisterHotKey)

    with pytest.raises(HotkeyConflictError, match="无界面"):
        HotkeyManager().register("Alt+V")


def test_register_does_not_record_failed_hotkey(monkeypatch) -> None:
    fake = FakeGui(fail_with=1409)
    monkeypatch.setattr(hotkey_module.win32gui, "RegisterHotKey", fake.RegisterHotKey)
    manager = HotkeyManager()

    with pytest.raises(HotkeyConflictError):
        manager.register("Alt+V")

    assert manager.registered == {}


def test_unregister_unknown_returns_false(fake_gui: FakeGui) -> None:
    assert HotkeyManager().unregister(999) is False


def test_replace_registers_new_before_dropping_old(fake_gui: FakeGui) -> None:
    """热更新时不能先注销再注册，否则中间有一段时间没有主热键。"""
    manager = HotkeyManager()
    old = manager.register("Alt+V")
    new = manager.replace(old, "Alt+C")

    kinds = [call[0] for call in fake_gui.calls]
    assert kinds.index("register") < kinds.index("unregister")
    assert str(manager.registered[new]) == "Alt+C"
    assert old not in manager.registered


def test_replace_keeps_old_when_new_conflicts(monkeypatch) -> None:
    fake = FakeGui()
    monkeypatch.setattr(hotkey_module.win32gui, "RegisterHotKey", fake.RegisterHotKey)
    monkeypatch.setattr(hotkey_module.win32gui, "UnregisterHotKey", fake.UnregisterHotKey)

    def flaky(hwnd, hotkey_id, modifiers, vk):
        if vk == ord("C"):
            raise pywintypes.error(1409, "RegisterHotKey", "taken")
        return fake.RegisterHotKey(hwnd, hotkey_id, modifiers, vk)

    monkeypatch.setattr(hotkey_module.win32gui, "RegisterHotKey", flaky)
    manager = HotkeyManager()
    old = manager.register("Alt+V")

    with pytest.raises(HotkeyConflictError):
        manager.replace(old, "Alt+C")

    assert str(manager.registered[old]) == "Alt+V"


def test_close_unregisters_everything(fake_gui: FakeGui) -> None:
    manager = HotkeyManager()
    manager.register("Alt+V")
    manager.register("Alt+Shift+V")
    manager.close()

    assert manager.registered == {}
    assert fake_gui.registered == set()


# ---------- 消息分发 ----------


def test_dispatch_message_matches_registered_id(fake_gui: FakeGui) -> None:
    manager = HotkeyManager()
    hotkey_id = manager.register("Alt+V")

    event = manager.dispatch_message(WM_HOTKEY, hotkey_id)

    assert event is not None
    assert event.hotkey_id == hotkey_id


def test_dispatch_message_ignores_other_messages(fake_gui: FakeGui) -> None:
    manager = HotkeyManager()
    hotkey_id = manager.register("Alt+V")

    assert manager.dispatch_message(0x0100, hotkey_id) is None  # WM_KEYDOWN


def test_dispatch_message_ignores_foreign_hotkey_id(fake_gui: FakeGui) -> None:
    manager = HotkeyManager()
    manager.register("Alt+V")

    assert manager.dispatch_message(WM_HOTKEY, 4242) is None
