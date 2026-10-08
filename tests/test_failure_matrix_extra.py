"""M5：不在这张失败矩阵里、但同样必须"说得出来"的情况。

以及反向的一条：**成功与取消必须安静**——只有失败与降级才该打扰用户。
通知发多了和没发一样，都会让用户不再看通知。
"""

from __future__ import annotations

import pytest
from PIL import Image

from src.injectors.base import InjectionOutcome, InjectionStatus
from tests.controller_harness import Harness
from tests.test_failure_matrix import dispatch_confirmed


# ---------- 不属于矩阵，但同样必须可见的失败 ----------


def test_empty_clipboard_notifies() -> None:
    harness = Harness()

    harness.build().dispatch()

    assert harness.warnings, "没有内容可分发给出的必须是提示，不是沉默"


def test_unknown_agent_notifies() -> None:
    harness = Harness(text="hi")
    controller = harness.build()
    controller.dispatch()

    controller.on_confirmed(["不存在的-agent"])

    assert harness.warnings
    assert "未知 Agent" in harness.warnings[0][1]


def test_unsupported_payload_notifies() -> None:
    """CLI 类型 AI 只收文本，图片要明确拒绝而不是粘进去当路径。"""
    harness = Harness(image=Image.new("RGB", (4, 4)))
    controller = harness.build()
    controller.dispatch()

    controller.on_confirmed(["windows-terminal"])

    assert harness.warnings
    assert "不支持图片" in harness.warnings[0][1]


# ---------- 反向：取消必须安静且无副作用 ----------


def test_cancel_is_silent_and_has_no_side_effects() -> None:
    """取消不是失败，所以既不该通知，也不该动剪贴板/窗口/键盘。"""
    harness = Harness(text="hi")
    controller = harness.build()
    controller.dispatch()
    harness.notifications.clear()

    controller.on_cancelled()

    assert harness.notifications == []
    assert harness.locator.asks == []


@pytest.mark.parametrize("status", [InjectionStatus.SUCCESS])
def test_success_is_quiet(status) -> None:
    """成功也不该打扰用户——只有失败与降级才弹通知。"""
    harness = Harness(text="hi")
    harness.injector.outcome = InjectionOutcome(status)

    dispatch_confirmed(harness, "windows-terminal")

    assert harness.notifications == []
