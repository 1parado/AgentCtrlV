"""T1.4：DIB <-> Image 转换测试。"""

from __future__ import annotations

import struct

import pytest
from PIL import Image

from src.core import dib


def _pattern_image(size=(64, 48)) -> Image.Image:
    image = Image.new("RGB", size)
    pixels = image.load()
    for x in range(size[0]):
        for y in range(size[1]):
            pixels[x, y] = (x * 4 % 256, y * 5 % 256, (x + y) * 3 % 256)
    return image


def test_round_trip_is_pixel_identical() -> None:
    original = _pattern_image()
    restored = dib.dib_to_image(dib.image_to_dib(original))
    assert restored.size == original.size
    assert list(restored.convert("RGB").getdata()) == list(original.getdata())


def test_dib_to_bmp_sets_correct_pixel_offset() -> None:
    raw = dib.image_to_dib(_pattern_image())
    bmp = dib.dib_to_bmp(raw)

    assert bmp[:2] == b"BM"
    file_size, _r1, _r2, offset = struct.unpack_from("<IHHI", bmp, 2)
    assert file_size == len(bmp)
    # 24bpp BI_RGB：14 字节文件头 + 40 字节信息头，无调色板
    assert offset == 14 + 40
    assert offset == 14 + dib.pixel_data_offset(raw)


def test_palette_offset_is_accounted_for() -> None:
    palette_image = Image.new("P", (16, 16))
    palette_image.putpalette([i % 256 for i in range(768)])
    raw = dib.image_to_dib(palette_image)
    # 统一转 RGB 后不应出现调色板
    assert dib.pixel_data_offset(raw) == 40


def test_eight_bit_dib_from_pillow_has_palette_offset() -> None:
    import io

    source = Image.new("P", (8, 8))
    source.putpalette([i % 256 for i in range(768)])
    buffer = io.BytesIO()
    source.save(buffer, format="BMP")
    raw = buffer.getvalue()[dib.BITMAPFILEHEADER_SIZE:]

    header_size = struct.unpack_from("<I", raw, 0)[0]
    assert dib.pixel_data_offset(raw) == header_size + 256 * 4
    # 仍应能被完整解码
    assert dib.dib_to_image(raw).size == (8, 8)


def test_short_dib_raises() -> None:
    with pytest.raises(dib.UnsupportedDibError, match="太短"):
        dib.dib_to_bmp(b"\x28\x00\x00\x00")


@pytest.mark.parametrize("compression", [dib.BI_JPEG, dib.BI_PNG])
def test_compressed_dib_is_rejected(compression: int) -> None:
    raw = bytearray(dib.image_to_dib(_pattern_image()))
    struct.pack_into("<I", raw, 16, compression)
    with pytest.raises(dib.UnsupportedDibError, match="BI_JPEG/BI_PNG"):
        dib.dib_to_bmp(bytes(raw))


def test_bitfields_masks_counted() -> None:
    """BI_BITFIELDS 时，3 个颜色掩码跟在头后面（12 字节）。"""
    base = dib.image_to_dib(_pattern_image())
    masks = struct.pack("<III", 0x00FF0000, 0x0000FF00, 0x000000FF)
    blob = bytearray(base[:40] + masks + base[40:])
    struct.pack_into("<I", blob, 16, dib.BI_BITFIELDS)

    assert dib.pixel_data_offset(bytes(blob)) == 40 + 12


def test_v5_header_with_trailing_masks() -> None:
    """回归：Windows 在 124 字节 BITMAPV5HEADER 之后**仍然会补 12 字节掩码**。

    实测 120x90 的 32bpp DIBV5 总长 43336 = 124 + 12 + 43200。
    只按头长度算会少 12 字节，解码出来整幅图错位。
    """
    width, height, bit_count = 4, 3, 32
    pixels = ((width * bit_count + 31) // 32) * 4 * height
    blob = bytearray(124 + 12 + pixels)

    struct.pack_into("<I", blob, 0, 124)  # biSize
    struct.pack_into("<i", blob, 4, width)
    struct.pack_into("<i", blob, 8, height)
    struct.pack_into("<H", blob, 12, 1)  # biPlanes
    struct.pack_into("<H", blob, 14, bit_count)
    struct.pack_into("<I", blob, 16, dib.BI_BITFIELDS)

    assert dib.pixel_data_offset(bytes(blob)) == 124 + 12
    assert len(dib.dib_to_bmp(bytes(blob))) == 14 + len(blob)


def test_offset_prefers_actual_byte_length() -> None:
    """头声明与字节数矛盾时，以真实字节数为准（自纠正）。"""
    base = dib.image_to_dib(_pattern_image())
    declared = bytearray(base)
    struct.pack_into("<I", declared, 16, dib.BI_BITFIELDS)  # 声称有掩码，实际没有
    assert dib.pixel_data_offset(bytes(declared)) == 40

    with_masks = bytes(declared[:40]) + b"\x00" * 12 + bytes(declared[40:])
    assert dib.pixel_data_offset(with_masks) == 52


def test_png_bytes_are_decodable() -> None:
    import io

    image = _pattern_image((20, 20))
    with Image.open(io.BytesIO(dib.image_to_png_bytes(image))) as decoded:
        assert decoded.size == (20, 20)
