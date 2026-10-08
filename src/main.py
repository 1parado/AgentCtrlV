"""AgentCtrlV 入口（M2：热键 + 环形菜单）。

装配顺序有意如此：
1. QApplication —— 菜单与托盘都依赖它，必须先起
2. 隐藏的 hotkey sink 窗口 —— RegisterHotKey 需要一个属于本线程的 HWND
3. 业务对象（剪贴板 / 窗口定位 / 菜单 / 托盘 / 控制器）
4. 最后注册热键并安装原生事件过滤

`--check` 用来做无界面自检：装配、注册热键、报告状态后退出，
便于在 CI 或远程终端里验证接线，而不用真的弹菜单。

M4 起配置改由 YAML 提供；M2 用 src/core/agents.py 里的内置 3 个 Agent。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from src.core.agents import load_agents  # noqa: E402
from src.core.clipboard import ClipboardManager  # noqa: E402
from src.core.controller import Controller  # noqa: E402
from src.core.hotkey import HotkeyConflictError, HotkeyError, HotkeyManager  # noqa: E402
from src.core.window import WindowLocator  # noqa: E402
from src.ui.qt_hotkey import HotkeyEventFilter  # noqa: E402
from src.ui.radial_menu import AgentItem, RadialMenu  # noqa: E402
from src.ui.tray import Tray  # noqa: E402
from src.utils.logger import get_logger, setup_logging  # noqa: E402

DISPATCH_HOTKEY = "Alt+V"
RESEND_HOTKEY = "Alt+Shift+V"


class Application:
    """把各部件接起来的壳，方便自检与测试复用。"""

    def __init__(self, qt_app: QApplication, logger=None) -> None:
        self._log = logger or get_logger(__name__)
        self.qt_app = qt_app

        # RegisterHotKey 需要一个本线程的 HWND；这个窗口永不显示，
        # 只为让 WM_HOTKEY 成为一条普通窗口消息，稳定经过原生事件过滤。
        self._hotkey_sink = QWidget()
        self._hotkey_sink.hide()

        self.agents = load_agents()
        self.clipboard = ClipboardManager()
        self.locator = WindowLocator()
        self.menu = RadialMenu(
            [AgentItem(a.id, a.name, a.icon, a.enabled) for a in self.agents]
        )
        self.tray = Tray()
        self.controller = Controller(
            clipboard=self.clipboard,
            locator=self.locator,
            menu=self.menu,
            notifier=self.tray.notify,
            agents=self.agents,
        )
        self.hotkeys = HotkeyManager(hwnd=int(self._hotkey_sink.winId()))
        self._filter = HotkeyEventFilter(self.hotkeys, self._on_hotkey)

        self.menu.confirmed.connect(self.controller.on_confirmed)
        self.menu.cancelled.connect(self.controller.on_cancelled)
        self.tray.dispatch_requested.connect(self.controller.dispatch)
        self.tray.resend_requested.connect(self.controller.resend)
        self.tray.quit_requested.connect(self.shutdown)

        self._dispatch_id: int | None = None
        self._resend_id: int | None = None

    # ---------- 热键 ----------

    def register_hotkeys(self) -> dict[str, str]:
        """注册热键。冲突时降级：通知用户 + 保留托盘菜单这条备用路径。"""
        bound: dict[str, str] = {}
        try:
            self._dispatch_id = self.hotkeys.register(DISPATCH_HOTKEY)
            bound["dispatch"] = DISPATCH_HOTKEY
        except (HotkeyConflictError, HotkeyError) as exc:
            self._log.error("主热键注册失败：%s", exc)
            self.tray.notify(
                "热键冲突",
                f"{DISPATCH_HOTKEY} 注册失败：{exc}\n仍可从托盘菜单触发分发。",
                warning=True,
            )

        try:
            self._resend_id = self.hotkeys.register(RESEND_HOTKEY)
            bound["resend"] = RESEND_HOTKEY
        except (HotkeyConflictError, HotkeyError) as exc:
            self._log.error("重发热键注册失败：%s", exc)
            self.tray.notify("热键冲突", f"{RESEND_HOTKEY} 注册失败：{exc}", warning=True)

        return bound

    def _on_hotkey(self, event) -> None:
        if event.hotkey_id == self._dispatch_id:
            self.controller.dispatch()
        elif event.hotkey_id == self._resend_id:
            self.controller.resend()
        else:
            self._log.debug("收到未知热键 id=%s", event.hotkey_id)

    # ---------- 生命周期 ----------

    def start(self) -> None:
        self.qt_app.installNativeEventFilter(self._filter)
        self.tray.show()
        self.register_hotkeys()

    def shutdown(self) -> None:
        self.hotkeys.close()
        self.tray.hide()
        self.qt_app.quit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AgentCtrlV")
    parser.add_argument("--check", action="store_true", help="装配自检后退出，不进入事件循环")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)

    qt_app = QApplication(sys.argv[:1])
    qt_app.setQuitOnLastWindowClosed(False)  # 菜单关闭不能让托盘程序退出

    application = Application(qt_app)

    if args.check:
        bound = application.register_hotkeys()
        print("AgentCtrlV M2 装配自检")
        print(f"  Agent 数量      : {len(application.agents)}")
        for agent in application.agents:
            kind = "CLI → 终端" if agent.type == "cli" else "GUI"
            target = agent.cli_match or agent.process
            print(f"    - {agent.name:20} {kind:10} {target}")
        print(f"  成功注册的热键  : {bound or '无'}")
        for key in ("dispatch", "resend"):
            if key not in bound:
                print(f"  ⚠️ {key} 热键未注册（被占用或不合法）")
        application.hotkeys.close()
        return 0

    application.start()
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
