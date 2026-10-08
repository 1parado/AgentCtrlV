"""粘贴目标探针：一个"能回报自己收到了什么"的 GUI 输入框。

**为什么需要它**：注入是发出去就没有回执的操作。要判断 Ctrl+V 到底有没有
送到，唯一可靠的办法是让目标窗口自己汇报——肉眼盯着记事本只能看到
"好像没粘上"，看不到到底是"按键被系统吞了"还是"窗口没激活"。

**为什么不用记事本**：Win11 记事本是多标签的**单进程**应用，
`notepad.exe` 只会往用户已经打开的记事本里加一个标签页。
拿它当目标会污染用户正在编辑的文档，而且无法安全回收。

这个探针是独立进程、唯一标题、由调用方完全拥有的 QTextEdit。
它每 150ms 把自身状态写成 JSON，于是"注入到底断在哪一环"可观测：

    active        窗口是否是系统前台窗口
    focused       焦点控件类名（QTextEdit 才会收到 Ctrl+V）
    keys_seen     实际收到的按键（空 = SendInput 根本没送到）
    paste_calls   QTextEdit::paste 被调用次数
    last_mime     最后一次粘贴的 mime 内容（has_image / has_text）

被 `scripts/selftest.py` 与 `tests/test_integration_injection.py` 共用。
一般不手动运行：

    python scripts/paste_target.py <state.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QTextEdit

TITLE = "AgentCtrlV-Probe-Target"
SELF_DESTRUCT_MS = 30_000
HEARTBEAT_MS = 150


class ProbeTarget(QTextEdit):
    """记录粘贴过程每一步的 QTextEdit。"""

    def __init__(self, state_path: Path) -> None:
        super().__init__()
        self._state_path = state_path
        self._keys: list[str] = []
        self._paste_calls = 0
        self._last_mime: dict | None = None
        self.setWindowTitle(TITLE)
        self.resize(640, 420)

    # --- 事件钩子 ---

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 只记键码，**不记文本**：这个窗口会抢焦点，可能收到用户真实敲击，
        # 不该把用户输入落到任何文件里（AGENTS.md 禁止记录用户内容）。
        self._keys.append(f"<{event.key():#x}>")
        super().keyPressEvent(event)

    def paste(self) -> None:
        self._paste_calls += 1
        super().paste()

    def insertFromMimeData(self, source) -> None:  # noqa: N802 - Qt 命名
        self._last_mime = {
            "has_image": source.hasImage(),
            "has_text": source.hasText(),
            "formats": sorted(source.formats()),
        }
        super().insertFromMimeData(source)
        self._dump()

    # --- 状态回报 ---

    def snapshot(self) -> dict:
        app = QApplication.instance()
        focus = app.focusWidget() if app else None
        return {
            "active": self.isActiveWindow(),
            "focused": type(focus).__name__ if focus else None,
            "keys_seen": self._keys,
            "paste_calls": self._paste_calls,
            "last_mime": self._last_mime,
        }

    def _dump(self) -> None:
        self._state_path.write_text(
            json.dumps(self.snapshot(), ensure_ascii=False), encoding="utf-8"
        )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: target_app.py <state.json>", file=sys.stderr)
        return 2

    state_path = Path(argv[1])
    app = QApplication([argv[0]])
    window = ProbeTarget(state_path)
    window.show()
    window.raise_()
    window.activateWindow()
    window.setFocus()

    heartbeat = QTimer()
    heartbeat.timeout.connect(window._dump)
    heartbeat.start(HEARTBEAT_MS)

    QTimer.singleShot(SELF_DESTRUCT_MS, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
