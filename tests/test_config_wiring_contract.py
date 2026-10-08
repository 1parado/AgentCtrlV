"""配置**接线**契约：字段到底有没有真正驱动行为。

与 test_config_schema_contract.py 的分工：
- 那边管"文档 ⇄ 模型"是否一致（字段存不存在）
- 这边管"模型 ⇄ 行为"是否打通（字段有没有人读、未实现项会不会出声）

这条检查是为 locate / hot_start / permissions / ui_ready_timeout 四处同类问题加的。
"""

from __future__ import annotations

import re

import pytest

from src.core.agents import AgentSpec, load_agents
from tests.test_config_schema_contract import (
    REPO, SECTION_MODELS, _modeled_fields,
)


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
    - `agents.py`：字段当然会出现在模型定义里
    - `agent_warnings.py`：那里提到字段，恰恰是为了说**它没被用上**；
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
