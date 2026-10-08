"""T3.1：Ctrl 多选 + 中心/回车确认。

单选快路径（T2.3）不能被多选破坏——所以这里同时断言"不加 Ctrl 时仍然一下就走"。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent

from src.ui.radial_menu import MAX_SELECTION, AgentItem, RadialMenu

AGENTS = [
    AgentItem("chatgpt-desktop", "ChatGPT"),
    AgentItem("cursor", "Cursor"),
    AgentItem("windows-terminal", "Windows Terminal"),
]


def _menu(agents=None) -> RadialMenu:
    return RadialMenu(agents or AGENTS)


def _click(menu: RadialMenu, x: float, y: float, *, ctrl: bool = False) -> None:
    modifiers = Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier
    local = QPointF(x, y)
    menu.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            local,
            menu.mapToGlobal(local),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            modifiers,
        )
    )


def _click_item(menu: RadialMenu, index: int, *, ctrl: bool = False) -> None:
    center = menu._item_center(index)
    _click(menu, center.x(), center.y(), ctrl=ctrl)


def _click_center(menu: RadialMenu) -> None:
    _click(menu, menu._center, menu._center)


def _key(menu: RadialMenu, key: Qt.Key) -> None:
    menu.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def _spy(menu: RadialMenu) -> dict:
    seen: dict = {"confirmed": [], "cancelled": []}
    menu.confirmed.connect(lambda ids: seen["confirmed"].append(list(ids)))
    menu.cancelled.connect(lambda: seen["cancelled"].append(True))
    return seen


# ---------- 多选 ----------


def test_ctrl_click_selects_without_sending(qt_app) -> None:
    """Ctrl+点击只切换选中，必须等确认——否则多选根本无从谈起。"""
    menu = _menu()
    seen = _spy(menu)

    _click_item(menu, 0, ctrl=True)

    assert menu.selected_ids == ("chatgpt-desktop",)
    assert seen["confirmed"] == []


def test_ctrl_click_same_item_twice_deselects(qt_app) -> None:
    menu = _menu()
    seen = _spy(menu)

    _click_item(menu, 1, ctrl=True)
    _click_item(menu, 1, ctrl=True)

    assert menu.selected_ids == ()
    assert seen["confirmed"] == []


def test_multi_select_then_center_confirms_all_in_menu_order(qt_app) -> None:
    """确认顺序按菜单排列，不按点击顺序——注入间隔才有可预测的次序。"""
    menu = _menu()
    seen = _spy(menu)
    menu.show()

    _click_item(menu, 2, ctrl=True)
    _click_item(menu, 0, ctrl=True)
    _click_center(menu)

    assert seen["confirmed"] == [["chatgpt-desktop", "windows-terminal"]]


def test_multi_select_then_enter_confirms(qt_app) -> None:
    menu = _menu()
    seen = _spy(menu)
    menu.show()

    _click_item(menu, 0, ctrl=True)
    _click_item(menu, 1, ctrl=True)
    _key(menu, Qt.Key.Key_Return)

    assert seen["confirmed"] == [["chatgpt-desktop", "cursor"]]


def test_selection_is_capped_at_prd_limit(qt_app) -> None:
    """PRD 限制最多 5 个；超出的点击不能静默丢弃，要有提示。"""
    agents = [AgentItem(f"a{i}", f"A{i}") for i in range(MAX_SELECTION + 1)]
    menu = _menu(agents)

    for index in range(MAX_SELECTION + 1):
        _click_item(menu, index, ctrl=True)

    assert len(menu.selected_ids) == MAX_SELECTION
    assert "最多选" in menu._hint


def test_deselecting_clears_the_cap_hint(qt_app) -> None:
    agents = [AgentItem(f"a{i}", f"A{i}") for i in range(MAX_SELECTION + 1)]
    menu = _menu(agents)
    for index in range(MAX_SELECTION + 1):
        _click_item(menu, index, ctrl=True)

    _click_item(menu, 0, ctrl=True)  # 取消一个

    assert menu._hint == ""
    assert len(menu.selected_ids) == MAX_SELECTION - 1


def test_disabled_agent_cannot_be_selected_with_ctrl(qt_app) -> None:
    menu = _menu([AgentItem("a", "A"), AgentItem("b", "B", enabled=False)])
    seen = _spy(menu)

    _click_item(menu, 1, ctrl=True)
    _click_center(menu)

    assert menu.selected_ids == ()
    assert seen["confirmed"] == []


# ---------- 单选快路径不能被拖慢 ----------


def test_plain_click_still_confirms_immediately(qt_app) -> None:
    menu = _menu()
    seen = _spy(menu)

    _click_item(menu, 1)

    assert seen["confirmed"] == [["cursor"]], "单击仍是一步到位，不需要再点中心"


def test_plain_click_replaces_a_multi_selection(qt_app) -> None:
    """已经 Ctrl 选了多个，再普通点一个 = 就发它一个（用户意图明确）。"""
    menu = _menu()
    seen = _spy(menu)
    menu.show()

    _click_item(menu, 0, ctrl=True)
    _click_item(menu, 1, ctrl=True)
    _click_item(menu, 2)

    assert seen["confirmed"] == [["windows-terminal"]]


# ---------- 取消 ----------


def test_escape_after_multi_select_cancels_without_confirming(qt_app) -> None:
    menu = _menu()
    seen = _spy(menu)
    menu.show()
    _click_item(menu, 0, ctrl=True)
    _click_item(menu, 1, ctrl=True)

    _key(menu, Qt.Key.Key_Escape)

    assert seen["cancelled"] == [True]
    assert seen["confirmed"] == []
    assert menu._selected == set()


def test_center_click_with_empty_selection_does_nothing(qt_app) -> None:
    menu = _menu()
    seen = _spy(menu)
    menu.show()

    _click_center(menu)

    assert seen["confirmed"] == []
    assert seen["cancelled"] == []


@pytest.mark.parametrize("count", [1, 2, MAX_SELECTION])
def test_selected_ids_are_stable_for_any_count(qt_app, count: int) -> None:
    agents = [AgentItem(f"a{i}", f"A{i}") for i in range(MAX_SELECTION)]
    menu = _menu(agents)

    for index in range(count):
        _click_item(menu, index, ctrl=True)

    assert menu.selected_ids == tuple(f"a{i}" for i in range(count))
