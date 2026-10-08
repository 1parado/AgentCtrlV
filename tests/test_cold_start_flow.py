"""冷启动的完整流程（ColdStarter.ensure_window）与 UI 就绪等待。

单看启动/等窗口/新会话的零件行为在 test_cold_start.py；这里验证它们**串起来**
以后的行为，以及"窗口出现 != 能收输入"这个实测坑位。
"""

from __future__ import annotations

from src.core import cold_start
from src.core.agents import AgentSpec, ColdStartSpec
from src.core.cold_start import ColdStartError, ColdStarter
from tests.test_cold_start import WINDOW, FakeClock, SequenceLocator, gui_agent


# ---------- ColdStarter.ensure_window ----------


def test_already_running_skips_launch() -> None:
    launched: list[str] = []
    starter = ColdStarter(SequenceLocator([WINDOW]), launcher=launched.append)

    result = starter.ensure_window(gui_agent())

    assert result.ok and result.window is WINDOW
    assert launched == [], "已经在跑就不该再启动一次"


def test_launches_then_waits_for_window() -> None:
    launched: list[str] = []
    clock = FakeClock()
    starter = ColdStarter(
        SequenceLocator([None, WINDOW]),
        launcher=launched.append,
        sleep=clock.sleep,
        clock=clock,
    )

    result = starter.ensure_window(gui_agent())

    assert result.ok
    assert launched == ["C:\\Demo\\Demo.exe"]


def test_no_launch_command_explains_why() -> None:
    starter = ColdStarter(SequenceLocator([None]))

    result = starter.ensure_window(AgentSpec(id="x", name="X", process="X.exe"))

    assert not result.ok
    assert "没有配置 launch" in result.detail


def test_cli_agent_cannot_be_cold_started() -> None:
    """寄生在终端里的 CLI 我们不替用户开终端跑命令。"""
    agent = AgentSpec(
        id="codex-cli",
        name="Codex CLI",
        type="cli",
        process="WindowsTerminal.exe",
        cli_match="codex.js",
        launch="C:\\somewhere\\codex.exe",
    )
    starter = ColdStarter(SequenceLocator([None]))

    result = starter.ensure_window(agent)

    assert not result.ok
    assert "跑在终端里" in result.detail


def test_launch_failure_returns_reason() -> None:
    def boom(_command: str) -> None:
        raise ColdStartError("启动命令被拒绝")

    starter = ColdStarter(SequenceLocator([None]), launcher=boom)

    result = starter.ensure_window(gui_agent())

    assert not result.ok
    assert "启动命令被拒绝" in result.detail


def test_window_never_appearing_reports_timeout() -> None:
    clock = FakeClock()
    agent = gui_agent(cold_start=ColdStartSpec(ready_timeout=1000))
    starter = ColdStarter(
        SequenceLocator([None]), launcher=lambda _c: None, sleep=clock.sleep, clock=clock
    )

    result = starter.ensure_window(agent)

    assert not result.ok
    assert "没等到它的窗口" in result.detail
    assert "1.0s" in result.detail


def test_new_session_warning_is_carried_back() -> None:
    clock = FakeClock()
    agent = gui_agent(cold_start=ColdStartSpec(new_session=True, new_session_method="uia_button"))
    starter = ColdStarter(
        SequenceLocator([None, WINDOW]),
        launcher=lambda _c: None,
        sleep=clock.sleep,
        clock=clock,
    )

    result = starter.ensure_window(agent)

    assert result.ok
    assert "uia_button" in result.detail, "窗口起来了，但新会话没触发，必须带回来告知"


# ---------- ui_ready_timeout：窗口出现 != 能收输入 ----------
# 回归 2026-10 实测：冷启动的探针窗口出现了、也到了前台，但紧接着的 Ctrl+V
# 完全没被处理——应用还在初始化。CONFIG_SCHEMA 的 ui_ready_timeout 此前
# 被建模却从未使用。


def test_ui_ready_waits_and_settles() -> None:
    clock = FakeClock()
    locator = SequenceLocator([None, WINDOW])
    starter = ColdStarter(locator, launcher=lambda _c: None, sleep=clock.sleep, clock=clock)

    result = starter.ensure_window(gui_agent())

    assert result.ok
    assert locator.activate_calls >= 1, "必须真的去确认过窗口能到前台"
    assert result.detail == "", "就绪了就不该有告警"


def test_ui_ready_unreachable_is_reported() -> None:
    """切不到前台时必须说出来——注入大概率会失败，用户该知道为什么。"""
    clock = FakeClock()
    locator = SequenceLocator([None, WINDOW], activatable=False)
    starter = ColdStarter(locator, launcher=lambda _c: None, sleep=clock.sleep, clock=clock)

    result = starter.ensure_window(gui_agent())

    assert result.ok, "窗口确实起来了，不该当成冷启动失败"
    assert "没能把它切到前台" in result.detail


def test_ui_ready_is_bounded_by_configured_timeout() -> None:
    """不能无限等——上限就是配置里的 ui_ready_timeout。"""
    clock = FakeClock()
    locator = SequenceLocator([None, WINDOW], activatable=False)
    agent = gui_agent(cold_start=ColdStartSpec(ui_ready_timeout=1000))
    starter = ColdStarter(locator, launcher=lambda _c: None, sleep=clock.sleep, clock=clock)

    starter.ensure_window(agent)

    assert 1.0 <= clock.now <= 1.0 + cold_start.POLL_INTERVAL_S + cold_start.UI_SETTLE_S
