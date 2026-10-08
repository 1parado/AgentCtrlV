"""AgentCtrlV 入口（M4：热键 + 环形菜单 + 配置化）。

装配顺序有意如此：
1. QApplication —— 菜单与托盘都依赖它，必须先起
2. 隐藏的 hotkey sink 窗口 —— RegisterHotKey 需要一个属于本线程的 HWND
3. 配置与业务对象（剪贴板 / 窗口定位 / 菜单 / 托盘 / 控制器）
4. 最后注册热键并安装原生事件过滤

配置来源：`config/config.yaml`（app 段 + 可选的 agents 列表）与
`config/agents/*.yaml`（每个 Agent 一个文件）。

`--check` 用来做无界面自检：装配、加载配置、注册热键、报告问题后退出，
便于在 CI 或远程终端里验证接线，而不用真的弹菜单。
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
from src.core.config import Config, load_config  # noqa: E402
from src.core.controller import Controller  # noqa: E402
from src.core.hotkey import HotkeyConflictError, HotkeyError, HotkeyManager  # noqa: E402
from src.core.window import WindowLocator  # noqa: E402
from src.ui.qt_hotkey import HotkeyEventFilter  # noqa: E402
from src.ui.radial_menu import AgentItem, RadialMenu  # noqa: E402
from src.ui.tray import Tray  # noqa: E402
from src.utils.logger import get_logger, setup_logging  # noqa: E402


class Application:
    """把各部件接起来的壳，方便自检与测试复用。"""

    def __init__(self, qt_app: QApplication, config: Config | None = None, logger=None) -> None:
        self._log = logger or get_logger(__name__)
        self.qt_app = qt_app
        self.config = config or load_config()
        behavior = self.config.app.behavior

        # RegisterHotKey 需要一个本线程的 HWND；这个窗口永不显示，
        # 只为让 WM_HOTKEY 成为一条普通窗口消息，稳定经过原生事件过滤。
        self._hotkey_sink = QWidget()
        self._hotkey_sink.hide()

        self.agents = load_agents(extra=self.config.inline_agents)
        self.clipboard = ClipboardManager()
        self.locator = WindowLocator()
        self.menu = RadialMenu(
            [AgentItem(a.id, a.name, a.icon, a.enabled) for a in self.agents],
            max_selection=behavior.multi_target_max,
        )
        self.tray = Tray()
        self.controller = Controller(
            clipboard=self.clipboard,
            locator=self.locator,
            menu=self.menu,
            notifier=self.tray.notify,
            agents=self.agents,
            interval_s=behavior.multi_target_delay / 1000.0,
            restore_clipboard=behavior.restore_clipboard,
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
        hotkeys = self.config.app.hotkeys
        bound: dict[str, str] = {}

        try:
            self._dispatch_id = self.hotkeys.register(hotkeys.dispatch_clipboard)
            bound["dispatch"] = hotkeys.dispatch_clipboard
        except (HotkeyConflictError, HotkeyError) as exc:
            self._log.error("主热键注册失败：%s", exc)
            self.tray.notify(
                "热键冲突",
                f"{hotkeys.dispatch_clipboard} 注册失败：{exc}\n仍可从托盘菜单触发分发。",
                warning=True,
            )

        try:
            self._resend_id = self.hotkeys.register(hotkeys.resend_last)
            bound["resend"] = hotkeys.resend_last
        except (HotkeyConflictError, HotkeyError) as exc:
            self._log.error("重发热键注册失败：%s", exc)
            self.tray.notify(
                "热键冲突", f"{hotkeys.resend_last} 注册失败：{exc}", warning=True
            )

        # 截图分发还没实现。**不注册**它：注册了却什么都不做，
        # 等于把用户的 Alt+S 全局吞掉，比不注册糟糕得多。
        self._log.warning(
            "hotkeys.dispatch_screenshot=%s 已配置但截图分发尚未实现，未注册该热键",
            hotkeys.dispatch_screenshot,
        )
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


def _print_check(application: "Application") -> None:
    config = application.config
    behavior = config.app.behavior
    hotkeys = config.app.hotkeys

    print("AgentCtrlV 装配自检（M4 配置化）")
    print(f"  配置文件        : {config.source or '未找到，全部使用默认值'}")

    if config.problems:
        print(f"  ⚠️ 配置问题 {len(config.problems)} 条：")
        for problem in config.problems:
            print(f"      - {problem}")

    print(f"  热键            : {hotkeys.dispatch_clipboard} 分发 / "
          f"{hotkeys.resend_last} 重发 / {hotkeys.dispatch_screenshot} 截图（未实现，未注册）")
    print(f"  行为            : 多目标间隔 {behavior.multi_target_delay}ms、"
          f"上限 {behavior.multi_target_max}、"
          f"恢复剪贴板 {'是' if behavior.restore_clipboard else '否'}")
    print(f"  Agent 数量      : {len(application.agents)}"
          f"（内联 {len(config.inline_agents)} + config/agents/）")
    for agent in application.agents:
        kind = "CLI → 终端" if agent.cli_match else "GUI"
        target = agent.cli_match or agent.process
        cold = "可冷启动" if agent.can_launch else "不可冷启动"
        print(f"    - {agent.name:20} {kind:10} {cold:10} {target}")

    bound = application.register_hotkeys()
    print(f"  成功注册的热键  : {bound or '无'}")
    for key in ("dispatch", "resend"):
        if key not in bound:
            print(f"    ⚠️ {key} 热键未注册（被占用或不合法）")
    application.hotkeys.close()
    print("  提示            : 改 YAML 即可增删 Agent 或调整热键，无需改代码")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)

    qt_app = QApplication(sys.argv[:1])
    qt_app.setQuitOnLastWindowClosed(False)  # 菜单关闭不能让托盘程序退出

    application = Application(qt_app)

    if args.check:
        _print_check(application)
        return 0

    application.start()
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
