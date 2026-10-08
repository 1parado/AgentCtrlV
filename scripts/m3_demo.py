"""M3 验收：冷启动（T3.2）+ 新会话策略（T3.3）+ 多选依次注入（T3.1）。

目标窗口全部用**自有探针**（scripts/paste_target.py），不碰用户正在用的应用。
探针会回报"我到底收到了什么"，所以判定不靠肉眼，也不依赖本机装没装某个 AI 应用。

    python scripts/m3_demo.py             # 冷启动 1 轮 + 多选 1 轮
    python scripts/m3_demo.py --runs 5    # 多选跑 5 轮并给出成功率

为什么多选要能跑多轮：`SendInput` 没有回执，投递本身存在偶发丢键
（见 docs/ARCHITECTURE.md）。与其给一个薛定谔的 ✅/❌，不如把**成功率**
报出来——这也是 TASKS.md M5「成功率 >95%」唯一能被度量的方式。

退出码：0 = 冷启动通过且多选每轮都送达；1 = 有环节没达标。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtWidgets import QApplication  # noqa: E402

from scripts.m3_probe import (  # noqa: E402
    PROBE,
    StubMenu,
    TimingInjector,
    click_center,
    click_item,
    find_window,
    kill_by_title,
    probe_agent,
    read_state,
    wait_for_text,
    wait_for_window,
)
from src.core.clipboard import ClipboardManager  # noqa: E402
from src.core.cold_start import ColdStarter  # noqa: E402
from src.core.controller import MULTI_TARGET_INTERVAL_S, Controller  # noqa: E402
from src.core.window import WindowLocator  # noqa: E402
from src.ui.radial_menu import AgentItem, RadialMenu  # noqa: E402
from src.utils.logger import setup_logging  # noqa: E402

PAYLOAD = "AgentCtrlV M3 验收载荷"
COLD_TITLE = "AgentCtrlV-M3-Cold"
MULTI_TITLES = ("AgentCtrlV-M3-A", "AgentCtrlV-M3-B", "AgentCtrlV-M3-C")


def part_cold_start(app, manager: ClipboardManager, locator: WindowLocator, notices: list) -> bool:
    print("\n=== A. 冷启动：探针没在跑 -> 按 launch 启动 -> 等窗口 -> 注入 ===")
    state_path = ROOT / "_m3_cold.json"
    state_path.unlink(missing_ok=True)
    agent = probe_agent("cold", state_path, COLD_TITLE)

    if find_window(locator, COLD_TITLE) is not None:
        print("  ⚠️ 前置条件不成立：该探针窗口已存在，测的不是冷启动")
        return False
    print("  前置检查：该窗口现在不存在 ✓")

    controller = Controller(
        clipboard=manager,
        locator=locator,
        menu=StubMenu(),
        notifier=lambda title, message, **kw: notices.append(message),
        agents=(agent,),
        cold_starter=ColdStarter(locator),
    )

    manager.write_text(PAYLOAD)
    started = time.monotonic()
    controller.dispatch()
    controller.on_confirmed([agent.id])
    elapsed = time.monotonic() - started

    window = find_window(locator, COLD_TITLE)
    received = wait_for_text(state_path)
    print(f"  启动+等待+注入总耗时：{elapsed:.2f}s")
    print(f"  窗口是否出现：{'是' if window else '否'}  hwnd={getattr(window, 'hwnd', None)}")
    print(f"  探针是否确认收到文本：{'是' if received else '否'}")
    if notices:
        print(f"  通知：{notices}")
    ok = window is not None and received and not notices
    print(f"  {'✅ 冷启动链路通过' if ok else '❌ 冷启动未通过'}")
    return ok


def _spawn_probes() -> list[tuple]:
    handles = []
    for index, title in enumerate(MULTI_TITLES, start=1):
        state_path = ROOT / f"_m3_multi_{index}.json"
        state_path.unlink(missing_ok=True)
        process = subprocess.Popen(
            [sys.executable, str(PROBE), str(state_path), title],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        handles.append((process, state_path, title))
    return handles


def run_multi_once(
    app, manager: ClipboardManager, locator: WindowLocator, quiet: bool
) -> tuple[int, list[float], list[str]]:
    """跑一轮多选，返回 (送达个数, 相邻注入间隔, 通知)。"""
    handles = _spawn_probes()
    try:
        for _process, _state, title in handles:
            if not wait_for_window(locator, title):
                return 0, [], [f"探针窗口 {title} 没出现"]

        agents = tuple(
            probe_agent(f"p{i}", state, title)
            for i, (_p, state, title) in enumerate(handles, start=1)
        )
        menu = RadialMenu([AgentItem(a.id, a.name) for a in agents])
        stamps: list[float] = []
        outcomes: list[tuple[str, str]] = []
        notices: list[str] = []
        controller = Controller(
            clipboard=manager,
            locator=locator,
            menu=menu,
            notifier=lambda title, message, **kw: notices.append(message),
            agents=agents,
            injector_factory=lambda agent: TimingInjector(
                agent, manager, locator, stamps, outcomes
            ),
        )
        menu.confirmed.connect(controller.on_confirmed)
        menu.cancelled.connect(controller.on_cancelled)

        manager.write_text(PAYLOAD)
        controller.dispatch()
        app.processEvents()

        for index in range(len(handles)):
            click_item(menu, index, ctrl=True)
        if len(menu.selected_ids) != len(handles):
            return 0, [], [f"多选只选中 {len(menu.selected_ids)} 个"]
        click_center(menu)
        app.processEvents()

        delivered = sum(1 for _p, state, _t in handles if wait_for_text(state))
        gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]

        if not quiet:
            print(f"  选中：{menu.selected_ids}")
            print(f"  送达：{delivered}/{len(handles)}   注入器自报：{[s for _a, s in outcomes]}")
            for _process, state_path, title in handles:
                state = read_state(state_path)
                mime = state.get("last_mime") or {}
                print(
                    f"    {title}: 收到的按键={state.get('keys_seen')} has_text={mime.get('has_text')}"
                )
            print(f"  相邻注入间隔：{[f'{g:.3f}s' for g in gaps]}")
        return delivered, gaps, notices
    finally:
        for process, _state, _title in handles:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        for _process, state_path, _title in handles:
            state_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="M3 验收：冷启动 + 多选")
    parser.add_argument("--runs", type=int, default=1, help="多选跑几轮（用于统计成功率）")
    args = parser.parse_args()

    setup_logging("WARNING")
    app = QApplication.instance() or QApplication([])
    manager = ClipboardManager()
    locator = WindowLocator()
    notices: list[str] = []

    original = manager.capture()
    print("=" * 68)
    print("AgentCtrlV M3 验收（冷启动 + 多选）")
    print("=" * 68)
    print(f"原剪贴板：{original.describe()}")

    cold_ok = False
    try:
        cold_ok = part_cold_start(app, manager, locator, notices)
    finally:
        kill_by_title(locator, COLD_TITLE)
        (ROOT / "_m3_cold.json").unlink(missing_ok=True)

    print(f"\n=== B. 多选：Ctrl 点 {len(MULTI_TITLES)} 个 -> 中心确认 -> 依次注入（{args.runs} 轮）===")
    full_runs = 0
    all_gaps: list[float] = []
    try:
        for index in range(1, args.runs + 1):
            delivered, gaps, run_notices = run_multi_once(
                app, manager, locator, quiet=args.runs > 1
            )
            all_gaps.extend(gaps)
            if delivered == len(MULTI_TITLES) and not run_notices:
                full_runs += 1
            flag = "✅" if delivered == len(MULTI_TITLES) and not run_notices else "❌"
            print(f"  第 {index} 轮：送达 {delivered}/{len(MULTI_TITLES)} {flag} {run_notices or ''}")
    finally:
        for title in MULTI_TITLES:
            kill_by_title(locator, title)
        for index in range(1, len(MULTI_TITLES) + 1):
            (ROOT / f"_m3_multi_{index}.json").unlink(missing_ok=True)
        print(f"\n原剪贴板已恢复：{manager.restore(original)}")

    rate = full_runs / args.runs if args.runs else 0.0
    if all_gaps:
        worst = min(all_gaps)
        interval_ok = worst >= MULTI_TARGET_INTERVAL_S * 0.9
        print(f"  注入间隔：最小 {worst:.3f}s（要求 ≥{MULTI_TARGET_INTERVAL_S * 0.9:.2f}s）"
              f" -> {'符合' if interval_ok else '不符合'}")
    else:
        interval_ok = False

    multi_ok = full_runs == args.runs and interval_ok
    print("\n" + "=" * 68)
    print(f"  冷启动          {'✅ 通过' if cold_ok else '❌ 失败'}")
    print(f"  多选 {full_runs}/{args.runs} 轮全部送达（成功率 {rate:.0%}）{'✅' if multi_ok else '❌'}")
    passed = cold_ok and multi_ok
    print(f"结论：{'M3 验收通过' if passed else 'M3 验收未通过'}")
    print("=" * 68)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
