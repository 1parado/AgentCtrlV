"""M5 性能测量：菜单弹出延迟、热启动延迟、成功率。

分成两档，因为它们的"可测条件"完全不同：

- **默认（无干扰档）**：只测不需要抢前台的部分——剪贴板读取/解码、菜单几何与绘制。
  可以随时跑，不会打扰你正在用的窗口。
- **--live**：测完整链路（热键触发 → 菜单可见、确认 → 目标收到）。
  它必须让目标窗口拿到前台，**会在你干活时抢焦点**，所以只在桌面空闲时跑。

判定基线（TASKS.md M5）：菜单弹出 <150ms、热启动 <800ms、成功率 >95%。

    python scripts/bench.py
    python scripts/bench.py --live
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 默认走 offscreen：测的是我们自己的计算与绘制开销，不去动用户的窗口
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.core.agents import load_agents  # noqa: E402
from src.core.clipboard import ClipboardManager  # noqa: E402
from src.core.payload import Payload  # noqa: E402
from src.ui.radial_menu import AgentItem, RadialMenu  # noqa: E402
from src.utils.logger import setup_logging  # noqa: E402

MENU_BUDGET_MS = 150.0
WARMUP = 3
ROUNDS = 20


def make_image(width: int, height: int) -> Image.Image:
    """造一张有细节的图（纯色图会因压缩率失真，测不出真实解码成本）。"""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(0, height, 4):
        for x in range(0, width, 4):
            color = ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256)
            for dy in range(4):
                for dx in range(4):
                    if x + dx < width and y + dy < height:
                        pixels[x + dx, y + dy] = color
    return image


def timeit(func, rounds: int = ROUNDS, warmup: int = WARMUP) -> tuple[float, float]:
    """返回 (中位数 ms, 最大 ms)。中位数比平均值更能反映"平时有多快"。"""
    for _ in range(warmup):
        func()
    samples: list[float] = []
    for _ in range(rounds):
        start = time.perf_counter()
        func()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), max(samples)


def bench_payload() -> list[tuple[str, float, float]]:
    """剪贴板读取 + 解码 + 组 Payload——它发生在菜单显示**之前**，直接占用预算。"""
    manager = ClipboardManager()
    original = manager.capture()
    rows: list[tuple[str, float, float]] = []
    try:
        for label, image in (
            ("文本 200 字", None),
            ("图片 800x600", make_image(800, 600)),
            ("图片 1920x1080", make_image(1920, 1080)),
            ("图片 3840x2160", make_image(3840, 2160)),
        ):
            if image is None:
                manager.write_text("剪贴板文本" * 25)
                median, worst = timeit(lambda: Payload.from_text("剪贴板文本" * 25))
                rows.append((label, median, worst))
                continue

            manager.write_image(image)
            median, worst = timeit(lambda: Payload.from_image(manager.read_image()))
            rows.append((label, median, worst))
    finally:
        manager.restore(original)
    return rows


def bench_write() -> list[tuple[str, float, float]]:
    """热启动路径上的写入成本：确认之后要把载荷重新编码回剪贴板。

    这段发生在"用户已经点完"之后，占的是热启动 <800ms 的预算，
    和菜单那条预算不是同一笔账。
    """
    manager = ClipboardManager()
    original = manager.capture()
    rows: list[tuple[str, float, float]] = []
    try:
        text = "热启动载荷" * 40
        rows.append(("文本 200 字", *timeit(lambda: manager.write_text(text))))
        for label, size in (("图片 1920x1080", (1920, 1080)), ("图片 3840x2160", (3840, 2160))):
            image = make_image(*size)
            rows.append((label, *timeit(lambda img=image: manager.write_image(img))))
    finally:
        manager.restore(original)
    return rows


def bench_menu(agents) -> list[tuple[str, float, float]]:
    app = QApplication.instance() or QApplication([])
    items = [AgentItem(a.id, a.name, a.icon, a.enabled) for a in agents]

    construct = timeit(lambda: RadialMenu(items), rounds=5, warmup=1)

    # 常驻实例：真实运行时菜单在启动时就建好，热键触发只做 reposition + show
    menu = RadialMenu(items)
    pos = QPoint(600, 400)
    show = timeit(lambda: (menu.show_at(pos), app.processEvents()))
    paint = timeit(lambda: menu.grab())
    return [
        ("菜单构造（仅启动时一次）", *construct),
        ("show_at + 事件处理（热键路径）", *show),
        ("整帧绘制 grab（上界代理）", *paint),
    ]


def report(rows: list[tuple[str, float, float]], budget_ms: float | None, title: str) -> None:
    print(f"\n=== {title} ===")
    print(f"  {'项目':<34}{'中位数':>10}{'最大':>10}   判定")
    for label, median, worst in rows:
        verdict = ""
        if budget_ms is not None:
            verdict = "✅ 达标" if median < budget_ms else f"❌ 超预算({budget_ms:.0f}ms)"
        print(f"  {label:<34}{median:>8.1f}ms{worst:>8.1f}ms   {verdict}")


def bench_live() -> int:
    """完整链路：热键 → 菜单可见，确认 → 目标真的收到。

    需要目标窗口拿到前台，所以只在桌面空闲时跑。用自有探针做目标：
    探针会回报"我到底收到了什么"，所以"送达"是被确认的，不是猜的。
    """
    import subprocess

    from scripts.m3_probe import PROBE, find_window, kill_by_title, read_state
    from src.core.clipboard import ClipboardManager as RealClipboard
    from src.core.controller import Controller
    from src.core.window import WindowLocator

    title = "AgentCtrlV-Bench"
    state_path = ROOT / "_bench_state.json"
    state_path.unlink(missing_ok=True)
    process = subprocess.Popen(
        [sys.executable, str(PROBE), str(state_path), title],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    locator = WindowLocator()
    manager = RealClipboard()
    original = manager.capture()
    app = QApplication.instance() or QApplication([])
    print("\n=== 完整链路（--live）===")
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and find_window(locator, title) is None:
            time.sleep(0.2)
        window = find_window(locator, title)
        if window is None:
            print("  ❌ 探针窗口没起来")
            return 1

        from src.core.agents import AgentSpec, InjectSpec, WindowSpec

        agent = AgentSpec(
            id="bench",
            name="Bench",
            process=Path(sys.executable).name,
            window=WindowSpec(title_pattern=f"{title}*"),
            inject=InjectSpec(paste_delay=0, render_delay=0),
        )

        samples_dispatch: list[float] = []
        samples_deliver: list[float] = []
        for _ in range(5):
            state_path.unlink(missing_ok=True)
            manager.write_text("bench 载荷")
            menu = RadialMenu([AgentItem(agent.id, agent.name)])
            controller = Controller(
                clipboard=manager,
                locator=locator,
                menu=menu,
                notifier=lambda *a, **k: None,
                agents=(agent,),
                interval_s=0.0,
                menu_settle_s=0.0,
            )
            menu.confirmed.connect(controller.on_confirmed)

            start = time.perf_counter()
            controller.dispatch()
            app.processEvents()
            samples_dispatch.append((time.perf_counter() - start) * 1000)

            start = time.perf_counter()
            controller.on_confirmed([agent.id])
            app.processEvents()
            arrived = False
            while time.perf_counter() - start < 5:
                mime = read_state(state_path).get("last_mime") or {}
                if mime.get("has_text"):
                    arrived = True
                    break
                time.sleep(0.02)
            if arrived:
                samples_deliver.append((time.perf_counter() - start) * 1000)

        rows = [("dispatch → 菜单可见（含剪贴板读取）", statistics.median(samples_dispatch), max(samples_dispatch))]
        if samples_deliver:
            rows.append(("确认 → 目标收到（热启动）", statistics.median(samples_deliver), max(samples_deliver)))
        report(rows, budget_ms=None, title="完整链路")
        print(f"  送达 {len(samples_deliver)}/5 次")
        return 0 if len(samples_deliver) == 5 else 1
    finally:
        manager.restore(original)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        kill_by_title(locator, title)
        state_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="M5 性能测量")
    parser.add_argument("--live", action="store_true", help="测完整链路（会抢前台）")
    args = parser.parse_args()

    setup_logging("ERROR")
    agents = load_agents()

    print("=" * 68)
    print("AgentCtrlV M5 性能测量")
    print("=" * 68)
    print(f"  Agent {len(agents)} 个，预算：菜单 <{MENU_BUDGET_MS:.0f}ms / 热启动 <800ms / 成功率 >95%")
    print(f"  平台 {os.environ.get('QT_QPA_PLATFORM')}"
          f"（offscreen 下测不到真实窗口激活开销，那部分属于 --live）")

    report(bench_payload(), budget_ms=None, title="剪贴板读取 + 解码（菜单前，占用同一预算）")
    report(bench_write(), budget_ms=None, title="写入剪贴板（确认后，占用热启动预算）")
    report(bench_menu(agents), budget_ms=MENU_BUDGET_MS, title="菜单路径")

    if args.live:
        return bench_live()
    print("\n提示：加 --live 可测完整链路（需要桌面空闲）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
