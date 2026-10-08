"""Agent 定义与加载（对齐 CONFIG_SCHEMA.md）。

M2 起不再把 Agent 硬编码在代码里：Agent 列表来自 `config/agents/*.yaml`，
用 pydantic 强校验。这样做的直接原因是**菜单容量是有限的**——
环形菜单在 110px 半径下超过 8~10 项就会互相重叠，而一台机器上
可能同时装着十几个 Agent（GUI 的 + 终端里的 CLI 的）。
哪些进菜单是用户的选择，不是产品能替他决定的。

校验用 `extra="ignore"`：现有 YAML 里有 locate/inject/cold_start/hot_start
等尚未消费的段，M3/M4 会逐步用上，现在不该因为它们存在就拒绝加载。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.core.agent_warnings import warn_ineffective_options
from src.utils.logger import get_logger

AGENTS_DIR = Path("config/agents")

PayloadKind = Literal["image", "text"]


class WindowSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title_pattern: str = "*"
    multi_instance: str = "recent"


class LocateSpec(BaseModel):
    """怎么把焦点放进输入框（CONFIG_SCHEMA 的 `locate` 段）。

    M1–M4 只实现了 `blind`：激活窗口即认为焦点就位，直接盲粘。
    `uia` / `click` 尚未实现——但**不能静默忽略**，见 _warn_ineffective_options。
    """

    model_config = ConfigDict(extra="ignore")

    method: Literal["uia", "click", "blind"] = "blind"
    uia_control: str | None = None
    uia_search_depth: int = 10
    uia_timeout: int = 1000
    click_coords: list[int] | None = None


class InjectSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    method: str = "clipboard"
    paste_delay: int = 300
    render_delay: int = 500
    auto_enter: bool = False


class ColdStartSpec(BaseModel):
    """Agent 没在运行时怎么把它拉起来（T3.2 / T3.3）。

    字段与 CONFIG_SCHEMA.md 的 `cold_start` 段一一对应。
    """

    model_config = ConfigDict(extra="ignore")

    new_session: bool = False
    #: hotkey = 窗口就绪后发一次快捷键（如 Ctrl+N 开新会话）
    #: uia_button = 点界面上的"新会话"按钮（M3 尚未实现，会明确报错而不是静默跳过）
    #: none = 不处理新会话
    new_session_method: Literal["hotkey", "uia_button", "none"] = "hotkey"
    new_session_hotkey: str = "Ctrl+N"
    ready_timeout: int = 8000
    ui_ready_timeout: int = 3000


class HotStartSpec(BaseModel):
    """Agent 已经在跑时的会话策略（CONFIG_SCHEMA 的 `hot_start` 段）。

    M1–M5 都还没实现会话策略：热启动就是"复用当前的输入框"。
    `reuse_session: false`（每次强制新会话）与 `clear_input: true`（先清空输入框）
    都尚未实现——但**不能静默忽略**，见 _warn_ineffective_options。
    PRD 的说法是"冷启动可强制新会话 / 热启动复用当前"，这两项属于后续里程碑。
    """

    model_config = ConfigDict(extra="ignore")

    reuse_session: bool = True
    clear_input: bool = False


class AgentSpec(BaseModel):
    """一个注入目标。字段名与 CONFIG_SCHEMA.md 的 Agent 段一致。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    type: Literal["gui", "cli"] = "gui"
    enabled: bool = True
    process: str
    #: 仅 cli 用：识别"终端里真正在跑的那个 CLI"的片段。
    #: 可写进程名（claude.exe）或命令行片段（codex.js / @google/gemini-cli），
    #: 因为 npm 装的 CLI 多数以 node.exe 运行，只比进程名会有歧义。
    #: 注意：这个字段是 CONFIG_SCHEMA.md 尚未收录的扩展，需要时补进文档。
    cli_match: str | None = None
    launch: str = ""
    window: WindowSpec = Field(default_factory=WindowSpec)
    locate: LocateSpec = Field(default_factory=LocateSpec)
    inject: InjectSpec = Field(default_factory=InjectSpec)
    cold_start: ColdStartSpec = Field(default_factory=ColdStartSpec)
    hot_start: HotStartSpec = Field(default_factory=HotStartSpec)
    #: CONFIG_SCHEMA 的声明项。**不参与决策**：是否提权是运行时按进程完整性级别
    #: 实测判断的（见 permissions.py），配置里写什么都不会改变这个事实。
    permissions: Literal["normal", "elevated"] = "normal"
    #: 默认留空表示"没写"，由 _apply_type_defaults 按 type 补。
    #: **不能**在这里直接给 ["image","text"]——那样校验器永远看不到空值，
    #: `type: cli` 的默认约束就成了死代码。本项目的配置契约检查正是这样抓到的：
    #: 它说 `type` 没被读取，我当时把它当成误报登记进例外表，结果它是对的。
    supported_payloads: list[PayloadKind] = Field(default_factory=list)
    icon: str | None = None

    @property
    def can_launch(self) -> bool:
        """配了 launch 才谈得上冷启动。

        注意：GUI Agent 可以靠 shell:AppsFolder / exe 路径启动；
        寄生在终端里的 CLI Agent 不行（我们不会替用户开终端跑命令），
        所以它们的 launch 留空，冷启动会自动跳过并说明原因。
        """
        return bool(self.launch.strip())

    @model_validator(mode="after")
    def _apply_type_defaults(self) -> "AgentSpec":
        """CONFIG_SCHEMA 约束：CLI 类型 Agent 默认只收文本。

        没写 `supported_payloads` 时按 `type` 补默认值。不补的话
        `type: cli` 就只是个装饰字段——写了不影响任何行为，
        而文档却承诺了这条默认值。
        """
        if not self.supported_payloads:
            self.supported_payloads = ["text"] if self.type == "cli" else ["image", "text"]
        return self

    # --- 便捷读取，避免调用方到处走 window./inject. ---
    @property
    def title_pattern(self) -> str:
        return self.window.title_pattern

    @property
    def paste_delay_ms(self) -> int:
        return self.inject.paste_delay

    @property
    def render_delay_ms(self) -> int:
        return self.inject.render_delay

    def supports(self, kind: str) -> bool:
        return kind in self.supported_payloads


class AgentConfigError(ValueError):
    """Agent 配置无法加载。"""


#: 兜底内置（config/agents 缺失时用），与 PRD v0.1 一致
BUILTIN_AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        id="windows-terminal",
        name="Windows Terminal",
        type="gui",
        process="WindowsTerminal.exe",
        window=WindowSpec(title_pattern="*"),
        inject=InjectSpec(paste_delay=200, render_delay=300),
        supported_payloads=["text"],
    ),
)


def _load_directory(base: Path, log) -> list[AgentSpec]:
    """读 config/agents/*.yaml。坏掉一个文件不带走其余。"""
    if not base.is_dir():
        log.warning("Agent 配置目录 %s 不存在", base)
        return []

    specs: list[AgentSpec] = []
    seen: dict[str, Path] = {}
    for path in sorted(base.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            spec = AgentSpec.model_validate(raw)
        except (yaml.YAMLError, ValidationError, OSError) as exc:
            log.error("Agent 配置 %s 加载失败，已跳过：%s", path.name, exc)
            continue

        if spec.id in seen:
            log.error(
                "Agent id 重复：%s 同时出现在 %s 与 %s，已跳过后一个",
                spec.id,
                seen[spec.id].name,
                path.name,
            )
            continue
        seen[spec.id] = path

        if not spec.enabled:
            log.debug("Agent %s 已禁用（enabled: false），不进菜单", spec.id)
            continue
        specs.append(spec)
    return specs


def load_agents(
    directory: Path | str = AGENTS_DIR,
    *,
    extra: tuple[AgentSpec, ...] = (),
    logger=None,
) -> tuple[AgentSpec, ...]:
    """汇总所有 Agent：`config/agents/*.yaml` + `config.yaml` 里的内联 `agents:`。

    - 目录不存在或没有任何可用条目 → 退回内置，并明确告知（不静默）
    - 单个文件非法 → 记 error 并跳过该文件，其余照常加载
    - `enabled: false` → 跳过（用户用它控制菜单里出现哪些）
    - 两种来源出现同一个 id → 保留 config/agents/ 里的定义并记 error
    """
    log = logger or get_logger(__name__)
    specs = _load_directory(Path(directory), log)

    seen = {spec.id for spec in specs}
    for spec in extra:
        if spec.id in seen:
            log.error(
                "Agent id 重复：%s（来自 config.yaml 的 agents 列表）已被 "
                "config/agents/ 里的定义占用，已跳过",
                spec.id,
            )
            continue
        if not spec.enabled:
            log.debug("内联 Agent %s 已禁用，不进菜单", spec.id)
            continue
        seen.add(spec.id)
        specs.append(spec)

    if not specs:
        log.error("没有可用的 Agent 配置（%s 与 config.yaml 都是空的），改用内置", directory)
        return BUILTIN_AGENTS

    warn_ineffective_options(specs, log)
    log.info("已加载 %d 个 Agent：%s", len(specs), "、".join(s.name for s in specs))
    return tuple(specs)


def find_agent(agent_id: str, agents: tuple[AgentSpec, ...] | list[AgentSpec]) -> AgentSpec | None:
    for agent in agents:
        if agent.id == agent_id:
            return agent
    return None
