"""T1.4：SendInput 原语测试（不真的敲键盘）。"""

from __future__ import annotations

import ctypes

import pytest

from src.utils import sendinput

pytestmark = pytest.mark.skipif(
    ctypes.sizeof(ctypes.c_void_p) != 8, reason="结构体布局按 x64 校验"
)


def test_input_struct_layout() -> None:
    assert ctypes.sizeof(sendinput.INPUT) == 40
    assert sendinput.INPUT.type.offset == 0


def test_key_event_encodes_vk_and_scan() -> None:
    event = sendinput._key_event(sendinput.VK_V, up=False)
    assert event.type == sendinput.INPUT_KEYBOARD
    assert event.ki.wVk == sendinput.VK_V
    assert event.ki.wScan != 0
    assert event.ki.dwFlags == 0


def test_key_event_marks_keyup() -> None:
    event = sendinput._key_event(sendinput.VK_CONTROL, up=True)
    assert event.ki.dwFlags & sendinput.KEYEVENTF_KEYUP


def test_send_hotkey_presses_modifier_first_and_releases_last(monkeypatch) -> None:
    captured: list = []
    monkeypatch.setattr(sendinput, "send_inputs", captured.extend)

    sendinput.send_hotkey(sendinput.VK_V, sendinput.VK_CONTROL)

    sequence = [
        (event.ki.wVk, bool(event.ki.dwFlags & sendinput.KEYEVENTF_KEYUP)) for event in captured
    ]
    assert sequence == [
        (sendinput.VK_CONTROL, False),
        (sendinput.VK_V, False),
        (sendinput.VK_V, True),
        (sendinput.VK_CONTROL, True),
    ]


def test_send_hotkey_supports_multiple_modifiers(monkeypatch) -> None:
    captured: list = []
    monkeypatch.setattr(sendinput, "send_inputs", captured.extend)

    sendinput.send_hotkey(sendinput.VK_V, sendinput.VK_CONTROL, sendinput.VK_SHIFT)

    downs = [e.ki.wVk for e in captured if not e.ki.dwFlags & sendinput.KEYEVENTF_KEYUP]
    ups = [e.ki.wVk for e in captured if e.ki.dwFlags & sendinput.KEYEVENTF_KEYUP]
    assert downs == [sendinput.VK_CONTROL, sendinput.VK_SHIFT, sendinput.VK_V]
    assert ups == [sendinput.VK_V, sendinput.VK_SHIFT, sendinput.VK_CONTROL]


def test_send_ctrl_v_uses_v_and_control(monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(sendinput, "send_hotkey", lambda vk, *mods: seen.append((vk, mods)))

    sendinput.send_ctrl_v()

    assert seen == [(sendinput.VK_V, (sendinput.VK_CONTROL,))]


def test_send_inputs_with_no_events_is_a_noop(monkeypatch) -> None:
    monkeypatch.setattr(
        sendinput._user32, "SendInput", lambda *args: pytest.fail("不应调用 SendInput")
    )
    sendinput.send_inputs([])


def test_send_inputs_raises_when_system_accepts_fewer_events(monkeypatch) -> None:
    monkeypatch.setattr(sendinput._user32, "SendInput", lambda count, array, size: count - 1)

    with pytest.raises(OSError, match="只提交了"):
        sendinput.send_inputs([sendinput._key_event(sendinput.VK_V, up=False)])
