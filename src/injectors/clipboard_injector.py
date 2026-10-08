"""剪贴板注入器（T1.3）——GUI 首选路径。

完整时序（ARCHITECTURE.md「剪贴板注入完整时序」）：
  1 保存原剪贴板 → 2 写 Payload → 3 激活窗口 → 4 聚焦输入框
  → 5 等 paste_delay → 6 SendInput Ctrl+V → 7 等 render_delay → 8 恢复剪贴板

第 8 步放在 finally 里：无论中途哪一步失败，都不能把用户的剪贴板留在
Payload 状态（AGENTS.md「剪贴板操作必须完整保存/恢复原内容」）。

M1 阶段第 4 步是盲粘（激活窗口即认为焦点就位）；UIA 聚焦留给 M2/M3。
"""

from __future__ import annotations

import time
from typing import Callable

import pywintypes
import win32gui

from src.core.clipboard import ClipboardManager
from src.core.payload import Payload
from src.core.permissions import PermissionVerdict, check_can_inject
from src.core.window import WindowInfo, WindowLocator
from src.injectors.base import InjectionOutcome, InjectionStatus, Injector
from src.utils import sendinput
from src.utils.logger import get_logger

DEFAULT_PASTE_DELAY_MS = 300
DEFAULT_RENDER_DELAY_MS = 500
DEFAULT_ACTIVATE_TIMEOUT = 2.0


class ClipboardInjector(Injector):
    name = "clipboard"

    def __init__(
        self,
        clipboard: ClipboardManager,
        locator: WindowLocator,
        *,
        paste_delay_ms: int = DEFAULT_PASTE_DELAY_MS,
        render_delay_ms: int = DEFAULT_RENDER_DELAY_MS,
        activate_timeout: float = DEFAULT_ACTIVATE_TIMEOUT,
        restore_clipboard: bool = True,
        auto_enter: bool = False,
        paste_fn: Callable[[], None] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        foreground_fn: Callable[[], int] | None = None,
        permission_fn: Callable[[int], PermissionVerdict] | None = None,
        logger=None,
    ) -> None:
        if auto_enter:
            # AGENTS.md v0.1 禁止事项第一条：不要自动回车发送。
            # 宁可拒绝启动，也不要"配置里写了就照做"。
            raise ValueError("v0.1 禁止自动回车（auto_enter 必须为 False）")

        self._clipboard = clipboard
        self._locator = locator
        self._paste_delay = max(0, paste_delay_ms) / 1000.0
        self._render_delay = max(0, render_delay_ms) / 1000.0
        self._activate_timeout = activate_timeout
        self._restore = restore_clipboard
        self._paste_fn = paste_fn or sendinput.send_ctrl_v
        self._sleep = sleep_fn or time.sleep
        self._monotonic = monotonic_fn or time.monotonic
        self._foreground_fn = foreground_fn or win32gui.GetForegroundWindow
        self._permission_fn = permission_fn or check_can_inject
        self._log = logger or get_logger(__name__)

    def inject(self, payload: Payload, target: WindowInfo) -> InjectionOutcome:
        """把 payload 送进 target。**对调用方保证是全函数：任何情况下都返回结果，不抛异常。**

        这条保证对 M3 的多目标调度是必需的——一个目标炸了不能带走整批。
        所以最外层兜住所有异常，记日志并转成 FAILED。
        """
        try:
            return self._inject(payload, target)
        except Exception as exc:  # noqa: BLE001 - 全函数契约，见 docstring
            self._log.exception("注入出现未预期异常：%s", exc)
            return InjectionOutcome(InjectionStatus.FAILED, f"{type(exc).__name__}: {exc}")

    def _inject(self, payload: Payload, target: WindowInfo) -> InjectionOutcome:
        if payload.is_empty:
            self._log.error("Payload 为空，拒绝注入")
            return InjectionOutcome(InjectionStatus.FAILED, "Payload 为空")

        # UIPI 检查放在最前面：注定失败就不要先动用户的剪贴板
        verdict = self._permission_fn(target.pid)
        if not verdict.allowed:
            self._log.error("权限不足，拒绝注入：%s", verdict.reason)
            return InjectionOutcome(InjectionStatus.FAILED, verdict.reason)
        self._log.debug("权限检查：%s", verdict.reason)

        snapshot = None
        if self._restore:
            snapshot = self._clipboard.capture()
            if snapshot.is_empty:
                self._log.warning("原剪贴板为空；注入结束后剪贴板会停留在 Payload 内容")

        try:
            return self._run(payload, target)
        except (pywintypes.error, OSError, ValueError) as exc:
            self._log.exception("注入过程异常：%s", exc)
            return InjectionOutcome(InjectionStatus.FAILED, f"{type(exc).__name__}: {exc}")
        finally:
            if snapshot is not None:
                # 恢复剪贴板的问题**只记日志，绝不改写本次注入结论**：
                # 内容可能已经送到了，把 SUCCESS 变成 FAILED 会误导调用方。
                # 真实 restore() 已是全函数，这里是防御性的第二道闸。
                try:
                    if not self._clipboard.restore(snapshot):
                        self._log.error("注入结束但剪贴板未能完整恢复，请检查上文错误")
                except Exception as exc:  # noqa: BLE001 - 见上
                    self._log.exception("恢复剪贴板时出现未预期异常（不影响注入结论）：%s", exc)

    # ---------- 内部 ----------

    def _run(self, payload: Payload, target: WindowInfo) -> InjectionOutcome:
        self._write_payload(payload)
        self._log.debug("已写入 Payload：%s", payload.describe())

        if not self._locator.activate(target.hwnd, timeout=self._activate_timeout):
            # 宁可不粘，也不要把内容粘进一个不知道是什么的窗口
            return InjectionOutcome(
                InjectionStatus.FAILED,
                f"窗口未能激活（hwnd={target.hwnd}），已放弃粘贴以避免粘错目标。"
                "常见原因：目标以管理员权限运行（Windows 会拒绝跨权限激活），"
                "或前台被全屏独占窗口占用。",
            )

        if not self._wait_paste_delay(target.hwnd):
            return InjectionOutcome(
                InjectionStatus.FAILED, "粘贴前复核失败：前台窗口已不是目标窗口"
            )

        self._paste_fn()
        self._log.debug("已发送 Ctrl+V，等待 render_delay=%.0fms", self._render_delay * 1000)
        if self._render_delay > 0:
            self._sleep(self._render_delay)
        return InjectionOutcome(InjectionStatus.SUCCESS)

    def _write_payload(self, payload: Payload) -> None:
        if payload.kind == "image":
            assert payload.image is not None  # Payload.__post_init__ 已保证
            self._clipboard.write_image(payload.image)
        else:
            assert payload.text is not None
            self._clipboard.write_text(payload.text)

    def _wait_paste_delay(self, target_hwnd: int) -> bool:
        """等待目标窗口完成重绘，并在粘贴前复核前台窗口仍是目标。

        这一步是「粘错窗口」这个最危险失败模式的最后一道闸门：
        Ctrl+V 一旦发出去就收不回来，所以发之前必须再确认一次。
        """
        deadline = self._monotonic() + self._paste_delay
        while True:
            if self._foreground_fn() != target_hwnd:
                self._log.error(
                    "粘贴前前台窗口已变为 hwnd=%s（目标 %s），放弃本次注入",
                    self._foreground_fn(),
                    target_hwnd,
                )
                return False
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return True
            self._sleep(min(remaining, 0.05))
