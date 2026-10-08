"""T1.4：ClipboardManager 的打开重试与全格式快照/恢复测试。

全部跑在 FakeClipboard 上，不接触真实剪贴板。
图片/文本读写路径见 test_clipboard_io.py。
"""

from __future__ import annotations

import pytest

from src.core.clipboard import ClipboardError
from src.core.clipboard_snapshot import (
    CF_BITMAP,
    CF_DIB,
    CF_HDROP,
    CF_UNICODETEXT,
    BytesEntry,
    ClipboardSnapshot,
    FileDropEntry,
    TextEntry,
    pack_dropfiles,
    unpack_dropfiles,
)
from tests.conftest import FakeClipboard, make_manager, patch_clipboard

SECRET = "SUPER-SECRET-CLIPBOARD-TEXT"


# ---------- 打开重试 ----------


def test_open_retries_until_success(monkeypatch) -> None:
    fake = FakeClipboard(open_failures=2)
    patch_clipboard(monkeypatch, fake)
    manager = make_manager(fake, retries=3)

    with manager.open():
        assert fake.is_open

    assert fake.open_calls == 3
    assert fake.close_calls == 1


def test_open_raises_after_all_retries(monkeypatch) -> None:
    fake = FakeClipboard(open_failures=99)
    patch_clipboard(monkeypatch, fake)

    with pytest.raises(ClipboardError, match="重试 3 次后仍失败"):
        with make_manager(fake, retries=3).open():
            pass

    assert fake.open_calls == 3
    assert fake.close_calls == 0


def test_clipboard_closed_even_when_body_raises(monkeypatch) -> None:
    fake = FakeClipboard()
    patch_clipboard(monkeypatch, fake)

    with pytest.raises(RuntimeError):
        with make_manager(fake).open():
            raise RuntimeError("boom")

    assert fake.close_calls == 1
    assert not fake.is_open


# ---------- 快照 ----------


def test_capture_collects_all_copyable_formats(monkeypatch) -> None:
    fake = FakeClipboard(
        data={
            CF_UNICODETEXT: SECRET,
            CF_DIB: b"\x00" * 64,
            CF_HDROP: pack_dropfiles(("C:\\a.png",)),
            0xC001: b"private",
        }
    )
    patch_clipboard(monkeypatch, fake)

    snapshot = make_manager(fake).capture()

    assert snapshot.entries[CF_UNICODETEXT] == TextEntry(SECRET)
    assert snapshot.entries[CF_DIB] == BytesEntry(b"\x00" * 64)
    assert snapshot.entries[CF_HDROP] == FileDropEntry(("C:\\a.png",))
    assert snapshot.entries[0xC001] == BytesEntry(b"private")
    assert snapshot.skipped == {}


def test_capture_skips_gdi_handles_with_reason(monkeypatch) -> None:
    fake = FakeClipboard(data={CF_BITMAP: 987654, CF_UNICODETEXT: "x"})
    patch_clipboard(monkeypatch, fake)

    snapshot = make_manager(fake).capture()

    assert CF_BITMAP not in snapshot.entries
    assert "GDI" in snapshot.skipped[CF_BITMAP]
    assert snapshot.entries[CF_UNICODETEXT] == TextEntry("x")


def test_capture_records_read_errors_instead_of_silence(monkeypatch) -> None:
    fake = FakeClipboard(data={CF_UNICODETEXT: "x", 0xC0FF: b"y"})
    patch_clipboard(monkeypatch, fake)

    def boom(fmt):
        if fmt == 0xC0FF:
            raise OSError("locked")
        return fake.data[fmt]

    monkeypatch.setattr("win32clipboard.GetClipboardData", boom)
    snapshot = make_manager(fake).capture()

    assert 0xC0FF not in snapshot.entries
    assert "读取异常" in snapshot.skipped[0xC0FF]


def test_capture_reads_hdrop_tuple_from_pywin32(monkeypatch) -> None:
    """回归：pywin32 对 CF_HDROP 返回的是文件名**元组**，不是 bytes。

    漏掉这一支会让"剪贴板里只有文件"时快照整体为空，
    进而 restore() 拒绝恢复、用户的文件剪贴板被 Payload 永久覆盖。
    """
    paths = ("C:\\temp\\a.png", "C:\\temp\\b.txt")
    fake = FakeClipboard(data={CF_HDROP: paths})
    patch_clipboard(monkeypatch, fake)

    snapshot = make_manager(fake).capture()

    assert snapshot.entries[CF_HDROP] == FileDropEntry(paths)
    assert snapshot.skipped == {}
    assert not snapshot.is_empty


def test_capture_rejects_empty_hdrop_tuple(monkeypatch) -> None:
    fake = FakeClipboard(data={CF_HDROP: ()})
    patch_clipboard(monkeypatch, fake)

    snapshot = make_manager(fake).capture()

    assert snapshot.is_empty
    assert "内容为空" in snapshot.skipped[CF_HDROP]


# ---------- 恢复 ----------


def test_restore_round_trips_every_format(monkeypatch) -> None:
    original = {
        CF_UNICODETEXT: SECRET,
        CF_DIB: b"\x01" * 128,
        CF_HDROP: pack_dropfiles(("C:\\a.png", "C:\\b.txt")),
        0xC001: b"private-bytes",
    }
    fake = FakeClipboard(data=dict(original))
    patch_clipboard(monkeypatch, fake)
    manager = make_manager(fake)

    snapshot = manager.capture()
    fake.data = {CF_UNICODETEXT: "clobbered"}  # 模拟被 Payload 覆写
    assert manager.restore(snapshot)

    assert fake.data[CF_UNICODETEXT] == SECRET
    assert fake.data[CF_DIB] == b"\x01" * 128
    assert fake.data[0xC001] == b"private-bytes"
    assert unpack_dropfiles(fake.data[CF_HDROP]) == ("C:\\a.png", "C:\\b.txt")
    assert fake.empty_calls == 1


def test_restore_empty_snapshot_does_not_wipe_clipboard(monkeypatch) -> None:
    fake = FakeClipboard(data={CF_UNICODETEXT: "keep me"})
    patch_clipboard(monkeypatch, fake)

    assert make_manager(fake).restore(ClipboardSnapshot()) is False
    assert fake.data[CF_UNICODETEXT] == "keep me"
    assert fake.empty_calls == 0


def test_restore_reports_partial_failure(monkeypatch) -> None:
    fake = FakeClipboard(data={CF_UNICODETEXT: "a", 0xC001: b"b"})
    patch_clipboard(monkeypatch, fake)
    manager = make_manager(fake)
    snapshot = manager.capture()

    def flaky(fmt, value):
        if fmt == 0xC001:
            raise OSError("rejected")
        fake.data[fmt] = value

    monkeypatch.setattr("win32clipboard.SetClipboardData", flaky)
    assert manager.restore(snapshot) is False


def test_restore_never_raises_when_clipboard_unavailable(monkeypatch) -> None:
    """restore() 常在 finally 里被调用；抛异常会掩盖真正的失败原因。"""
    fake = FakeClipboard(data={CF_UNICODETEXT: "x"}, open_failures=99)
    patch_clipboard(monkeypatch, fake)
    manager = make_manager(fake, retries=2)

    snapshot = ClipboardSnapshot(entries={CF_UNICODETEXT: TextEntry("y")})

    assert manager.restore(snapshot) is False  # 返回 False 而不是抛 ClipboardError



