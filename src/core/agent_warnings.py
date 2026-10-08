"""把「配置里写了、但当前版本不会生效」的项大声说出来。

独立成一个模块，一是因为这份清单会随着功能补齐不断变化（属于独立关注点），
二是因为它本身就该被单独审视：**沉默地忽略配置是最糟的一种失败**——
用户以为配了、实际没有，而且永远不会知道。

每一条都对应 `tests/test_config_schema_contract.py` 里的
`NOT_IMPLEMENTED` / `NOT_READ_DIRECTLY` 登记项，改这里请一起改那里。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只用于类型标注，避免与 agents.py 形成循环导入
    from src.core.agents import AgentSpec


def warn_ineffective_options(specs: "list[AgentSpec]", log) -> None:
    """逐项检查"配了但不生效"的字段并记 warning。"""
    for spec in specs:
        if spec.inject.auto_enter:
            log.warning(
                "Agent %s 配了 inject.auto_enter=true，但 v0.1 禁止自动回车，该项不生效",
                spec.id,
            )
        if spec.inject.method != "clipboard":
            log.warning(
                "Agent %s 配了 inject.method=%s，但至今只实现了 clipboard 注入，该项不生效",
                spec.id,
                spec.inject.method,
            )
        if spec.locate.method != "blind":
            log.warning(
                "Agent %s 配了 locate.method=%s，但至今只实现了 blind（激活即盲粘），"
                "该项不生效——当前不会去找输入框",
                spec.id,
                spec.locate.method,
            )
        if not spec.hot_start.reuse_session:
            log.warning(
                "Agent %s 配了 hot_start.reuse_session=false（每次强制新会话），"
                "但会话策略尚未实现，仍会复用当前输入框",
                spec.id,
            )
        if spec.hot_start.clear_input:
            log.warning(
                "Agent %s 配了 hot_start.clear_input=true（发送前清空输入框），"
                "但该功能尚未实现，输入框里原有的内容不会被清掉",
                spec.id,
            )
        if spec.permissions == "elevated":
            log.warning(
                "Agent %s 声明 permissions=elevated，但该字段不参与决策："
                "是否提权由运行时按进程完整性级别实测判断（v0.1 也不支持注入提权目标）",
                spec.id,
            )
        if spec.window.multi_instance != "recent":
            log.warning(
                "Agent %s 配了 window.multi_instance=%s，但多实例选择尚未实现，"
                "当前永远取最近匹配到的那个窗口",
                spec.id,
                spec.window.multi_instance,
            )
