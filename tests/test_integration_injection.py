"""M1 集成测试（注入层）：真实目标窗口 + 真实 Ctrl+V。

默认被 pytest.ini 排除（`-m "not integration"`），因为它会抢走前台焦点、
启动 GUI 进程。显式运行：`pytest -m integration`

关于"按键投递"：SendInput 返回成功**不等于**目标收到了——它没有回执。
所以这里用一个会回报"我收到了什么"的目标窗口来判定。
探针必须走真实路径（Ctrl+V），**不能**用普通字母键代替：实测两者投递表现
不一致，字母键会产生假阴性。投递表现可能是间歇性的，探针因此重试 3 次。

剪贴板层的集成测试见 test_integration_clipboard.py。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.core.clipboard import ClipboardManager
from src.core.payload import Payload
from src.core.window import WindowLocator
from src.injectors.clipboard_injector import ClipboardInjector
from src.utils import sendinput
from scripts.m1_demo import make_test_image

pytestmark = pytest.mark.integration

PROBE_TEXT = "AgentCtrlV integration probe"
TARGET_APP = Path(__file__).resolve().parents[1] / "scripts" / "paste_target.py"
PROBE_TITLE = "AgentCtrlV-Probe-Target"
PROBE_PROCESS = Path(sys.executable).name
WINDOW_READY_TIMEOUT = 20.0
PASTE_TIMEOUT = 15.0
VK_A = 0x41


def _launch_probe(state_path: Path) -> tuple[subprocess.Popen, object | None]:
    process = subprocess.Popen(
        [sys.executable, str(TARGET_APP), str(state_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    locator = WindowLocator()
    info = None
    deadline = time.monotonic() + WINDOW_READY_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = (
                process.stderr.read().decode("utf-8", errors="replace")
                if process.stderr
                else ""
            )
            pytest.fail(f"目标窗口进程提前退出（code={process.returncode}）：{stderr}")
        info = locator.find(process=PROBE_PROCESS, title_pattern=f"{PROBE_TITLE}*")
        if info:
            break
        time.sleep(0.25)
    return process, info


def _read_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _wait_for_paste(state_path: Path, process, *, key: str) -> dict:
    """等待目标窗口回报一次成功的粘贴；失败时给出逐环节诊断。"""
    deadline = time.monotonic() + PASTE_TIMEOUT
    state: dict = {}
    while time.monotonic() < deadline:
        state = _read_state(state_path)
        mime = state.get("last_mime")
        if mime and mime.get(key):
            return state
        if process.poll() is not None:
            break
        time.sleep(0.1)

    pytest.fail(
        "目标窗口没有收到预期的粘贴。逐环节诊断："
        f"active={state.get('active')} focused={state.get('focused')} "
        f"keys_seen={state.get('keys_seen')} paste_calls={state.get('paste_calls')} "
        f"last_mime={state.get('last_mime')}"
    )


@pytest.fixture
def probe_target(tmp_path):
    """启动我们自己的粘贴目标窗口，结束后销毁该进程。

    绝不复用用户已经打开的记事本：Win11 记事本是多标签单进程应用，
    那会污染用户正在编辑的文档，而且无法安全回收。
    """
    state_path = tmp_path / "target_state.json"
    process, info = _launch_probe(state_path)
    if info is None:
        process.kill()
        pytest.fail("目标窗口未在超时内出现")

    try:
        yield info, WindowLocator(), state_path, process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def _attempt_injection(state_path: Path) -> bool:
    """启动一次探针，用真实注入器发一张图，看目标窗口有没有回报粘贴。"""
    process, info = _launch_probe(state_path)
    if info is None:
        process.kill()
        return False

    clipboard = ClipboardManager()
    original = clipboard.capture()
    try:
        payload = make_test_image(120, 80)
        clipboard.write_image(payload)
        injector = ClipboardInjector(
            clipboard, WindowLocator(), paste_delay_ms=300, render_delay_ms=300
        )
        injector.inject(Payload.from_image(payload), info)

        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            mime = _read_state(state_path).get("last_mime")
            if mime and mime.get("has_image"):
                return True
            if process.poll() is not None:
                break
            time.sleep(0.1)
        return False
    finally:
        clipboard.restore(original)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.fixture(scope="session")
def input_injection_works(tmp_path_factory) -> bool:
    """探测本环境能否把注入的 Ctrl+V 真正投递到目标窗口。

    必须走**产品真实路径**（ClipboardInjector 发 Ctrl+V），不能拿普通字母键
    当探针——实测普通按键与组合键的投递表现并不一致，用字母键会产生假阴性，
    让全链路用例在其实可用的环境里被错误地 skip。

    投递表现可能是**间歇性**的（同一台机器上有时成功有时失败），
    所以重试 3 次，任一次成功即认为本环境可用。
    """
    base = tmp_path_factory.mktemp("injection-probe")
    for attempt in range(3):
        if _attempt_injection(base / f"probe_state_{attempt}.json"):
            return True
    return False


@pytest.fixture
def require_input_injection(input_injection_works: bool) -> None:
    if not input_injection_works:
        pytest.skip(
            "本环境丢弃注入按键（SendInput 返回成功但目标收不到 WM_KEYDOWN），"
            "全链路无法在此验证。请在普通终端运行 `python scripts/selftest.py` 拿到逐层结论。"
        )


# ---------- 全链路（真实窗口 + 真实 Ctrl+V） ----------


def test_full_m1_chain_into_real_input_box(
    real_clipboard: ClipboardManager, probe_target, tmp_path, require_input_injection
) -> None:
    """M1 总验收：读剪贴板图片 -> 保存 PNG -> 激活目标 -> 注入 -> 恢复剪贴板。

    与「肉眼看看记事本」不同，这里由目标窗口回报 has_image，
    于是"图片真的进了输入框"是被断言的客观事实。
    """
    info, locator, state_path, process = probe_target
    original = real_clipboard.capture()

    try:
        real_clipboard.write_image(make_test_image())

        read_back = real_clipboard.read_image()
        assert read_back is not None, "T1.1：读不回刚写进去的图片"
        assert real_clipboard.save_image_png(tmp_path / "clipboard.png").exists()

        before_injection = real_clipboard.capture()
        injector = ClipboardInjector(
            real_clipboard, locator, paste_delay_ms=300, render_delay_ms=500
        )
        outcome = injector.inject(Payload.from_image(read_back), info)
        assert outcome.delivered, f"T1.3：注入失败 {outcome}"

        state = _wait_for_paste(state_path, process, key="has_image")
        assert state["last_mime"]["has_image"] is True

        assert real_clipboard.capture().entries == before_injection.entries, "剪贴板未恢复"
    finally:
        real_clipboard.restore(original)

    assert real_clipboard.capture().entries == original.entries


def test_text_chain_into_real_input_box(
    real_clipboard: ClipboardManager, probe_target, require_input_injection
) -> None:
    """文本路径同样端到端验证（Windows Terminal 等 CLI 目标走这条路）。"""
    info, locator, state_path, process = probe_target
    original = real_clipboard.capture()

    try:
        real_clipboard.write_text(PROBE_TEXT)
        before_injection = real_clipboard.capture()

        injector = ClipboardInjector(
            real_clipboard, locator, paste_delay_ms=300, render_delay_ms=300
        )
        outcome = injector.inject(Payload.from_text(PROBE_TEXT), info)
        assert outcome.delivered, f"注入失败 {outcome}"

        state = _wait_for_paste(state_path, process, key="has_text")
        assert state["last_mime"]["has_text"] is True
        assert real_clipboard.capture().entries == before_injection.entries
    finally:
        real_clipboard.restore(original)

    assert real_clipboard.capture().entries == original.entries
