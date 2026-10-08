"""源码卫生：把"肉眼看不出来"的规则变成会自动变红的检查。

两条规则都来自真实事故或 AGENTS.md 明文要求：

1. **不得出现控制字符**（2026-10 事故）
   在 PowerShell 双引号 here-string 里反引号是转义符，`` `a `` 会被解释成
   BEL(U+0007)。于是 `` `agents.py` `` 被写成了 "BEL + gents.py"。
   编辑器里完全看不见，一路提交并推到了公开仓库才被发现。
2. **单文件不超过 300 行**（AGENTS.md 明文）
   此前每轮都靠临时命令人工核对——规则写在宪法里，却没有任何东西保证它成立。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: 允许出现在文本里的"正常"控制字符
ALLOWED_CONTROL = {"\n", "\r", "\t"}

TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".txt", ".ini", ".cfg", ".toml"}
SKIP_DIRS = {".venv", ".git", "__pycache__", ".pytest_cache", "build", "dist"}

MAX_LINES = 300


def _text_files() -> list[Path]:
    files = []
    for path in sorted(REPO.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in TEXT_SUFFIXES:
            files.append(path)
    return files


def test_no_control_characters_in_text_files() -> None:
    """控制字符不可见，只能在代码里挡住——它已经溜进过一次公开仓库。"""
    offenders: list[str] = []
    for path in _text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            bad = [ch for ch in line if ord(ch) < 32 and ch not in ALLOWED_CONTROL]
            if bad:
                codes = ", ".join(f"U+{ord(ch):04X}" for ch in bad)
                offenders.append(f"{path.relative_to(REPO)}:{lineno} 含有 {codes}")

    assert offenders == [], (
        "以下位置含有不可见的控制字符（常见成因：PowerShell here-string 里 "
        f"反引号被当转义符）:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("path", [p for p in _text_files() if p.suffix == ".py"])
def test_python_file_within_line_limit(path: Path) -> None:
    """AGENTS.md：单文件不超过 300 行。以前只靠人工核对，现在自动拦。"""
    lines = path.read_text(encoding="utf-8").splitlines()

    assert len(lines) <= MAX_LINES, (
        f"{path.relative_to(REPO)} 有 {len(lines)} 行，超过 AGENTS.md 的 {MAX_LINES} 行上限；"
        "请按职责拆分，不要放宽上限"
    )


def test_hygiene_check_actually_scans_something() -> None:
    """防止检查本身退化成空转（比如后缀名写错导致一个文件都没扫到）。"""
    files = _text_files()

    assert len(files) > 40
    assert any(p.suffix == ".py" for p in files)
    assert any(p.suffix == ".md" for p in files)
