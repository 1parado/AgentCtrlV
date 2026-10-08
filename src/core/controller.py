"""M2 业务链路：热键 → 环形菜单 → 注入（T2.1 / T2.3）。

职责：
- 热键触发时**先**把剪贴板内容取成 Payload（菜单打开期间用户可能改剪贴板）
- 让用户选 Agent，再把 Payload 注入选中目标的输入框
- 失败必须可见：没找到窗口、Agent 不支持该载荷、注入失败都要报出来

不做的事：热键注册（HotkeyManager）、菜单绘制（RadialMenu）、
多目标调度间隔（T3.1）、冷启动（T3.2）。
"""

from __future__ import annotations

from typing import Callable, Protocol

from src.core.agents import BUILTIN_AGENTS, AgentSpec, find_agent
from src.core.payload import Payload
from src.core.window import WindowInfo, WindowLocator
from src.injectors.base import InjectionOutcome, InjectionStatus
from src.injectors.clipboard_injector import ClipboardInjector
from src.utils.logger import get_logger

NotifyFn = Callable[..., None]


class ClipboardLike(Protocol):
    def read_image(self): ...
    def read_text(self) -> str | None: ...


class MenuLike(Protocol):
    def show_at(self, pos) -> None: ...
    def isVisible(self) -> bool: ...


class Controller:
    """把一次热键触发放大成一次完整的分发动作。"""

    def __init__(
        self,
        *,
        clipboard: ClipboardLike,
        locator: WindowLocator,
        menu: MenuLike,
        notifier: NotifyFn,
        agents: tuple[AgentSpec, ...] = BUILTIN_AGENTS,
        injector_factory: Callable[[AgentSpec], ClipboardInjector] | None = None,
        cursor_pos: Callable[[], object] | None = None,
        logger=None,
    ) -> None:
        self._clipboard = clipboard
        self._locator = locator
        self._menu = menu
        self._notify = notifier
        self._agents = agents
        self._injector_factory = injector_factory or self._default_injector
        self._cursor_pos = cursor_pos
        self._last_payload: Payload | None = None
        self._log = logger or get_logger(__name__)

    # ---------- 触发入口 ----------

    def dispatch(self) -> None:
        """Alt+V：分发当前剪贴板。"""
        if self._menu.isVisible():
            self._log.debug("菜单已打开，忽略重复触发")
            return

        payload = self._read_payload()
        if payload is None:
            self._notify(
                "没有可分发的剪贴板内容",
                "请先复制图片或文本，再按热键",
                warning=True,
            )
            return

        self._last_payload = payload
        self._log.info("准备分发 %s", payload.describe())
        self._show_menu()

    def resend(self) -> None:
        """Alt+Shift+V：重发上一次 Payload（失败保留可重试）。"""
        if self._menu.isVisible():
            return
        if self._last_payload is None:
            self._notify("没有可重发的内容", "请先用 Alt+V 分发一次", warning=True)
            return
        payload = self._last_payload
        if payload.kind == "image" and payload.image is not None:
            self._last_payload = Payload.from_image(payload.image)  # 防止底层被改写
        self._log.info("重发 %s", payload.describe())
        self._show_menu()

    # ---------- 菜单回调 ----------

    def on_confirmed(self, agent_ids: list[str]) -> None:
        payload = self._last_payload
        if payload is None:
            self._log.error("菜单确认但没有任何 Payload，忽略")
            return

        outcomes: list[tuple[str, InjectionOutcome]] = []
        for agent_id in agent_ids:
            outcomes.append((agent_id, self._inject(agent_id, payload)))

        self._report(outcomes)

    def on_cancelled(self) -> None:
        """Esc / 点空白：必须无副作用——不写剪贴板、不激活窗口、不注入。"""
        self._log.info("用户取消，未做任何注入")

    # ---------- 内部 ----------

    def _read_payload(self) -> Payload | None:
        image = self._clipboard.read_image()
        if image is not None:
            return Payload.from_image(image)
        text = self._clipboard.read_text()
        if text and text.strip():
            return Payload.from_text(text)
        return None

    def _show_menu(self) -> None:
        pos = self._cursor_pos() if self._cursor_pos else _default_cursor_pos()
        self._menu.show_at(pos)

    def _default_injector(self, agent: AgentSpec) -> ClipboardInjector:
        return ClipboardInjector(
            self._clipboard,
            self._locator,
            paste_delay_ms=agent.paste_delay_ms,
            render_delay_ms=agent.render_delay_ms,
        )

    def _locate_window(self, agent: AgentSpec) -> WindowInfo | None:
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
            return self._locator.find_host_window(
                agent.cli_match, terminal_process=agent.process
            )
        return self._locator.find(process=agent.process, title_pattern=agent.title_pattern)

    def _missing_window_reason(self, agent: AgentSpec) -> str:
        if agent.cli_match:
            return (
                f"没找到正在运行 {agent.cli_match} 的 {agent.process} 窗口"
                f"——请先在终端里启动 {agent.name}"
            )
        # 把该进程当前真实的窗口标题列出来，让"找不到"变成一步就能修的事
        return (
            f"没找到 {agent.name} 的窗口（进程 {agent.process}，"
            f"标题 {agent.title_pattern!r}）——请先启动它。"
            f"{self._locator.describe_candidates(agent.process)}"
        )

    def _inject(self, agent_id: str, payload: Payload) -> InjectionOutcome:
        agent = find_agent(agent_id, self._agents)
        if agent is None:
            return InjectionOutcome(InjectionStatus.FAILED, f"未知 Agent：{agent_id}")
        if not agent.enabled:
            return InjectionOutcome(InjectionStatus.FAILED, f"{agent.name} 已禁用")
        if not agent.supports(payload.kind):
            return InjectionOutcome(
                InjectionStatus.FAILED,
                f"{agent.name} 不支持{_kind_label(payload.kind)}（supported_payloads="
                f"{agent.supported_payloads}）",
            )

        window = self._locate_window(agent)
        if window is None:
            return InjectionOutcome(InjectionStatus.FAILED, self._missing_window_reason(agent))

        outcome = self._injector_factory(agent).inject(payload, window)
        self._log.info("%s -> %s", agent.name, outcome)
        return outcome

    def _report(self, outcomes: list[tuple[str, InjectionOutcome]]) -> None:
        failed = [(aid, o) for aid, o in outcomes if o.status is InjectionStatus.FAILED]
        degraded = [(aid, o) for aid, o in outcomes if o.status is InjectionStatus.DEGRADED]

        if failed:
            names = "、".join(_name(aid, self._agents) for aid, _ in failed)
            detail = "；".join(f"{_name(aid, self._agents)}：{o.detail}" for aid, o in failed)
            self._notify(f"{len(failed)} 个目标失败", detail or f"{names} 未送达", warning=True)
            return
        if degraded:
            detail = "；".join(f"{_name(aid, self._agents)}：{o.detail}" for aid, o in degraded)
            self._notify("可能未完全成功", detail, warning=True)
            return

        self._log.info("全部目标已派发：%s", "、".join(_name(aid, self._agents) for aid, _ in outcomes))


def _kind_label(kind: str) -> str:
    return "图片" if kind == "image" else "文本"


def _name(agent_id: str, agents: tuple[AgentSpec, ...]) -> str:
    agent = find_agent(agent_id, agents)
    return agent.name if agent else agent_id


def _default_cursor_pos():
    from PySide6.QtGui import QCursor

    return QCursor.pos()
