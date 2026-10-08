# AgentCtrlV 任务列表

按里程碑拆分，每个任务必须有明确输入、输出和验收标准。
**AI 一次只做当前指定的一个任务，不要提前做后续任务。**

---

## M1：单链路跑通（注入核心）

目标：手动调用函数，能把剪贴板图片粘贴到记事本（Mock）。

> **状态：代码已完成，自动化验证通过，人工验收待做。**
>
> | 项 | 结果 |
> |----|------|
> | T1.1 读剪贴板图片并保存 | ✅ 已实现；集成测试验证 `clipboard.png` 产出且剪贴板逐格式未变 |
> | T1.2 找窗口 + 激活 | ✅ 已实现；`SetForegroundWindow` 被拒后 Alt 释放前台锁成功抢到前台 |
> | T1.3 剪贴板注入时序 | ✅ 已实现；时序由单元测试逐事件断言 |
> | T1.4 单元测试 | ✅ `pytest tests/` → 108 passed；`pytest -m integration` → 5 passed |
> | 集成测试发现的真 bug | ✅ 已修复并加回归：CF_HDROP 元组导致快照为空、DIBV5 掩码导致解码错位（见 ARCHITECTURE.md） |
> | **M1 总验收（按键真正送达）** | ✅ **已通过**——集成测试走真实注入器发 Ctrl+V，目标窗口回报粘贴成功；`pytest -m integration` → 7 passed（无 skip），`python scripts/selftest.py` → 5/5 |

**验收命令**：`python scripts/selftest.py`（自动判定，退出码 0/1）
或 `python scripts/m1_demo.py all`（传统方式，目视确认记事本）

### T1.1 读取剪贴板图片并保存
- 输入：剪贴板中有图片
- 输出：`clipboard.png` 文件 + 原剪贴板完整恢复
- 要求：用 pywin32，OpenClipboard 失败重试 3 次，间隔 100ms
- 验收：运行脚本后，当前目录出现 clipboard.png，原剪贴板内容不变
- 实现：`src/core/clipboard.py`、`clipboard_image.py`、`clipboard_snapshot.py`、`dib.py`
- 命令：`python scripts/m1_demo.py read [--make-test-image]`

### T1.2 找到并激活记事本窗口
- 输入：记事本已打开
- 输出：记事本窗口被激活到前台
- 要求：实现 force_foreground（SetForegroundWindow + AttachThreadInput 兜底）
- 验收：调用后记事本成为前台窗口
- 实现：`src/core/window.py`
- 命令：`python scripts/m1_demo.py activate`

### T1.3 剪贴板注入（Ctrl+V）
- 输入：图片已在剪贴板，目标窗口已激活
- 输出：图片出现在记事本中
- 要求：完整时序（保存 → 写 → 激活 → 等待 → Ctrl+V → 等待 → 恢复）
- 验收：手动运行后，图片出现在记事本，原剪贴板恢复
- 实现：`src/injectors/base.py`、`clipboard_injector.py`、`src/utils/sendinput.py`
- 命令：`python scripts/m1_demo.py inject`

### T1.4 单元测试覆盖
- 测试 ClipboardManager 保存/恢复
- 测试窗口查找与激活（可用 mock）
- 验收：`pytest tests/` 通过
- 实现：`tests/`（含 `FakeClipboard` 夹具与 Qt 目标探针）

**M1 总验收**：一条命令跑通「读剪贴板图片 → 粘贴到记事本 → 恢复剪贴板」
- 命令：`python scripts/m1_demo.py all`

---

## M2：热键 + 环形菜单

> **状态：代码已完成并通过自动化验证；真实 Agent 的人工验收待做。**
>
> | 项 | 结果 |
> |----|------|
> | T2.1 注册 Alt+V + 冲突检测 | ✅ 已实现；真实注册成功（`python src/main.py --check`），冲突分支单测覆盖 |
> | T2.2 环形菜单（3 Agent） | ✅ 已实现；offscreen 渲染验证几何与配色，菜单对象常驻 |
> | T2.3 单击发送 + Esc 取消 | ✅ 已实现；单击即确认、Esc/点空白取消且无副作用，均有测试 |
> | M2 自动化验证 | ✅ `pytest tests/` → 194 passed；`pytest -m integration` → 7 passed |
> | **M2 总验收（真实 Agent）** | ⚠️ **待你验收**——本机**未安装 ChatGPT Desktop 与 Cursor**，无法跑「Alt+V → 选 ChatGPT → 图片进输入框」。见下方说明 |

### 本机环境实测

| Agent | 本机状态 |
|-------|----------|
| ChatGPT Desktop | ❌ 未安装（Store 包与 `%LOCALAPPDATA%\Programs\ChatGPT` 都不存在） |
| Cursor | ❌ 未安装 |
| Windows Terminal | ✅ 运行中；窗口标题实测为 `WorkBuddy2API Gateway`（随 shell 变），所以 `title_pattern: "*"` + 进程过滤是**唯一可行**的匹配方式，不是配置缺陷 |

**未做真实终端注入的理由**：向一个正在运行的终端粘贴文本会改动用户 shell 的当前状态，
且无法确认终端里当时跑的是什么（REPL / 编辑器 / TUI 都可能误解释输入）。
所以只验证了「能找到这个真实窗口」，实际文本注入留给人工按 ACCEPTANCE.md 场景 4 执行。

### T2.1 注册 Alt+V，冲突检测
- 验收：能注册；被占用时托盘提示并降级
- 实现：`src/core/hotkey.py`（解析/校验/注册/热更新）、`src/ui/qt_hotkey.py`（消息桥）
- 降级方式：注册失败 → 托盘气泡说明原因 + 保留托盘菜单作为备用触发路径。
  "允许改键" 属于 M5 的热键自定义 UI，M2 不做。
- 命令：`python src/main.py --check`

### T2.2 环形菜单 UI（3 个 Agent）
- 验收：按 Alt+V 弹出菜单，显示 3 个 Agent，<150ms
- 实现：`src/ui/radial_menu.py`；菜单对象在启动时创建并常驻，
  触发时只做 reposition + show，避免冷启动 Qt 吃掉预算
- 注意：150ms 的实测需要在有真实显示器的机器上量；offscreen 只能验证逻辑与绘制

### T2.3 单击发送 + Esc 取消
- 验收：点 ChatGPT → 图片进入输入框；Esc 取消无副作用
- 实现：`src/core/controller.py`（热键 → 菜单 → 注入）、`src/ui/tray.py`（失败通知）
- "取消无副作用"由单测断言：不读剪贴板、不找窗口、不注入

**M2 总验收**：Alt+V → 选 ChatGPT → 图片出现在 ChatGPT 输入框
- 前置：安装 ChatGPT Desktop 或 Cursor，并先跑 `python src/main.py --check` 确认热键可用
- 自动化替代：`pytest -m integration` 已用自有探针窗口验证了同一条链路（含真实 Ctrl+V）

---

## M3：多选 + 冷启动

### T3.1 Ctrl 多选 + 中心确认
- 验收：Ctrl+点多个 → Enter 依次发送，间隔 800ms

### T3.2 冷启动流程
- 验收：Agent 未运行时，点击后能启动、等窗口、注入

### T3.3 新会话策略
- 验收：cold_start.new_session=true 时触发 Ctrl+N 或等效操作

**M3 总验收**：未启动的 Agent 能冷启动并注入；多选 3 个依次成功

---

## M4：配置化

### T4.1 完整 YAML + pydantic Schema
### T4.2 3 个内置 Agent 全部走配置
### T4.3 用户可添加自定义 Agent（基础）

**M4 总验收**：修改 YAML 即可新增/调整 Agent，无需改代码

---

## M5：打磨

- 菜单弹出 <150ms
- 热启动 <800ms
- 成功率 >95%
- 失败矩阵全部覆盖通知
- 热键自定义 UI
