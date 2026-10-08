"""T3.1 多选调度 + T3.2 冷启动在业务链路上的行为。

冷启动细节的行为在 test_cold_start.py；这里只验证 Controller 有没有正确
把它接进来（什么时候该调、失败怎么报）。
"""

from __future__ import annotations

from PIL import Image

from src.core.cold_start import ColdStartResult
from src.core.controller import MENU_CLOSE_SETTLE_S, MULTI_TARGET_INTERVAL_S
from src.injectors.base import InjectionOutcome, InjectionStatus
from tests.controller_harness import TERMINAL_WINDOW, WINDOW, Harness


class RecordingInjector:
    """记录"这个载荷被发给了哪个 Agent"（默认注入器只记 window，认不出是谁）。"""

    def __init__(self, agent, seen: list, outcome=None) -> None:
        self.agent = agent
        self.seen = seen
        self.outcome = outcome or InjectionOutcome(InjectionStatus.SUCCESS)

    def inject(self, payload, window):
        self.seen.append(self.agent.id)
        return self.outcome


class FakeColdStarter:
    def __init__(self, result: ColdStartResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def ensure_window(self, agent):
        self.calls.append(agent.id)
        return self.result


# ---------- T3.1 多选 ----------


def test_multi_target_sends_in_menu_order() -> None:
    seen: list[str] = []
    harness = Harness(text="hello")  # 文本载荷三个 Agent 都支持
    controller = harness.build(
        injector_factory=lambda agent: RecordingInjector(agent, seen),
        sleep=lambda _s: None,
    )
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop", "cursor", "windows-terminal"])

    assert seen == ["chatgpt-desktop", "cursor", "windows-terminal"]


def test_multi_target_waits_between_targets() -> None:
    """注入前先等前台交接（menu settle），目标之间再各等 800ms。"""
    sleeps: list[float] = []
    harness = Harness(text="hello")
    controller = harness.build(sleep=sleeps.append)
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop", "cursor", "windows-terminal"])

    assert sleeps == [MENU_CLOSE_SETTLE_S, MULTI_TARGET_INTERVAL_S, MULTI_TARGET_INTERVAL_S]


def test_settle_happens_before_the_first_target_only() -> None:
    """settle 是"菜单关了、前台在交接"这一次性的等待，不该每轮都付。"""
    sleeps: list[float] = []
    harness = Harness(text="hello")
    controller = harness.build(sleep=sleeps.append)
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop", "cursor"])

    assert sleeps.count(MENU_CLOSE_SETTLE_S) == 1


def test_single_target_only_pays_the_settle() -> None:
    """单选是快路径：只有前台交接那一次等待，没有多选间隔。"""
    sleeps: list[float] = []
    harness = Harness(image=Image.new("RGB", (4, 4)))
    controller = harness.build(sleep=sleeps.append)
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop"])

    assert sleeps == [MENU_CLOSE_SETTLE_S]
    assert len(harness.injector.calls) == 1


def test_multi_target_reports_one_failure_and_keeps_going() -> None:
    """一个目标失败不该中断其余目标，而且失败要汇总通知。"""
    seen: list[str] = []
    harness = Harness(text="hello")
    failing = InjectionOutcome(InjectionStatus.FAILED, "没找到窗口")

    def factory(agent):
        outcome = failing if agent.id == "cursor" else None
        return RecordingInjector(agent, seen, outcome)

    controller = harness.build(injector_factory=factory, sleep=lambda _s: None)
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop", "cursor", "windows-terminal"])

    assert seen == ["chatgpt-desktop", "cursor", "windows-terminal"]
    assert harness.warnings
    assert "Cursor" in harness.warnings[0][1]
    assert "没找到窗口" in harness.warnings[0][1]


def test_multi_target_all_success_is_silent() -> None:
    harness = Harness(image=Image.new("RGB", (4, 4)))
    controller = harness.build(sleep=lambda _s: None)
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop", "cursor"])

    assert harness.notifications == []
    assert len(harness.injector.calls) == 2


# ---------- T3.2 冷启动 ----------


def test_missing_window_triggers_cold_start() -> None:
    """窗口找不到时自动冷启动，起来之后照常注入。"""
    starter = FakeColdStarter(ColdStartResult(WINDOW))
    harness = Harness(image=Image.new("RGB", (4, 4)), window=None)
    controller = harness.build(cold_starter=starter)
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop"])

    assert starter.calls == ["chatgpt-desktop"]
    assert harness.injector.calls, "冷启动拿到窗口后必须继续注入"
    assert harness.notifications == []


def test_existing_window_does_not_cold_start() -> None:
    starter = FakeColdStarter(ColdStartResult(WINDOW))
    harness = Harness(image=Image.new("RGB", (4, 4)))
    controller = harness.build(cold_starter=starter)
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop"])

    assert starter.calls == [], "已经在跑就不该走冷启动"


def test_cold_start_failure_is_reported_and_nothing_injected() -> None:
    starter = FakeColdStarter(ColdStartResult(None, "已请求启动 ChatGPT，但 8.0s 内没等到它的窗口"))
    harness = Harness(image=Image.new("RGB", (4, 4)), window=None)
    controller = harness.build(cold_starter=starter)
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop"])

    assert harness.injector.calls == []
    assert harness.warnings
    assert "没等到它的窗口" in harness.warnings[0][1]


def test_cold_start_warning_downgrades_to_degraded() -> None:
    """窗口起来了、内容也送了，但新会话没触发——必须说"可能未完全成功"。"""
    starter = FakeColdStarter(ColdStartResult(TERMINAL_WINDOW, "新会话未触发"))
    harness = Harness(text="hi", window=None)
    controller = harness.build(cold_starter=starter)
    controller.dispatch()
    controller.on_confirmed(["windows-terminal"])

    assert harness.injector.calls, "内容仍应送出"
    assert harness.warnings
    assert "可能未完全成功" in harness.warnings[0][0]
