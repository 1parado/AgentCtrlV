"""看一眼当前有哪些可注入的窗口，以及每个 Agent 能不能定位到。

排查"为什么找不到窗口"的第一步：先看现实，再改配置。

    python scripts/list_windows.py
    python scripts/list_windows.py --process ZCode.exe   # 只看某个进程
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.agents import load_agents  # noqa: E402
from src.core.window import (  # noqa: E402
    WindowLocator,
    _enum_top_level_windows,
    find_processes_by_match,
)
from src.utils.logger import setup_logging  # noqa: E402


def list_windows(process_filter: str | None) -> None:
    windows = _enum_top_level_windows()
    if process_filter:
        wanted = process_filter.lower()
        windows = [w for w in windows if w.process.lower() == wanted]

    print(f"=== 可见顶层窗口（{len(windows)} 个）===")
    if not windows:
        print("  （没有匹配的窗口）")
        return
    for info in sorted(windows, key=lambda w: w.process.lower()):
        print(f"  {info.process:26} pid={info.pid:<7} hwnd={info.hwnd:<10} {info.title[:52]!r}")


def resolve_agents() -> None:
    agents = load_agents()
    locator = WindowLocator()
    print(f"\n=== Agent 定位结果（{len(agents)} 个）===")
    for agent in agents:
        if agent.cli_match:
            pids = find_processes_by_match(agent.cli_match)
            window = locator.find_host_window(agent.cli_match, terminal_process=agent.process)
            kind = "CLI→终端"
        else:
            pids = []
            window = locator.find(process=agent.process, title_pattern=agent.title_pattern)
            kind = "GUI"

        if window is not None:
            status = f"✅ hwnd={window.hwnd} {window.title[:40]!r}"
        elif agent.cli_match and not pids:
            status = f"❌ 没在运行（找不到 {agent.cli_match} 进程）"
        elif agent.cli_match:
            status = f"❌ 找到 {len(pids)} 个进程，但它不在可见终端里"
        else:
            status = f"❌ 没有匹配窗口（进程 {agent.process}，标题 {agent.title_pattern!r}）"

        print(f"  {agent.name:20} {kind:9} {status}")


def main() -> int:
    parser = argparse.ArgumentParser(description="列出可注入窗口与 Agent 定位结果")
    parser.add_argument("--process", default=None, help="只看某个进程名的窗口")
    args = parser.parse_args()

    setup_logging("WARNING")
    list_windows(args.process)
    if not args.process:
        resolve_agents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
