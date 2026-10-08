"""剪贴板读写与全格式快照/恢复（T1.1）。

设计要点：
- OpenClipboard 失败重试 3 次、间隔 100ms（TASKS.md T1.1 硬要求）
- 快照按 CF_* 枚举**所有**格式；只有 GDI 对象句柄无法按字节复制，
  它们由 CF_DIB / CF_DIBV5 覆盖，且放弃原因会记入日志（不静默）
- 恢复走 EmptyClipboard + 逐格式 SetClipboardData
- 日志只输出格式 ID 与字节数，绝不输出内容（AGENTS.md）
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pywintypes
import win32clipboard
import win32con
from PIL import Image

from src.core import clipboard_image
from src.core.clipboard_snapshot import (
    CF_DIB,
    CF_DIBV5,
    CF_HDROP,
    BytesEntry,
    ClipboardSnapshot,
    Entry,
    FileDropEntry,
    TextEntry,
    is_gdi_handle_format,
    pack_dropfiles,
    unpack_dropfiles,
)
from src.utils.logger import describe_image, get_logger

PNG_FORMAT_NAME = clipboard_image.PNG_FORMAT_NAME
OPEN_RETRIES = 3
OPEN_RETRY_DELAY = 0.1


class ClipboardError(RuntimeError):
    """剪贴板操作失败，且重试后仍无法完成。"""


class ClipboardManager:
    """剪贴板读写门面。所有 Win32 剪贴板调用都收敛在这里。"""

    def __init__(
        self,
        retries: int = OPEN_RETRIES,
        retry_delay: float = OPEN_RETRY_DELAY,
        logger=None,
    ) -> None:
        self._retries = max(1, retries)
        self._retry_delay = retry_delay
        self._log = logger or get_logger(__name__)
        self._png_format: int | None = None

    # ---------- 基础设施 ----------

    @contextmanager
    def open(self) -> Iterator[None]:
        """打开剪贴板，失败重试 retries 次。全部失败则抛 ClipboardError。"""
        last_error: Exception | None = None
        opened = False
        for attempt in range(1, self._retries + 1):
            try:
                win32clipboard.OpenClipboard()
                opened = True
                break
            except (pywintypes.error, OSError) as exc:
                last_error = exc
                self._log.warning(
                    "OpenClipboard 第 %d/%d 次失败：%s", attempt, self._retries, exc
                )
                if attempt < self._retries:
                    time.sleep(self._retry_delay)

        if not opened:
            raise ClipboardError(f"OpenClipboard 重试 {self._retries} 次后仍失败：{last_error}")

        try:
            yield
        finally:
            try:
                win32clipboard.CloseClipboard()
            except (pywintypes.error, OSError) as exc:  # 不能静默
                self._log.error("CloseClipboard 失败：%s", exc)

    def png_format_id(self) -> int:
        """注册格式 "PNG" 的 ID（Chromium/Electron 系优先读它）。"""
        if self._png_format is None:
            self._png_format = win32clipboard.RegisterClipboardFormat(PNG_FORMAT_NAME)
        return self._png_format

    def available_formats(self) -> list[int]:
        formats: list[int] = []
        with self.open():
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if fmt == 0:
                    break
                formats.append(fmt)
        return formats

    def format_name(self, fmt: int) -> str:
        try:
            return win32clipboard.GetClipboardFormatName(fmt)
        except (pywintypes.error, OSError):
            return f"CF_{fmt:#06x}"

    # ---------- 快照 / 恢复 ----------

    def capture(self) -> ClipboardSnapshot:
        """保存剪贴板全部格式。不修改剪贴板。"""
        snapshot = ClipboardSnapshot()
        with self.open():
            fmt = 0
            while True:
                fmt = win32clipboard.EnumClipboardFormats(fmt)
                if fmt == 0:
                    break
                try:
                    entry, reason = self._read_format(fmt)
                except (pywintypes.error, OSError) as exc:
                    snapshot.skipped[fmt] = f"读取异常 {type(exc).__name__}"
                    self._log.warning("读取剪贴板格式 %s 失败：%s", self.format_name(fmt), exc)
                    continue
                if entry is None:
                    snapshot.skipped[fmt] = reason
                else:
                    snapshot.entries[fmt] = entry

        self._log.debug("剪贴板快照：%s", snapshot.describe())
        return snapshot

    def _read_format(self, fmt: int) -> tuple[Entry | None, str]:
        if is_gdi_handle_format(fmt):
            return None, "GDI 句柄（已由 CF_DIB/CF_DIBV5 覆盖）"

        data = win32clipboard.GetClipboardData(fmt)

        # pywin32 对 CF_HDROP 直接返回文件名元组，不走 bytes 路径。
        # 漏掉这一支会让「剪贴板里只有文件」时快照变空，
        # 进而导致 restore() 拒绝恢复、用户的文件剪贴板被 Payload 永久覆盖。
        if fmt == CF_HDROP and isinstance(data, (tuple, list)):
            paths = tuple(str(item) for item in data)
            if not paths:
                return None, "CF_HDROP 内容为空"
            return FileDropEntry(paths), ""

        if isinstance(data, str):
            return TextEntry(data), ""
        if isinstance(data, (bytes, bytearray)):
            raw = bytes(data)
            if fmt == CF_HDROP:
                try:
                    return FileDropEntry(unpack_dropfiles(raw)), ""
                except ValueError as exc:
                    self._log.warning("CF_HDROP 解析失败，退化为原始字节：%s", exc)
            return BytesEntry(raw), ""
        return None, f"非内存句柄（{type(data).__name__}）"

    def restore(self, snapshot: ClipboardSnapshot) -> bool:
        """把快照写回剪贴板。返回是否全部格式都成功。

        **不抛异常**：它经常在 finally 里被调用，抛出去会掩盖真正的失败原因。
        剪贴板被占用导致打不开时记 error 并返回 False。
        """
        if snapshot.is_empty:
            self._log.warning("快照为空，跳过恢复（不清空剪贴板，避免破坏用户数据）")
            return False

        failed: list[int] = []
        try:
            with self.open():
                win32clipboard.EmptyClipboard()
                for fmt, entry in sorted(snapshot.entries.items()):
                    try:
                        self._write_entry(fmt, entry)
                    except (pywintypes.error, OSError, ValueError) as exc:
                        failed.append(fmt)
                        self._log.error("恢复格式 %s 失败：%s", self.format_name(fmt), exc)
        except ClipboardError as exc:
            self._log.error("恢复剪贴板失败（打不开剪贴板）：%s", exc)
            return False

        if failed:
            self._log.error(
                "剪贴板恢复不完整：%d/%d 个格式失败（%s）",
                len(failed),
                len(snapshot.entries),
                ", ".join(self.format_name(f) for f in failed),
            )
            return False
        self._log.debug("剪贴板已恢复：%d 个格式", len(snapshot.entries))
        return True

    def _write_entry(self, fmt: int, entry: Entry) -> None:
        if isinstance(entry, TextEntry):
            win32clipboard.SetClipboardData(fmt, entry.value)
        elif isinstance(entry, FileDropEntry):
            win32clipboard.SetClipboardData(fmt, pack_dropfiles(entry.paths))
        elif isinstance(entry, BytesEntry):
            win32clipboard.SetClipboardData(fmt, entry.value)
        else:  # pragma: no cover - 类型系统已排除
            raise ValueError(f"未知条目类型：{type(entry).__name__}")

    # ---------- 读 ----------

    def has_image(self) -> bool:
        with self.open():
            candidates = (CF_DIB, CF_DIBV5, win32con.CF_BITMAP, self.png_format_id())
            return any(win32clipboard.IsClipboardFormatAvailable(f) for f in candidates)

    def read_image(self) -> Image.Image | None:
        """读取剪贴板图片。优先级：PNG（无损含 alpha）> CF_DIBV5 > CF_DIB。"""
        with self.open():
            image = clipboard_image.decode_image(self.png_format_id(), self._log)
        if image is None:
            self._log.info("剪贴板中没有可解码的图片")
        return image

    def read_text(self) -> str | None:
        with self.open():
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                if isinstance(data, str):
                    return data
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_TEXT):
                raw = win32clipboard.GetClipboardData(win32con.CF_TEXT)
                if isinstance(raw, (bytes, bytearray)):
                    return bytes(raw).decode("mbcs", errors="replace")
        return None

    def save_image_png(self, path: str | Path) -> Path:
        """T1.1 验收：把剪贴板图片存成 PNG 文件。"""
        target = Path(path)
        image = self.read_image()
        if image is None:
            raise ClipboardError("剪贴板中没有图片，无法保存")
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG")
        self._log.info("已保存 %s（%s）", target, describe_image(image))
        return target

    # ---------- 写 ----------

    def write_text(self, text: str) -> None:
        with self.open():
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)

    def write_image(self, image: Image.Image) -> None:
        """写入图片：PNG + CF_DIB（+ CF_BITMAP 尽力而为）。"""
        dib, png = clipboard_image.encode_formats(image)
        with self.open():
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(self.png_format_id(), png)
            win32clipboard.SetClipboardData(CF_DIB, dib)
            clipboard_image.write_bitmap_best_effort(dib, self._log)
        self._log.debug("已写入剪贴板图片（PNG + CF_DIB）：%s", describe_image(image))
