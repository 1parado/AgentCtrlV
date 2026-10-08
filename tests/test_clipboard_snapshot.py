"""T1.4：剪贴板快照数据模型与纯函数测试。"""

from __future__ import annotations

import pytest

from src.core.clipboard_snapshot import (
    CF_BITMAP,
    CF_DIB,
    CF_GDIOBJFIRST,
    CF_HDROP,
    CF_PRIVATEFIRST,
    CF_UNICODETEXT,
    BytesEntry,
    ClipboardSnapshot,
    FileDropEntry,
    TextEntry,
    entry_size,
    is_gdi_handle_format,
    is_private_format,
    pack_dropfiles,
    unpack_dropfiles,
)

SECRET = "SUPER-SECRET-CLIPBOARD-TEXT"


@pytest.mark.parametrize(
    "fmt",
    [CF_BITMAP, 3, 9, 14, 0x0080, 0x0082, 0x0083, 0x008E, CF_GDIOBJFIRST, CF_GDIOBJFIRST + 5],
)
def test_gdi_handle_formats_detected(fmt: int) -> None:
    assert is_gdi_handle_format(fmt)


@pytest.mark.parametrize("fmt", [CF_DIB, CF_UNICODETEXT, CF_HDROP, 0xC001, CF_PRIVATEFIRST])
def test_non_gdi_formats_are_copyable(fmt: int) -> None:
    assert not is_gdi_handle_format(fmt)


def test_private_format_range() -> None:
    assert is_private_format(CF_PRIVATEFIRST)
    assert not is_private_format(CF_PRIVATEFIRST - 1)


@pytest.mark.parametrize("wide", [True, False])
def test_dropfiles_round_trip(wide: bool) -> None:
    paths = ("C:\\temp\\a.png", "C:\\temp\\b.txt")
    assert unpack_dropfiles(pack_dropfiles(paths, wide=wide)) == paths


def test_dropfiles_layout() -> None:
    packed = pack_dropfiles(("C:\\x.png",))
    assert len(packed) >= 20
    # pFiles 指向紧随 DROPFILES 之后的路径列表
    assert int.from_bytes(packed[0:4], "little") == 20
    # fWide = 1
    assert int.from_bytes(packed[16:20], "little") == 1
    assert packed[20:].decode("utf-16-le").startswith("C:\\x.png")


def test_dropfiles_single_path_has_double_nul_terminator() -> None:
    packed = pack_dropfiles(("C:\\x.png",))
    assert packed.endswith(b"\x00\x00\x00\x00")


def test_unpack_rejects_short_and_bad_offset() -> None:
    with pytest.raises(ValueError, match="太短"):
        unpack_dropfiles(b"\x00" * 4)
    bad = bytearray(pack_dropfiles(("C:\\x.png",)))
    bad[0:4] = (9999).to_bytes(4, "little")
    with pytest.raises(ValueError, match="偏移非法"):
        unpack_dropfiles(bytes(bad))


def test_snapshot_empty_flag() -> None:
    assert ClipboardSnapshot().is_empty
    assert not ClipboardSnapshot(entries={1: TextEntry("x")}).is_empty


def test_snapshot_describe_hides_content() -> None:
    snapshot = ClipboardSnapshot(
        entries={CF_UNICODETEXT: TextEntry(SECRET), CF_DIB: BytesEntry(b"\x00" * 32)},
        skipped={CF_BITMAP: "GDI 句柄"},
    )
    described = snapshot.describe()
    assert SECRET not in described
    assert "formats=2" in described
    assert "skipped=" in described
    assert "GDI 句柄" in described


def test_empty_snapshot_describe() -> None:
    assert ClipboardSnapshot().describe() == "snapshot(empty)"


def test_entry_size_by_kind() -> None:
    assert entry_size(TextEntry("abc")) == 3
    assert entry_size(BytesEntry(b"abcde")) == 5
    assert entry_size(FileDropEntry(("a", "b"))) == 2
