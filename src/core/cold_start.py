"""冷启动（T3.2）与新会话策略（T3.3）。

**诚实边界：启动成功 != Agent 就绪。**
`os.startfile` / `Popen` 只能证明"启动请求被系统接受了"，没有回执——
这和 SendInput 是同一类问题。所以冷启动的成功判据是**窗口真的出现了**，
而不是"没抛异常"。

失败不静默：没配 launch、启动命令报错、窗口迟迟不出现、新会话方式不支持，
每种都返回一句人话，由上层弹通知。
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from win32con import VK_CONTROL, VK_LWIN, VK_MENU, VK_SHIFT

from src.core import targeting
from src.core.agents import AgentSpec, ColdStartSpec
from src.core.hotkey import HotkeyModifier, HotkeyError, parse_hotkey
from src.core.window import WindowInfo, WindowLocator
from src.utils.logger import get_logger
from src.utils.sendinput import send_hotkey

#: 等窗口出现时的轮询间隔；太密是白烧 CPU，太疏是白等
POLL_INTERVAL_S = 0.25
#: 发完新会话快捷键后留一点时间让界面开始切换
NEW_SESSION_SETTLE_S = 0.4

#: RegisterHotKey 的 MOD_* 与 SendInput 要的 VK 不是一回事，必须映射
_MOD_TO_VK = (
    (HotkeyModifier.CTRL, VK_CONTROL),
    (HotkeyModifier.ALT, VK_MENU),
    (HotkeyModifier.SHIFT, VK_SHIFT),
    (HotkeyModifier.WIN, VK_LWIN),
)


class ColdStartError(RuntimeError):
    """连启动请求都没能发出去。"""


def modifiers_to_vks(modifiers: int) -> list[int]:
    """把 MOD_* 位标志转成 SendInput 需要的虚拟键码列表。"""
    return [vk for flag, vk in _MOD_TO_VK if modifiers & flag]


@dataclass(frozen=True)
class ColdStartResult:
    """cold start 的结果。

    window 为 None 表示失败，detail 说明原因；
    window 不为 None 但 detail 非空表示"起来了，但有件事得告诉你"。
    """

    window: WindowInfo | None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.window is not None


def launch(agent: AgentSpec, *, launcher: Callable[[str], None] | None = None) -> None:
    """把 Agent 拉起来。

    只负责"把启动请求发出去"。调用方必须再用 wait_for_window 确认真的起来了。
    """
    # 配置里可以写 %LOCALAPPDATA% 这类变量（不同用户路径不同），
    # 但 os.startfile / Path 都不会自己展开，必须先展开。
    command = os.path.expandvars(agent.launch.strip())
    if not command:
        raise ColdStartError(f"{agent.name} 没有配置 launch 命令")

    try:
        if launcher is not None:
            launcher(command)
        elif command.lower().startswith(("shell:", "http://", "https://")):
            # shell:AppsFolder\... 是 Windows 的应用别名，只有 ShellExecute 认得，
            # Popen 不认。os.startfile 就是 ShellExecute 的封装。
            os.startfile(command)  # noqa: S606 - 命令来自用户自己的配置
        elif Path(command).is_file():
            os.startfile(command)  # noqa: S606
        else:
            # 带参数的命令行：split() 处理不了带空格的路径，只能交给 shell
            subprocess.Popen(command, shell=True)  # noqa: S602
    except OSError as exc:
        raise ColdStartError(f"启动 {agent.name} 失败：{exc}") from exc


def wait_for_window(
    locator: WindowLocator,
    agent: AgentSpec,
    timeout_s: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> WindowInfo | None:
    """轮询等待窗口出现。超时返回 None（由调用方决定怎么报）。"""
    deadline = clock() + timeout_s
    while True:
        window = targeting.locate_window(locator, agent)
        if window is not None:
            return window
        if clock() >= deadline:
            return None
        sleep(POLL_INTERVAL_S)


def start_new_session(spec: ColdStartSpec, *, sender: Callable[[int, list[int]], None] | None = None) -> str:
    """按配置开一个新会话。返回空串表示没问题，否则返回原因。"""
    if not spec.new_session or spec.new_session_method == "none":
        return ""
    if spec.new_session_method == "uia_button":
        # M3 只做 hotkey。不认识的方式要明确说，不能假装做过了。
        return "new_session_method=uia_button 尚未实现（M3 只做 hotkey），新会话未触发"

    try:
        parsed = parse_hotkey(spec.new_session_hotkey)
    except HotkeyError as exc:
        return f"新会话快捷键 {spec.new_session_hotkey!r} 不可用：{exc}"

    vks = modifiers_to_vks(parsed.modifiers)
    try:
        if sender is not None:
            sender(parsed.vk, vks)
        else:
            send_hotkey(parsed.vk, *vks)
    except OSError as exc:
        return f"发送新会话快捷键失败：{exc}"
    return ""


class ColdStarter:
    """"Agent 没在跑就把它拉起来，再交回窗口"的完整流程。"""

    def __init__(
        self,
        locator: WindowLocator,
        *,
        launcher: Callable[[str], None] | None = None,
        sender: Callable[[int, list[int]], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        logger=None,
    ) -> None:
        self._locator = locator
        self._launcher = launcher
        self._sender = sender
        self._sleep = sleep
        self._clock = clock
        self._log = logger or get_logger(__name__)

    def ensure_window(self, agent: AgentSpec) -> ColdStartResult:
        """返回可用窗口；已经在跑就直接返回，不会重复启动。"""
        existing = targeting.locate_window(self._locator, agent)
        if existing is not None:
            return ColdStartResult(existing)

        blocked = targeting.cold_start_blocked_reason(agent)
        if blocked:
            reason = targeting.missing_window_reason(self._locator, agent)
            return ColdStartResult(None, f"{reason}；{blocked}")

        try:
            launch(agent, launcher=self._launcher)
        except ColdStartError as exc:
            self._log.error("冷启动失败：%s", exc)
            return ColdStartResult(None, str(exc))

        timeout_s = agent.cold_start.ready_timeout / 1000.0
        self._log.info("已请求启动 %s，等待窗口出现（最多 %.1fs）", agent.name, timeout_s)
        window = wait_for_window(
            self._locator, agent, timeout_s, sleep=self._sleep, clock=self._clock
        )
        if window is None:
            return ColdStartResult(
                None,
                f"已请求启动 {agent.name}，但 {timeout_s:.1f}s 内没等到它的窗口"
                "——它可能启动较慢，或界面语言/标题与 title_pattern 不符",
            )

        self._log.info("%s 窗口已出现：hwnd=%s", agent.name, window.hwnd)
        warning = start_new_session(agent.cold_start, sender=self._sender)
        if warning:
            self._log.warning("%s", warning)
        else:
            self._sleep(NEW_SESSION_SETTLE_S)
        return ColdStartResult(window, warning)
