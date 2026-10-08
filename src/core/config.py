"""应用级配置（CONFIG_SCHEMA 的 `app:` 段）。

Agent 的定义住在 `config/agents/*.yaml`；这里只管"应用怎么跑"：
热键、监听开关、以及行为开关（是否恢复剪贴板、多目标间隔与上限）。

设计原则与 Agent 配置一致：
- 缺文件 -> 用默认值，并**明确说明**用了默认值（不是静默）
- 文件存在但字段非法 -> 记 error 并退回该字段的默认值，不拦住程序启动
  （托盘工具因为一个手写错字就起不来，比带默认值启动 + 大声报错更糟）
- v0.1 明令禁止的东西（自动回车）即便配置里写了也不生效，并明确告知
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.core.agents import AgentSpec
from src.core.hotkey import HotkeyError, parse_hotkey
from src.utils.logger import get_logger

DEFAULT_CONFIG_PATH = Path("config/config.yaml")


class HotkeyConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dispatch_clipboard: str = "Alt+V"
    dispatch_screenshot: str = "Alt+S"
    resend_last: str = "Alt+Shift+V"


class TriggerConfig(BaseModel):
    """监听类触发。M4 只负责解析与校验，实际监听属于后续里程碑。"""

    model_config = ConfigDict(extra="ignore")

    listen_screenshot_dir: bool = False
    listen_clipboard_image: bool = False
    listen_clipboard_text: bool = False

    @property
    def any_enabled(self) -> bool:
        return (
            self.listen_screenshot_dir
            or self.listen_clipboard_image
            or self.listen_clipboard_text
        )


class BehaviorConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    restore_clipboard: bool = True
    #: v0.1 禁止自动回车（AGENTS.md）。配置里写 true 也不会生效，见 load_config。
    auto_enter: bool = False
    multi_target_delay: int = 800
    multi_target_max: int = 5


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hotkeys: HotkeyConfig = Field(default_factory=HotkeyConfig)
    triggers: TriggerConfig = Field(default_factory=TriggerConfig)
    behavior: BehaviorConfig = Field(default_factory=BehaviorConfig)


@dataclass(frozen=True)
class Config:
    """一次加载的完整结果：应用配置 + config.yaml 里内联的 Agent。"""

    app: AppConfig = field(default_factory=AppConfig)
    inline_agents: tuple[AgentSpec, ...] = ()
    #: 加载过程中发现的问题，供 --check 直接展示（不静默）
    problems: tuple[str, ...] = ()
    source: Path | None = None


def save_hotkeys(
    hotkeys: HotkeyConfig,
    path: Path | str = DEFAULT_CONFIG_PATH,
    *,
    logger=None,
) -> tuple[bool, str]:
    """把热键写回 config.yaml。返回 (是否写入, 说明)。

    **刻意做得很保守**：只对已存在的 `app.hotkeys:` 块做逐行替换，
    不重新序列化整个文件——那样会把用户的注释和排版全部抹掉。

    找不到锚点时**拒绝写入**并说明原因，而不是硬塞一个 `app:` 块进去：
    文件里已经有一个 `app:` 时再追加一个会造成重复键，把配置写坏。
    热键仍然会在本次运行内生效（调用方照常 replace），只是没能落盘。
    """
    log = logger or get_logger(__name__)
    target = Path(path)
    if not target.is_file():
        return False, f"{target} 不存在（本次热键已生效，重启后会回到默认值）"

    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        log.error("读取 %s 失败：%s", target, exc)
        return False, f"读取 {target} 失败：{exc}"

    if not re.search(r"^\s*hotkeys:\s*$", text, re.M):
        return False, f"{target} 里没有 app.hotkeys 段，未改动文件（请手动添加后重试）"

    updated, changes = text, 0
    for key, value in (
        ("dispatch_clipboard", hotkeys.dispatch_clipboard),
        ("resend_last", hotkeys.resend_last),
    ):
        pattern = re.compile(rf"^(\s*{key}:\s*).*$", re.M)
        if not pattern.search(updated):
            continue
        # 用 lambda 替换：re.sub 会把替换串里的反斜杠当转义
        updated = pattern.sub(lambda m, v=value: m.group(1) + f'"{v}"', updated)
        changes += 1

    if changes == 0:
        return False, f"{target} 的 hotkeys 段里没有可替换的键，未改动文件"
    if updated == text:
        return True, f"{target} 无需改动（值相同）"

    try:
        target.write_text(updated, encoding="utf-8")
    except OSError as exc:
        log.error("写入 %s 失败：%s", target, exc)
        return False, f"写入 {target} 失败：{exc}"

    log.info("已把热键写回 %s（%d 处）", target, changes)
    return True, f"已写入 {target}"


def _read_yaml(path: Path, log) -> dict[str, Any] | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        log.error("配置文件 %s 读取失败：%s", path, exc)
        return None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        log.error("配置文件 %s 顶层必须是映射，实际是 %s", path, type(raw).__name__)
        return None
    return raw


def _validate_hotkeys(config: HotkeyConfig, log) -> tuple[HotkeyConfig, list[str]]:
    """热键必须能被解析，且不能是系统保留组合（CONFIG_SCHEMA 约束）。"""
    problems: list[str] = []
    for field_name in ("dispatch_clipboard", "dispatch_screenshot", "resend_last"):
        value = getattr(config, field_name)
        try:
            parse_hotkey(value)
        except HotkeyError as exc:
            problems.append(f"hotkeys.{field_name}={value!r} 不可用：{exc}")
            log.error("配置里的热键 %s 不可用：%s", value, exc)
    return config, problems


def _inline_agents(raw: Any, log) -> tuple[tuple[AgentSpec, ...], list[str]]:
    """解析 config.yaml 里的 `agents:` 列表。

    逐个校验：坏掉一个不该带走其余，也不该带走 app 段。
    """
    if raw in (None, []):
        return (), []
    if not isinstance(raw, list):
        message = f"配置里的 agents 必须是列表，实际是 {type(raw).__name__}"
        log.error("%s", message)
        return (), [message]

    specs: list[AgentSpec] = []
    problems: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        try:
            spec = AgentSpec.model_validate(item)
        except ValidationError as exc:
            message = f"agents[{index}] 校验失败：{exc.error_count()} 处问题"
            log.error("%s（%s）", message, str(exc).replace("\n", " ")[:200])
            problems.append(message)
            continue
        if spec.id in seen:
            message = f"agents[{index}] 的 id={spec.id} 与前面的重复，已跳过"
            log.error("%s", message)
            problems.append(message)
            continue
        seen.add(spec.id)
        if not spec.enabled:
            log.debug("内联 Agent %s 已禁用，不进菜单", spec.id)
            continue
        specs.append(spec)
    return tuple(specs), problems


def load_config(path: Path | str = DEFAULT_CONFIG_PATH, *, logger=None) -> Config:
    """加载 config.yaml。文件不存在就用默认值（并说明）。"""
    log = logger or get_logger(__name__)
    target = Path(path)
    if not target.is_file():
        log.info("未找到 %s，全部使用默认配置（复制 config/config.example.yaml 可自定义）", target)
        return Config()

    raw = _read_yaml(target, log)
    if raw is None:
        return Config(problems=(f"{target} 无法解析，已全部退回默认配置",), source=target)

    problems: list[str] = []
    try:
        app = AppConfig.model_validate(raw.get("app") or {})
    except ValidationError as exc:
        message = f"app 段校验失败，已退回默认：{str(exc).replace(chr(10), ' ')[:200]}"
        log.error("%s", message)
        app, problems = AppConfig(), [message]

    app.hotkeys, hotkey_problems = _validate_hotkeys(app.hotkeys, log)
    problems.extend(hotkey_problems)

    if app.behavior.auto_enter:
        # AGENTS.md v0.1 禁止事项第一条。写了也不生效，但不能不说。
        message = "behavior.auto_enter=true 在 v0.1 不生效（禁止自动回车），已按 false 处理"
        log.error("%s", message)
        problems.append(message)
        app.behavior = app.behavior.model_copy(update={"auto_enter": False})

    if app.behavior.multi_target_max < 1:
        message = f"behavior.multi_target_max={app.behavior.multi_target_max} 不合法，已按 1 处理"
        log.error("%s", message)
        problems.append(message)
        app.behavior = app.behavior.model_copy(update={"multi_target_max": 1})

    inline, inline_problems = _inline_agents(raw.get("agents"), log)
    problems.extend(inline_problems)

    if app.triggers.any_enabled:
        # 解析了但还没实现，必须说出来，不能让人以为已经在监听了
        message = "triggers.* 已解析，但监听功能尚未实现（M4 只做配置化），当前不会有任何监听行为"
        log.warning("%s", message)
        problems.append(message)

    log.info(
        "已加载配置 %s：热键 %s / %s，多目标间隔 %sms、上限 %s，内联 Agent %d 个",
        target,
        app.hotkeys.dispatch_clipboard,
        app.hotkeys.resend_last,
        app.behavior.multi_target_delay,
        app.behavior.multi_target_max,
        len(inline),
    )
    return Config(app=app, inline_agents=inline, problems=tuple(problems), source=target)
