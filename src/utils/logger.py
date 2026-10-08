"""日志基础设施。

规则（AGENTS.md）：
- 使用 logging 模块，日志级别可配置
- **禁止在日志中记录用户内容（图片/文本）**
- 不允许静默失败，必须记录日志或通知用户

因此本模块额外提供 describe_text / describe_image：只输出长度、尺寸、
哈希前缀等元信息，用于替代直接打印内容。
"""

from __future__ import annotations

import hashlib
import logging
import sys
from typing import Any

ROOT_LOGGER_NAME = "agentctrlv"
DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
DEFAULT_DATEFMT = "%H:%M:%S"

_configured = False


def setup_logging(level: str | int = "INFO", log_file: str | None = None) -> logging.Logger:
    """配置根日志器。可重复调用，只有第一次生效（除非显式 reset）。"""
    global _configured

    root = logging.getLogger(ROOT_LOGGER_NAME)
    if _configured:
        root.setLevel(level)
        return root

    root.setLevel(level)
    root.propagate = False
    formatter = logging.Formatter(DEFAULT_FORMAT, datefmt=DEFAULT_DATEFMT)

    # 真实控制台交给 WindowsConsoleIO；被重定向到管道/文件时强制 UTF-8，
    # 否则会用 locale 编码（GBK）写出，导致日志乱码或 UnicodeEncodeError
    stream = sys.stderr
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            if stream.isatty():
                reconfigure(errors="replace")
            else:
                reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 环境相关
            pass

    console = logging.StreamHandler(stream)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file:
        # delay=True：不立即建文件；encoding=utf-8 保证跨机器可读
        file_handler = logging.FileHandler(log_file, encoding="utf-8", delay=True)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    """获取子日志器。name 传 __name__ 即可。"""
    if name.startswith(ROOT_LOGGER_NAME):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")


def describe_text(text: str | None) -> str:
    """安全地描述文本：只给长度和哈希前缀，绝不输出内容。"""
    if text is None:
        return "text(none)"
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"text(len={len(text)}, sha256={digest})"


def describe_image(image: Any) -> str:
    """安全地描述图片：只给尺寸和模式，绝不输出像素。"""
    if image is None:
        return "image(none)"
    size = getattr(image, "size", None)
    mode = getattr(image, "mode", "?")
    return f"image(size={size}, mode={mode})"


def describe_bytes(data: bytes | None) -> str:
    """安全地描述二进制数据：只给字节数。"""
    if data is None:
        return "bytes(none)"
    return f"bytes(len={len(data)})"
