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


#: 建模了、但在 src/ 里**找不到属性访问**的字段，以及原因。
#:
#: 新增配置字段时必须二选一：真的接上线（于是能搜到 `.字段名`），
#: 或者登记到这里并写清为什么。**这个清单就是"哪些配置目前是装饰品"的答案。**
NOT_READ_DIRECTLY = {
    "window.multi_instance": "多实例选择（first/all）尚未实现；非默认值会警告",
    "locate.click_coords": "click 定位尚未实现，随 locate.method 一起警告",
    "locate.uia_control": "uia 定位尚未实现，随 locate.method 一起警告",
    "locate.uia_search_depth": "uia 定位尚未实现，随 locate.method 一起警告",
    "locate.uia_timeout": "uia 定位尚未实现，随 locate.method 一起警告",
    "inject.paste_delay": "经 paste_delay_ms 属性读取（属性名带 _ms 后缀）",
    "inject.render_delay": "经 render_delay_ms 属性读取（属性名带 _ms 后缀）",
    "hot_start.clear_input": "会话策略尚未实现；配 true 时加载会警告",
    "hot_start.reuse_session": "会话策略尚未实现；配 false 时加载会警告",
    #: type 只在**模型内部**被 _apply_type_defaults 用来补 supported_payloads 默认值。
    #: 运行时不按 type 分支（目标定位看的是 cli_match），所以搜不到属性访问。
    "type": "仅用于在模型层给 supported_payloads 补默认值；运行时不按 type 分支",
}


def _behaviour_sources() -> list[str]:
    """只搜"真正驱动行为"的代码。

    刻意排除两个文件：
    - gents.py：字段当然会出现在模型定义里
    - gent_warnings.py：那里提到字段，恰恰是为了说**它没被用上**；
      把它算成"已读取"，等于让检查自己骗自己（这个坑当场就踩到了）
    """
    excluded = {"agents.py", "agent_warnings.py"}
    return [
        path.read_text(encoding="utf-8")
        for path in (REPO / "src").rglob("*.py")
        if path.name not in excluded
    ]


def test_every_modeled_field_is_read_or_declared_unread() -> None:
    """防止「schema 里写了、代码不读、也不吭声」再次发生。

    这条检查是为 `locate` / `hot_start` / `permissions` / `ui_ready_timeout`
    四处同类问题加的——它们都是靠验收时偶然撞见才发现的，太慢。

    判定方式：该字段名在 `src/`（除模型定义本身）里能不能找到 `.字段名` 的属性访问。
    找不到就必须登记在 NOT_READ_DIRECTLY 里。
    """
    haystack = "\n".join(_behaviour_sources())

    unexplained: list[str] = []
    for dotted in sorted(_modeled_fields()):
        short = dotted.split(".")[-1]
        # 段容器（window / locate / inject …）本身不被读取，读的是它的子字段
        if "." not in dotted and short in SECTION_MODELS:
            continue
        if re.search(rf"\.{re.escape(short)}\b", haystack):
            continue
        if dotted in NOT_READ_DIRECTLY:
            continue
        unexplained.append(dotted)

    assert unexplained == [], (
        "这些字段建模了却在 src/ 里找不到任何使用，也没登记原因："
        f"{unexplained}\n要么接上线，要么加到 NOT_READ_DIRECTLY 并写清理由。"
    )


def test_not_read_directly_entries_are_still_accurate() -> None:
    """登记表不能腐烂：已经接上线的字段要把它从清单里删掉。"""
    haystack = "\n".join(_behaviour_sources())
    modeled = _modeled_fields()

    stale = []
    for dotted in NOT_READ_DIRECTLY:
        assert dotted in modeled, f"{dotted} 已不在模型里，登记表过期"
        short = dotted.split(".")[-1]
        if re.search(rf"\.{re.escape(short)}\b", haystack):
            stale.append(dotted)

    assert stale == [], f"这些字段已经接上线了，请从 NOT_READ_DIRECTLY 移除：{stale}"


# ---------- 2b. schema 承诺的类型默认值 ----------


def test_cli_type_defaults_to_text_only() -> None:
    """CONFIG_SCHEMA 约束：CLI 类型 Agent 默认 `supported_payloads: ["text"]`。

    这条默认值**曾经是死代码**：字段自带 default_factory(["image","text"])，
    校验器永远看不到空值，所以 `type: cli` 配了等于没配。
    是上面"type 没被读取"那条提示把它逼出来的——我一度把它当误报登记进例外表，
    结果它是对的。
    """
    cli = AgentSpec(id="c", name="C", type="cli", process="C.exe")
    gui = AgentSpec(id="g", name="G", type="gui", process="G.exe")
    explicit = AgentSpec(
        id="e", name="E", type="cli", process="E.exe", supported_payloads=["image"]
    )

    assert cli.supported_payloads == ["text"]
    assert not cli.supports("image"), "CLI 默认不该收图片，否则会往终端里粘图"
    assert gui.supported_payloads == ["image", "text"]
    assert explicit.supported_payloads == ["image"], "显式写了就尊重显式"


# ---------- 3. 尚未实现的项必须出声 ----------


class RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def __getattr__(self, level: str):
        def handler(message, *args, **kwargs):
            if level in ("warning", "error"):
                self.warnings.append(message % args if args else str(message))

        return handler


#: (说明, YAML 片段, 期望出现在警告里的关键词)
#:
#: 新增一个"配置里能写但 v0.1 不生效"的字段时，**必须**加到这里并让它出声。
NOT_IMPLEMENTED = [
    ("locate.method 非 blind", 'locate:\n  method: "uia"\n', "locate.method"),
    ("inject.method 非 clipboard", 'inject:\n  method: "uia"\n', "inject.method"),
    ("inject.auto_enter", "inject:\n  auto_enter: true\n", "auto_enter"),
    ("hot_start.reuse_session=false", "hot_start:\n  reuse_session: false\n", "reuse_session"),
    ("hot_start.clear_input=true", "hot_start:\n  clear_input: true\n", "clear_input"),
    ("permissions=elevated", 'permissions: "elevated"\n', "permissions"),
]


@pytest.mark.parametrize(
    "label,snippet,keyword", NOT_IMPLEMENTED, ids=[row[0] for row in NOT_IMPLEMENTED]
)
def test_not_implemented_option_warns(tmp_path, label: str, snippet: str, keyword: str) -> None:
    path = tmp_path / "demo.yaml"
    path.write_text(f'id: demo\nname: "D"\nprocess: "D.exe"\n{snippet}', encoding="utf-8")
    log = RecordingLogger()

    load_agents(tmp_path, logger=log)

    assert any(keyword in text for text in log.warnings), (
        f"{label} 配置了却不生效，而且没有任何警告——这正是要防的静默失败"
    )


def test_default_config_never_warns(tmp_path) -> None:
    """反过来：默认值不该吵人，否则警告会被用户当噪音忽略。"""
    path = tmp_path / "demo.yaml"
    path.write_text('id: demo\nname: "D"\nprocess: "D.exe"\n', encoding="utf-8")
    log = RecordingLogger()

    load_agents(tmp_path, logger=log)

    assert log.warnings == []


def test_shipped_configs_are_honest() -> None:
    """随仓库下发的配置描述的是**实际行为**，不该触发任何"该项不生效"。"""
    log = RecordingLogger()

    load_agents(REPO / "config" / "agents", logger=log)

    assert log.warnings == []
