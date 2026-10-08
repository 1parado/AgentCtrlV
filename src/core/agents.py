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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.utils.logger import get_logger

AGENTS_DIR = Path("config/agents")

PayloadKind = Literal["image", "text"]


class WindowSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title_pattern: str = "*"
    multi_instance: str = "recent"


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
    inject: InjectSpec = Field(default_factory=InjectSpec)
    cold_start: ColdStartSpec = Field(default_factory=ColdStartSpec)
    supported_payloads: list[PayloadKind] = Field(default_factory=lambda: ["image", "text"])
    icon: str | None = None

    @property
    def can_launch(self) -> bool:
        """配了 launch 才谈得上冷启动。

        注意：GUI Agent 可以靠 shell:AppsFolder / exe 路径启动；
        寄生在终端里的 CLI Agent 不行（我们不会替用户开终端跑命令），
        所以它们的 launch 留空，冷启动会自动跳过并说明原因。
        """
        return bool(self.launch.strip())

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


def load_agents(directory: Path | str = AGENTS_DIR, *, logger=None) -> tuple[AgentSpec, ...]:
    """加载 `config/agents/*.yaml`。

    - 目录不存在或没有任何可用条目 → 退回内置，并明确告知（不静默）
    - 单个文件非法 → 记 error 并跳过该文件，其余照常加载
    - `enabled: false` → 跳过（用户用它控制菜单里出现哪些）
    """
    log = logger or get_logger(__name__)
    base = Path(directory)
    if not base.is_dir():
        log.warning("Agent 配置目录 %s 不存在，改用内置 %d 个 Agent", base, len(BUILTIN_AGENTS))
        return BUILTIN_AGENTS

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

    if not specs:
        log.error("%s 里没有可用的 Agent 配置，改用内置", base)
        return BUILTIN_AGENTS

    log.info("已加载 %d 个 Agent：%s", len(specs), "、".join(s.name for s in specs))
    return tuple(specs)


def find_agent(agent_id: str, agents: tuple[AgentSpec, ...] | list[AgentSpec]) -> AgentSpec | None:
    for agent in agents:
        if agent.id == agent_id:
            return agent
    return None
