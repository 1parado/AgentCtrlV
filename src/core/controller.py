"""业务链路：热键 → 环形菜单 → 注入（T2.1 / T2.3 / T3.1 / T3.2）。

职责：
- 热键触发时**先**把剪贴板内容取成 Payload（菜单打开期间用户可能改剪贴板）
- 让用户选 Agent（可多选），再把 Payload 逐个注入
- Agent 没在运行时按配置冷启动（T3.2）
- 失败必须可见：找不到窗口、不支持该载荷、注入失败、冷启动超时都要报出来

不做的事：热键注册（HotkeyManager）、菜单绘制（RadialMenu）、
启动与等待窗口的实现（cold_start.ColdStarter）。
"""

from __future__ import annotations

import time
from typing import Callable, Protocol

from src.core import targeting
from src.core.agents import BUILTIN_AGENTS, AgentSpec, find_agent
from src.core.cold_start import ColdStarter
from src.core.payload import Payload
from src.core.window import WindowLocator
from src.injectors.base import InjectionOutcome, InjectionStatus
from src.injectors.clipboard_injector import ClipboardInjector
from src.utils.logger import get_logger

NotifyFn = Callable[..., None]

#: T3.1：多选时两个目标之间的间隔。留出这段时间是为了让上一个 Agent
#: 的界面先稳定下来，也避免连续抢前台把上一次粘贴打断。
MULTI_TARGET_INTERVAL_S = 0.8

#: 菜单刚隐藏、开始注入之前先等一会儿。
#: 实测依据：不加这一步时，多选里**越靠前的目标越容易收不到 V 键**
#: （探针只收到 Ctrl 的 keydown）。原因是我们自己的菜单窗口刚隐藏，
#: 前台还在交接，SendInput 把 Ctrl 发给了目标、把 V 发给了别人——
#: 一次 SendInput 的事件是**逐条**按当时的焦点窗口投递的，不是原子操作。
MENU_CLOSE_SETTLE_S = 0.35


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
        cold_starter: ColdStarter | None = None,
        interval_s: float = MULTI_TARGET_INTERVAL_S,
        menu_settle_s: float = MENU_CLOSE_SETTLE_S,
        sleep: Callable[[float], None] = time.sleep,
        logger=None,
    ) -> None:
        self._clipboard = clipboard
        self._locator = locator
        self._menu = menu
        self._notify = notifier
        self._agents = agents
        self._injector_factory = injector_factory or self._default_injector
        self._cursor_pos = cursor_pos
        self._cold_starter = cold_starter or ColdStarter(locator)
        self._interval_s = interval_s
        self._menu_settle_s = menu_settle_s
        self._sleep = sleep
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
        if agent_ids:
            # 先让前台从"我们的菜单"交接出去，否则第一个目标最容易丢键
            self._sleep(self._menu_settle_s)
        for index, agent_id in enumerate(agent_ids):
            if index:
                # T3.1：目标之间留间隔，让上一个 Agent 的界面先稳定下来
                self._sleep(self._interval_s)
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

        warning = ""
        window = targeting.locate_window(self._locator, agent)
        if window is None:
            # T3.2：没在跑就按配置冷启动，真的起来了再注入
            result = self._cold_starter.ensure_window(agent)
            if result.window is None:
                return InjectionOutcome(InjectionStatus.FAILED, result.detail)
            window = result.window
            warning = result.detail

        outcome = self._injector_factory(agent).inject(payload, window)
        self._log.info("%s -> %s", agent.name, outcome)
        if warning and outcome.ok:
            # 内容送出去了，但冷启动过程里有件事没成（例如新会话没触发），
            # 属于"可能未完全成功"，必须让用户看得见。
            return InjectionOutcome(InjectionStatus.DEGRADED, warning)
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
