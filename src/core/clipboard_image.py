"""剪贴板图片编解码（PNG / CF_DIB / CF_BITMAP）。

从 clipboard.py 拆出来，一是 AGENTS.md 的单文件 300 行约束，
二是把"图片格式细节"和"剪贴板会话/快照"分开。

约定：本模块函数都假定**调用方已经打开了剪贴板**（ClipboardManager.open()）。
"""

from __future__ import annotations

import ctypes
import io

import pywintypes
import win32clipboard
import win32con
from PIL import Image

from src.core import dib as dib_module
from src.core.clipboard_snapshot import CF_DIB, CF_DIBV5
from src.utils.logger import describe_image

PNG_FORMAT_NAME = "PNG"


def _gdi32():
    return ctypes.WinDLL("gdi32", use_last_error=True)


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def decode_image(png_format_id: int, log) -> Image.Image | None:
    """读取剪贴板图片。优先级：PNG（无损含 alpha）> CF_DIBV5 > CF_DIB。"""
    if win32clipboard.IsClipboardFormatAvailable(png_format_id):
        raw = win32clipboard.GetClipboardData(png_format_id)
        if isinstance(raw, (bytes, bytearray)):
            with Image.open(io.BytesIO(bytes(raw))) as opened:
                opened.load()
                log.debug("从注册格式 PNG 读取到 %s", describe_image(opened))
                return opened.copy()

    for fmt in (CF_DIBV5, CF_DIB):
        if not win32clipboard.IsClipboardFormatAvailable(fmt):
            continue
        raw = win32clipboard.GetClipboardData(fmt)
        if not isinstance(raw, (bytes, bytearray)):
            continue
        try:
            image = dib_module.dib_to_image(bytes(raw))
        except (dib_module.UnsupportedDibError, OSError, ValueError) as exc:
            log.warning("格式 %s 解码失败，尝试下一优先级：%s", fmt, exc)
            continue
        log.debug("从 %s 读取到 %s", fmt, describe_image(image))
        return image

    return None


def encode_formats(image: Image.Image) -> tuple[bytes, bytes]:
    """把图片编码成 (CF_DIB 字节, PNG 字节)。"""
    return dib_module.image_to_dib(image), dib_module.image_to_png_bytes(image)


def write_bitmap_best_effort(dib: bytes, log) -> None:
    """补一个 CF_BITMAP，照顾只认 DDB 的老程序。

    失败只降级不中断：CF_DIB 已经覆盖绝大多数消费端。
    成功后句柄所有权归系统，**不能** DeleteObject。
    """
    gdi32 = _gdi32()
    user32 = _user32()
    gdi32.CreateDIBitmap.restype = ctypes.c_void_p
    hdc = user32.GetDC(None)
    if not hdc:
        log.warning("GetDC 失败，跳过 CF_BITMAP（CF_DIB 仍可用）")
        return

    buffer = ctypes.create_string_buffer(dib, len(dib))
    info_ptr = ctypes.cast(buffer, ctypes.c_void_p)
    bits_ptr = ctypes.c_void_p(ctypes.addressof(buffer) + dib_module.pixel_data_offset(dib))
    hbitmap = None
    try:
        hbitmap = gdi32.CreateDIBitmap(hdc, info_ptr, 4, bits_ptr, info_ptr, 0)  # CBM_INIT
        if not hbitmap:
            log.warning(
                "CreateDIBitmap 失败（err=%s），跳过 CF_BITMAP", ctypes.get_last_error()
            )
            return
        win32clipboard.SetClipboardData(win32con.CF_BITMAP, hbitmap)
        hbitmap = None  # 所有权已转移给剪贴板
    except (pywintypes.error, OSError) as exc:
        log.warning("写入 CF_BITMAP 失败，仅保留 CF_DIB：%s", exc)
    finally:
        if hbitmap:
            gdi32.DeleteObject(ctypes.c_void_p(hbitmap))
        user32.ReleaseDC(None, hdc)
