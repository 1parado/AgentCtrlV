"""窗口查找与前台激活（T1.2）。

force_foreground 分三段尝试，因为 Windows 的前台锁（foreground lock）会
直接让 SetForegroundWindow 静默失败：
  1. AttachThreadInput + BringWindowToTop + SetForegroundWindow
  2. 敲一下 Alt 释放前台锁，再重试第 1 步
  3. 轮询确认，超时返回 False（由上层决定降级或通知）
"""

from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass

import psutil
import pywintypes
import win32api
import win32con
import win32gui
import win32process

from src.utils import sendinput
from src.utils.logger import get_logger

DEFAULT_ACTIVATE_TIMEOUT = 2.0
FOREGROUND_POLL_INTERVAL = 0.05


class WindowNotFoundError(LookupError):
    """找不到匹配的窗口。"""


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process: str


def _process_name(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except (psutil.Error, OSError):
        return ""


def _ancestor_pids(pid: int, *, limit: int = 12) -> list[int]:
    """从 pid 往上走的祖先链（不含自己）。防线：循环与深度都设上限。"""
    chain: list[int] = []
    seen: set[int] = set()
    try:
        current = psutil.Process(pid)
    except (psutil.Error, OSError):
        return chain

    for _ in range(limit):
        try:
            current = current.parent()
        except (psutil.Error, OSError):
            break
        if current is None or current.pid in seen or current.pid <= 4:
            break
        seen.add(current.pid)
        chain.append(current.pid)
    return chain


def find_processes_by_match(match: str) -> list[int]:
    """按"进程名精确匹配 或 命令行包含该片段"找 pid（大小写不敏感）。

    为什么不能只比进程名：npm 装的 CLI 大多以 `node.exe` 运行
    （实测 Codex CLI 与 Gemini CLI 都是），只比进程名会有歧义。
    命令行片段（如 `codex.js`、`@google/gemini-cli`）能精确区分它们。
    """
    wanted = (match or "").lower()
    if not wanted:
        return []
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        name = (proc.info.get("name") or "").lower()
        if name == wanted:
            pids.append(int(proc.info["pid"]))
            continue
        cmdline = " ".join(proc.info.get("cmdline") or []).lower()
        if wanted and wanted in cmdline:
            pids.append(int(proc.info["pid"]))
    return pids


def _enum_top_level_windows() -> list[WindowInfo]:
    """按 Z 序（最前优先）返回可见的顶层窗口。"""
    found: list[WindowInfo] = []
    name_cache: dict[int, str] = {}

    def callback(hwnd: int, _param) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        # 跳过有 owner 的弹出窗口（对话框等），只保留真正的顶层窗口
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER):
            return True
        try:
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        except pywintypes.error:
            return True
        if pid not in name_cache:
            name_cache[pid] = _process_name(pid)
        found.append(WindowInfo(hwnd=hwnd, title=title, pid=pid, process=name_cache[pid]))
        return True

    win32gui.EnumWindows(callback, None)
    return found


class WindowLocator:
    """按进程名 + 标题模式定位窗口，并把它弄到前台。"""

    def __init__(self, logger=None) -> None:
        self._log = logger or get_logger(__name__)

    def find_all(
        self,
        *,
        process: str | None = None,
        title_pattern: str | None = None,
    ) -> list[WindowInfo]:
        matches = []
        for info in _enum_top_level_windows():
            if process and info.process.lower() != process.lower():
                continue
            if title_pattern and not fnmatch.fnmatchcase(
                info.title.lower(), title_pattern.lower()
            ):
                continue
            matches.append(info)
        return matches

    def find(
        self,
        *,
        process: str | None = None,
        title_pattern: str | None = None,
        multi_instance: str = "recent",
    ) -> WindowInfo | None:
        """multi_instance: recent/first 取 Z 序最前的一个；all 返回首个并提示。"""
        matches = self.find_all(process=process, title_pattern=title_pattern)
        if not matches:
            self._log.warning(
                "未找到窗口（process=%r, title_pattern=%r）", process, title_pattern
            )
            return None
        if multi_instance == "all" and len(matches) > 1:
            self._log.info("匹配到 %d 个窗口，M1 阶段先取最前面的一个", len(matches))
        chosen = matches[0]
        self._log.debug(
            "命中窗口 hwnd=%s pid=%s process=%s title=%r",
            chosen.hwnd,
            chosen.pid,
            chosen.process,
            chosen.title,
        )
        return chosen

    def find_host_window(
        self,
        cli_match: str,
        *,
        terminal_process: str | None = None,
    ) -> WindowInfo | None:
        """找到"正在运行某个 CLI 的那个终端窗口"。

        CLI Agent 没有自己的窗口，注入目标是**宿主终端**。与其猜终端标题——
        CLI 会动态改写标题，猜错就等于把失败伪装成成功——不如从进程树反推：
        该 CLI 进程的祖先进程里，谁拥有可见顶层窗口。

        cli_match 可以写进程名（claude.exe）或命令行片段（codex.js），
        因为 npm 装的 CLI 多数以 node.exe 运行，光比进程名有歧义。

        terminal_process 给定时只接受该宿主（例如 WindowsTerminal.exe），
        避免把 IDE 内置终端误判成目标。

        **已知限制**：Windows Terminal 的多个标签页共用同一个窗口，
        所以两个 CLI 跑在同一窗口的不同标签时无法区分——这时需要用户
        先把目标标签切到前台（Ctrl+V 本来就只会进当前聚焦的窗格）。
        """
        windows_by_pid: dict[int, WindowInfo] = {}
        for info in _enum_top_level_windows():
            windows_by_pid.setdefault(info.pid, info)

        for pid in find_processes_by_match(cli_match):
            for ancestor in _ancestor_pids(pid):
                window = windows_by_pid.get(ancestor)
                if window is None:
                    continue
                if terminal_process and window.process.lower() != terminal_process.lower():
                    continue
                self._log.debug(
                    "由 %s(pid=%s) 的祖先进程 pid=%s 定位到终端窗口 hwnd=%s",
                    cli_match,
                    pid,
                    ancestor,
                    window.hwnd,
                )
                return window

        self._log.warning("没有找到正在运行 %s 的终端窗口（该 CLI 是否已启动？）", cli_match)
        return None

    def describe_candidates(self, process: str, *, limit: int = 6) -> str:
        """把某进程当前真实的窗口标题列出来，用于"找不到窗口"时给出可操作线索。"""
        titles = [info.title for info in self.find_all(process=process)]
        if not titles:
            return f"{process} 当前没有任何可见窗口"
        shown = titles[:limit]
        suffix = "" if len(titles) <= limit else f" …（共 {len(titles)} 个）"
        return f"{process} 当前窗口标题：{' | '.join(repr(t) for t in shown)}{suffix}"

    def activate(self, hwnd: int, timeout: float = DEFAULT_ACTIVATE_TIMEOUT) -> bool:
        return self.force_foreground(hwnd, timeout=timeout, logger=self._log)

    @staticmethod
    def force_foreground(hwnd: int, timeout: float = DEFAULT_ACTIVATE_TIMEOUT, logger=None) -> bool:
        log = logger or get_logger(__name__)
        if not win32gui.IsWindow(hwnd):
            raise WindowNotFoundError(f"hwnd={hwnd} 不是有效窗口")

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        if win32gui.GetForegroundWindow() == hwnd:
            return True

        _try_set_foreground(hwnd, log)
        if _wait_foreground(hwnd, timeout * 0.6):
            return True

        log.debug("首次激活未生效，尝试 Alt 释放前台锁后重试")
        try:
            sendinput.send_hotkey(sendinput.VK_MENU)
        except OSError as exc:
            log.warning("发送 Alt 失败：%s", exc)
        _try_set_foreground(hwnd, log)

        if _wait_foreground(hwnd, timeout * 0.4):
            return True

        log.error(
            "激活窗口失败：hwnd=%s 前台仍是 hwnd=%s",
            hwnd,
            win32gui.GetForegroundWindow(),
        )
        return False


def _try_set_foreground(hwnd: int, log) -> None:
    """AttachThreadInput 兜底：先并入目标线程输入队列，再抢前台。"""
    current_tid = win32api.GetCurrentThreadId()
    try:
        target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
    except pywintypes.error as exc:
        log.error("获取窗口线程 ID 失败：%s", exc)
        return

    attached = False
    if target_tid and target_tid != current_tid:
        try:
            attached = bool(win32process.AttachThreadInput(current_tid, target_tid, True))
        except pywintypes.error as exc:
            log.warning("AttachThreadInput 失败：%s", exc)

    try:
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        try:
            win32gui.SetFocus(hwnd)
        except pywintypes.error as exc:
            # 未 attach 时 SetFocus 必然失败，属预期
            log.debug("SetFocus 未生效：%s", exc)
    except pywintypes.error as exc:
        log.warning("SetForegroundWindow 被拒绝：%s", exc)
    finally:
        if attached:
            try:
                win32process.AttachThreadInput(current_tid, target_tid, False)
            except pywintypes.error as exc:  # pragma: no cover
                log.warning("AttachThreadInput 解除失败：%s", exc)


def _wait_foreground(hwnd: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(FOREGROUND_POLL_INTERVAL)
