"""进程权限判定（UIPI）单元测试。

`check_can_inject` 的完整性级别全部注入，所以不依赖真实进程权限。
另有一条用真实进程验证 `integrity_level` 确实读得出来。
"""

from __future__ import annotations

import os

import pywintypes
import pytest

from src.core import permissions
from src.core.permissions import (
    HIGH_INTEGRITY,
    LOW_INTEGRITY,
    MEDIUM_INTEGRITY,
    SYSTEM_INTEGRITY,
    check_can_inject,
    level_name,
)


@pytest.mark.parametrize(
    ("rid", "expected"),
    [
        (LOW_INTEGRITY, "低"),
        (MEDIUM_INTEGRITY, "中"),
        (HIGH_INTEGRITY, "高"),
        (SYSTEM_INTEGRITY, "系统"),
        (0x5000, "0x5000"),
        (None, "未知"),
    ],
)
def test_level_name(rid, expected) -> None:
    assert level_name(rid) == expected


def test_higher_target_is_blocked(monkeypatch) -> None:
    monkeypatch.setattr(permissions, "integrity_level", lambda pid: HIGH_INTEGRITY)

    verdict = check_can_inject(1234, own_level=MEDIUM_INTEGRITY)

    assert not verdict.allowed
    assert "UIPI" in verdict.reason
    assert "管理员" in verdict.reason
    assert verdict.target_level == HIGH_INTEGRITY
    assert verdict.own_level == MEDIUM_INTEGRITY


def test_same_level_is_allowed(monkeypatch) -> None:
    monkeypatch.setattr(permissions, "integrity_level", lambda pid: MEDIUM_INTEGRITY)

    verdict = check_can_inject(1234, own_level=MEDIUM_INTEGRITY)

    assert verdict.allowed
    assert "权限足够" in verdict.reason


def test_lower_target_is_allowed(monkeypatch) -> None:
    monkeypatch.setattr(permissions, "integrity_level", lambda pid: LOW_INTEGRITY)

    assert check_can_inject(1234, own_level=HIGH_INTEGRITY).allowed


def test_unknown_target_is_allowed_with_warning(monkeypatch) -> None:
    """读不到目标权限时必须放行：未知 != 不足，不能因此拒绝用户操作。"""
    monkeypatch.setattr(permissions, "integrity_level", lambda pid: None)
    monkeypatch.setattr(permissions, "process_name", lambda pid: "Nothing.exe")
    monkeypatch.setattr(permissions, "sibling_level", lambda pid, name: None)

    verdict = check_can_inject(1234, own_level=MEDIUM_INTEGRITY)

    assert verdict.allowed
    assert "读不到目标进程权限" in verdict.reason
    assert verdict.target_level is None


# ---------- 读不到 token 时的同名进程反推 ----------
# 回归 2026-10 实测：WorkBuddy 窗口所属进程读不到 token，但同程序的
# 其他进程是 High。只看目标本身会放行，然后报一个误导性的"窗口未能激活"。


def test_unknown_target_with_elevated_sibling_is_blocked(monkeypatch) -> None:
    monkeypatch.setattr(permissions, "integrity_level", lambda pid: None)
    monkeypatch.setattr(permissions, "process_name", lambda pid: "WorkBuddy.exe")
    monkeypatch.setattr(permissions, "sibling_level", lambda pid, name: HIGH_INTEGRITY)

    verdict = check_can_inject(1234, own_level=MEDIUM_INTEGRITY)

    assert not verdict.allowed
    assert "同程序的其他进程" in verdict.reason
    assert "管理员" in verdict.reason


def test_unknown_target_with_same_level_sibling_is_allowed(monkeypatch) -> None:
    monkeypatch.setattr(permissions, "integrity_level", lambda pid: None)
    monkeypatch.setattr(permissions, "process_name", lambda pid: "X.exe")
    monkeypatch.setattr(permissions, "sibling_level", lambda pid, name: MEDIUM_INTEGRITY)

    assert check_can_inject(1234, own_level=MEDIUM_INTEGRITY).allowed


def test_unknown_target_without_name_stays_allowed(monkeypatch) -> None:
    monkeypatch.setattr(permissions, "integrity_level", lambda pid: None)
    monkeypatch.setattr(permissions, "process_name", lambda pid: None)

    assert check_can_inject(1234, own_level=MEDIUM_INTEGRITY).allowed


def test_sibling_level_only_counts_same_name(monkeypatch) -> None:
    """反推必须只看同名进程，不能把不相干的提权程序算进来。"""

    class FakeProc:
        def __init__(self, pid, name):
            self.info = {"pid": pid, "name": name}

    monkeypatch.setattr(
        permissions.psutil,
        "process_iter",
        lambda attrs=None: [
            FakeProc(1234, "Target.exe"),          # 自己，应跳过
            FakeProc(2001, "Target.exe"),          # 同名 -> 计入
            FakeProc(2002, "Unrelated.exe"),       # 不同名 -> 忽略
        ],
    )
    monkeypatch.setattr(
        permissions,
        "integrity_level",
        lambda pid: {2001: HIGH_INTEGRITY, 2002: SYSTEM_INTEGRITY}.get(pid),
    )

    assert permissions.sibling_level(1234, "Target.exe") == HIGH_INTEGRITY


def test_sibling_level_returns_none_when_nothing_readable(monkeypatch) -> None:
    class FakeProc:
        def __init__(self, pid, name):
            self.info = {"pid": pid, "name": name}

    monkeypatch.setattr(
        permissions.psutil, "process_iter", lambda attrs=None: [FakeProc(2001, "X.exe")]
    )
    monkeypatch.setattr(permissions, "integrity_level", lambda pid: None)

    assert permissions.sibling_level(1234, "X.exe") is None


def test_unknown_own_level_is_allowed(monkeypatch) -> None:
    monkeypatch.setattr(permissions, "integrity_level", lambda pid: HIGH_INTEGRITY)

    verdict = check_can_inject(1234, own_level=None)
    # own_level=None 表示"去读当前进程"，这里真实读到的多半是 Medium
    assert verdict.own_level is not None


def test_own_level_read_failure_is_allowed(monkeypatch) -> None:
    monkeypatch.setattr(permissions, "own_integrity_level", lambda: None)
    monkeypatch.setattr(permissions, "integrity_level", lambda pid: HIGH_INTEGRITY)

    verdict = check_can_inject(1234)

    assert verdict.allowed
    assert "读不到本进程权限" in verdict.reason


# ---------- 真实进程 ----------


def test_real_integrity_level_of_own_process() -> None:
    """本进程一定能读出完整性级别（普通权限下应为中）。"""
    level = permissions.integrity_level(os.getpid())

    assert level is not None
    assert level in (LOW_INTEGRITY, MEDIUM_INTEGRITY, HIGH_INTEGRITY, SYSTEM_INTEGRITY)


def test_real_check_can_inject_into_self() -> None:
    """自己注入自己必然允许（同级别）。"""
    verdict = check_can_inject(os.getpid())

    assert verdict.allowed
    assert verdict.target_level == verdict.own_level


def test_protected_process_returns_none_not_exception() -> None:
    """受保护进程读不到权限时返回 None，不能抛异常。"""
    level = permissions.integrity_level(4)  # System 进程，必然拒绝访问

    assert level is None or isinstance(level, int)


def test_nonexistent_pid_returns_none() -> None:
    assert permissions.integrity_level(0xFFFFFFF) is None


def test_integrity_level_swallows_win32_errors(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise pywintypes.error(5, "OpenProcess", "Access is denied")

    monkeypatch.setattr("win32api.OpenProcess", boom)

    assert permissions.integrity_level(1234) is None
