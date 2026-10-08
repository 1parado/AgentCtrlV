"""DIB（设备无关位图）与 PIL Image 的互转。

剪贴板里的图片格式是 CF_DIB —— 一个**去掉 14 字节 BITMAPFILEHEADER** 的
BMP。Pillow 不认识裸 DIB，所以这里负责补/去文件头。
"""

from __future__ import annotations

import io
import struct

from PIL import Image

BITMAPFILEHEADER_SIZE = 14
MIN_INFOHEADER_SIZE = 40

BI_RGB = 0
BI_BITFIELDS = 3
BI_JPEG = 4
BI_PNG = 5


class UnsupportedDibError(ValueError):
    """DIB 结构无法用 BMP 解码器处理。"""


def _offset_from_length(
    dib: bytes, header_size: int, bit_count: int, compression: int
) -> int | None:
    """用总长度反推像素偏移。

    剪贴板 DIB 的 blob 恰好是「头 + 调色板/掩码 + 像素」，所以
    len(dib) - 像素字节数 就是真实偏移。这比静态推算可靠，因为
    Windows 在 BITMAPV5HEADER 之后**仍然会补 12 字节颜色掩码**。
    """
    if compression not in (BI_RGB, BI_BITFIELDS):
        return None
    if bit_count not in (1, 4, 8, 16, 24, 32):
        return None

    width = struct.unpack_from("<i", dib, 4)[0]
    height = struct.unpack_from("<i", dib, 8)[0]
    if width <= 0 or height == 0:
        return None

    stride = ((width * bit_count + 31) // 32) * 4
    offset = len(dib) - stride * abs(height)
    # 合理区间：至少装得下头，最多再装 1KB 的调色板/掩码/对齐
    if header_size <= offset <= header_size + 1024:
        return offset
    return None


def pixel_data_offset(dib: bytes) -> int:
    """计算像素数据**相对 DIB 起点**的偏移（不含 14 字节文件头）。

    实测坑（2026-10）：120x90 的 32bpp DIBV5 总长 43336 = 124 + 12 + 43200。
    只按「头长度 + 调色板」静态推算会少 12 字节，整幅图错位。
    所以优先用总长度反推，静态推算只作兜底。
    """
    header_size = struct.unpack_from("<I", dib, 0)[0]
    compression = struct.unpack_from("<I", dib, 16)[0]
    bit_count = struct.unpack_from("<H", dib, 14)[0]

    if compression in (BI_JPEG, BI_PNG):
        raise UnsupportedDibError(
            f"CF_DIB 使用压缩类型 {compression}（BI_JPEG/BI_PNG），不是可解码的位图"
        )

    derived = _offset_from_length(dib, header_size, bit_count, compression)
    if derived is not None:
        return derived

    extra = 0
    if compression == BI_BITFIELDS:
        # 无论头是 40 还是 124，掩码都跟在头后面
        extra = 12
    elif bit_count <= 8 and compression == BI_RGB:
        clr_used = struct.unpack_from("<I", dib, 32)[0] if header_size >= MIN_INFOHEADER_SIZE else 0
        extra = (clr_used or (1 << bit_count)) * 4

    return header_size + extra


def dib_to_bmp(dib: bytes) -> bytes:
    """给裸 DIB 补上 BITMAPFILEHEADER，得到 Pillow 能打开的 BMP 字节流。"""
    if len(dib) < MIN_INFOHEADER_SIZE:
        raise UnsupportedDibError(f"DIB 太短：{len(dib)} 字节")

    offset = BITMAPFILEHEADER_SIZE + pixel_data_offset(dib)
    if offset > len(dib):
        raise UnsupportedDibError(f"DIB 像素偏移 {offset} 超出数据长度 {len(dib)}")

    header = b"BM" + struct.pack("<IHHI", BITMAPFILEHEADER_SIZE + len(dib), 0, 0, offset)
    return header + dib


def dib_to_image(dib: bytes) -> Image.Image:
    """解码 CF_DIB。调用方负责处理异常。"""
    with Image.open(io.BytesIO(dib_to_bmp(dib))) as opened:
        opened.load()
        return opened.copy()


def image_to_dib(image: Image.Image) -> bytes:
    """编码为 CF_DIB（24bpp BI_RGB）。

    统一转 RGB：CF_DIB 的 alpha 语义在各消费端不一致（多数应用按 BGRX 处理），
    带 alpha 反而容易出现黑底。透明度需求由 PNG 格式单独承载。
    """
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="BMP")
    return buffer.getvalue()[BITMAPFILEHEADER_SIZE:]


def image_to_png_bytes(image: Image.Image) -> bytes:
    """编码为 PNG 字节流，用于注册格式 "PNG"（Chromium/Electron 系优先读它）。

    **刻意用 compress_level=1**：这个 PNG 只活在剪贴板里、几秒后就被恢复掉，
    体积毫无意义（实测 level 6 -> 1 只有 11KB -> 33KB，4K 下 39KB -> 121KB），
    但编码时间差得很明显——4K 从 188ms 降到 128ms。它落在**热启动**路径上
    （用户确认之后才写剪贴板），所以省下来的是用户实打实等的时间。

    不用 level=0：实测并不更快（4K 139ms），体积却暴涨到 24MB。
    PNG 在任何压缩级别下都是无损的，所以这里没有画质代价。
    """
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()
