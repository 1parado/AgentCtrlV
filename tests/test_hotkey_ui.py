"""M5：热键自定义 UI 与改键的落地/回滚。

改键最容易出的问题是"换到一半失败"，留下一个既不是旧配置也不是新配置的状态。
这里重点钉住那条路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QKeySequence

from src.core.config import HotkeyConfig, load_config, save_hotkeys
from src.core.hotkey import HotkeyConflictError
from src.main import Application
from src.ui.hotkey_dialog import HotkeyDialog, sequence_to_text, text_to_sequence

CONFIG_WITH_HOTKEYS = """
# 我的注释：这行必须活下来
app:
  hotkeys:
    dispatch_clipboard: "Alt+V"   # 分发
    resend_last: "Alt+Shift+V"
  behavior:
    multi_target_max: 3   # 上限
"""


def write(tmp_path: Path, text: str = CONFIG_WITH_HOTKEYS) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class FakeHotkeys:
    """记录注册/注销顺序，并可指定某些组合必然冲突。"""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.registered: dict[int, str] = {}
        self.unregistered: list[int] = []
        self.fail_on = fail_on or set()
        self._next = 1

    def register(self, text: str) -> int:
        if text in self.fail_on:
            raise HotkeyConflictError(f"{text} 已被其他程序占用")
        hotkey_id = self._next
        self._next += 1
        self.registered[hotkey_id] = text
        return hotkey_id

    def unregister(self, hotkey_id: int) -> bool:
        self.unregistered.append(hotkey_id)
        return self.registered.pop(hotkey_id, None) is not None


def build_app(qt_app, tmp_path, *, fail_on: set[str] | None = None):
    config = load_config(write(tmp_path))
    application = Application(qt_app, config=config)
    fake = FakeHotkeys(fail_on)
    application.hotkeys = fake
    application._dispatch_id = fake.register("Alt+V")
    application._resend_id = fake.register("Alt+Shift+V")
    notices: list[tuple] = []
    application.tray.notify = lambda title, message, **kw: notices.append((title, message, kw))
    return application, fake, notices


# ---------- 键名转换 ----------


def test_qt_sequence_converts_to_config_wording() -> None:
    """Qt 说 Meta/Del/Return，配置契约说 Win/Delete/Enter。"""
    assert sequence_to_text(QKeySequence("Ctrl+Alt+C")) == "Ctrl+Alt+C"
    assert sequence_to_text(QKeySequence("Meta+V")) == "Win+V"
    assert sequence_to_text(QKeySequence("Del")) == "Delete"
    assert sequence_to_text(QKeySequence("Return")) == "Enter"


def test_empty_sequence_converts_to_empty_text() -> None:
    assert sequence_to_text(QKeySequence()) == ""


@pytest.mark.parametrize("text", ["Alt+V", "Ctrl+Alt+C", "Win+V", "Alt+Shift+V"])
def test_conversion_round_trips(text: str) -> None:
    assert sequence_to_text(text_to_sequence(text)) == text


# ---------- 对话框校验 ----------


def test_dialog_rejects_empty_hotkey(qt_app) -> None:
    dialog = HotkeyDialog("", "Alt+Shift+V")

    dialog.accept()

    assert not dialog.result(), "空热键不该被接受"
    assert "不能为空" in dialog.error_text()


def test_dialog_rejects_reserved_combo(qt_app) -> None:
    """校验复用 parse_hotkey，UI 里不另写一份规则。"""
    dialog = HotkeyDialog("Ctrl+Alt+Del", "Alt+Shift+V")

    dialog.accept()

    assert "不可用" in dialog.error_text()


def test_dialog_rejects_identical_hotkeys(qt_app) -> None:
    dialog = HotkeyDialog("Alt+V", "Alt+V")

    dialog.accept()

    assert "不能是同一个" in dialog.error_text()


def test_dialog_accepts_valid_pair(qt_app) -> None:
    dialog = HotkeyDialog("Ctrl+Alt+C", "Ctrl+Alt+R")

    dialog.accept()

    assert dialog.result(), "合法热键应当可以通过"
    assert dialog.values() == ("Ctrl+Alt+C", "Ctrl+Alt+R")


# ---------- 落盘 ----------


def test_save_hotkeys_preserves_comments_and_other_keys(tmp_path) -> None:
    path = write(tmp_path)

    saved, _detail = save_hotkeys(
        HotkeyConfig(dispatch_clipboard="Ctrl+Alt+C", resend_last="Ctrl+Alt+R"), path
    )

    text = path.read_text(encoding="utf-8")
    assert saved
    assert "# 我的注释：这行必须活下来" in text
    assert "multi_target_max: 3   # 上限" in text
    reloaded = load_config(path)
    assert reloaded.app.hotkeys.dispatch_clipboard == "Ctrl+Alt+C"
    assert reloaded.app.behavior.multi_target_max == 3


def test_save_hotkeys_refuses_without_anchor(tmp_path) -> None:
    """找不到锚点就拒绝写入——硬塞一个 app: 块会造成重复键、把配置写坏。"""
    path = write(tmp_path, "app:\n  behavior:\n    multi_target_max: 2\n")
    before = path.read_text(encoding="utf-8")

    saved, detail = save_hotkeys(HotkeyConfig(), path)

    assert not saved
    assert "hotkeys" in detail
    assert path.read_text(encoding="utf-8") == before


def test_save_hotkeys_reports_missing_file(tmp_path) -> None:
    saved, detail = save_hotkeys(HotkeyConfig(), tmp_path / "nope.yaml")

    assert not saved
    assert "不存在" in detail


# ---------- 改键：生效与回滚 ----------


def test_apply_hotkeys_swaps_both_and_persists(qt_app, tmp_path) -> None:
    application, fake, notices = build_app(qt_app, tmp_path)

    assert application.apply_hotkeys("Ctrl+Alt+C", "Ctrl+Alt+R")

    assert set(fake.registered.values()) == {"Ctrl+Alt+C", "Ctrl+Alt+R"}
    assert sorted(fake.unregistered) == [1, 2]
    assert load_config(tmp_path / "config.yaml").app.hotkeys.resend_last == "Ctrl+Alt+R"
    assert notices and not notices[0][2].get("warning")


def test_apply_hotkeys_keeps_unchanged_side(qt_app, tmp_path) -> None:
    """只改一个键时，另一个不能重新注册——RegisterHotKey 会和自己撞车。"""
    application, fake, _notices = build_app(qt_app, tmp_path)

    assert application.apply_hotkeys("Alt+V", "Ctrl+Alt+R")

    assert set(fake.registered.values()) == {"Alt+V", "Ctrl+Alt+R"}
    assert fake.unregistered == [2], "只该注销被替换掉的那个"


def test_apply_hotkeys_rolls_back_when_second_conflicts(qt_app, tmp_path) -> None:
    """换到一半失败必须整体回滚，不能留下半新半旧的热键。"""
    application, fake, notices = build_app(qt_app, tmp_path, fail_on={"Ctrl+Alt+R"})

    assert application.apply_hotkeys("Ctrl+Alt+C", "Ctrl+Alt+R") is False

    assert set(fake.registered.values()) == {"Alt+V", "Alt+Shift+V"}, "必须保持原状"
    assert fake.unregistered == [3], "只该注销刚注册成功的那个新键"
    assert application._dispatch_id == 1 and application._resend_id == 2
    assert notices and notices[0][2].get("warning")
    assert "已保持原热键" in notices[0][1]


def test_apply_hotkeys_rolls_back_when_first_conflicts(qt_app, tmp_path) -> None:
    application, fake, notices = build_app(qt_app, tmp_path, fail_on={"Ctrl+Alt+C"})

    assert application.apply_hotkeys("Ctrl+Alt+C", "Ctrl+Alt+R") is False

    assert set(fake.registered.values()) == {"Alt+V", "Alt+Shift+V"}
    assert "热键未更改" in notices[0][0]


def test_apply_hotkeys_warns_when_it_cannot_persist(qt_app, tmp_path) -> None:
    """没配 config.yaml 时热键仍然立即生效，但必须说清"重启会回到默认值"。"""
    application = Application(qt_app)  # 不传 config -> 没有文件
    fake = FakeHotkeys()
    application.hotkeys = fake
    application._dispatch_id = fake.register("Alt+V")
    application._resend_id = fake.register("Alt+Shift+V")
    notices: list[tuple] = []
    application.tray.notify = lambda title, message, **kw: notices.append((title, message, kw))

    assert application.apply_hotkeys("Ctrl+Alt+C", "Ctrl+Alt+R")

    assert set(fake.registered.values()) == {"Ctrl+Alt+C", "Ctrl+Alt+R"}
    assert notices and notices[0][2].get("warning"), "没能落盘必须提示"
