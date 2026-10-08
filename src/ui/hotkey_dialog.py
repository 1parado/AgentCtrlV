"""M5：热键自定义 UI。

M2 时热键写死在代码里，T2.1 说的"允许改键"就落在这里。

用 Qt 自带的 QKeySequenceEdit 做**按键捕获**（比让用户手打 "Alt+V" 好得多），
再把 Qt 的序列文本转成 CONFIG_SCHEMA 用的写法（Win 而不是 Meta、Delete 而不是 Del），
最后交给 `parse_hotkey` 校验——**同一套校验**，不在 UI 里另写一份规则，
否则两边迟早会不一致。
"""

from __future__ import annotations

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QKeySequenceEdit,
    QLabel,
    QVBoxLayout,
)

from src.core.hotkey import HotkeyError, parse_hotkey

#: Qt 的键名 -> CONFIG_SCHEMA / parse_hotkey 认的写法
_QT_TO_OURS = {
    "Meta": "Win",
    "Del": "Delete",
    "Return": "Enter",
    "Esc": "Escape",
    "Ins": "Insert",
    "PgUp": "PageUp",
    "PgDown": "PageDown",
    "Backspace": "Backspace",
}


def sequence_to_text(sequence: QKeySequence) -> str:
    """把 QKeySequence 转成 "Ctrl+Alt+C" 这种配置写法。"""
    text = sequence.toString(QKeySequence.SequenceFormat.PortableText)
    if not text:
        return ""
    parts = [part.strip() for part in text.split("+") if part.strip()]
    return "+".join(_QT_TO_OURS.get(part, part) for part in parts)


def text_to_sequence(text: str) -> QKeySequence:
    """配置写法 -> QKeySequence，用于回显当前值。"""
    if not text:
        return QKeySequence()
    reverse = {ours: qt for qt, ours in _QT_TO_OURS.items()}
    parts = [reverse.get(part.strip(), part.strip()) for part in text.split("+") if part.strip()]
    return QKeySequence("+".join(parts))


class HotkeyDialog(QDialog):
    """两个热键的编辑框 + 校验。确认后由调用方负责生效与落盘。"""

    def __init__(self, dispatch: str, resend: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置热键")
        self.setModal(True)

        self._dispatch = QKeySequenceEdit(text_to_sequence(dispatch))
        self._resend = QKeySequenceEdit(text_to_sequence(resend))
        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setStyleSheet("color: #d9534f;")

        form = QFormLayout()
        form.addRow("分发剪贴板", self._dispatch)
        form.addRow("重发上一次", self._resend)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("热键需包含至少一个修饰键（Ctrl / Alt / Shift / Win）。"))
        layout.addWidget(self._error)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str]:
        return (
            sequence_to_text(self._dispatch.keySequence()),
            sequence_to_text(self._resend.keySequence()),
        )

    def error_text(self) -> str:
        return self._error.text()

    def accept(self) -> None:  # noqa: N802 - Qt API
        """校验通过才关窗。校验规则复用 parse_hotkey，和启动时完全一致。"""
        dispatch, resend = self.values()
        for label, text in (("分发", dispatch), ("重发", resend)):
            if not text:
                self._error.setText(f"{label}热键不能为空")
                return
            try:
                parse_hotkey(text)
            except HotkeyError as exc:
                self._error.setText(f"{label}热键不可用：{exc}")
                return
        if dispatch == resend:
            self._error.setText("两个热键不能是同一个组合")
            return
        super().accept()
