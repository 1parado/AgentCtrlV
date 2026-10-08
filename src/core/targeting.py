"""把"用户选中的 Agent"翻译成"要注入的那个窗口"，以及失败时怎么解释。

这段逻辑原本长在 Controller 里。M3 的冷启动也要用它（反复找、等窗口出现），
所以抽出来共用，免得两处各写一份然后慢慢漂移。
"""

from __future__ import annotations

from src.core.agents import AgentSpec
from src.core.window import WindowInfo, WindowLocator


def locate_window(locator: WindowLocator, agent: AgentSpec) -> WindowInfo | None:
    """定位注入目标窗口。

    判据是 **cli_match 有没有值**，不是 `type`：
    `type: cli` 在 CONFIG_SCHEMA 里表示"默认只收文本"，
    而 Windows Terminal 本身就是 `type: cli`，它却是那个窗口本身
    （没有寄生在终端里的 CLI），所以它走普通的进程+标题匹配。

    有 cli_match 时：CLI 没有自己的窗口，且会动态改写终端标题——
    靠标题猜等于把失败伪装成成功，所以改成"找到真正在运行的那个
    CLI 进程，再顺进程树往上找宿主窗口"。
    """
    if agent.cli_match:
        return locator.find_host_window(agent.cli_match, terminal_process=agent.process)
    return locator.find(process=agent.process, title_pattern=agent.title_pattern)


def missing_window_reason(locator: WindowLocator, agent: AgentSpec) -> str:
    """没找到窗口时，给出**可操作**的解释。

    - CLI Agent：告诉用户去终端里启动它（我们没有它自己的窗口可找）
    - GUI Agent：把该进程当前真实的窗口标题列出来，
      让"找不到"变成看一眼就能改对配置的事
    """
    if agent.cli_match:
        return (
            f"没找到正在运行 {agent.cli_match} 的 {agent.process} 窗口"
            f"——请先在终端里启动 {agent.name}"
        )
    return (
        f"没找到 {agent.name} 的窗口（进程 {agent.process}，"
        f"标题 {agent.title_pattern!r}）——请先启动它。"
        f"{locator.describe_candidates(agent.process)}"
    )


def cold_start_blocked_reason(agent: AgentSpec) -> str:
    """为什么这个 Agent 不能冷启动（配置层面）。"""
    if agent.cli_match:
        return f"{agent.name} 跑在终端里，本项目不会替你开终端执行命令，请手动启动它"
    if not agent.can_launch:
        return f"{agent.name} 没有配置 launch 命令，无法自动启动"
    return ""
