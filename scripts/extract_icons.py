"""从真实可执行文件里提取图标，供环形菜单使用。

优先用 Qt 的 QFileIconProvider 拿**系统为该文件注册的图标**——
它走 shell 的图标解析，能正确处理高 DPI 与 alpha，比手撸
ExtractIconEx + GetIconInfo 组合位图/掩码可靠得多。

图标来源按这个顺序找：
1. 正在运行的同名进程的 .exe 路径（最准）
2. PATH 上的可执行文件
3. 常见安装目录（Program Files / %LOCALAPPDATA%\\Programs）

    python scripts/extract_icons.py [--size 256] [--out assets/agents]
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 注意：这里**不能**用 QT_QPA_PLATFORM=offscreen。
# QFileIconProvider 是走平台主题（Windows 上是 shell）取图标的，
# offscreen 平台没有 shell 集成，会给每个文件都返回同一个通用占位图标。
# 实测：offscreen 下 11 个 exe 提取出的 PNG 字节完全相同。
os.environ.pop("QT_QPA_PLATFORM", None)

import psutil  # noqa: E402
import win32gui  # noqa: E402
from PySide6.QtCore import QFileInfo, QSize  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileIconProvider  # noqa: E402

from src.core.agents import load_agents  # noqa: E402

#: agent.id -> 用来取图标的可执行文件名/进程名
ICON_SOURCES: dict[str, str] = {
    "zcode": "ZCode.exe",
    "workbuddy": "WorkBuddy.exe",
    "kimi-code": "Kimi Code.exe",
    "opencode-desktop": "OpenCode.exe",
    "windows-terminal": "WindowsTerminal.exe",
    "claude-code": "claude.exe",
    "codex-cli": "codex.exe",
    "grok-cli": "grok.exe",
    "kimi-cli": "kimi.exe",
    "opencode-cli": "opencode.exe",
    # 注意：gemini-cli 故意不在这里——它是纯 JS（无原生二进制），
    # 系统只会给一个通用"脚本"图标，不如让菜单退回字母头像更好认。
}

#: 自动探测找不到时用的显式路径（支持 %ENV% 与 glob）
ICON_SOURCE_PATHS: dict[str, str] = {
    "windows-terminal": r"C:\Program Files\WindowsApps\Microsoft.WindowsTerminal_*\WindowsTerminal.exe",
    "opencode-cli": r"%APPDATA%\npm\node_modules\opencode-ai\bin\opencode.exe",
    "codex-cli": r"%APPDATA%\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\*\bin\codex.exe",
}


def resolve_explicit(agent_id: str) -> Path | None:
    """按显式路径/glob 找图标来源（%VAR% 会展开）。"""
    pattern = ICON_SOURCE_PATHS.get(agent_id)
    if not pattern:
        return None
    expanded = os.path.expandvars(pattern)
    base, _, tail = expanded.partition("*")
    if not tail:
        path = Path(expanded)
        return path if path.is_file() else None

    parent = Path(base).parent
    if not parent.is_dir():
        return None
    for candidate in sorted(parent.glob("*" + tail)):
        if candidate.is_file():
            return candidate
    return None

SEARCH_DIRS = (
    Path(r"C:\Program Files"),
    Path(r"C:\Program Files (x86)"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
    Path(os.environ.get("APPDATA", "")) / "npm",
    Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin",
    Path(os.environ.get("USERPROFILE", "")) / ".grok" / "bin",
    Path(os.environ.get("USERPROFILE", "")) / ".kimi-code" / "bin",
    Path(r"C:\Users") / os.environ.get("USERNAME", "") / "AppData" / "Local" / "Microsoft" / "WindowsApps",
)


def find_from_running_process(name: str) -> Path | None:
    wanted = name.lower()
    for proc in psutil.process_iter(["name", "exe"]):
        proc_name = (proc.info.get("name") or "").lower()
        if proc_name != wanted:
            continue
        exe = proc.info.get("exe")
        if exe and Path(exe).exists():
            return Path(exe)
    return None


def find_on_path(name: str) -> Path | None:
    found = shutil.which(name)
    return Path(found) if found else None


def find_in_common_dirs(name: str) -> Path | None:
    stem = Path(name).stem
    for root in SEARCH_DIRS:
        if not root.is_dir():
            continue
        # 直接命中
        direct = root / name
        if direct.is_file():
            return direct
        # 一层子目录里找（Program Files\ZCode\ZCode.exe 这种布局）
        try:
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                candidate = child / name
                if candidate.is_file():
                    return candidate
                # 目录名和 exe 名不完全一致时再放宽一次
                for exe in child.glob("*.exe"):
                    if exe.stem.lower() == stem.lower():
                        return exe
        except (OSError, PermissionError):
            continue
    return None


def find_deep_in_npm(name: str, *, max_depth: int = 8) -> Path | None:
    """npm 包会把真正的二进制藏在很深的 vendor 目录里。

    例：codex 的真实可执行文件在
    .../@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe
    所以需要按名字递归找，不能只看一层。
    """
    roots = [Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules"]
    wanted = name.lower()
    stem = Path(name).stem.lower()

    for root in roots:
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for current, dirs, files in os.walk(root):
            if len(Path(current).parts) - base_depth > max_depth:
                dirs[:] = []
                continue
            for filename in files:
                lower = filename.lower()
                if lower == wanted or Path(lower).stem == stem and lower.endswith(".exe"):
                    return Path(current) / filename
    return None


def resolve_source(name: str) -> Path | None:
    for resolver in (
        find_from_running_process,
        find_on_path,
        find_in_common_dirs,
        find_deep_in_npm,
    ):
        path = resolver(name)
        if path is not None:
            return path
    return None


def has_embedded_icon(path: Path) -> bool:
    """该可执行文件自己带图标资源吗？

    没有内嵌图标时，shell 会回退成一个"通用控制台程序"图标——那样
    一堆 CLI 会拿到**完全相同**的图标，菜单里根本分不出来。
    这种情况宁可不要图标，让环形菜单退回字母头像（每个 Agent 都不同）。
    实测：grok.exe / codex.exe 都没有内嵌图标。
    """
    try:
        large, small = win32gui.ExtractIconEx(str(path), 0)
    except Exception:  # noqa: BLE001 - 取不到就当没有
        return False
    for handle in list(large) + list(small):
        try:
            win32gui.DestroyIcon(handle)
        except Exception:  # noqa: BLE001
            pass
    return bool(large or small)


def extract(provider: QFileIconProvider, source: Path, size: int) -> QIcon:
    return provider.icon(QFileInfo(str(source)))


def main() -> int:
    parser = argparse.ArgumentParser(description="提取各 Agent 的真实图标")
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--out", default="assets/agents")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    provider = QFileIconProvider()
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    known = {a.id for a in load_agents()}
    print(f"{'agent':<20}{'来源 exe':<52}{'结果'}")
    print("-" * 100)
    ok = 0
    for agent_id, exe_name in ICON_SOURCES.items():
        if agent_id not in known:
            print(f"{agent_id:<20}{exe_name:<52}跳过（不在当前 Agent 列表）")
            continue
        source = resolve_explicit(agent_id) or resolve_source(exe_name)
        if source is None:
            print(f"{agent_id:<20}{exe_name:<52}❌ 找不到可执行文件")
            continue
        target = out_dir / f"{agent_id}.png"
        if not has_embedded_icon(source):
            # 别留旧图，否则菜单会继续显示上一次的通用图标
            target.unlink(missing_ok=True)
            print(f"{agent_id:<20}{str(source)[:50]:<52}⏭  无内嵌图标，改用字母头像")
            continue
        icon = extract(provider, source, args.size)
        if icon.isNull():
            print(f"{agent_id:<20}{str(source)[:50]:<52}❌ 系统没给图标")
            continue
        pixmap = icon.pixmap(QSize(args.size, args.size))
        if pixmap.isNull():
            print(f"{agent_id:<20}{str(source)[:50]:<52}❌ 位图为空")
            continue
        pixmap.save(str(target), "PNG")
        ok += 1
        print(f"{agent_id:<20}{str(source)[:50]:<52}✅ {pixmap.width()}x{pixmap.height()} -> {target.name}")

    print(f"\n成功 {ok}/{len(ICON_SOURCES)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
