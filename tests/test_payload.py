"""T1.4：Payload 模型与「日志不泄露内容」测试。"""

from __future__ import annotations

import pytest
from PIL import Image

from src.core.payload import Payload, PayloadError

SECRET = "SUPER-SECRET-CLIPBOARD-TEXT"


def test_from_text_roundtrip() -> None:
    payload = Payload.from_text(SECRET)
    assert payload.kind == "text"
    assert payload.text == SECRET
    assert payload.image is None


def test_from_image_roundtrip() -> None:
    image = Image.new("RGB", (4, 4))
    payload = Payload.from_image(image)
    assert payload.kind == "image"
    assert payload.image is image
    assert payload.text is None


def test_image_without_image_rejected() -> None:
    with pytest.raises(PayloadError, match="必须提供 image"):
        Payload(kind="image")


def test_text_without_text_rejected() -> None:
    with pytest.raises(PayloadError, match="必须提供 text"):
        Payload(kind="text")


def test_kind_mismatch_rejected() -> None:
    with pytest.raises(PayloadError, match="不能同时提供"):
        Payload(kind="text", text="x", image=Image.new("RGB", (2, 2)))


def test_unknown_kind_rejected() -> None:
    with pytest.raises(PayloadError, match="未知 kind"):
        Payload(kind="video", text="x")  # type: ignore[arg-type]


def test_blank_text_is_empty() -> None:
    assert Payload.from_text("   \n\t ").is_empty
    assert not Payload.from_text("a").is_empty
    assert not Payload.from_image(Image.new("RGB", (1, 1))).is_empty


def test_repr_and_describe_never_leak_content() -> None:
    payload = Payload.from_text(SECRET)
    for rendered in (repr(payload), payload.describe(), str(payload)):
        assert SECRET not in rendered
    assert "len=" in payload.describe()


def test_image_describe_never_leaks_pixels() -> None:
    payload = Payload.from_image(Image.new("RGB", (7, 9), (1, 2, 3)))
    assert payload.describe() == "Payload(image(size=(7, 9), mode=RGB))"
