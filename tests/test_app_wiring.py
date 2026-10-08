"""M4/T4.1：配置真的驱动了装配结果，而不只是被解析了。

解析出来却没接上线，是配置化最典型的"看起来做完了"的假象。
"""

from __future__ import annotations

from pathlib import Path

from src.core.config import load_config
from src.main import Application


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_behavior_reaches_menu_and_controller(qt_app, tmp_path) -> None:
    config = load_config(
        write(
            tmp_path,
            """
app:
  behavior:
    multi_target_delay: 500
    multi_target_max: 3
    restore_clipboard: false
""",
        )
    )

    application = Application(qt_app, config=config)

    assert application.menu._max_selection == 3
    assert application.controller._interval_s == 0.5
    assert application.controller._restore_clipboard is False


def test_hotkeys_reach_the_application(qt_app, tmp_path) -> None:
    config = load_config(
        write(
            tmp_path,
            """
app:
  hotkeys:
    dispatch_clipboard: "Ctrl+Alt+C"
    resend_last: "Ctrl+Alt+R"
""",
        )
    )

    application = Application(qt_app, config=config)

    assert application.config.app.hotkeys.dispatch_clipboard == "Ctrl+Alt+C"
    assert application.config.app.hotkeys.resend_last == "Ctrl+Alt+R"


def test_menu_selection_cap_follows_config(qt_app, tmp_path) -> None:
    """上限从配置来，菜单就得按它拦——两条路径不能各说各话。"""
    config = load_config(write(tmp_path, "app:\n  behavior:\n    multi_target_max: 2\n"))
    application = Application(qt_app, config=config)

    application.menu._selected = {"a", "b"}
    application.menu._toggle("c")

    assert len(application.menu._selected) == 2
    assert "最多选 2 个" in application.menu._hint


def test_agents_come_from_config_directory(qt_app) -> None:
    application = Application(qt_app)

    assert len(application.agents) >= 8
