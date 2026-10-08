"""共享测试夹具。

用 FakeClipboard 在**不碰真实剪贴板**的前提下测全格式快照/恢复，
避免单元测试污染用户环境（MOCK.md 注意事项）。
"""

from __future__ import annotations

import os

import pywintypes
import pytest

from src.core.clipboard import ClipboardManager


@pytest.fixture(scope="session")
def qt_app():
    """无界面 Qt 应用，用于跑环形菜单的逻辑测试。

    用 offscreen 平台插件：不需要真实显示器，也不会弹出窗口抢用户焦点。
    注意 offscreen 下没有真正的 HWND，所以热键注册类测试不能靠它。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def real_clipboard() -> ClipboardManager:
    """真实剪贴板管理器。**只给集成测试用**；单元测试请用 fake_clipboard。"""
    return ClipboardManager()


def patch_clipboard(monkeypatch, fake: "FakeClipboard") -> None:
    """把 win32clipboard 的所有公开方法替换成 fake 的绑定方法。"""
    for attr in dir(fake):
        if attr.startswith("_") or not callable(getattr(fake, attr)):
            continue
        monkeypatch.setattr(f"win32clipboard.{attr}", getattr(fake, attr), raising=False)


def make_manager(fake: "FakeClipboard", **kwargs) -> ClipboardManager:
    """构造一个用 fake 剪贴板的管理器；默认关掉重试间隔以免拖慢测试。"""
    kwargs.setdefault("retry_delay", 0.0)
    return ClipboardManager(**kwargs)


class FakeClipboard:
    """win32clipboard 的最小可用替身，语义与真实 API 对齐。"""

    def __init__(self, data: dict[int, object] | None = None, open_failures: int = 0) -> None:
        self.data: dict[int, object] = dict(data or {})
        self.open_failures = open_failures
        self.open_calls = 0
        self.close_calls = 0
        self.empty_calls = 0
        self.is_open = False
        self.registered: dict[str, int] = {}
        self._next_registered = 0xC000

    # --- 会话 ---
    def OpenClipboard(self, hwnd=None) -> None:  # noqa: N802 - 对齐 Win32 命名
        self.open_calls += 1
        if self.open_failures > 0:
            self.open_failures -= 1
            raise pywintypes.error(5, "OpenClipboard", "Access is denied")
        self.is_open = True

    def CloseClipboard(self) -> None:  # noqa: N802
        self.is_open = False
        self.close_calls += 1

    # --- 格式枚举 ---
    def EnumClipboardFormats(self, fmt: int = 0) -> int:  # noqa: N802
        for candidate in sorted(self.data):
            if candidate > fmt:
                return candidate
        return 0

    def RegisterClipboardFormat(self, name: str) -> int:  # noqa: N802
        if name not in self.registered:
            self._next_registered += 1
            self.registered[name] = self._next_registered
        return self.registered[name]

    def GetClipboardFormatName(self, fmt: int) -> str:  # noqa: N802
        for name, value in self.registered.items():
            if value == fmt:
                return name
        raise pywintypes.error(87, "GetClipboardFormatName", "invalid format")

    # --- 数据 ---
    def GetClipboardData(self, fmt: int):  # noqa: N802
        if fmt not in self.data:
            raise pywintypes.error(1168, "GetClipboardData", "format not available")
        return self.data[fmt]

    def SetClipboardData(self, fmt: int, value) -> None:  # noqa: N802
        self.data[fmt] = value

    def EmptyClipboard(self) -> None:  # noqa: N802
        self.data.clear()
        self.empty_calls += 1

    def IsClipboardFormatAvailable(self, fmt: int) -> bool:  # noqa: N802
        return fmt in self.data


@pytest.fixture
def fake_clipboard(monkeypatch) -> FakeClipboard:
    """把 win32clipboard 替换成一个空的 FakeClipboard。"""
    fake = FakeClipboard()
    patch_clipboard(monkeypatch, fake)
    return fake
