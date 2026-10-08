"""把 WM_HOTKEY 从 Qt 的原生消息循环喂给 HotkeyManager。

为什么用 `QAbstractNativeEventFilter` 而不是自己起消息线程：
Qt 已经在跑本线程的消息循环，再起一个只会带来线程亲和性与退出顺序的麻烦。

为什么要 hwnd 而不是 NULL：热键注册在 NULL 上时 WM_HOTKEY 变成**线程消息**，
能否被过滤链看见取决于 Qt 版本；注册到我们自己隐藏窗口的 HWND 上，
它就是一条普通窗口消息，稳定会经过原生事件过滤。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter

from src.core.hotkey import HotkeyEvent, HotkeyManager
from src.utils.logger import get_logger


class MSG(ctypes.Structure):
    """Win32 MSG 结构（只需要前几个字段）。"""

    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]


class HotkeyEventFilter(QAbstractNativeEventFilter):
    """把命中本管理器注册 id 的 WM_HOTKEY 转成回调。"""

    def __init__(
        self,
        manager: HotkeyManager,
        on_hotkey: Callable[[HotkeyEvent], None],
        logger=None,
    ) -> None:
        super().__init__()
        self._manager = manager
        self._on_hotkey = on_hotkey
        self._log = logger or get_logger(__name__)

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API
        try:
            raw = int(message)
            msg = ctypes.cast(raw, ctypes.POINTER(MSG)).contents
            wparam = int(msg.wParam)
            message_id = int(msg.message)
        except (TypeError, ValueError, OSError) as exc:  # pragma: no cover - 防御
            self._log.debug("无法解析原生消息：%s", exc)
            return False, 0

        event = self._manager.dispatch_message(message_id, wparam)
        if event is None:
            return False, 0

        try:
            self._on_hotkey(event)
        except Exception as exc:  # noqa: BLE001 - 回调异常不能穿透原生消息循环
            self._log.exception("热键回调异常：%s", exc)
        return True, 0
