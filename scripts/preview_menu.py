"""把环形菜单渲染成 PNG，用于设计评审（不用真的按热键）。

用**真实**平台渲染而不是 offscreen：offscreen 下 Qt 找不到字体，
文字全都画不出来，预览就失去了意义。

    python scripts/preview_menu.py [--out menu_preview.png] [--select zcode]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 故意不设 QT_QPA_PLATFORM：offscreen 没有字体，渲染出来是空壳
import os  # noqa: E402

os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.core.agents import load_agents  # noqa: E402
from src.ui.radial_menu import AgentItem, RadialMenu  # noqa: E402
from src.utils.logger import setup_logging  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染环形菜单预览图")
    parser.add_argument("--out", default="menu_preview.png")
    parser.add_argument("--select", default=None, help="高亮某个 agent id，模拟已选中")
    parser.add_argument("--check", action="store_true", help="只检查图标是否都加载成功")
    args = parser.parse_args()

    setup_logging("WARNING")
    app = QApplication.instance() or QApplication([])

    agents = load_agents()
    items = [AgentItem(a.id, a.name, a.icon, a.enabled) for a in agents]

    missing = [i.id for i in items if not (i.icon and Path(i.icon).exists())]
    print(f"Agent {len(items)} 个，其中有真实图标的 {len(items) - len(missing)} 个")
    if missing:
        print(f"  退回字母头像：{'、'.join(missing)}")
    if args.check:
        return 0

    menu = RadialMenu(items)
    if args.select:
        menu._selected = {args.select}
    menu.show()
    app.processEvents()
    menu.repaint()

    pixmap = menu.grab()
    out = Path(args.out)
    pixmap.save(str(out), "PNG")
    print(f"已渲染 {pixmap.width()}x{pixmap.height()} -> {out}")

    menu.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
