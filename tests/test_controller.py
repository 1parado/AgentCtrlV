"""M2/T2.3：控制器业务流程测试（热键 → 菜单 → 注入）。

全部依赖注入为假件，不碰真实剪贴板/窗口/键盘。
窗口定位相关用例见 test_controller_locate.py。
"""

from __future__ import annotations

from PIL import Image

from src.injectors.base import InjectionOutcome, InjectionStatus
from tests.controller_harness import TERMINAL_WINDOW, WINDOW, Harness


# ---------- 触发 ----------


def test_dispatch_captures_image_payload_then_shows_menu() -> None:
    harness = Harness(image=Image.new("RGB", (5, 5)))
    harness.build().dispatch()

    assert harness.menu.shown == ["cursor"]
    assert harness.notifications == []


def test_dispatch_falls_back_to_text() -> None:
    harness = Harness(text="hello")
    controller = harness.build()
    controller.dispatch()

    assert harness.menu.shown == ["cursor"]
    # 注入时用的是文本载荷
    controller.on_confirmed(["chatgpt-desktop"])
    payload, _window = harness.injector.calls[0]
    assert payload.kind == "text"


def test_dispatch_prefers_image_over_text() -> None:
    harness = Harness(image=Image.new("RGB", (5, 5)), text="also text")
    controller = harness.build()
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop"])

    payload, _window = harness.injector.calls[0]
    assert payload.kind == "image"


def test_dispatch_without_payload_warns_and_does_not_open_menu() -> None:
    harness = Harness()
    harness.build().dispatch()

    assert harness.menu.shown == []
    assert harness.warnings
    assert "没有可分发的剪贴板内容" in harness.warnings[0][0]


def test_dispatch_ignores_repeat_when_menu_open() -> None:
    harness = Harness(image=Image.new("RGB", (5, 5)))
    harness.menu.visible = True
    harness.build().dispatch()

    assert harness.menu.shown == []


def test_blank_text_is_not_a_payload() -> None:
    harness = Harness(text="   \n ")
    harness.build().dispatch()

    assert harness.menu.shown == []
    assert harness.warnings


# ---------- 确认 / 取消 ----------


def test_confirm_injects_into_matching_window() -> None:
    harness = Harness(image=Image.new("RGB", (5, 5)))
    controller = harness.build()
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop"])

    assert harness.locator.asks == [("ChatGPT.exe", "ChatGPT*")]
    payload, window = harness.injector.calls[0]
    assert window is WINDOW
    assert payload.kind == "image"
    assert harness.warnings == []


def test_confirm_reports_missing_window() -> None:
    harness = Harness(image=Image.new("RGB", (5, 5)), window=None)
    controller = harness.build()
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop"])

    assert harness.injector.calls == []
    assert harness.warnings
    assert "没找到" in harness.warnings[0][1]


def test_confirm_reports_unsupported_payload() -> None:
    """Windows Terminal 只收文本；给图片必须明确报错，不能静默什么都不做。"""
    harness = Harness(image=Image.new("RGB", (5, 5)), window=TERMINAL_WINDOW)
    controller = harness.build()
    controller.dispatch()
    controller.on_confirmed(["windows-terminal"])

    assert harness.injector.calls == []
    assert harness.warnings
    assert "不支持图片" in harness.warnings[0][1]


def test_confirm_reports_injection_failure() -> None:
    harness = Harness(
        image=Image.new("RGB", (5, 5)),
        outcome=InjectionOutcome(InjectionStatus.FAILED, "窗口未能激活"),
    )
    controller = harness.build()
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop"])

    assert harness.warnings
    assert "窗口未能激活" in harness.warnings[0][1]


def test_confirm_reports_degraded_as_possibly_unsuccessful() -> None:
    harness = Harness(
        image=Image.new("RGB", (5, 5)),
        outcome=InjectionOutcome(InjectionStatus.DEGRADED, "降级为盲粘"),
    )
    controller = harness.build()
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop"])

    assert harness.warnings
    assert "可能未完全成功" in harness.warnings[0][0]


def test_confirm_with_unknown_agent_is_reported() -> None:
    harness = Harness(image=Image.new("RGB", (5, 5)))
    controller = harness.build()
    controller.dispatch()
    controller.on_confirmed(["nonexistent"])

    assert harness.injector.calls == []
    assert harness.warnings
    assert "未知 Agent" in harness.warnings[0][1]


def test_confirm_without_payload_is_ignored() -> None:
    harness = Harness(image=Image.new("RGB", (5, 5)))
    controller = harness.build()
    controller.on_confirmed(["chatgpt-desktop"])  # 没先 dispatch

    assert harness.injector.calls == []
    assert harness.notifications == []


def test_cancel_has_no_side_effects() -> None:
    harness = Harness(image=Image.new("RGB", (5, 5)))
    controller = harness.build()
    controller.dispatch()
    controller.on_cancelled()

    assert harness.injector.calls == []
    assert harness.locator.asks == []
    assert harness.notifications == []


# ---------- 重发 ----------


def test_resend_without_history_warns() -> None:
    harness = Harness()
    harness.build().resend()

    assert harness.menu.shown == []
    assert harness.warnings
    assert "没有可重发的内容" in harness.warnings[0][0]


def test_resend_reuses_last_payload_even_if_clipboard_changed() -> None:
    harness = Harness(image=Image.new("RGB", (5, 5)))
    controller = harness.build()
    controller.dispatch()

    harness.clipboard.image = None
    harness.clipboard.text = "changed"
    controller.resend()
    controller.on_confirmed(["chatgpt-desktop"])

    payload, _window = harness.injector.calls[0]
    assert payload.kind == "image"
