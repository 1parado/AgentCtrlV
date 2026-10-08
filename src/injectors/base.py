"""注入器抽象（ARCHITECTURE.md「Injector（抽象）」）。

注入是**发出去就没有回执**的操作：目标窗口不会告诉我们它到底粘上没有。
所以结果不是简单的成功/失败，而是三态：

- SUCCESS  链路全部走通，且每一步都**没有报错**
- DEGRADED 走到了兜底路径（例如盲粘），**可能**成功
- FAILED   确定没有送到，Payload 保留可重试

⚠️ 关于 SUCCESS 的准确含义：它表示"已派发且无异常"，**不等于**"目标已收到"。
SendInput 没有回执，返回值只说明事件被系统接受。实测（2026-10）确实观察到过
"SendInput 返回成功但目标窗口一个按键都没收到"的情况，且投递表现可能是
间歇性的。因此调用方必须按 ARCHITECTURE.md 的说法把它当作"标记可能成功"，
绝不能据此向用户断言"已经粘好了"。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from src.core.payload import Payload
from src.core.window import WindowInfo


class InjectionStatus(str, Enum):
    SUCCESS = "success"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True)
class InjectionOutcome:
    status: InjectionStatus
    detail: str = ""

    @property
    def delivered(self) -> bool:
        """是否已经"派发出去"（SUCCESS 与 DEGRADED 都算，供多目标汇总使用）。

        注意：这只表示没有报错，不代表目标窗口确实消费了内容。
        """
        return self.status is not InjectionStatus.FAILED

    @property
    def ok(self) -> bool:
        return self.status is InjectionStatus.SUCCESS

    def __str__(self) -> str:
        return self.status.value if not self.detail else f"{self.status.value}: {self.detail}"


class Injector(ABC):
    """注入接口。每个实现负责一种把 Payload 送进目标窗口的手段。"""

    #: 与 CONFIG_SCHEMA 的 inject.method 对应
    name: str = "base"

    @abstractmethod
    def inject(self, payload: Payload, target: WindowInfo) -> InjectionOutcome:
        """把 payload 送进 target 窗口。实现必须自己保证异常不外泄。"""
