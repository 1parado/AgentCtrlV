"""剪贴板快照的数据模型与纯函数辅助。

AGENTS.md 要求「剪贴板操作必须完整保存/恢复原内容（所有格式）」。
这里只放**不依赖 Win32 调用**的部分：数据模型、DROPFILES 编解码、
格式分类、日志描述。真正的 EnumClipboardFormats 循环在 clipboard.py。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Union

# --- 剪贴板格式常量（避免依赖 win32con 才能做纯测试） ---
CF_TEXT = 1
CF_BITMAP = 2
CF_METAFILEPICT = 3
CF_OEMTEXT = 7
CF_DIB = 8
CF_PALETTE = 9
CF_UNICODETEXT = 13
CF_ENHMETAFILE = 14
CF_HDROP = 15
CF_LOCALE = 16
CF_DIBV5 = 17
CF_OWNERDISPLAY = 0x0080
CF_DSPBITMAP = 0x0082
CF_DSPMETAFILEPICT = 0x0083
CF_DSPENHMETAFILE = 0x008E
CF_PRIVATEFIRST = 0x0200
CF_PRIVATELAST = 0x02FF
CF_GDIOBJFIRST = 0x0300
CF_GDIOBJLAST = 0x03FF

#: 句柄不是 HGLOBAL、无法按字节复制；其中位图类由 CF_DIB / CF_DIBV5 兜住
GDI_HANDLE_FORMATS = frozenset(
    {
        CF_BITMAP,
        CF_PALETTE,
        CF_METAFILEPICT,
        CF_ENHMETAFILE,
        CF_OWNERDISPLAY,
        CF_DSPBITMAP,
        CF_DSPMETAFILEPICT,
        CF_DSPENHMETAFILE,
    }
)

DROPFILES_SIZE = 20


@dataclass(frozen=True)
class TextEntry:
    value: str


@dataclass(frozen=True)
class BytesEntry:
    value: bytes


@dataclass(frozen=True)
class FileDropEntry:
    paths: tuple[str, ...]


Entry = Union[TextEntry, BytesEntry, FileDropEntry]


def is_gdi_handle_format(fmt: int) -> bool:
    """判断格式的句柄是否为 GDI 对象（不可按 HGLOBAL 复制）。"""
    if fmt in GDI_HANDLE_FORMATS:
        return True
    return CF_GDIOBJFIRST <= fmt <= CF_GDIOBJLAST


def is_private_format(fmt: int) -> bool:
    return CF_PRIVATEFIRST <= fmt <= CF_PRIVATELAST


def pack_dropfiles(paths: tuple[str, ...], *, wide: bool = True) -> bytes:
    """把文件路径列表编码成 CF_HDROP 需要的 DROPFILES 结构。"""
    if wide:
        blob = b"".join(p.encode("utf-16-le") + b"\x00\x00" for p in paths) + b"\x00\x00"
        f_wide = 1
    else:
        blob = b"".join(p.encode("mbcs", errors="replace") + b"\x00" for p in paths) + b"\x00"
        f_wide = 0
    # DROPFILES { DWORD pFiles; POINT pt; BOOL fNC; BOOL fWide; }
    return struct.pack("<IiiII", DROPFILES_SIZE, 0, 0, 0, f_wide) + blob


def unpack_dropfiles(data: bytes) -> tuple[str, ...]:
    """解析 CF_HDROP 数据，返回路径元组。"""
    if len(data) < DROPFILES_SIZE:
        raise ValueError(f"CF_HDROP 数据太短：{len(data)} 字节")
    offset, _x, _y, _nc, f_wide = struct.unpack_from("<IiiII", data, 0)
    if not DROPFILES_SIZE <= offset <= len(data):
        raise ValueError(f"CF_HDROP 路径偏移非法：{offset}")
    blob = data[offset:]
    encoding = "utf-16-le" if f_wide else "mbcs"
    text = blob.decode(encoding, errors="replace")
    return tuple(part for part in text.split("\x00") if part)


def entry_size(entry: Entry) -> int:
    """条目的字节体量，仅用于日志。"""
    if isinstance(entry, TextEntry):
        return len(entry.value.encode("utf-8", errors="replace"))
    if isinstance(entry, BytesEntry):
        return len(entry.value)
    return len(entry.paths)


@dataclass
class ClipboardSnapshot:
    """原剪贴板内容。entries 为「格式 ID -> 内容」，skipped 记录放弃的格式及原因。"""

    entries: dict[int, Entry] = field(default_factory=dict)
    skipped: dict[int, str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.entries

    def describe(self) -> str:
        """安全描述：只输出格式 ID 与字节数，绝不输出内容。"""
        if self.is_empty:
            return "snapshot(empty)"
        parts = [f"{fmt}:{entry_size(entry)}B" for fmt, entry in sorted(self.entries.items())]
        skipped = [f"{fmt}({reason})" for fmt, reason in sorted(self.skipped.items())]
        text = f"snapshot(formats={len(self.entries)} [{', '.join(parts)}])"
        if skipped:
            text += f" skipped=[{', '.join(skipped)}]"
        return text
