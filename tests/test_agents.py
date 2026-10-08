"""Agent 配置加载测试（config/agents/*.yaml → AgentSpec）。

直接验证"哪些 Agent 进菜单"这条用户可控的链路，包括坏配置的行为。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.agents import (
    BUILTIN_AGENTS,
    AgentSpec,
    find_agent,
    load_agents,
)

REPO_AGENTS_DIR = Path(__file__).resolve().parents[1] / "config" / "agents"

VALID = """
id: demo
name: "Demo"
type: gui
process: "Demo.exe"
window:
  title_pattern: "Demo*"
inject:
  paste_delay: 123
  render_delay: 456
supported_payloads: ["text"]
"""


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------- 仓库自带配置 ----------


def test_repo_agent_configs_all_load() -> None:
    agents = load_agents(REPO_AGENTS_DIR)

    assert len(agents) >= 8
    ids = [a.id for a in agents]
    assert len(ids) == len(set(ids)), "配置里出现重复 id"


def test_repo_configs_cover_real_local_agents() -> None:
    """回归：菜单里必须能看到本机真实存在的 Agent，而不是 PRD 里那三个。"""
    agents = load_agents(REPO_AGENTS_DIR)
    ids = {a.id for a in agents}

    assert {"zcode", "workbuddy", "claude-code"} <= ids


def test_repo_configs_respect_enabled_flag() -> None:
    """本机没装的 ChatGPT/Cursor 用 enabled: false 挡在菜单外。"""
    agents = load_agents(REPO_AGENTS_DIR)
    ids = {a.id for a in agents}

    assert "chatgpt-desktop" not in ids
    assert "cursor" not in ids


def test_repo_cli_agents_declare_a_cli_match() -> None:
    """CLI Agent 必须能指出"终端里到底在跑什么"，否则定位只能靠猜标题。"""
    for agent in load_agents(REPO_AGENTS_DIR):
        if agent.type == "cli" and agent.id != "windows-terminal":
            assert agent.cli_match, f"{agent.id} 缺少 cli_match"
            assert agent.supported_payloads == ["text"], f"{agent.id} 的 CLI 不该收图片"


# ---------- 加载行为 ----------


def test_loads_nested_fields(tmp_path) -> None:
    _write(tmp_path, "demo.yaml", VALID)

    agent = load_agents(tmp_path)[0]

    assert agent.title_pattern == "Demo*"
    assert agent.paste_delay_ms == 123
    assert agent.render_delay_ms == 456
    assert agent.supports("text")
    assert not agent.supports("image")


def test_enabled_false_is_skipped(tmp_path) -> None:
    _write(tmp_path, "a.yaml", VALID)
    _write(tmp_path, "b.yaml", VALID.replace("id: demo", "id: hidden").replace(
        "type: gui", "type: gui\nenabled: false"
    ))

    ids = {a.id for a in load_agents(tmp_path)}

    assert ids == {"demo"}


def test_invalid_file_is_reported_and_others_still_load(tmp_path) -> None:
    _write(tmp_path, "good.yaml", VALID)
    _write(tmp_path, "broken.yaml", "id: broken\nname: 'X'\n")  # 缺 process

    agents = load_agents(tmp_path)

    assert [a.id for a in agents] == ["demo"]


def test_malformed_yaml_does_not_kill_loading(tmp_path) -> None:
    _write(tmp_path, "good.yaml", VALID)
    _write(tmp_path, "junk.yaml", "id: [unclosed\n")

    assert [a.id for a in load_agents(tmp_path)] == ["demo"]


def test_duplicate_id_keeps_first(tmp_path) -> None:
    _write(tmp_path, "a.yaml", VALID)
    _write(tmp_path, "b.yaml", VALID.replace('name: "Demo"', 'name: "Demo 2"'))

    agents = load_agents(tmp_path)

    assert len(agents) == 1
    assert agents[0].name == "Demo"


def test_unknown_fields_are_ignored_for_forward_compat(tmp_path) -> None:
    """现有 YAML 里有 locate/cold_start 等尚未消费的段，不该因此拒绝加载。"""
    extra = VALID + """
locate:
  method: "uia"
cold_start:
  new_session: true
permissions: "normal"
"""
    _write(tmp_path, "demo.yaml", extra)

    assert load_agents(tmp_path)[0].id == "demo"


def test_missing_directory_falls_back_to_builtin(tmp_path) -> None:
    agents = load_agents(tmp_path / "does-not-exist")

    assert agents == BUILTIN_AGENTS


def test_empty_directory_falls_back_to_builtin(tmp_path) -> None:
    assert load_agents(tmp_path) == BUILTIN_AGENTS


def test_auto_enter_field_is_parsed_but_defaults_off(tmp_path) -> None:
    """CONFIG_SCHEMA 有 auto_enter，但 v0.1 禁止自动回车，默认必须是 false。"""
    _write(tmp_path, "demo.yaml", VALID)

    assert load_agents(tmp_path)[0].inject.auto_enter is False


# ---------- 查询 ----------


def test_find_agent_by_id() -> None:
    agents = load_agents(REPO_AGENTS_DIR)

    found = find_agent("zcode", agents)

    assert found is not None
    assert found.name == "ZCode"
    assert find_agent("nope", agents) is None


def test_builtin_fallback_is_text_only_terminal() -> None:
    assert len(BUILTIN_AGENTS) == 1
    assert BUILTIN_AGENTS[0].supported_payloads == ["text"]


@pytest.mark.parametrize("bad", ["image", "video"])
def test_supports_is_strict(tmp_path, bad: str) -> None:
    _write(tmp_path, "demo.yaml", VALID)

    assert not load_agents(tmp_path)[0].supports(bad)


def test_spec_accepts_both_payload_kinds() -> None:
    spec = AgentSpec(id="x", name="X", process="X.exe")

    assert spec.supports("image") and spec.supports("text")
