"""T1.4：剪贴板注入器完整时序测试（全部依赖注入为假件）。"""

from __future__ import annotations

import pytest
from PIL import Image

from src.core.clipboard_snapshot import ClipboardSnapshot, TextEntry
from src.core.payload import Payload
from src.core.permissions import PermissionVerdict
from src.core.window import WindowInfo
from src.injectors.base import InjectionStatus
from src.injectors.clipboard_injector import ClipboardInjector

TARGET = WindowInfo(hwnd=1001, title="记事本", pid=42, process="Notepad.exe")


class FakeClipboard:
    def __init__(self, events, snapshot=None, restore_result=True):
        self.events = events
        self.snapshot = snapshot if snapshot is not None else ClipboardSnapshot(
            entries={1: TextEntry("original")}
        )
        self.restore_result = restore_result
        self.restored = []
        self.written = []

    def capture(self):
        self.events.append("capture")
        return self.snapshot

    def restore(self, snapshot):
        self.events.append("restore")
        self.restored.append(snapshot)
        return self.restore_result

    def write_image(self, image):
        self.events.append("write_image")
        self.written.append(image)

    def write_text(self, text):
        self.events.append("write_text")
        self.written.append(text)


class FakeLocator:
    def __init__(self, events, result=True):
        self.events = events
        self.result = result
        self.activated = []

    def activate(self, hwnd, timeout=2.0):
        self.events.append("activate")
        self.activated.append(hwnd)
        return self.result


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Harness:
    def __init__(
        self, *, activate=True, restore=True, paste_raises=False, snapshot=None, permitted=True
    ):
        self.events = []
        self.clipboard = FakeClipboard(self.events, snapshot, restore_result=restore)
        self.locator = FakeLocator(self.events, result=activate)
        self.clock = FakeClock()
        self.foreground = {"hwnd": TARGET.hwnd}
        self.paste_raises = paste_raises
        self.permitted = permitted

    def paste(self):
        self.events.append("paste")
        if self.paste_raises:
            raise OSError("SendInput failed")

    def permission(self, pid: int) -> PermissionVerdict:
        self.events.append("permission")
        if self.permitted:
            return PermissionVerdict(True, "权限足够", 0x2000, 0x2000)
        return PermissionVerdict(False, "目标窗口以更高权限运行，UIPI 禁止注入", 0x3000, 0x2000)

    def build(self, **kwargs) -> ClipboardInjector:
        kwargs.setdefault("paste_delay_ms", 300)
        kwargs.setdefault("render_delay_ms", 500)
        return ClipboardInjector(
            self.clipboard,
            self.locator,
            paste_fn=self.paste,
            sleep_fn=self.clock.sleep,
            monotonic_fn=self.clock.monotonic,
            foreground_fn=lambda: self.foreground["hwnd"],
            permission_fn=self.permission,
            **kwargs,
        )


def test_success_runs_full_documented_sequence() -> None:
    harness = Harness()
    outcome = harness.build().inject(Payload.from_text("hello"), TARGET)

    assert outcome.status is InjectionStatus.SUCCESS
    assert outcome.ok
    assert harness.events == ["permission", "capture", "write_text", "activate", "paste", "restore"]


def test_permission_denied_fails_fast_without_touching_clipboard() -> None:
    """UIPI 拦截时必须在动剪贴板之前就退出，并给出准确原因。"""
    harness = Harness(permitted=False)
    outcome = harness.build().inject(Payload.from_text("x"), TARGET)

    assert outcome.status is InjectionStatus.FAILED
    assert "UIPI" in outcome.detail
    assert harness.events == ["permission"]  # 没碰剪贴板、没激活、没粘贴


def test_delays_match_configuration() -> None:
    harness = Harness()
    harness.build(paste_delay_ms=300, render_delay_ms=500).inject(Payload.from_text("x"), TARGET)

    assert harness.clock.sleeps[-1] == pytest.approx(0.5)
    assert sum(harness.clock.sleeps) == pytest.approx(0.8)


def test_zero_paste_delay_skips_waiting() -> None:
    harness = Harness()
    harness.build(paste_delay_ms=0, render_delay_ms=0).inject(Payload.from_text("x"), TARGET)

    assert harness.clock.sleeps == []


def test_image_payload_uses_write_image() -> None:
    harness = Harness()
    harness.build().inject(Payload.from_image(Image.new("RGB", (3, 3))), TARGET)

    assert harness.events == [
        "permission",
        "capture",
        "write_image",
        "activate",
        "paste",
        "restore",
    ]


def test_activation_failure_never_pastes_and_still_restores() -> None:
    harness = Harness(activate=False)
    outcome = harness.build().inject(Payload.from_text("x"), TARGET)

    assert outcome.status is InjectionStatus.FAILED
    assert "paste" not in harness.events
    assert harness.events[-1] == "restore"
    assert harness.clipboard.restored


def test_foreground_switch_blocks_paste_and_restores() -> None:
    harness = Harness()
    injector = harness.build()
    harness.foreground["hwnd"] = 4242  # 粘贴前被别的窗口抢走前台

    outcome = injector.inject(Payload.from_text("x"), TARGET)

    assert outcome.status is InjectionStatus.FAILED
    assert "paste" not in harness.events
    assert harness.events[-1] == "restore"


def test_paste_exception_is_reported_and_clipboard_restored() -> None:
    harness = Harness(paste_raises=True)
    outcome = harness.build().inject(Payload.from_text("x"), TARGET)

    assert outcome.status is InjectionStatus.FAILED
    assert "SendInput" in outcome.detail
    assert harness.events[-1] == "restore"


def test_empty_payload_is_rejected_before_touching_clipboard() -> None:
    harness = Harness()
    outcome = harness.build().inject(Payload.from_text("   "), TARGET)

    assert outcome.status is InjectionStatus.FAILED
    assert harness.events == []


def test_restore_can_be_disabled() -> None:
    harness = Harness()
    outcome = harness.build(restore_clipboard=False).inject(Payload.from_text("x"), TARGET)

    assert outcome.status is InjectionStatus.SUCCESS
    assert "capture" not in harness.events
    assert "restore" not in harness.events


def test_restore_failure_is_surfaced_but_injection_still_reported() -> None:
    harness = Harness(restore=False)
    outcome = harness.build().inject(Payload.from_text("x"), TARGET)

    # 注入本身成功，但恢复失败必须留下痕迹（不静默）
    assert outcome.status is InjectionStatus.SUCCESS
    assert harness.clipboard.restored


def test_empty_snapshot_does_not_break_injection() -> None:
    harness = Harness(snapshot=ClipboardSnapshot())
    outcome = harness.build().inject(Payload.from_text("x"), TARGET)

    assert outcome.status is InjectionStatus.SUCCESS
    assert harness.events[-1] == "restore"


def test_auto_enter_is_refused_at_construction() -> None:
    harness = Harness()
    with pytest.raises(ValueError, match="禁止自动回车"):
        harness.build(auto_enter=True)


def test_injector_name_matches_config_schema() -> None:
    assert ClipboardInjector.name == "clipboard"


# ---------- 全函数契约（M3 多目标调度依赖它） ----------


def test_inject_never_raises_when_capture_fails() -> None:
    """剪贴板打不开时也必须返回结果，不能把异常抛给调度器。"""
    harness = Harness()

    def boom():
        raise RuntimeError("剪贴板被别的程序占用")

    harness.clipboard.capture = boom
    outcome = harness.build().inject(Payload.from_text("x"), TARGET)

    assert outcome.status is InjectionStatus.FAILED
    assert "剪贴板被别的程序占用" in outcome.detail


def test_inject_never_raises_on_unexpected_error() -> None:
    harness = Harness()
    injector = harness.build()
    injector._permission_fn = lambda pid: (_ for _ in ()).throw(RuntimeError("意料之外"))

    outcome = injector.inject(Payload.from_text("x"), TARGET)

    assert outcome.status is InjectionStatus.FAILED
    assert "意料之外" in outcome.detail


def test_inject_never_raises_when_restore_explodes() -> None:
    harness = Harness()

    def boom(snapshot):
        raise RuntimeError("恢复时炸了")

    harness.clipboard.restore = boom
    outcome = harness.build().inject(Payload.from_text("x"), TARGET)

    # 注入本身成功；恢复的异常不能把它变成崩溃
    assert outcome.status is InjectionStatus.SUCCESS
