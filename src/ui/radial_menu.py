"""轻量环形菜单（M2/T2.2-T2.3）。

设计约束：
- QApplication 启动时创建一次、菜单对象常驻；热键触发时只 reposition/show，
  避免冷启动 Qt 破坏 <150ms 的菜单预算。
- 尺寸与半径随 Agent 数量自适应：把半径钉死在 112px 时，超过 8 个条目
  就会沿圆周互相重叠。Agent 是用户配置的，数量不可预设。
- 单击项目立即确认（T2.3，保持快路径）；Ctrl+点击切换多选、中心或 Enter 确认（T3.1）；
  Esc/点空白取消且不触碰剪贴板。
- 绘制不依赖图标库，Agent 可选用 PNG/ICO 图标，缺图时显示首字母。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PySide6.QtWidgets import QDialog

MAX_MENU_ITEMS = 12
#: PRD：环形菜单单选 + Ctrl 多选，最多 5 个
MAX_SELECTION = 5
ITEM_RADIUS = 39
MIN_RING_RADIUS = 112
LABEL_SPACE = 46
ITEM_ARC = ITEM_RADIUS * 2 + 12  # 每个条目沿圆周需要的弧长（含间隙）


def compute_geometry(count: int) -> tuple[float, int]:
    """按条目数算 (环半径, 窗口边长)，保证圆周上放得下且不重叠。"""
    radius = max(MIN_RING_RADIUS, ITEM_ARC * count / (2 * math.pi))
    size = int(2 * (radius + ITEM_RADIUS + LABEL_SPACE))
    return radius, size


@dataclass(frozen=True)
class AgentItem:
    id: str
    name: str
    icon: str | None = None
    enabled: bool = True


class RadialMenu(QDialog):
    """常驻、无边框、键盘可操作的 Agent 选择器。"""

    confirmed = Signal(list)
    cancelled = Signal()

    def __init__(self, agents: list[AgentItem], parent=None) -> None:
        super().__init__(parent)
        if not agents:
            raise ValueError("环形菜单至少需要一个 Agent")
        if len(agents) > MAX_MENU_ITEMS:
            raise ValueError(
                f"环形菜单最多支持 {MAX_MENU_ITEMS} 个 Agent（当前 {len(agents)} 个）；"
                "请在 config/agents/*.yaml 里用 enabled: false 关掉暂时不用的"
            )
        self._agents = list(agents)
        self._selected: set[str] = set()
        self._hovered: int | None = None
        self._hint = ""
        self._angles = self._build_angles(len(agents))
        self._radius, size = compute_geometry(len(agents))
        self._center = size / 2
        self._size = size
        self._center_radius = min(42.0, self._radius - ITEM_RADIUS - 6)

        self.setFixedSize(size, size)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowIcon(QIcon("assets/icons/app.ico"))

    @staticmethod
    def _build_angles(count: int) -> list[float]:
        # 从正上方开始，顺时针排列；3 个 Agent 自然呈 120° 分布。
        return [-math.pi / 2 + (2 * math.pi * i / count) for i in range(count)]

    @property
    def selected_ids(self) -> tuple[str, ...]:
        return tuple(agent.id for agent in self._agents if agent.id in self._selected)

    def show_at(self, global_pos: QPoint) -> None:
        """显示在鼠标附近并确保整个菜单在屏幕内。"""
        screen = self.screen()
        if screen is None:
            self.move(global_pos - QPoint(int(self._center), int(self._center)))
        else:
            area = screen.availableGeometry()
            x = max(area.left(), min(global_pos.x() - int(self._center), area.right() - self._size))
            y = max(area.top(), min(global_pos.y() - int(self._center), area.bottom() - self._size))
            self.move(x, y)
        self._selected.clear()
        self._hovered = None
        self._hint = ""
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.update()

    def _item_center(self, index: int) -> QPointF:
        angle = self._angles[index]
        return QPointF(
            self._center + math.cos(angle) * self._radius,
            self._center + math.sin(angle) * self._radius,
        )

    def _item_at(self, point: QPointF) -> int | None:
        for index in range(len(self._agents)):
            center = self._item_center(index)
            if math.hypot(point.x() - center.x(), point.y() - center.y()) <= ITEM_RADIUS:
                return index
        return None

    def _in_center(self, point: QPointF) -> bool:
        return math.hypot(point.x() - self._center, point.y() - self._center) <= self._center_radius

    def _confirm(self) -> None:
        if not self._selected:
            return
        selected = list(self.selected_ids)
        self.hide()
        self.confirmed.emit(selected)

    def _cancel(self) -> None:
        self._selected.clear()
        self.hide()
        self.cancelled.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._confirm()
        else:
            super().keyPressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._hovered = self._item_at(event.position())
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        index = self._item_at(point)
        if index is None:
            if self._in_center(point):
                self._confirm()
            else:
                self._cancel()
            return

        agent = self._agents[index]
        if not agent.enabled:
            return

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # T3.1：Ctrl+点击 = 切换选中，**不**立即发送，等中心/回车确认
            self._toggle(agent.id)
            return

        # 单击就是"就它了"：保留 T2.3 的快路径，不为多选牺牲常用场景
        self._selected = {agent.id}
        self._confirm()

    def _toggle(self, agent_id: str) -> None:
        if agent_id in self._selected:
            self._selected.discard(agent_id)
            self._hint = ""
        elif len(self._selected) >= MAX_SELECTION:
            # 不静默丢弃用户的操作，明确告诉他到上限了
            self._hint = f"最多选 {MAX_SELECTION} 个"
        else:
            self._selected.add(agent_id)
            self._hint = ""
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(13, 18, 32, 238))
        inset = 8.0
        painter.drawEllipse(QRectF(inset, inset, self._size - inset * 2, self._size - inset * 2))

        for index, agent in enumerate(self._agents):
            center = self._item_center(index)
            selected = agent.id in self._selected
            hovered = index == self._hovered
            if selected:
                color = QColor(50, 150, 255, 245)
            elif hovered:
                color = QColor(60, 74, 102, 245)
            else:
                color = QColor(35, 45, 67, 245)
            painter.setBrush(color)
            painter.setPen(
                QPen(QColor(130, 180, 255, 180) if selected else QColor(90, 105, 130, 150), 2)
            )
            painter.drawEllipse(center, ITEM_RADIUS, ITEM_RADIUS)

            icon_drawn = False
            if agent.icon and Path(agent.icon).exists():
                icon = QIcon(agent.icon).pixmap(38, 38)
                painter.drawPixmap(QPoint(int(center.x() - 19), int(center.y() - 19)), icon)
                icon_drawn = True
            if not icon_drawn:
                painter.setPen(QColor(235, 241, 255))
                painter.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
                painter.drawText(
                    QRectF(center.x() - 22, center.y() - 20, 44, 40),
                    Qt.AlignmentFlag.AlignCenter,
                    agent.name[:1].upper(),
                )

            painter.setPen(QColor(236, 240, 248))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(
                QRectF(center.x() - 62, center.y() + ITEM_RADIUS + 6, 124, 24),
                Qt.AlignmentFlag.AlignCenter,
                agent.name,
            )

        painter.setBrush(QColor(25, 34, 52, 255))
        painter.setPen(QPen(QColor(100, 120, 155, 180), 2))
        painter.drawEllipse(QPointF(self._center, self._center), self._center_radius, self._center_radius)
        painter.setPen(QColor(240, 245, 255))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        count = len(self._selected)
        if count == 0:
            label = "选择 Agent"
        elif count == 1:
            label = "发送"
        else:
            label = f"发送 {count} 个"
        painter.drawText(
            QRectF(self._center - 44, self._center - 18, 88, 36),
            Qt.AlignmentFlag.AlignCenter,
            label,
        )

        if self._hint:
            painter.setPen(QColor(255, 196, 120))
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(
                QRectF(self._center - 60, self._center + 20, 120, 18),
                Qt.AlignmentFlag.AlignCenter,
                self._hint,
            )
