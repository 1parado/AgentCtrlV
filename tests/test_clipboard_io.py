"""ClipboardManager 的图片/文本读写路径测试（全部跑在 FakeClipboard 上）。

会话重试、全格式快照/恢复见 test_clipboard.py。
"""

from __future__ import annotations

import pytest
from PIL import Image

from src.core import dib
from src.core.clipboard import ClipboardError
from src.core.clipboard_snapshot import CF_DIB, CF_UNICODETEXT
from tests.conftest import FakeClipboard, make_manager, patch_clipboard

SECRET = "SUPER-SECRET-CLIPBOARD-TEXT"


# ---------- 读图片 ----------


def test_read_image_prefers_registered_png(monkeypatch) -> None:
    png_image = Image.new("RGB", (11, 13), (255, 0, 0))
    dib_image = Image.new("RGB", (99, 99), (0, 0, 255))
    fake = FakeClipboard()
    patch_clipboard(monkeypatch, fake)
    png_format = fake.RegisterClipboardFormat("PNG")
    fake.data = {
        png_format: dib.image_to_png_bytes(png_image),
        CF_DIB: dib.image_to_dib(dib_image),
    }

    result = make_manager(fake).read_image()

    assert result is not None
    assert result.size == (11, 13)


def test_read_image_falls_back_to_dib(monkeypatch) -> None:
    source = Image.new("RGB", (17, 19), (0, 255, 0))
    fake = FakeClipboard(data={CF_DIB: dib.image_to_dib(source)})
    patch_clipboard(monkeypatch, fake)

    result = make_manager(fake).read_image()

    assert result is not None
    assert result.size == (17, 19)


def test_read_image_returns_none_when_absent(monkeypatch) -> None:
    fake = FakeClipboard(data={CF_UNICODETEXT: "text only"})
    patch_clipboard(monkeypatch, fake)

    assert make_manager(fake).read_image() is None
    assert make_manager(fake).has_image() is False


def test_has_image_true_for_dib(monkeypatch) -> None:
    fake = FakeClipboard(data={CF_DIB: b"\x00" * 8})
    patch_clipboard(monkeypatch, fake)

    assert make_manager(fake).has_image() is True


# ---------- 存 PNG ----------


def test_save_image_png_raises_without_image(monkeypatch, tmp_path) -> None:
    fake = FakeClipboard()
    patch_clipboard(monkeypatch, fake)

    with pytest.raises(ClipboardError, match="没有图片"):
        make_manager(fake).save_image_png(tmp_path / "out.png")


def test_save_image_png_writes_file(monkeypatch, tmp_path) -> None:
    fake = FakeClipboard(data={CF_DIB: dib.image_to_dib(Image.new("RGB", (5, 6)))})
    patch_clipboard(monkeypatch, fake)

    target = make_manager(fake).save_image_png(tmp_path / "nested" / "out.png")

    assert target.exists()
    with Image.open(target) as written:
        assert written.size == (5, 6)


# ---------- 读 / 写文本 ----------


def test_read_text_prefers_unicode(monkeypatch) -> None:
    fake = FakeClipboard(data={CF_UNICODETEXT: SECRET})
    patch_clipboard(monkeypatch, fake)

    assert make_manager(fake).read_text() == SECRET


def test_write_text_sets_unicode(monkeypatch) -> None:
    fake = FakeClipboard(data={CF_DIB: b"\x00" * 8})
    patch_clipboard(monkeypatch, fake)

    make_manager(fake).write_text(SECRET)

    assert fake.data == {CF_UNICODETEXT: SECRET}
    assert fake.empty_calls == 1


def test_write_image_sets_png_and_dib(monkeypatch) -> None:
    fake = FakeClipboard(data={CF_UNICODETEXT: "old"})
    patch_clipboard(monkeypatch, fake)
    manager = make_manager(fake)
    # CF_BITMAP 走真实 GDI 句柄，单元测试里隔离掉（集成测试已覆盖）
    monkeypatch.setattr(
        "src.core.clipboard_image.write_bitmap_best_effort", lambda dib_bytes, log: None
    )

    manager.write_image(Image.new("RGB", (8, 9), (10, 20, 30)))

    assert CF_DIB in fake.data
    assert manager.png_format_id() in fake.data
    assert CF_UNICODETEXT not in fake.data
