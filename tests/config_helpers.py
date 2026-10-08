"""配置类测试的共用零件（多个测试文件共用，避免两处各写一份）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.agents import load_agents
from src.core.config import DEFAULT_CONFIG_PATH, AppConfig, load_config

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "config" / "config.example.yaml"


class RecordingLogger:
    """记录各级日志，用来断言"该说的话有没有说"。"""

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _record(self, level: str):
        def handler(message, *args, **kwargs):
            self.records.append((level, message % args if args else str(message)))

        return handler

    def __getattr__(self, name: str):
        if name in {"debug", "info", "warning", "error", "exception"}:
            return self._record(name)
        raise AttributeError(name)

    def at(self, level: str) -> list[str]:
        return [text for lvl, text in self.records if lvl == level]


VALID = """
app:
  hotkeys:
    dispatch_clipboard: "Ctrl+Alt+C"
    resend_last: "Ctrl+Alt+R"
  behavior:
    multi_target_delay: 500
    multi_target_max: 3
    restore_clipboard: false
"""


def write(tmp_path: Path, text: str, name: str = "config.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path
