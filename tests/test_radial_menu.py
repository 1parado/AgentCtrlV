"""M2/T2.2-T2.3：环形菜单逻辑测试（offscreen Qt，不需要真显示器）。

覆盖：条目排布与命中判定、单击即确认、Esc / 点空白取消、
禁用项不可选、以及"取消必须无副作用"。
"""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent

from src.ui.radial_menu import (
    ITEM_RADIUS,
    MAX_MENU_ITEMS,
    AgentItem,
    RadialMenu,
    compute_geometry,
)

AGENTS = [
    AgentItem("chatgpt-desktop", "ChatGPT"),
    AgentItem("cursor", "Cursor"),
    AgentItem("windows-terminal", "Windows Terminal"),
]


def _menu(agents=None) -> RadialMenu:
    return RadialMenu(agents or AGENTS)


def _click(menu: RadialMenu, x: float, y: float, button=Qt.MouseButton.LeftButton) -> None:
    local = QPointF(x, y)
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        local,
        menu.mapToGlobal(local),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )
    menu.mousePressEvent(event)


def _key(menu: RadialMenu, key: Qt.Key) -> None:
    menu.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def _spy(menu: RadialMenu) -> dict:
    seen: dict = {"confirmed": [], "cancelled": []}
    menu.confirmed.connect(lambda ids: seen["confirmed"].append(list(ids)))
    menu.cancelled.connect(lambda: seen["cancelled"].append(True))
    return seen


# ---------- 构造 ----------


def test_requires_at_least_one_agent(qt_app) -> None:
    with pytest.raises(ValueError, match="至少需要一个"):
        RadialMenu([])


def test_rejects_too_many_agents(qt_app) -> None:
    with pytest.raises(ValueError, match="最多支持"):
        RadialMenu([AgentItem(f"a{i}", f"A{i}") for i in range(MAX_MENU_ITEMS + 1)])


def test_accepts_more_than_eight_agents(qt_app) -> None:
    """Agent 数量是用户配的，菜单必须放得下 8 个以上。"""
    menu = RadialMenu([AgentItem(f"a{i}", f"A{i}") for i in range(MAX_MENU_ITEMS)])

    assert len(menu._agents) == MAX_MENU_ITEMS


def test_three_agents_are_evenly_spaced(qt_app) -> None:
    menu = _menu()
    angles = menu._angles

    assert len(angles) == 3
    # 第一个在正上方
    assert angles[0] == pytest.approx(-math.pi / 2)
    # 相邻夹角 120°
    assert (angles[1] - angles[0]) == pytest.approx(2 * math.pi / 3)


def test_geometry_grows_with_item_count() -> None:
    """条目一多，半径和窗口必须跟着长，否则圆周上会重叠。"""
    small_radius, small_size = compute_geometry(3)
    large_radius, large_size = compute_geometry(12)

    assert small_radius == pytest.approx(112)
    assert large_radius > small_radius
    assert large_size > small_size


def test_neighbours_never_overlap() -> None:
    """任意条目数下，相邻条目的圆心距都必须大于直径。"""
    for count in range(3, MAX_MENU_ITEMS + 1):
        radius, _size = compute_geometry(count)
        gap = 2 * radius * math.sin(math.pi / count)
        assert gap > ITEM_RADIUS * 2, f"{count} 个条目时圆心距 {gap:.1f} 小于直径"


def test_item_centers_stay_on_ring(qt_app) -> None:
    menu = _menu()
    for index in range(3):
        center = menu._item_center(index)
        distance = math.hypot(center.x() - menu._center, center.y() - menu._center)
        assert distance == pytest.approx(menu._radius)


# ---------- 命中判定 ----------


def test_item_hit_testing_hits_each_item(qt_app) -> None:
    menu = _menu()
    for index in range(3):
        center = menu._item_center(index)
        assert menu._item_at(center) == index


def test_item_hit_testing_rejects_outside(qt_app) -> None:
    menu = _menu()
    assert menu._item_at(QPointF(4, 4)) is None


def test_item_hit_testing_respects_radius(qt_app) -> None:
    menu = _menu()
    center = menu._item_center(0)
    assert menu._item_at(QPointF(center.x(), center.y() + ITEM_RADIUS - 1)) == 0
    assert menu._item_at(QPointF(center.x(), center.y() + ITEM_RADIUS + 5)) is None


def test_center_zone_detected(qt_app) -> None:
    menu = _menu()
    assert menu._in_center(QPointF(menu._center, menu._center))
    assert not menu._in_center(QPointF(menu._center + 100, menu._center))


# ---------- 交互 ----------


def test_single_click_confirms_immediately(qt_app) -> None:
    """T2.3：单击就发送，不需要二次确认。"""
    menu = _menu()
    seen = _spy(menu)
    center = menu._item_center(1)

    _click(menu, center.x(), center.y())

    assert seen["confirmed"] == [["cursor"]]
    assert seen["cancelled"] == []
    assert not menu.isVisible()


def test_clicking_each_item_confirms_that_item(qt_app) -> None:
    menu = _menu()
    for index, agent in enumerate(AGENTS):
        seen = _spy(menu)
        center = menu._item_center(index)
        _click(menu, center.x(), center.y())
        assert seen["confirmed"] == [[agent.id]]


def test_escape_cancels_without_confirming(qt_app) -> None:
    menu = _menu()
    seen = _spy(menu)
    menu.show()

    _key(menu, Qt.Key.Key_Escape)

    assert seen["cancelled"] == [True]
    assert seen["confirmed"] == []
    assert not menu.isVisible()


def test_clicking_blank_area_cancels(qt_app) -> None:
    menu = _menu()
    seen = _spy(menu)
    menu.show()

    _click(menu, 12, 12)  # 圆外空白

    assert seen["cancelled"] == [True]
    assert seen["confirmed"] == []


def test_center_click_confirms_only_when_something_selected(qt_app) -> None:
    menu = _menu()
    seen = _spy(menu)
    menu.show()

    _click(menu, menu._center, menu._center)  # 没选任何 Agent

    assert seen["confirmed"] == []
    assert seen["cancelled"] == []


def test_enter_without_selection_does_nothing(qt_app) -> None:
    menu = _menu()
    seen = _spy(menu)
    menu.show()

    _key(menu, Qt.Key.Key_Return)

    assert seen["confirmed"] == []


def test_disabled_agent_cannot_be_confirmed(qt_app) -> None:
    menu = _menu([AgentItem("a", "A"), AgentItem("b", "B", enabled=False)])
    seen = _spy(menu)
    center = menu._item_center(1)

    _click(menu, center.x(), center.y())

    assert seen["confirmed"] == []


def test_right_click_is_ignored(qt_app) -> None:
    menu = _menu()
    seen = _spy(menu)
    center = menu._item_center(0)

    _click(menu, center.x(), center.y(), button=Qt.MouseButton.RightButton)

    assert seen["confirmed"] == []
    assert seen["cancelled"] == []


def test_show_at_clears_previous_selection(qt_app) -> None:
    menu = _menu()
    seen = _spy(menu)
    menu._selected = {"cursor"}
    menu.show_at(QPointF(100, 100).toPoint())

    assert menu.selected_ids == ()
    _key(menu, Qt.Key.Key_Escape)
    assert seen["cancelled"] == [True]


def test_menu_size_is_fixed_for_instant_showing(qt_app) -> None:
    """尺寸固定 + 常驻对象，才能满足 T2.2 的 <150ms 预算。"""
    menu = _menu()
    expected = compute_geometry(len(AGENTS))[1]
    assert menu.width() == expected
    assert menu.height() == expected


def test_menu_actually_paints_pixels(qt_app) -> None:
    """paintEvent 的冒烟测试：菜单必须真的画出可见内容，不能是透明空壳。"""
    menu = _menu()
    menu.show()
    menu.repaint()

    pixmap = menu.grab()
    assert not pixmap.isNull()

    image = pixmap.toImage()
    opaque = 0
    for y in range(0, image.height(), 5):
        for x in range(0, image.width(), 5):
            colour = image.pixelColor(x, y)
            if colour.alpha() > 200 and (colour.red() + colour.green() + colour.blue()) > 90:
                opaque += 1
    assert opaque > 200, f"菜单看起来是空的（可见像素仅 {opaque}）"
