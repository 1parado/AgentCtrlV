"""自检结论逻辑的单元测试。

`evaluate_delivery` 是纯函数，所以"注入失败到底是哪一环"这套判断
可以在不碰真实窗口的前提下被完整覆盖。
"""

from __future__ import annotations

import pytest

from scripts.selftest import Check, evaluate_delivery


def test_delivery_success() -> None:
    state = {"active": True, "focused": "ProbeTarget", "keys_seen": ["v"], "last_mime": {"has_image": True}}
    check = evaluate_delivery(state, "has_image")

    assert check.ok
    assert "确认收到" in check.detail


def test_delivery_success_requires_expected_key() -> None:
    """收到的是文本而不是图片时，has_image 这一项不能算通过。"""
    state = {"active": True, "keys_seen": ["v"], "last_mime": {"has_image": False, "has_text": True}}
    assert not evaluate_delivery(state, "has_image").ok


def test_delivery_no_state_means_probe_missing() -> None:
    check = evaluate_delivery({}, "has_image")

    assert not check.ok
    assert "没有回报" in check.detail


def test_delivery_reports_activation_failure() -> None:
    state = {"active": False, "focused": "ProbeTarget", "keys_seen": [], "last_mime": None}
    check = evaluate_delivery(state, "has_image")

    assert not check.ok
    assert "前台窗口" in check.detail


def test_delivery_detects_swallowed_keystrokes() -> None:
    """回归：窗口是前台、控件有焦点，但一个键都没收到。"""
    state = {"active": True, "focused": "ProbeTarget", "keys_seen": [], "last_mime": None}
    check = evaluate_delivery(state, "has_image")

    assert not check.ok
    assert "一个按键都没收到" in check.detail
    assert "间歇" in check.detail


def test_delivery_keys_arrived_but_no_paste() -> None:
    state = {
        "active": True,
        "focused": "ProbeTarget",
        "keys_seen": ["v"],
        "paste_calls": 0,
        "last_mime": None,
    }
    check = evaluate_delivery(state, "has_image")

    assert not check.ok
    assert "没有触发粘贴" in check.detail or "没触发粘贴" in check.detail
    assert "v" in check.detail


@pytest.mark.parametrize(
    ("ok", "mark"),
    [(True, "✅"), (False, "❌")],
)
def test_check_render_shows_position_and_mark(ok: bool, mark: str) -> None:
    rendered = Check("剪贴板层", ok, "细节").render(2, 4)

    assert rendered.startswith("[2/4] 剪贴板层")
    assert mark in rendered
    assert "细节" in rendered
