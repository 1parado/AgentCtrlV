# AgentCtrlV Mock / 测试环境说明

M1 阶段**不依赖真实 AI Agent**，使用系统自带程序做 Mock。

## 推荐 Mock 目标

| Mock 目标 | 用途 | 如何准备 |
|-----------|------|----------|
| 记事本 (notepad.exe) | 图片 + 文本注入测试 | 开始菜单搜索「记事本」打开 |
| 写字板 (wordpad) | 图片注入备选 | 可选 |
| 计算器 | 仅验证窗口激活（不支持粘贴图片） | 可选 |
| Windows Terminal | CLI 文本注入 | 已安装 Win11 自带 |

## M1 测试步骤（推荐）

1. 打开**记事本**
2. 任意截图或复制一张图片到剪贴板
3. 运行当前任务脚本（例如 T1.3）
4. 观察：
   - 图片是否出现在记事本
   - 原剪贴板是否恢复（再 Ctrl+V 到别处验证）

## 自动化测试怎么跑（M1 起）

```bash
pip install -r requirements.txt

python scripts/selftest.py      # 一键自检：不需要肉眼，直接给逐层结论
pytest tests/                   # 单元测试：不碰真实剪贴板，可随时跑
pytest -m integration           # 集成测试：会临时改写剪贴板、开关 GUI 进程
python scripts/m1_demo.py all   # 手动验收：跑通全链路，然后目视确认记事本
```

**优先用 `scripts/selftest.py`。** 它按「运行环境 / 剪贴板层 / 窗口层 / 按键投递」
四层逐项判定，失败时明确指出断在哪一环，退出码 0/1 可直接接 CI。
`m1_demo.py all` 保留给需要"眼见为实"的场合（MOCK.md 的传统验收方式）。

单元测试用 `tests/conftest.py` 里的 `FakeClipboard` 替身，**不会**污染真实剪贴板。
集成测试在 `finally` 里恢复原剪贴板。

### 关于"按键投递"用例的能力探针

集成测试里两条全链路用例（图片/文本真正粘进输入框）带一个能力探针
`input_injection_works`。它**走产品真实路径**：启动探针窗口、用真实的
`ClipboardInjector` 发一张图、再看目标窗口有没有回报粘贴成功。

探针**不能用普通字母键代替 Ctrl+V**——实测两者投递表现不一致，
用字母键会产生假阴性，把其实可用的环境误判成"被拦截"。
详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#注入的可观测性sendinput-的返回值不是送达证明)。

投递表现**可能是间歇性的**，所以探针会重试 3 次；3 次都失败才 skip。
真要排查环境问题时，用 `python scripts/selftest.py` 拿逐层结论。

### 为什么集成测试不用记事本当目标

Win11 记事本是**多标签单进程**应用：`notepad.exe` 只会往用户已打开的记事本里
加一个标签页。拿它做自动化目标会污染用户正在编辑的文档，而且无法安全回收。
所以自动化改用 [scripts/paste_target.py](scripts/paste_target.py)——
一个独立进程、唯一标题、由调用方完全拥有、并且能回报"我收到了什么"的 Qt 窗口。

记事本仍然保留为**人工验收**的 Mock 目标（见上文 M1 测试步骤）。

## 注意事项

- 记事本对图片支持有限（Win11 记事本已支持粘贴图片）
- 如果图片无法粘贴到记事本，可改用写字板或直接用真实 ChatGPT 做验证
- 所有测试脚本必须**恢复原剪贴板**，避免污染用户环境
- 日志中禁止打印剪贴板实际内容

## 后续真实 Agent 测试

M2 起使用真实 Agent：

1. ChatGPT Desktop（从 Microsoft Store 安装）
2. Cursor（官网下载）
3. Windows Terminal（系统自带）

冷启动测试前请先完全退出对应进程。
