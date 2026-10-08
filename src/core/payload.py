"""Payload —— PRD 里的核心概念：要分发的内容。

只有两种：图片或文本。图片持有 PIL Image，文本持有 str。
describe() 是**唯一**允许进入日志的表示形式（不含内容）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from PIL import Image

from src.utils.logger import describe_image, describe_text

PayloadKind = Literal["image", "text"]


class PayloadError(ValueError):
    """Payload 构造非法。"""


@dataclass(frozen=True, eq=False)
class Payload:
    kind: PayloadKind
    image: Image.Image | None = field(default=None, repr=False)
    text: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.kind == "image":
            if self.image is None:
                raise PayloadError("kind=image 时必须提供 image")
            if self.text is not None:
                raise PayloadError("kind=image 时不能同时提供 text")
        elif self.kind == "text":
            if self.text is None:
                raise PayloadError("kind=text 时必须提供 text")
            if self.image is not None:
                raise PayloadError("kind=text 时不能同时提供 image")
        else:
            raise PayloadError(f"未知 kind: {self.kind!r}")

    @classmethod
    def from_image(cls, image: Image.Image) -> "Payload":
        return cls(kind="image", image=image)

    @classmethod
    def from_text(cls, text: str) -> "Payload":
        return cls(kind="text", text=text)

    @property
    def is_empty(self) -> bool:
        """文本为空视为空载荷；图片永远非空。"""
        if self.kind == "text":
            return not (self.text or "").strip()
        return False

    def describe(self) -> str:
        """安全描述，用于日志。绝不输出内容。"""
        if self.kind == "image":
            return f"Payload({describe_image(self.image)})"
        return f"Payload({describe_text(self.text)})"

    def __repr__(self) -> str:  # 防止 dataclass 默认 repr 泄露内容
        return self.describe()
