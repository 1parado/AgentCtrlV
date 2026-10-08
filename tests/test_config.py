"""M4/T4.1：应用级配置（app 段）——默认值、正常加载、写错了怎么办。"""

from __future__ import annotations

from pathlib import Path

from src.core.config import DEFAULT_CONFIG_PATH, AppConfig, load_config
from tests.config_helpers import VALID, RecordingLogger, write


# ---------- 默认值 ----------


def test_missing_file_uses_defaults_and_says_so(tmp_path) -> None:
    log = RecordingLogger()

    config = load_config(tmp_path / "nope.yaml", logger=log)

    assert config.app.hotkeys.dispatch_clipboard == "Alt+V"
    assert config.app.behavior.multi_target_delay == 800
    assert config.app.behavior.multi_target_max == 5
    assert config.app.behavior.restore_clipboard is True
    assert config.source is None
    assert log.at("info"), "用了默认值必须说出来，不能静默"


def test_default_path_matches_documented_location() -> None:
    assert DEFAULT_CONFIG_PATH == Path("config/config.yaml")


def test_defaults_match_config_schema() -> None:
    """默认值必须与 CONFIG_SCHEMA.md 的表格一致——那是契约。"""
    app = AppConfig()

    assert app.hotkeys.dispatch_clipboard == "Alt+V"
    assert app.hotkeys.dispatch_screenshot == "Alt+S"
    assert app.hotkeys.resend_last == "Alt+Shift+V"
    assert app.triggers.listen_screenshot_dir is False
    assert app.behavior.restore_clipboard is True
    assert app.behavior.auto_enter is False
    assert app.behavior.multi_target_delay == 800
    assert app.behavior.multi_target_max == 5


# ---------- 正常加载 ----------


def test_loads_app_section(tmp_path) -> None:
    config = load_config(write(tmp_path, VALID))

    assert config.app.hotkeys.dispatch_clipboard == "Ctrl+Alt+C"
    assert config.app.hotkeys.resend_last == "Ctrl+Alt+R"
    assert config.app.behavior.multi_target_delay == 500
    assert config.app.behavior.multi_target_max == 3
    assert config.app.behavior.restore_clipboard is False
    assert config.problems == ()


def test_partial_config_keeps_other_defaults(tmp_path) -> None:
    """只写想改的字段，其余照默认——这是配置化最基本的期望。"""
    config = load_config(write(tmp_path, 'app:\n  behavior:\n    multi_target_max: 2\n'))

    assert config.app.behavior.multi_target_max == 2
    assert config.app.behavior.multi_target_delay == 800
    assert config.app.hotkeys.dispatch_clipboard == "Alt+V"


def test_empty_file_is_fine(tmp_path) -> None:
    config = load_config(write(tmp_path, ""))

    assert config.app.behavior.multi_target_delay == 800
    assert config.problems == ()


# ---------- 坏配置 ----------


def test_malformed_yaml_falls_back_and_reports(tmp_path) -> None:
    log = RecordingLogger()

    config = load_config(write(tmp_path, "app: [unclosed\n"), logger=log)

    assert config.app.behavior.multi_target_delay == 800
    assert config.problems, "解析失败必须留下痕迹"
    assert log.at("error")


def test_non_mapping_top_level_is_reported(tmp_path) -> None:
    config = load_config(write(tmp_path, "- just\n- a list\n"))

    assert config.problems
    assert config.app.behavior.multi_target_delay == 800


def test_invalid_behavior_type_falls_back(tmp_path) -> None:
    config = load_config(write(tmp_path, 'app:\n  behavior:\n    multi_target_delay: "很久"\n'))

    assert config.app.behavior.multi_target_delay == 800
    assert config.problems


def test_invalid_hotkey_is_reported_but_others_kept(tmp_path) -> None:
    text = 'app:\n  hotkeys:\n    dispatch_clipboard: "这不是热键"\n    resend_last: "Ctrl+Alt+R"\n'
    config = load_config(write(tmp_path, text))

    assert any("dispatch_clipboard" in problem for problem in config.problems)
    assert config.app.hotkeys.resend_last == "Ctrl+Alt+R"


def test_reserved_hotkey_is_reported(tmp_path) -> None:
    """CONFIG_SCHEMA 约束：禁止系统保留组合。"""
    text = 'app:\n  hotkeys:\n    dispatch_clipboard: "Ctrl+Alt+Del"\n'
    config = load_config(write(tmp_path, text))

    assert any("dispatch_clipboard" in problem for problem in config.problems)


def test_auto_enter_is_refused_loudly(tmp_path) -> None:
    """AGENTS.md v0.1 禁止事项第一条：写了也不能生效，但不能不说。"""
    log = RecordingLogger()
    config = load_config(write(tmp_path, "app:\n  behavior:\n    auto_enter: true\n"), logger=log)

    assert config.app.behavior.auto_enter is False
    assert any("自动回车" in problem for problem in config.problems)
    assert log.at("error")


def test_multi_target_max_below_one_is_clamped(tmp_path) -> None:
    config = load_config(write(tmp_path, "app:\n  behavior:\n    multi_target_max: 0\n"))

    assert config.app.behavior.multi_target_max == 1
    assert config.problems


def test_triggers_enabled_admits_not_implemented(tmp_path) -> None:
    """解析了但没实现，必须说出来，不能让人以为已经在监听了。"""
    config = load_config(write(tmp_path, "app:\n  triggers:\n    listen_clipboard_image: true\n"))

    assert any("监听" in problem for problem in config.problems)
