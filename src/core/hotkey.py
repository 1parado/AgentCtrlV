"""全局热键解析、校验与 RegisterHotKey 管理（M2/T2.1）。

只负责系统热键生命周期，不负责触发后的业务动作：
- 解析用户可读字符串，如 Alt+Shift+V
- 拒绝无修饰键与系统保留组合
- 调用 RegisterHotKey，失败时抛 HotkeyConflictError（由托盘层通知）
- 线程安全地注销 / 热更新

**实测坑**：pywin32 的 `win32gui.RegisterHotKey` 成功时返回 `None`、
失败时**抛 `pywintypes.error`**。按返回值真假判断会把成功当失败，
所以这里一律走异常路径。

消息泵由调用方驱动：Qt 侧用 QAbstractNativeEventFilter 把 WM_HOTKEY
喂给 dispatch_message()，避免在这里偷偷起线程。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import IntFlag

import pywintypes
import win32gui

from src.utils.logger import get_logger

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK_LWIN = 0x5B
VK_RWIN = 0x5C


class HotkeyError(ValueError):
    """热键字符串无法解析或违反约束。"""


class HotkeyConflictError(RuntimeError):
    """RegisterHotKey 被系统拒绝，通常表示组合已被其他程序占用。"""


class HotkeyModifier(IntFlag):
    ALT = MOD_ALT
    CTRL = MOD_CONTROL
    SHIFT = MOD_SHIFT
    WIN = MOD_WIN


_MODIFIER_ALIASES = {
    "ALT": MOD_ALT,
    "OPTION": MOD_ALT,
    "CTRL": MOD_CONTROL,
    "CONTROL": MOD_CONTROL,
    "SHIFT": MOD_SHIFT,
    "WIN": MOD_WIN,
    "WINDOWS": MOD_WIN,
    "META": MOD_WIN,
}

#: 系统保留组合，提前给出明确错误，而不是等系统返回含糊的冲突
_RESERVED = {
    (MOD_CONTROL | MOD_ALT, "DELETE"),
    (MOD_WIN, "L"),
    (MOD_WIN, "R"),
    (MOD_WIN, "TAB"),
    (MOD_ALT, "TAB"),
    (MOD_CONTROL | MOD_SHIFT, "ESCAPE"),
}

_KEY_ALIASES = {
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "SPACE": 0x20,
    "TAB": 0x09,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "DEL": 0x2E,
    "INSERT": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
}


#: winerror -> 人话。注册失败的原因不止"被占用"，报错必须准确（失败要可见）
_WINERROR_HINTS = {
    1400: "窗口句柄无效——通常是在无界面（offscreen）平台上运行，热键没法注册",
    1409: "该热键已被其他程序注册",
}


@dataclass(frozen=True)
class HotkeySpec:
    """规范化后的热键。"""

    text: str
    modifiers: int
    vk: int

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class HotkeyEvent:
    hotkey_id: int


#: 别名 -> 规范名，保证保留组合检查不会因为写法不同而漏掉
_KEY_CANONICAL = {
    "ESC": "ESCAPE",
    "RETURN": "ENTER",
    "DEL": "DELETE",
}


def _parse_key(token: str) -> tuple[str, int]:
    key = token.strip().upper()
    if key in _KEY_ALIASES:
        return _KEY_CANONICAL.get(key, key), _KEY_ALIASES[key]
    if len(key) == 1 and (key.isalpha() or key.isdigit()):
        return key, ord(key)
    if len(key) >= 2 and key[0] == "F" and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:
            return key, 0x70 + number - 1
    raise HotkeyError(f"不支持的按键：{token!r}")


def parse_hotkey(text: str) -> HotkeySpec:
    """解析并校验 `Alt+V` 形式的热键。

    规则（CONFIG_SCHEMA.md）：必须至少一个修饰键；禁止系统保留组合。
    """
    if not isinstance(text, str) or not text.strip():
        raise HotkeyError("热键不能为空")

    tokens = [part.strip() for part in text.split("+")]
    if any(not token for token in tokens):
        raise HotkeyError(f"热键格式非法：{text!r}")

    modifiers = 0
    key_tokens: list[str] = []
    for token in tokens:
        alias = token.upper()
        if alias in _MODIFIER_ALIASES:
            bit = _MODIFIER_ALIASES[alias]
            if modifiers & bit:
                raise HotkeyError(f"重复修饰键：{token}")
            modifiers |= bit
        else:
            key_tokens.append(token)

    if not modifiers:
        raise HotkeyError("热键必须至少包含 Alt/Ctrl/Shift/Win 一个修饰键")
    if len(key_tokens) != 1:
        raise HotkeyError("热键必须且只能包含一个主按键")

    key_name, vk = _parse_key(key_tokens[0])
    if (modifiers, key_name) in _RESERVED:
        raise HotkeyError(f"系统保留组合，不允许注册：{text}")
    if (modifiers & MOD_WIN) and not (modifiers & ~MOD_WIN):
        raise HotkeyError(f"只按 Win 的组合不可用：{text}")

    canonical = [
        label
        for label, bit in (("Alt", MOD_ALT), ("Ctrl", MOD_CONTROL), ("Shift", MOD_SHIFT), ("Win", MOD_WIN))
        if modifiers & bit
    ]
    return HotkeySpec("+".join([*canonical, key_name]), modifiers, vk)


class HotkeyManager:
    """热键注册表。每个实例使用独立 id 命名空间。"""

    def __init__(self, *, hwnd: int | None = None, logger=None) -> None:
        self._hwnd = hwnd or 0
        self._log = logger or get_logger(__name__)
        self._lock = threading.RLock()
        self._next_id = 1
        self._registered: dict[int, HotkeySpec] = {}
        self._by_text: dict[str, int] = {}

    @property
    def registered(self) -> dict[int, HotkeySpec]:
        with self._lock:
            return dict(self._registered)

    def register(self, text: str) -> int:
        """注册热键，冲突时抛 HotkeyConflictError。返回系统分配的热键 id。"""
        spec = parse_hotkey(text)
        with self._lock:
            if spec.text in self._by_text:
                return self._by_text[spec.text]

            hotkey_id = self._next_id
            try:
                win32gui.RegisterHotKey(self._hwnd, hotkey_id, spec.modifiers | MOD_NOREPEAT, spec.vk)
            except pywintypes.error as exc:
                hint = _WINERROR_HINTS.get(exc.winerror, "可能已被其他程序占用")
                raise HotkeyConflictError(
                    f"无法注册 {spec.text}：{hint}（winerror={exc.winerror}）"
                ) from exc

            self._next_id += 1
            self._registered[hotkey_id] = spec
            self._by_text[spec.text] = hotkey_id
            self._log.info("已注册热键 %s（id=%d）", spec.text, hotkey_id)
            return hotkey_id

    def unregister(self, hotkey_id: int) -> bool:
        with self._lock:
            spec = self._registered.pop(hotkey_id, None)
            if spec is None:
                return False
            self._by_text.pop(spec.text, None)
            try:
                win32gui.UnregisterHotKey(self._hwnd, hotkey_id)
            except pywintypes.error as exc:
                self._log.error("注销热键 %s 失败：%s", spec.text, exc)
                return False
            self._log.info("已注销热键 %s", spec.text)
            return True

    def replace(self, old_id: int, new_text: str) -> int:
        """先注册新的，成功后才注销旧的，避免热更新时短暂失去主路径。"""
        new_id = self.register(new_text)
        if new_id != old_id:
            self.unregister(old_id)
        return new_id

    def dispatch_message(self, message: int, wparam: int) -> HotkeyEvent | None:
        """把一条原生消息翻译成热键事件；不是本管理器注册的热键返回 None。

        纯函数式，便于单测：不依赖 Qt，也不碰消息队列。
        """
        if message != WM_HOTKEY:
            return None
        hotkey_id = int(wparam)
        with self._lock:
            if hotkey_id not in self._registered:
                return None
        return HotkeyEvent(hotkey_id)

    def close(self) -> None:
        with self._lock:
            for hotkey_id in list(self._registered):
                self.unregister(hotkey_id)
