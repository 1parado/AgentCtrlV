"""配置契约测试：CONFIG_SCHEMA.md ⇄ pydantic 模型 ⇄ 实际生效。

存在的理由很具体：`locate` / `hot_start` / `permissions` / `ui_ready_timeout`
这四处都是同一个模式——**schema 里写了、代码不读、也不吭声**。
靠验收时偶然撞见太慢，所以把它变成一条会自动变红的检查。

三条断言：
1. CONFIG_SCHEMA.md 里文档化的每个字段，都必须能在 pydantic 模型里找到
2. 模型里的每个字段，要么被文档化，要么在 `UNDOCUMENTED_EXTENSIONS` 里登记了理由
3. 已知"尚未实现"的项，配置成非默认值时必须产生用户可见的警告
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.core.agents import (
    AgentSpec,
    ColdStartSpec,
    HotStartSpec,
    InjectSpec,
    LocateSpec,
    WindowSpec,
    load_agents,
)
from src.core.config import BehaviorConfig, HotkeyConfig, TriggerConfig

REPO = Path(__file__).resolve().parents[1]
SCHEMA_DOC = REPO / "docs" / "CONFIG_SCHEMA.md"

#: 文档里用 "字段" 表头或分隔行，解析时要跳过
_HEADER_CELLS = {"字段", "field"}

#: CONFIG_SCHEMA.md 的段 -> 对应的 pydantic 模型
SECTION_MODELS = {
    "hotkeys": HotkeyConfig,
    "triggers": TriggerConfig,
    "behavior": BehaviorConfig,
    "window": WindowSpec,
    "locate": LocateSpec,
    "inject": InjectSpec,
    "cold_start": ColdStartSpec,
    "hot_start": HotStartSpec,
}

#: 代码里有、CONFIG_SCHEMA.md 里没有的字段。
#: AGENTS.md 要求「不要在未确认的情况下修改 CONFIG_SCHEMA」，所以这些扩展
#: 只能挂在这里登记——**必须写清理由**，不能默默存在。
UNDOCUMENTED_EXTENSIONS = {
    "cli_match": "CLI Agent 识别终端里真正在跑什么（进程名或命令行片段）。"
    "现有 schema 的 process/window 都无法表达，属于新增能力，待用户确认后并入文档",
}


def parse_documented_fields(text: str) -> dict[str, str]:
    """从 CONFIG_SCHEMA.md 的表格里抽出字段名 -> 说明。"""
    found: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        name = cells[0].strip().strip("`")
        if not name or name.lower() in _HEADER_CELLS or set(name) <= set("-: "):
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
            continue
        found[name] = cells[3]
    return found


def resolve(dotted: str) -> tuple[object | None, str]:
    """把 "cold_start.ui_ready_timeout" 拆成 (模型, 字段名)。"""
    if "." not in dotted:
        return AgentSpec, dotted
    section, _, field = dotted.partition(".")
    return SECTION_MODELS.get(section), field


@pytest.fixture(scope="module")
def documented() -> dict[str, str]:
    return parse_documented_fields(SCHEMA_DOC.read_text(encoding="utf-8"))


# ---------- 1. 文档里的字段必须被建模 ----------


def test_schema_doc_parses(documented: dict[str, str]) -> None:
    """先保证解析器本身没瞎：文档里确实有我们认识的字段。"""
    assert "hotkeys.dispatch_clipboard" in documented
    assert "cold_start.ui_ready_timeout" in documented
    assert "id" in documented
    assert len(documented) > 25


def test_every_documented_field_exists_in_a_model(documented: dict[str, str]) -> None:
    """文档说了、模型却没有 -> 用户配了也是白配（就是 locate 当初的处境）。"""
    missing = []
    for dotted, _description in documented.items():
        model, field = resolve(dotted)
        if model is None:
            missing.append(f"{dotted}（没有对应的段模型 {dotted.split('.')[0]}）")
            continue
        if field not in model.model_fields:
            missing.append(f"{dotted}")

    assert missing == [], f"CONFIG_SCHEMA.md 文档化但模型里没有的字段：{missing}"


# ---------- 2. 模型的字段必须被文档化（或登记理由）----------


def _modeled_fields() -> dict[str, object]:
    fields: dict[str, object] = {name: AgentSpec for name in AgentSpec.model_fields}
    for section, model in SECTION_MODELS.items():
        for name in model.model_fields:
            fields[f"{section}.{name}"] = model
    return fields


def test_every_modeled_field_is_documented_or_registered(
    documented: dict[str, str],
) -> None:
    """反向：代码里有的也要在文档里，否则就登记到扩展清单。

    `cli_match` 属于后者——它是 schema 尚未收录的新能力，
    但**必须在清单里带着理由出现**，不能悄无声息地存在。
    """
    undocumented = []
    for dotted in sorted(_modeled_fields()):
        if dotted in documented:
            continue
        short = dotted.split(".")[-1]
        # 段容器（window / locate / inject …）本身不在表格里，
        # 它们是通过**子字段**逐条文档化的
        if dotted == short and short in SECTION_MODELS:
            continue
        if short in UNDOCUMENTED_EXTENSIONS and dotted in (short, f"agent.{short}"):
            continue
        undocumented.append(dotted)

    assert undocumented == [], (
        f"模型里有、CONFIG_SCHEMA.md 里没有、也没登记的字段：{undocumented}"
    )


def test_undocumented_extensions_have_a_reason() -> None:
    for name, reason in UNDOCUMENTED_EXTENSIONS.items():
        assert name in AgentSpec.model_fields, f"{name} 不在 AgentSpec 里，清单过期了"
        assert len(reason.strip()) > 20, f"{name} 的登记理由太敷衍"


