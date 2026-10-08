"""托盘图标与气泡通知（M2 最小实现）。

用 QSystemTrayIcon 而不是 pystray：既然 M2 已经必须引入 Qt（环形菜单），
再拉一个托盘库只会多一套事件循环和退出顺序要协调。

职责边界：只负责"显示状态 / 让用户触发动作 / 把失败说出来"，
不碰剪贴板、不碰热键注册（那些是 controller 的事）。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from src.utils.logger import get_logger

DEFAULT_ICON = "assets/icons/app.ico"


class Tray(QObject):
    """托盘常驻入口。"""

    dispatch_requested = Signal()
    resend_requested = Signal()
    hotkeys_requested = Signal()
    quit_requested = Signal()

    def __init__(self, app_name: str = "AgentCtrlV", icon_path: str = DEFAULT_ICON, logger=None) -> None:
        super().__init__()
        self._log = logger or get_logger(__name__)
        self._icon = QIcon(icon_path) if Path(icon_path).exists() else QIcon()
        self._tray = QSystemTrayIcon(self._icon)
        self._tray.setToolTip(app_name)

        menu = QMenu()
        self._dispatch_action = QAction("分发剪贴板 (Alt+V)", self)
        self._dispatch_action.triggered.connect(self.dispatch_requested.emit)
        self._resend_action = QAction("重发上一次 (Alt+Shift+V)", self)
        self._resend_action.triggered.connect(self.resend_requested.emit)
        hotkeys_action = QAction("设置热键…", self)
        hotkeys_action.triggered.connect(self.hotkeys_requested.emit)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_requested.emit)

        menu.addAction(self._dispatch_action)
        menu.addAction(self._resend_action)
        menu.addSeparator()
        menu.addAction(hotkeys_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)

    def show(self) -> bool:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._log.error("系统托盘不可用，托盘入口未启用")
            return False
        self._tray.show()
        return True

    def hide(self) -> None:
        self._tray.hide()

    def set_hotkey_hint(self, dispatch_text: str, resend_text: str) -> None:
        """热键被占用并降级后，让菜单文案反映实际可用的键。"""
        self._dispatch_action.setText(f"分发剪贴板 ({dispatch_text})")
        self._resend_action.setText(f"重发上一次 ({resend_text})")

    def notify(self, title: str, message: str, *, warning: bool = False) -> None:
        """气泡通知。失败必须可见——这是失败矩阵里所有"用户反馈"列的落点。"""
        icon = (
            QSystemTrayIcon.MessageIcon.Warning
            if warning
            else QSystemTrayIcon.MessageIcon.Information
        )
        self._tray.showMessage(title, message, icon, 5000)
        level = self._log.warning if warning else self._log.info
        level("通知用户：%s —— %s", title, message)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.dispatch_requested.emit()
