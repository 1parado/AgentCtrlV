"""M4/T4.3：Agent 从配置来——内联列表、样例可用性、无效选项要出声。"""

from __future__ import annotations

import pytest

from src.core.agents import load_agents
from src.core.config import load_config
from tests.config_helpers import EXAMPLE, REPO, RecordingLogger, write


# ---------- 内联 agents ----------


INLINE = """
app:
  behavior:
    multi_target_max: 4
agents:
  - id: demo
    name: "Demo"
    type: gui
    process: "Demo.exe"
    window:
      title_pattern: "Demo*"
"""


def test_inline_agents_are_loaded(tmp_path) -> None:
    config = load_config(write(tmp_path, INLINE))

    assert [a.id for a in config.inline_agents] == ["demo"]
    assert config.inline_agents[0].name == "Demo"


def test_inline_agent_without_title_pattern_uses_default(tmp_path) -> None:
    """window.title_pattern 在 schema 里是必填，但 AgentSpec 有默认值兜底。

    这里锁定当前行为：不写就是 "*"，而不是加载失败。
    """
    text = INLINE.replace('    window:\n      title_pattern: "Demo*"\n', "")
    config = load_config(write(tmp_path, text))

    assert config.inline_agents[0].title_pattern == "*"


def test_broken_inline_agent_does_not_kill_the_rest(tmp_path) -> None:
    text = """
app:
  behavior:
    multi_target_max: 4
agents:
  - id: bad
    name: "Bad"
  - id: good
    name: "Good"
    process: "Good.exe"
"""
    config = load_config(write(tmp_path, text))

    assert [a.id for a in config.inline_agents] == ["good"]
    assert config.problems
    # app 段必须活着——坏掉的 agent 不该带走热键与行为配置
    assert config.app.behavior.multi_target_max == 4


def test_duplicate_inline_id_keeps_first(tmp_path) -> None:
    text = """
agents:
  - id: demo
    name: "第一"
    process: "A.exe"
  - id: demo
    name: "第二"
    process: "B.exe"
"""
    config = load_config(write(tmp_path, text))

    assert [a.name for a in config.inline_agents] == ["第一"]
    assert any("重复" in problem for problem in config.problems)


def test_inline_agents_not_a_list_is_reported(tmp_path) -> None:
    config = load_config(write(tmp_path, "agents:\n  demo:\n    name: x\n"))

    assert config.inline_agents == ()
    assert config.problems


def test_disabled_inline_agent_is_skipped(tmp_path) -> None:
    text = """
agents:
  - id: off
    name: "Off"
    enabled: false
    process: "Off.exe"
"""
    assert load_config(write(tmp_path, text)).inline_agents == ()


# ---------- 随仓库下发的样例必须能用（T4.3 的承诺）----------


def test_shipped_example_config_loads_cleanly() -> None:
    """用户复制的就是这份。它必须能直接跑，且不该产生任何问题提示。"""
    config = load_config(EXAMPLE)

    assert config.problems == ()
    assert len(config.inline_agents) == 3
    assert {a.id for a in config.inline_agents} == {"chatgpt-desktop", "cursor", "windows-terminal"}


def test_shipped_example_agents_merge_with_agent_directory() -> None:
    """样例里的 3 个 Agent 会和 config/agents/ 合并，重复 id 只保留目录里的那份。"""
    config = load_config(EXAMPLE)
    log = RecordingLogger()

    agents = load_agents(REPO / "config" / "agents", extra=config.inline_agents, logger=log)

    ids = [a.id for a in agents]
    assert len(ids) == len(set(ids)), "合并后不能有重复 id"
    assert "windows-terminal" in ids
    assert log.at("error"), "windows-terminal 在两处都定义了，必须报告而不是默默取一个"


# ---------- 无效选项要说出来 ----------


def test_agent_auto_enter_true_is_warned(tmp_path) -> None:
    """Agent 级 inject.auto_enter 写了也不生效——沉默忽略是最糟的失败。"""
    path = tmp_path / "demo.yaml"
    path.write_text(
        'id: demo\nname: "D"\nprocess: "D.exe"\ninject:\n  auto_enter: true\n',
        encoding="utf-8",
    )
    log = RecordingLogger()

    load_agents(tmp_path, logger=log)

    assert any("auto_enter" in text for text in log.at("warning"))


def test_agent_non_clipboard_method_is_warned(tmp_path) -> None:
    path = tmp_path / "demo.yaml"
    path.write_text(
        'id: demo\nname: "D"\nprocess: "D.exe"\ninject:\n  method: "uia"\n',
        encoding="utf-8",
    )
    log = RecordingLogger()

    load_agents(tmp_path, logger=log)

    assert any("inject.method" in text for text in log.at("warning"))


@pytest.mark.parametrize("bad", [0, -3])
def test_non_positive_delay_is_clamped_by_injector_not_config(tmp_path, bad: int) -> None:
    """负数延迟交给注入器 max(0, ...) 兜底；配置层不做无谓的拒绝。"""
    path = tmp_path / "demo.yaml"
    path.write_text(
        f'id: demo\nname: "D"\nprocess: "D.exe"\ninject:\n  paste_delay: {bad}\n',
        encoding="utf-8",
    )

    assert load_agents(tmp_path)[0].paste_delay_ms == bad


def _one_agent(tmp_path, extra_yaml: str, log=None):
    path = tmp_path / "demo.yaml"
    path.write_text(f'id: demo\nname: "D"\nprocess: "D.exe"\n{extra_yaml}', encoding="utf-8")
    return load_agents(tmp_path, logger=log)


def test_locate_uia_is_warned(tmp_path) -> None:
    """locate 段以前连模型都没有、被静默丢弃。"""
    log = RecordingLogger()

    _one_agent(tmp_path, 'locate:\n  method: "uia"\n', log)

    assert any("locate.method" in text for text in log.at("warning"))


def test_hot_start_forced_new_session_is_warned(tmp_path) -> None:
    """会话策略尚未实现；配了就必须说，否则用户以为每次都会开新会话。"""
    log = RecordingLogger()

    _one_agent(tmp_path, "hot_start:\n  reuse_session: false\n", log)

    assert any("reuse_session" in text for text in log.at("warning"))


def test_hot_start_clear_input_is_warned(tmp_path) -> None:
    log = RecordingLogger()

    _one_agent(tmp_path, "hot_start:\n  clear_input: true\n", log)

    assert any("clear_input" in text for text in log.at("warning"))


def test_permissions_elevated_is_warned(tmp_path) -> None:
    """该字段不参与决策——是否提权是运行时实测的。"""
    log = RecordingLogger()

    _one_agent(tmp_path, 'permissions: "elevated"\n', log)

    assert any("permissions" in text for text in log.at("warning"))


def test_default_options_produce_no_warnings(tmp_path) -> None:
    """默认值不该吵人：只有"配了但不生效"才警告。"""
    log = RecordingLogger()

    _one_agent(tmp_path, "", log)

    assert log.at("warning") == []


def test_shipped_agent_configs_produce_no_warnings() -> None:
    """随仓库下发的配置必须描述**实际行为**，不能声明没实现的东西。

    这是一条回归：早先我给 6 个 Agent 都写了 locate.method: uia，
    结果每次启动都刷 6 条"该项不生效"的警告。
    """
    log = RecordingLogger()

    load_agents(REPO / "config" / "agents", logger=log)

    assert [text for text in log.at("warning")] == []

