"""M1 集成测试（剪贴板层）：真实 Windows 剪贴板。

默认被 pytest.ini 排除（`-m "not integration"`），因为它会改写用户剪贴板。
显式运行：`pytest -m integration`

所有用例都在 finally 里恢复原剪贴板（MOCK.md 注意事项）。
注入链路的集成测试见 test_integration_injection.py。
"""

from __future__ import annotations

import ctypes

import pytest
import win32clipboard
import win32con
from PIL import Image, ImageChops

from src.core import dib as dib_module
from src.core.clipboard import ClipboardManager
from src.core.clipboard_snapshot import CF_DIB, CF_HDROP, FileDropEntry, pack_dropfiles
from scripts.m1_demo import make_test_image

pytestmark = pytest.mark.integration

PROBE_TEXT = "AgentCtrlV integration probe"
CBM_INIT = 4


def _create_hbitmap(image: Image.Image) -> int:
    """把 PIL 图片变成 GDI 位图句柄，用于构造"只有 CF_BITMAP"的剪贴板。

    只给测试用，所以留在测试文件里，不进产品代码。
    成功后句柄所有权交给剪贴板，不能再 DeleteObject。
    """
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32.CreateDIBitmap.restype = ctypes.c_void_p

    raw = dib_module.image_to_dib(image)
    buffer = ctypes.create_string_buffer(raw, len(raw))
    info_ptr = ctypes.cast(buffer, ctypes.c_void_p)
    bits_ptr = ctypes.c_void_p(ctypes.addressof(buffer) + dib_module.pixel_data_offset(raw))

    hdc = user32.GetDC(None)
    try:
        handle = gdi32.CreateDIBitmap(hdc, info_ptr, CBM_INIT, bits_ptr, info_ptr, 0)
    finally:
        user32.ReleaseDC(None, hdc)
    assert handle, "CreateDIBitmap 失败"
    return int(handle)


def _query_hdrop_files() -> tuple[str, ...]:
    """用系统的 DragQueryFileW 枚举剪贴板 HDROP——独立于 pywin32 的权威裁判。

    pywin32 对 CF_HDROP 的 GetClipboardData 返回类型不稳定（tuple 或 str），
    不能用来做断言；DragQueryFileW 才是 Explorer 实际使用的那条路径。
    """
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetClipboardData.restype = ctypes.c_void_p
    shell32.DragQueryFileW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_wchar_p,
        ctypes.c_uint,
    ]
    shell32.DragQueryFileW.restype = ctypes.c_uint

    with ClipboardManager().open():
        handle = user32.GetClipboardData(win32con.CF_HDROP)
        if not handle:
            return ()
        count = shell32.DragQueryFileW(ctypes.c_void_p(handle), 0xFFFFFFFF, None, 0)
        paths = []
        for index in range(count):
            length = shell32.DragQueryFileW(ctypes.c_void_p(handle), index, None, 0)
            buffer = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(ctypes.c_void_p(handle), index, buffer, length + 1)
            paths.append(buffer.value)
        return tuple(paths)


# ---------- 快照 / 恢复 ----------


def test_snapshot_restore_round_trip(real_clipboard: ClipboardManager) -> None:
    original = real_clipboard.capture()
    assert not original.is_empty, "测试前置条件：剪贴板不应为空"

    try:
        real_clipboard.write_text(PROBE_TEXT)
        assert real_clipboard.read_text() == PROBE_TEXT
    finally:
        assert real_clipboard.restore(original) is True

    after = real_clipboard.capture()

    # 保证范围是"捕获到的格式逐字节还原"，**不包括 skipped**：
    # 读不出来的私有格式（实测 Chromium 的 49154/49156）本来就没被捕获，
    # 而且同一个格式本次读失败、下次可能读成功，拿它做断言会随机变红。
    assert set(after.entries) == set(original.entries), (
        f"恢复后格式集合不同：{original.describe()} -> {after.describe()}"
    )
    assert after.entries == original.entries


def test_image_write_read_is_pixel_identical(real_clipboard: ClipboardManager) -> None:
    original = real_clipboard.capture()
    try:
        payload = make_test_image(320, 200)
        real_clipboard.write_image(payload)
        restored = real_clipboard.read_image()
        assert restored is not None
        assert restored.size == payload.size
        assert ImageChops.difference(restored.convert("RGB"), payload).getbbox() is None
    finally:
        real_clipboard.restore(original)


def test_save_image_png_writes_decodable_file(
    real_clipboard: ClipboardManager, tmp_path
) -> None:
    original = real_clipboard.capture()
    try:
        real_clipboard.write_image(Image.new("RGB", (40, 30), (12, 34, 56)))
        target = real_clipboard.save_image_png(tmp_path / "clipboard.png")
        with Image.open(target) as saved:
            assert saved.size == (40, 30)
    finally:
        real_clipboard.restore(original)


def test_file_drop_round_trip(real_clipboard: ClipboardManager, tmp_path) -> None:
    """CF_HDROP（拖放文件）走真实剪贴板往返。

    这是快照里唯一需要**结构化编解码**而非原样搬字节的格式，也是最容易
    在恢复时悄悄丢数据的一个——回归它曾经导致"快照整体为空"的 bug。
    """
    original = real_clipboard.capture()

    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    files = (str(first), str(second))

    try:
        with real_clipboard.open():
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, pack_dropfiles(files))
        assert _query_hdrop_files() == files, "前置条件：系统应能枚举出这两个文件"

        snapshot = real_clipboard.capture()
        assert snapshot.entries.get(CF_HDROP) == FileDropEntry(files), snapshot.describe()
        assert CF_HDROP not in snapshot.skipped

        real_clipboard.write_text("clobbered")
        assert real_clipboard.restore(snapshot) is True

        assert _query_hdrop_files() == files, "恢复后的 HDROP 系统解析不出来"
    finally:
        real_clipboard.restore(original)


def test_bitmap_only_clipboard_is_decodable(real_clipboard: ClipboardManager) -> None:
    """只提供 CF_BITMAP（老程序）时也必须能读出图片。

    实测结论：Windows 会**自动合成** CF_DIB / CF_DIBV5，
    所以即使剪贴板里只有 GDI 句柄，read_image() 也能走 DIB 路径解出来。
    这里同时覆盖了 DIBV5 的 32bpp BI_BITFIELDS 布局（曾经解码错位）。
    """
    original = real_clipboard.capture()
    source = make_test_image(120, 90)

    try:
        with real_clipboard.open():
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_BITMAP, _create_hbitmap(source))

        with real_clipboard.open():
            available = set()
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if fmt == 0:
                    break
                available.add(fmt)

        assert win32con.CF_BITMAP in available, "前置条件：剪贴板里应有 CF_BITMAP"
        assert CF_DIB in available, f"系统未合成 CF_DIB，需补 GetDIBits 兜底：{available}"

        restored = real_clipboard.read_image()
        assert restored is not None, "CF_BITMAP 剪贴板读不出图片"
        assert restored.size == source.size
        assert ImageChops.difference(restored.convert("RGB"), source).getbbox() is None
    finally:
        real_clipboard.restore(original)
