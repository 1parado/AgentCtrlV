"""自检的**结论层**：把观测结果翻译成人能读懂的判定。

刻意与"驱动探针"的 I/O 代码分开：
- 这里是纯逻辑（`evaluate_delivery` 是纯函数），可以被单测完整覆盖
- 那里要起进程、抢焦点、改剪贴板，不适合在单测里跑
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str

    def render(self, index: int, total: int) -> str:
        mark = "✅" if self.ok else "❌"
        return f"[{index}/{total}] {self.name:<10} {mark} {self.detail}"


def evaluate_delivery(state: dict, expected_key: str) -> Check:
    """把目标窗口回报的状态翻译成结论。**纯函数**，便于单测覆盖各分支。"""
    name = "按键投递"
    mime = state.get("last_mime")

    if mime and mime.get(expected_key):
        return Check(name, True, "目标窗口确认收到了 Ctrl+V 的内容")

    if not state:
        return Check(name, False, "目标窗口没有回报任何状态（进程可能没起来）")
    if not state.get("active"):
        return Check(name, False, "目标窗口不是前台窗口——激活失败，检查是否被其他窗口抢占")
    if not state.get("keys_seen"):
        return Check(
            name,
            False,
            "SendInput 报告成功，但目标窗口一个按键都没收到。\n"
            "           先重跑一次本自检：投递表现可能是间歇性的。\n"
            "           若持续失败，再排查常驻的热键 / 输入法 / 自动化类软件，"
            "并确认目标窗口真的抢到了前台焦点。",
        )
    return Check(
        name,
        False,
        f"目标收到了按键但没触发粘贴（keys_seen={state.get('keys_seen')}，"
        f"focused={state.get('focused')}）",
    )


def report(checks: list[Check]) -> None:
    print("=" * 68)
    print("AgentCtrlV M1 自检")
    print("=" * 68)
    total = len(checks)
    for index, check in enumerate(checks, start=1):
        print(check.render(index, total))
    print("-" * 68)
    failed = [check for check in checks if not check.ok]
    if not failed:
        print("结论：M1 链路在本机完全可用。")
    else:
        print(f"结论：{len(failed)}/{total} 项失败 —— 首个失败项是「{failed[0].name}」。")
    print("=" * 68)
