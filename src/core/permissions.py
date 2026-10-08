"""进程权限判定——UIPI 硬边界。

Windows 的 UIPI 规则：**完整性级别较低的进程无法向级别较高的进程注入输入**。
所以"以管理员身份运行的目标窗口"是 v0.1 明确不支持的场景
（ARCHITECTURE.md 失败矩阵），必须在注入**之前**识别出来并给出准确原因。

否则用户看到的只是"窗口未能激活"，完全猜不到真正的问题是权限——
这正是 AGENTS.md「失败必须可见」要避免的情况。
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass

import psutil
import pywintypes
import win32api
import win32con
import win32security

LOW_INTEGRITY = 0x1000
MEDIUM_INTEGRITY = 0x2000
HIGH_INTEGRITY = 0x3000
SYSTEM_INTEGRITY = 0x4000

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_LEVEL_NAMES = {
    LOW_INTEGRITY: "低",
    MEDIUM_INTEGRITY: "中",
    HIGH_INTEGRITY: "高",
    SYSTEM_INTEGRITY: "系统",
}


def level_name(rid: int | None) -> str:
    if rid is None:
        return "未知"
    return _LEVEL_NAMES.get(rid, f"0x{rid:x}")


def integrity_level(pid: int) -> int | None:
    """返回进程完整性级别 RID；无法判断时返回 None（不抛异常）。

    None 的常见原因：目标是受保护的系统进程（OpenProcess 被拒）、
    或进程已退出。调用方必须把 None 当作"未知"，**不能**当作"低"。
    """
    handle = None
    token = None
    try:
        handle = win32api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
        sid, _attributes = win32security.GetTokenInformation(
            token, win32security.TokenIntegrityLevel
        )
        count = sid.GetSubAuthorityCount()
        if not count:
            return None
        return int(sid.GetSubAuthority(count - 1))
    except (pywintypes.error, AttributeError, ValueError, OSError):
        return None
    finally:
        # 句柄关闭失败无关紧要，但仍然不静默吞掉异常类型
        with contextlib.suppress(pywintypes.error, OSError):
            if token is not None:
                token.Close()
        with contextlib.suppress(pywintypes.error, OSError):
            if handle is not None:
                handle.Close()


def own_integrity_level() -> int | None:
    return integrity_level(os.getpid())


def process_name(pid: int) -> str | None:
    try:
        return psutil.Process(pid).name()
    except (psutil.Error, OSError):
        return None


def sibling_level(pid: int, name: str) -> int | None:
    """同名进程里能读到的**最高**完整性级别。

    存在的原因（2026-10 实测）：提权程序的某个进程可能既读不到 token
    （OpenProcessToken 拒绝访问），又拥有窗口。这时直接判定"权限未知"会让
    我们带着错误的前提去激活，最后抛出一个误导性的"窗口未能激活"。
    而同一个程序的其他进程往往读得到，用它们反推整个程序的提权状态。

    例：WorkBuddy 窗口所属 pid 29564 读不到 token，但同名的 3948/20140
    都是 High（0x3000），足以判定"这个程序跑在更高权限上"。
    """
    best: int | None = None
    wanted = name.lower()
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["pid"] == pid:
            continue
        if (proc.info.get("name") or "").lower() != wanted:
            continue
        level = integrity_level(proc.info["pid"])
        if level is not None and (best is None or level > best):
            best = level
    return best


@dataclass(frozen=True)
class PermissionVerdict:
    allowed: bool
    reason: str
    target_level: int | None
    own_level: int | None


def check_can_inject(pid: int, *, own_level: int | None = None) -> PermissionVerdict:
    """按 UIPI 规则判断能否向 pid 注入输入。

    own_level 可注入以便测试；默认读当前进程。
    无法判断时一律**放行**并在 reason 里说明——权限未知不等于权限不足，
    不能因为读不到就拒绝用户操作（真正的失败会在注入时暴露）。
    """
    own = own_integrity_level() if own_level is None else own_level
    target = integrity_level(pid)

    if target is None:
        # 目标读不到 token 时不能就此放行——可能是提权程序的窗口进程。
        # 用同名进程反推，避免带着错误前提去激活、最后报一个误导性的错。
        name = process_name(pid)
        inferred = sibling_level(pid, name) if name else None
        if inferred is not None and own is not None and inferred > own:
            return PermissionVerdict(
                False,
                f"目标进程读不到权限，但同程序的其他进程以更高权限运行"
                f"（{level_name(inferred)} > 本进程 {level_name(own)}）——"
                "Windows 的 UIPI 会拒绝激活与注入。"
                "请以管理员身份重启 AgentCtrlV，或改用普通权限启动目标程序。",
                inferred,
                own,
            )
        return PermissionVerdict(True, "读不到目标进程权限，按可注入处理", None, own)
    if own is None:
        return PermissionVerdict(True, "读不到本进程权限，按可注入处理", target, None)
    if target > own:
        return PermissionVerdict(
            False,
            f"目标窗口以更高权限运行（完整性级别 {level_name(target)} > 本进程 {level_name(own)}），"
            "Windows 的 UIPI 禁止向其注入输入。"
            "请以管理员身份重启 AgentCtrlV，或改用普通权限启动目标程序。",
            target,
            own,
        )
    return PermissionVerdict(
        True,
        f"权限足够（目标 {level_name(target)}，本进程 {level_name(own)}）",
        target,
        own,
    )
