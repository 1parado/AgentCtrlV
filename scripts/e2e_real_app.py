"""对**真实应用**做端到端验证：热键 → 环形菜单 → 选中目标 → 内容进入输入框。

走的是产品真实路径（真实 RadialMenu + 真实 ClipboardInjector + 真实激活与 Ctrl+V），
不是直接调注入器绕过菜单。不靠肉眼判断，用两个客观信号：

1. 粘贴前后抓目标窗口截图做像素差
2. 尽量用 UI Automation 把输入框内容读回来做文本比对

会临时改写剪贴板、抢前台焦点，**结束后无条件恢复原剪贴板**。

    python scripts/e2e_real_app.py zcode
    python scripts/e2e_real_app.py workbuddy

注意：目标若以管理员权限运行，UIPI 会阻止注入（这是 Windows 的设计，不是 bug）。
本脚本会明确报出这种情况，而不是给出误导性的"窗口未能激活"。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.pop("QT_QPA_PLATFORM", None)

import win32gui  # noqa: E402
from PIL import ImageChops, ImageGrab  # noqa: E402
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.core.agents import find_agent, load_agents  # noqa: E402
from src.core.clipboard import ClipboardManager  # noqa: E402
from src.core.controller import Controller  # noqa: E402
from src.core.window import WindowLocator  # noqa: E402
from src.ui.radial_menu import AgentItem, RadialMenu  # noqa: E402
from src.utils.logger import setup_logging  # noqa: E402

PAYLOAD_TEXT = "AgentCtrlV E2E 验证：这一行由工具自动粘贴（不会回车）"


def grab_window(hwnd: int):
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)


def click_item(menu: RadialMenu, index: int) -> None:
    """在菜单上真正点一下那个条目。"""
    center = menu._item_center(index)
    menu.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            center,
            menu.mapToGlobal(center),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def read_input_via_uia(hwnd: int) -> str | None:
    """尽力用 UIA 把输入框的值读回来；读不到返回 None。"""
    try:
        import uiautomation as auto
    except ImportError:
        return None
    try:
        window = auto.ControlFromHandle(hwnd)
        if window is None:
            return None
        found: list[str] = []
        for control, _depth in auto.WalkControl(window, maxDepth=12):
            if control.ControlTypeName not in ("EditControl", "DocumentControl"):
                continue
            try:
                value = control.GetValuePattern().Value
            except Exception:  # noqa: BLE001
                value = getattr(control, "Name", None)
            if value:
                found.append(str(value))
        return " | ".join(found) if found else None
    except Exception as exc:  # noqa: BLE001 - 读不到不影响结论
        print(f"  UIA 读取失败：{type(exc).__name__}: {exc}")
        return None


def main() -> int:
    target_id = sys.argv[1] if len(sys.argv) > 1 else "zcode"
    setup_logging("INFO")
    app = QApplication.instance() or QApplication([])

    agents = load_agents()
    agent = find_agent(target_id, agents)
    if agent is None:
        print(f"配置里没有 Agent: {target_id}")
        return 2

    clipboard = ClipboardManager()
    locator = WindowLocator()
    window = locator.find(process=agent.process, title_pattern=agent.title_pattern)
    print(f"目标窗口：{window}")
    if window is None:
        print(f"{agent.name} 没有可见窗口，请先启动它")
        return 1

    menu = RadialMenu([AgentItem(a.id, a.name, a.icon, a.enabled) for a in agents])
    notices: list[str] = []
    controller = Controller(
        clipboard=clipboard,
        locator=locator,
        menu=menu,
        notifier=lambda title, message, **kw: notices.append(f"{title}: {message}"),
        agents=agents,
    )
    menu.confirmed.connect(controller.on_confirmed)
    menu.cancelled.connect(controller.on_cancelled)

    original = clipboard.capture()
    print(f"原剪贴板：{original.describe()}")

    try:
        print(f"激活窗口：{locator.activate(window.hwnd, timeout=3.0)}")
        time.sleep(0.6)
        before = grab_window(window.hwnd)

        clipboard.write_text(PAYLOAD_TEXT)
        print(f"已把 {len(PAYLOAD_TEXT)} 字符写入剪贴板")

        print("触发 dispatch（等同按下热键）…")
        controller.dispatch()
        app.processEvents()
        print(f"  菜单可见={menu.isVisible()}")

        index = [a.id for a in agents].index(target_id)
        print(f"点击第 {index} 个条目（{agent.name}）…")
        click_item(menu, index)
        app.processEvents()
        print(f"  菜单可见={menu.isVisible()}")

        time.sleep(2.0)
        after = grab_window(window.hwnd)

        print("\n=== 验证 ===")
        diff = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
        bbox = diff.getbbox()
        changed = sum(1 for value in diff.convert("L").getdata() if value > 24)
        print(f"窗口像素变化区域：{bbox}")
        print(f"明显变化的像素数：{changed}")
        print(f"失败通知：{notices or '（无）'}")

        text = read_input_via_uia(window.hwnd)
        print(f"UIA 读回输入框：{text!r}")

        if text and PAYLOAD_TEXT[:12] in text:
            print("✅ UIA 确认输入框里出现了我们的文本")
        elif notices:
            print("❌ 注入报告了失败，详见上面的通知")
        elif changed > 200:
            print("✅ 窗口内容确实变了（截图差）；UIA 读不到内容不影响结论")
        else:
            print("❌ 没有观察到任何变化")

        before.save("e2e_before.png")
        after.save("e2e_after.png")
        print("已保存 e2e_before.png / e2e_after.png 供目视核对")
    finally:
        print(f"\n原剪贴板已恢复：{clipboard.restore(original)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
