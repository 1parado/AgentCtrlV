"""M1 一键自检：不需要肉眼观察，直接给出「这条链路在你机器上能不能用」。

    python scripts/selftest.py

它按顺序验证五件事，任何一项失败都明确指出断在哪一环：

    1/5 运行环境    Python / Windows 版本、依赖是否齐
    2/5 剪贴板层    全格式快照 -> 覆写 -> 恢复，逐格式比对
    3/5 权限层      目标进程的完整性级别是否允许注入（UIPI 硬边界）
    4/5 窗口层      找到并激活目标窗口
    5/5 按键投递    SendInput 发出的 Ctrl+V 是否**真的**被目标窗口收到

第 5 项是本项目最大的不确定性：SendInput 返回成功**不等于**目标收到了。
它没有回执，所以这里用一个会回报"我收到了什么"的目标窗口来判定
（scripts/paste_target.py）。注意探针必须走真实路径（Ctrl+V），
不能用普通字母键代替——实测两者投递表现不一致，用字母键会得出假阴性。

退出码：0 = 全部通过；1 = 有环节失败。
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.clipboard import ClipboardManager  # noqa: E402
from src.core import permissions  # noqa: E402
from src.core.payload import Payload  # noqa: E402
from src.core.window import WindowLocator  # noqa: E402
from src.injectors.clipboard_injector import ClipboardInjector  # noqa: E402
from src.utils.logger import setup_logging  # noqa: E402
from scripts.m1_demo import make_test_image  # noqa: E402
# 结论层单独成模块（纯逻辑、可单测）。这里再导出一次，
# 于是 scripts.selftest.Check / evaluate_delivery 仍然可用。
from scripts.selftest_report import Check, evaluate_delivery, report  # noqa: E402

PROBE = ROOT / "scripts" / "paste_target.py"
PROBE_TITLE = "AgentCtrlV-Probe-Target"
WINDOW_READY_TIMEOUT = 20.0
PROBE_READY_TIMEOUT = 8.0
DELIVERY_TIMEOUT = 10.0
#: 投递表现可能间歇，跑多次把成功率报出来（见 check_delivery）
DELIVERY_ATTEMPTS = 3


def check_environment() -> Check:
    try:
        import PIL  # noqa: F401
        import psutil  # noqa: F401
        import pywintypes  # noqa: F401
        import win32clipboard  # noqa: F401
    except ImportError as exc:
        return Check("运行环境", False, f"依赖缺失：{exc}（先 pip install -r requirements.txt）")

    return Check(
        "运行环境",
        True,
        f"Python {platform.python_version()} / {platform.system()} {platform.release()}，依赖齐全",
    )


def check_clipboard(manager: ClipboardManager) -> Check:
    """全格式快照 -> 覆写 -> 恢复，逐格式比对。"""
    try:
        original = manager.capture()
        if original.is_empty:
            return Check("剪贴板层", False, "剪贴板为空，无法验证快照往返")

        manager.write_text("AgentCtrlV selftest")
        restored = manager.restore(original)
        after = manager.capture()
    except Exception as exc:  # noqa: BLE001 - 自检要给出结论而不是抛栈
        return Check("剪贴板层", False, f"{type(exc).__name__}: {exc}")

    if not restored or after.entries != original.entries:
        return Check("剪贴板层", False, f"恢复不一致：{original.describe()} -> {after.describe()}")
    return Check("剪贴板层", True, f"{len(original.entries)} 种格式逐字节还原一致")


def check_permissions(pid: int) -> Check:
    """UIPI：本进程能不能向目标进程注入输入。"""
    own = permissions.own_integrity_level()
    verdict = permissions.check_can_inject(pid, own_level=own)
    detail = (
        f"本进程 {permissions.level_name(own)} / 目标 {permissions.level_name(verdict.target_level)}"
    )
    if verdict.allowed:
        return Check("权限层", True, detail)
    return Check("权限层", False, verdict.reason)


def check_window(locator: WindowLocator, hwnd: int) -> Check:
    try:
        activated = locator.activate(hwnd, timeout=3.0)
    except Exception as exc:  # noqa: BLE001
        return Check("窗口层", False, f"{type(exc).__name__}: {exc}")
    if not activated:
        return Check("窗口层", False, f"hwnd={hwnd} 抢不到前台（可能被全屏独占窗口挡住）")
    return Check("窗口层", True, f"hwnd={hwnd} 已激活到前台")


def _launch_probe(state_path: Path) -> tuple[subprocess.Popen, object | None]:
    process = subprocess.Popen(
        [sys.executable, str(PROBE), str(state_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    locator = WindowLocator()
    info = None
    deadline = time.monotonic() + WINDOW_READY_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return process, None
        info = locator.find(process=Path(sys.executable).name, title_pattern=f"{PROBE_TITLE}*")
        if info:
            break
        time.sleep(0.25)
    if info is None:
        return process, None

    # 找到窗口 != 应用就绪。Qt 还会继续初始化控件与焦点，
    # 这期间注入就会"看起来像注入失败"。实测：不等就绪时成功率稳定只有 2/3，
    # 等的就是这一步——它是**探针的就绪竞态**，不是投递本身不稳定。
    ready_deadline = time.monotonic() + PROBE_READY_TIMEOUT
    while time.monotonic() < ready_deadline:
        if _read_state(state_path).get("focused"):
            return process, info
        if process.poll() is not None:
            break
        time.sleep(0.05)
    return process, info


def _read_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _delivery_attempt(manager: ClipboardManager, attempt: int) -> tuple[bool, dict]:
    """跑一次"注入图片并等目标回报"，返回 (是否成功, 目标状态)。"""
    state_path = ROOT / f"_selftest_state_{attempt}.json"
    state_path.unlink(missing_ok=True)
    process, info = _launch_probe(state_path)
    if info is None:
        if process.poll() is None:
            process.kill()
        return False, {}

    original = manager.capture()
    try:
        payload = make_test_image(200, 140)
        manager.write_image(payload)
        ClipboardInjector(
            manager, WindowLocator(), paste_delay_ms=300, render_delay_ms=500
        ).inject(Payload.from_image(payload), info)

        deadline = time.monotonic() + DELIVERY_TIMEOUT
        state: dict = {}
        while time.monotonic() < deadline:
            state = _read_state(state_path)
            mime = state.get("last_mime")
            if mime and mime.get("has_image"):
                return True, state
            if process.poll() is not None:
                break
            time.sleep(0.1)
        return False, state
    finally:
        manager.restore(original)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        state_path.unlink(missing_ok=True)


def check_delivery(manager: ClipboardManager, attempts: int = DELIVERY_ATTEMPTS) -> Check:
    """多次尝试并**报告成功率**，而不是给一个会被间歇性抖动的布尔值。

    同一台机器上投递表现会时而成功时而失败。只跑一次的话自检会随机报红，
    既吓人又不准确。跑 N 次把"不稳定"这个事实本身报出来，比一个薛定谔的
    ✅/❌ 有用得多。
    """
    successes = 0
    last_state: dict = {}
    for attempt in range(1, attempts + 1):
        ok, state = _delivery_attempt(manager, attempt)
        if ok:
            successes += 1
        else:
            last_state = state

    if successes == attempts:
        return Check("按键投递", True, f"{successes}/{attempts} 次成功，目标窗口都确认收到了 Ctrl+V")

    if successes:
        return Check(
            "按键投递",
            True,
            f"只有 {successes}/{attempts} 次成功——注入**不稳定**，偶发失败。"
            "请排查常驻的热键 / 输入法 / 自动化类软件，或给目标窗口留出更多 paste_delay",
        )

    return evaluate_delivery(last_state, "has_image")


def _check_probe_basics() -> tuple[Check, Check]:
    """权限层与窗口层需要一个**活着的**探针窗口，所以单独跑一轮。"""
    state_path = ROOT / "_selftest_probe.json"
    state_path.unlink(missing_ok=True)
    process, info = _launch_probe(state_path)
    if info is None:
        if process.poll() is None:
            process.kill()
        return (
            Check("权限层", False, "粘贴目标探针没起来，无法判断"),
            Check("窗口层", False, "粘贴目标探针没能在超时内出现"),
        )
    try:
        return check_permissions(info.pid), check_window(WindowLocator(), info.hwnd)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        state_path.unlink(missing_ok=True)


def run_selftest() -> int:
    checks: list[Check] = [check_environment()]

    manager = ClipboardManager()
    checks.append(check_clipboard(manager))

    permission_check, window_check = _check_probe_basics()
    checks.append(permission_check)
    checks.append(window_check)

    checks.append(check_delivery(manager))

    report(checks)
    return 0 if all(check.ok for check in checks) else 1


def main() -> int:
    setup_logging("WARNING")
    return run_selftest()


if __name__ == "__main__":
    sys.exit(main())
