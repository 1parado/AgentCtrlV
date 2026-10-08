"""M1 验收脚本 —— 把 T1.1 / T1.2 / T1.3 拆成可单独运行的子命令。

    python scripts/m1_demo.py read        # T1.1 读剪贴板图片 -> clipboard.png
    python scripts/m1_demo.py activate    # T1.2 找到并激活记事本
    python scripts/m1_demo.py inject      # T1.3 完整注入时序
    python scripts/m1_demo.py all         # M1 总验收：一条命令跑通全链路

依赖：pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from src.core.clipboard import ClipboardError, ClipboardManager  # noqa: E402
from src.core.payload import Payload  # noqa: E402
from src.core.window import WindowInfo, WindowLocator  # noqa: E402
from src.injectors.clipboard_injector import ClipboardInjector  # noqa: E402
from src.utils.logger import setup_logging  # noqa: E402

DEFAULT_OUTPUT = "clipboard.png"
DEFAULT_PROCESS = "notepad.exe"
WINDOW_READY_TIMEOUT = 8.0


def make_test_image(width: int = 640, height: int = 400) -> Image.Image:
    """生成一张图案明显的测试图，便于肉眼确认"确实粘过去了"。"""
    image = Image.new("RGB", (width, height), (18, 24, 38))
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, width - 20, height - 20], outline=(80, 170, 255), width=4)
    for i in range(0, width, 40):
        draw.line([(i, 0), (i, height)], fill=(40, 70, 120), width=1)
    for i in range(0, height, 40):
        draw.line([(0, i), (width, i)], fill=(40, 70, 120), width=1)
    draw.ellipse([width // 2 - 70, height // 2 - 70, width // 2 + 70, height // 2 + 70],
                 fill=(255, 140, 0))
    draw.text((24, height - 40), "AgentCtrlV M1 test payload", fill=(230, 240, 255))
    return image


def ensure_target(locator: WindowLocator, process: str, title: str, launch: bool) -> WindowInfo | None:
    """找到目标窗口；找不到就启动它并轮询等待（MOCK.md 用记事本作 Mock）。"""
    info = locator.find(process=process, title_pattern=title)
    if info or not launch:
        return info

    print(f"[m1] 未找到 {process}，启动中…")
    subprocess.Popen([process])
    deadline = time.monotonic() + WINDOW_READY_TIMEOUT
    while time.monotonic() < deadline:
        info = locator.find(process=process, title_pattern=title)
        if info:
            print(f"[m1] 窗口已就绪：hwnd={info.hwnd}")
            return info
        time.sleep(0.25)
    print(f"[m1] 等待 {WINDOW_READY_TIMEOUT:.0f}s 仍未出现窗口", file=sys.stderr)
    return None


def cmd_read(clipboard: ClipboardManager, args: argparse.Namespace) -> int:
    """T1.1：读剪贴板图片存成 PNG，并证明原剪贴板没被动过。"""
    if args.make_test_image and not clipboard.has_image():
        clipboard.write_image(make_test_image())
        print("[T1.1] 剪贴板无图片，已写入生成的测试图")

    before = clipboard.capture()
    print(f"[T1.1] 读前剪贴板：{before.describe()}")

    try:
        saved = clipboard.save_image_png(args.output)
    except ClipboardError as exc:
        print(f"[T1.1] 失败：{exc}", file=sys.stderr)
        return 1

    after = clipboard.capture()
    unchanged = before.entries == after.entries
    print(f"[T1.1] 已输出 {saved}（{saved.stat().st_size} 字节）")
    print(f"[T1.1] 原剪贴板未改变：{unchanged}")
    return 0 if unchanged else 1


def cmd_activate(locator: WindowLocator, args: argparse.Namespace) -> int:
    """T1.2：找到记事本并弄到前台。"""
    info = ensure_target(locator, args.process, args.title, args.launch)
    if not info:
        print(f"[T1.2] 失败：找不到 {args.process} 窗口", file=sys.stderr)
        return 1
    ok = locator.activate(info.hwnd, timeout=args.activate_timeout)
    print(f"[T1.2] 激活 {info.title!r} -> {ok}")
    return 0 if ok else 1


def cmd_inject(
    clipboard: ClipboardManager,
    locator: WindowLocator,
    injector: ClipboardInjector,
    args: argparse.Namespace,
) -> int:
    """T1.3：完整注入时序。"""
    info = ensure_target(locator, args.process, args.title, args.launch)
    if not info:
        print(f"[T1.3] 失败：找不到 {args.process} 窗口", file=sys.stderr)
        return 1

    image = clipboard.read_image()
    if image is None:
        if not args.make_test_image:
            print("[T1.3] 失败：剪贴板里没有图片（可加 --make-test-image 生成测试图）", file=sys.stderr)
            return 1
        image = make_test_image()
        clipboard.write_image(image)
        print("[T1.3] 剪贴板无图片，已写入生成的测试图")

    before = clipboard.capture()
    outcome = injector.inject(Payload.from_image(image), info)
    print(f"[T1.3] 注入结果：{outcome}")
    print("[T1.3] 注意：success 只代表「已派发且无异常」，本工具拿不到目标窗口的回执")

    after = clipboard.capture()
    if not args.keep_clipboard:
        print(f"[T1.3] 剪贴板已恢复：{after.entries == before.entries}")
    print("[T1.3] 请目视确认图片是否真的出现在记事本输入框里")
    return 0 if outcome.delivered else 1


def cmd_all(
    clipboard: ClipboardManager,
    locator: WindowLocator,
    injector: ClipboardInjector,
    args: argparse.Namespace,
) -> int:
    """M1 总验收：读剪贴板图片 -> 保存 -> 激活记事本 -> 注入 -> 恢复剪贴板。"""
    original = clipboard.capture()
    print(f"[M1] 原剪贴板：{original.describe()}")

    image = clipboard.read_image()
    if image is None:
        if not args.make_test_image:
            print("[M1] 失败：剪贴板里没有图片（可加 --make-test-image）", file=sys.stderr)
            return 1
        image = make_test_image()
        clipboard.write_image(image)
        print("[M1] 剪贴板无图片，已写入生成的测试图")

    saved = clipboard.save_image_png(args.output)
    print(f"[M1] 已保存 {saved}")

    info = ensure_target(locator, args.process, args.title, args.launch)
    if not info:
        print(f"[M1] 失败：找不到 {args.process} 窗口", file=sys.stderr)
        clipboard.restore(original)
        return 1

    outcome = injector.inject(Payload.from_image(image), info)
    print(f"[M1] 注入结果：{outcome}")
    print("[M1] 注意：success 只代表「已派发且无异常」，本工具拿不到目标窗口的回执")

    restored = clipboard.restore(original)
    print(f"[M1] 剪贴板已恢复：{restored}")

    if args.close_target:
        subprocess.run(["taskkill", "/PID", str(info.pid), "/F"], capture_output=True, check=False)
        print(f"[M1] 已关闭目标进程 pid={info.pid}")

    print("[M1] 请目视确认图片已粘贴到记事本输入框（这是 M1 的最终验收依据）")
    return 0 if outcome.delivered and restored else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AgentCtrlV M1 验收脚本")
    parser.add_argument("step", choices=["read", "activate", "inject", "all"])
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"PNG 输出路径（默认 {DEFAULT_OUTPUT}）")
    parser.add_argument("--process", default=DEFAULT_PROCESS, help="目标进程名")
    parser.add_argument("--title", default="*", help="目标窗口标题模式（支持 *）")
    parser.add_argument("--no-launch", dest="launch", action="store_false", help="目标未运行时不要自动启动")
    parser.add_argument("--make-test-image", action="store_true", help="剪贴板无图片时生成测试图")
    parser.add_argument("--keep-clipboard", action="store_true", help="注入后不恢复剪贴板（仅调试用）")
    parser.add_argument("--close-target", action="store_true", help="结束后关闭目标进程")
    parser.add_argument("--activate-timeout", type=float, default=2.0)
    parser.add_argument("--paste-delay", type=int, default=300)
    parser.add_argument("--render-delay", type=int, default=500)
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)

    clipboard = ClipboardManager()
    locator = WindowLocator()
    injector = ClipboardInjector(
        clipboard,
        locator,
        paste_delay_ms=args.paste_delay,
        render_delay_ms=args.render_delay,
        restore_clipboard=not args.keep_clipboard,
    )

    if args.step == "read":
        return cmd_read(clipboard, args)
    if args.step == "activate":
        return cmd_activate(locator, args)
    if args.step == "inject":
        return cmd_inject(clipboard, locator, injector, args)
    return cmd_all(clipboard, locator, injector, args)


if __name__ == "__main__":
    sys.exit(main())
