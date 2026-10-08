"""SendInput 底层原语（ctypes 实现，不引入新依赖）。

只负责"把按键事件送进系统输入队列"，不负责找窗口、不负责等待。
所有调用都是同步的，失败会抛 OSError，由上层决定降级策略。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_V = 0x56
VK_RETURN = 0x0D

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_user32.SendInput.restype = wintypes.UINT
_user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
_user32.MapVirtualKeyW.restype = wintypes.UINT


def _key_event(vk: int, up: bool) -> INPUT:
    scan = _user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
    flags = KEYEVENTF_KEYUP if up else 0
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0))


def send_inputs(events: list[INPUT]) -> None:
    """一次性提交一批按键事件。失败抛 OSError（含 GetLastError）。"""
    if not events:
        return
    count = len(events)
    array = (INPUT * count)(*events)
    sent = _user32.SendInput(count, array, ctypes.sizeof(INPUT))
    if sent != count:
        raise OSError(ctypes.get_last_error(), f"SendInput 只提交了 {sent}/{count} 个事件")


def send_hotkey(vk: int, *modifiers: int) -> None:
    """按下并释放一个组合键，例如 send_hotkey(VK_V, VK_CONTROL)。

    所有事件在一次 SendInput 调用中提交，避免与其他输入交错。
    """
    events: list[INPUT] = [_key_event(m, up=False) for m in modifiers]
    events.append(_key_event(vk, up=False))
    events.append(_key_event(vk, up=True))
    events.extend(_key_event(m, up=True) for m in reversed(modifiers))
    send_inputs(events)


def send_ctrl_v() -> None:
    """发送 Ctrl+V。注意：调用方必须已确认前台窗口正确。"""
    send_hotkey(VK_V, VK_CONTROL)
