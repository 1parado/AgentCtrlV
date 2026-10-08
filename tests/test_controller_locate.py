"""控制器窗口定位测试（GUI 按进程+标题；CLI 从进程树反推宿主终端）。"""

from __future__ import annotations

from PIL import Image

from src.core.agents import AgentSpec
from tests.controller_harness import TEST_AGENTS, TERMINAL_WINDOW, Harness


def test_test_agents_cover_the_three_kinds_under_test() -> None:
    assert {a.id for a in TEST_AGENTS} == {'chatgpt-desktop', 'cursor', 'windows-terminal'}


# ---------- CLI Agent 的窗口定位 ----------


def test_cli_agent_uses_host_window_lookup() -> None:
    """带 cli_match 的 Agent 必须走进程树定位，而不是按标题猜。"""
    agents = (
        AgentSpec(
            id="claude-code",
            name="Claude Code",
            type="cli",
            process="WindowsTerminal.exe",
            cli_match="claude.exe",
            supported_payloads=["text"],
        ),
    )
    harness = Harness(text="hello", window=TERMINAL_WINDOW)
    calls: list[tuple] = []

    def fake_host(cli_match, terminal_process=None):
        calls.append((cli_match, terminal_process))
        return TERMINAL_WINDOW

    harness.locator.find_host_window = fake_host
    controller = harness.build(agents=agents)
    controller.dispatch()
    controller.on_confirmed(["claude-code"])

    assert calls == [("claude.exe", "WindowsTerminal.exe")]
    assert harness.injector.calls[0][1] is TERMINAL_WINDOW


def test_cli_agent_without_running_process_reports_clearly() -> None:
    agents = (
        AgentSpec(
            id="codex-cli",
            name="Codex CLI",
            type="cli",
            process="WindowsTerminal.exe",
            cli_match="codex.js",
            supported_payloads=["text"],
        ),
    )
    harness = Harness(text="hello")
    harness.locator.find_host_window = lambda *a, **k: None
    controller = harness.build(agents=agents)
    controller.dispatch()
    controller.on_confirmed(["codex-cli"])

    assert harness.injector.calls == []
    assert harness.warnings
    assert "codex.js" in harness.warnings[0][1]


def test_missing_gui_window_lists_actual_titles() -> None:
    """找不到窗口时要给出该进程真实的标题，让配置一步就能改对。"""
    harness = Harness(image=Image.new("RGB", (5, 5)), window=None)
    harness.locator.describe_candidates = lambda process, limit=6: f"{process} 当前窗口标题：'真标题'"
    controller = harness.build()
    controller.dispatch()
    controller.on_confirmed(["chatgpt-desktop"])

    assert harness.warnings
    assert "真标题" in harness.warnings[0][1]
