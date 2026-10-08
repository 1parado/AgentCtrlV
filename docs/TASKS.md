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

> **状态：代码已完成并通过自动化验证；真实 Agent 验收已用 ZCode 完成。**
>
> | 项 | 结果 |
> |----|------|
> | T2.1 注册 Alt+V + 冲突检测 | ✅ 已实现；真实注册成功（`python src/main.py --check`），冲突分支单测覆盖 |
> | T2.2 环形菜单 | ✅ 已实现；几何随条目数自适应、菜单对象常驻；`scripts/preview_menu.py` 可渲染预览图 |
> | T2.3 单击发送 + Esc 取消 | ✅ 已实现；单击即确认、Esc/点空白取消且无副作用，均有测试 |
> | M2 自动化验证 | ✅ `pytest tests/` → 267 passed；`pytest -m integration` → 7 passed |
> | **M2 总验收（真实 Agent）** | ✅ **已通过**——对真实应用 ZCode 跑通「Alt+V → 菜单选中 → 内容进输入框」，用截图差判定（`scripts/e2e_real_app.py zcode`，窗口 5476 像素发生变化，无失败通知） |

### 本机环境实测

菜单里的 Agent 不再是 PRD 里那三个硬编码项，而是由 `config/agents/*.yaml` 决定
（见 M4）。本机实测装载 **11 个**：5 个 GUI + 6 个终端内 CLI。

| Agent | 本机状态 |
|-------|----------|
| ChatGPT Desktop / Cursor | ❌ 未安装（配置里 `enabled: false`，装上后改回 `true` 即进菜单） |
| ZCode / WorkBuddy / Kimi Code / OpenCode | GUI，按进程名 + 标题定位 |
| Claude Code / Codex / Gemini / Grok / Kimi / OpenCode CLI | 终端内，由**进程树反推宿主终端**；标题会随 shell 变，所以不靠标题猜 |
| Windows Terminal | ✅ 运行中；窗口标题实测为 `✳ Claude Code`（随 shell 变），所以 `title_pattern: "*"` + 进程过滤是**唯一可行**的匹配方式，不是配置缺陷 |

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

> **状态：代码已完成，自动化验收通过。**
>
> | 项 | 结果 |
> |----|------|
> | T3.1 Ctrl 多选 + 中心确认 | ✅ Ctrl+点击切换选中（上限 5，PRD 要求）、中心/回车确认、单选快路径保留；目标间 800ms |
> | T3.2 冷启动流程 | ✅ `launch` → 等窗口（`ready_timeout`）→ 注入；已为本机 5 个 GUI Agent 配上真实 launch 命令 |
> | T3.3 新会话策略 | ✅ `cold_start.new_session` + `new_session_hotkey`（如 Ctrl+N）；不支持的 `uia_button` **明确报未实现**而不是静默跳过 |
> | 单元测试 | ✅ 267 passed（新增 44 条覆盖多选/调度/冷启动/新会话） |
> | **M3 总验收** | ✅ `python scripts/m3_demo.py` → 冷启动 + 多选 3 个依次成功；**桌面空闲时**连跑 3 次全绿 |
>
> **验收方式说明**：`scripts/m3_demo.py` 用**自有探针窗口**做目标（`paste_target.py`），
> 探针会回报"我到底收到了什么"，所以判定不靠肉眼，也不依赖本机装没装某个 AI 应用。
> 冷启动部分是真启动（真实拉起进程、真等窗口出现）；多选部分走真实环形菜单
> （程序化 Ctrl+点击 → 中心确认）与真实注入器。
>
> ⚠️ **验收必须在桌面空闲时跑。** 这套验收要抢前台，如果你正在别的窗口里干活
> （实测：WPS 里开着文档），目标窗口会拿不到前台，结果是 `窗口未能激活，已放弃粘贴`
> ——**这是设计如此，不是 bug**：宁可不粘，也不要把内容粘进一个不知道是什么的窗口。
> 换句话说，本项目的"成功率"必须在"用户正在干活"之外的条件下度量。

### 关于"投递成功率"要怎么看

`SendInput` 没有回执，投递本身存在偶发丢键。用 `--runs` 采样：

```bash
python scripts/m3_demo.py --runs 5
```

**注意区分两类失败**，它们的含义完全不同：

| 现象 | 含义 |
|------|------|
| `窗口未能激活，已放弃粘贴` | 前台拿不到（你有别的窗口在前台）。**安全的拒绝**，不是丢件 |
| 目标只收到 `Ctrl` 没收到 `V` | 前台在**一次 SendInput 的事件之间**被切走。真的丢了 |

第二类是本项目最大的不确定性，M5 的「成功率 >95%」要针对它度量。

### 冷启动的实现边界（重要）

**启动成功 != Agent 就绪。** `os.startfile` / `Popen` 只能证明"启动请求被系统接受了"，
没有回执——这和 `SendInput` 是同一类问题。所以冷启动的成功判据是
**窗口真的出现了**，而不是"没抛异常"。

寄生在终端里的 CLI Agent **不支持冷启动**：我们不会替用户开终端执行命令。
配置层面会明确说明原因，而不是假装启动过。

### 实测修掉的一个真 bug：多选时"越靠前的目标越容易丢键"

第一次跑 M3 验收时现象很怪：

| 运行 | 探针 A | 探针 B | 探针 C |
|------|--------|--------|--------|
| 1 | ❌ 只收到 Ctrl | ❌ 只收到 Ctrl | ✅ |
| 2 | ❌ | ✅ | ✅ |
| 3 | ✅ | ✅ | ✅ |

规律是**总是靠后的成功**。探针只收到 `<Ctrl>` 的 keydown、没有 `<V>`，
说明一次 `SendInput` 的事件是**逐条**按当时的焦点窗口投递的——不是原子的。
菜单刚隐藏时前台还在交接，Ctrl 发给了目标、V 发给了别人。

修法：注入开始前先等 `MENU_CLOSE_SETTLE_S=350ms` 让前台交接完成。
修完连跑 3 次全部 3/3 通过。

> 教训：这种"总是最后一个成功"的规律**指向自己代码里的时序问题**，
> 不是"环境间歇性抖动"。别急着把它归类为不可控因素。

### T3.1 Ctrl 多选 + 中心确认
- 验收：Ctrl+点多个 → Enter 依次发送，间隔 800ms
- 实现：`src/ui/radial_menu.py`（选中状态与上限）、
  `src/core/controller.py`（顺序调度 + 间隔）

### T3.2 冷启动流程
- 验收：Agent 未运行时，点击后能启动、等窗口、注入
- 实现：`src/core/cold_start.py`（launch / wait_for_window / ColdStarter）、
  `src/core/targeting.py`（共用窗口定位）

### T3.3 新会话策略
- 验收：cold_start.new_session=true 时触发 Ctrl+N 或等效操作
- 实现：`cold_start.start_new_session`，按 `new_session_method` 分派

**M3 总验收**：未启动的 Agent 能冷启动并注入；多选 3 个依次成功
- 命令：`python scripts/m3_demo.py`

---

## M4：配置化

> **状态：代码已完成，自动化验收通过。**
>
> | 项 | 结果 |
> |----|------|
> | T4.1 完整 YAML + pydantic Schema | ✅ `app:` 段（hotkeys / triggers / behavior）已实现并**接上线**；Agent 段用 pydantic 强校验 |
> | T4.2 内置 Agent 全部走配置 | ✅ 代码里不再有硬编码 Agent，只剩一份"配置目录也没有"时的兜底 |
> | T4.3 用户可添加自定义 Agent | ✅ 两种方式：`config/agents/*.yaml` 一个文件一个 Agent，或在 `config.yaml` 的 `agents:` 列表里写 |
> | 单元测试 | ✅ 297 passed（本轮新增 30 条：配置默认值/坏配置/内联 Agent/装配接线） |
> | **M4 总验收** | ✅ 改 YAML 即可增删 Agent、改热键、改多目标间隔与上限，**无需改代码** |
>
> 验收命令：`python src/main.py --check`（会打印配置来源、生效的热键与行为、以及所有配置问题）

### 配置的两条来源

```yaml
# config/config.yaml —— app 段永远是这里；agents 列表可选
app:
  hotkeys:
    dispatch_clipboard: "Alt+V"
    resend_last: "Alt+Shift+V"
  behavior:
    multi_target_delay: 800    # T3.1 的目标间隔
    multi_target_max: 5        # PRD 的多选上限
agents: []                     # 也可以把 Agent 直接写在这里
```

```yaml
# config/agents/*.yaml —— 一个文件一个 Agent
id: my-agent
name: "我的 Agent"
process: "Mine.exe"
launch: 'C:\Mine\Mine.exe'
```

两条来源会**合并**；出现同一个 `id` 时保留 `config/agents/` 里的那份并记 error
（不静默取舍）。`config/config.example.yaml` 是随仓库下发的完整样例，
有测试保证它**能直接跑通且不产生任何问题提示**。

### 配置错了会怎样（设计取舍）

原则是「**不让一个手写错字把程序拦在门外，但也绝不假装没看见**」：

| 情况 | 行为 |
|------|------|
| 文件不存在 | 全部默认值 + 一条 info 说明（告诉我们"你正在用默认配置"） |
| YAML 语法错 / 顶层不是映射 | 全部默认值 + error + `--check` 里列出 |
| 某个字段类型错 | 该段退回默认 + error |
| 某个 Agent 坏掉 | 只跳过它，其余照常；**app 段不受影响** |
| 热键非法 / 是系统保留组合 | 报告出来；其余热键照常注册 |
| `behavior.auto_enter: true` | v0.1 禁止自动回车，强制按 false，并 error 说明 |
| `inject.auto_enter: true`（Agent 级） | 同样不生效，warning 说明 |
| `inject.method` 非 clipboard | M1–M4 只实现了 clipboard，warning 说明 |
| `triggers.*` 打开 | 解析了但监听**尚未实现**，warning 说明（避免以为已经在监听） |
| `hotkeys.dispatch_screenshot` | 截图分发未实现，**不注册该热键**——注册了却什么都不做等于全局吞掉用户的 Alt+S |

### T4.1 完整 YAML + pydantic Schema
- 实现：`src/core/config.py`（`AppConfig` / `HotkeyConfig` / `TriggerConfig` / `BehaviorConfig` / `load_config`）
- 接线：`src/main.py`（热键与行为）、`src/core/controller.py`（间隔、恢复剪贴板）、
  `src/ui/radial_menu.py`（多选上限）
- 校验复用 `parse_hotkey`，保留组合（Ctrl+Alt+Del、Win+L…）在这一层就被挡下

### T4.2 内置 Agent 全部走配置
- `src/core/agents.py` 里只剩 `BUILTIN_AGENTS` 这一个"连配置目录都没有"时的兜底
- 注意：`inject.auto_enter` / `inject.method` 这类"配置里能写但 v0.1 不生效"的项，
  现在会**出声**（见上表），不会再被安静地忽略

### T4.3 用户可添加自定义 Agent（基础）
- 放下一个 `config/agents/我的.yaml` 即出现在菜单里；
  超过菜单上限（12）时按 `enabled: false` 取舍
- 菜单容量与多选上限见 T2.2 / T3.1

**M4 总验收**：修改 YAML 即可新增/调整 Agent，无需改代码
- 命令：`python src/main.py --check`

---

## M5：打磨

> **状态：能离线测的都测了，需要抢前台的还没测（见下）。**
>
> | 指标 | 预算 | 实测 | 判定 |
> |------|------|------|------|
> | 菜单弹出 | <150ms | 菜单本身 **14.4ms**；加剪贴板读取见下表 | ⚠️ 部分（详见下） |
> | 热启动注入 | <800ms | 待测（需要桌面空闲） | ⏳ |
> | 成功率 | >95% | 待测（`m3_demo.py --runs N`） | ⏳ |
> | 失败矩阵全部覆盖通知 | — | ✅ 15 条测试逐行钉住 | ✅ |
> | 热键自定义 UI | — | ✅ 托盘「设置热键…」+ 校验 + 回滚 + 落盘 | ✅ |
> | 单元测试 | — | ✅ 330 passed | ✅ |

### 菜单弹出预算的真实构成

菜单显示**之前**要先读剪贴板并解码，所以预算里包含这一块。实测（`scripts/bench.py`）：

| 载荷 | 读取+解码 | + 菜单 14.4ms | 合计 |
|------|-----------|---------------|------|
| 文本 | 0.0ms | 14.4ms | **14ms** ✅ |
| 图片 1920×1080 | 15.3ms | 14.4ms | **30ms** ✅ |
| 图片 3840×2160 | 63.2ms | 14.4ms | **78ms** ✅ |

菜单对象在启动时构造（3.1ms，只付一次），热键路径只做 reposition + show。
**已知风险**：解码成本随像素线性增长，多屏拼接的截图（如 7680×2160）会到 ~130ms，
再大就顶到 150ms 预算边缘。真要优化，做法是在热键时只快照**原始字节**、
把解码推迟到用户确认之后——这会改动剪贴板/载荷链路，风险不小，留待有实测需求时再做。

### 为什么有些指标现在测不了

`--live` 与 `m3_demo.py` 都必须让目标窗口拿到前台。你正在 PyCharm 里写代码时，
它们会抢焦点、并且**正确地失败**（`窗口未能激活，已放弃粘贴`）。所以：

```bash
python scripts/bench.py                 # 离线档：随时可跑，不打扰你
python scripts/bench.py --live          # 完整链路：请在桌面空闲时跑
python scripts/m3_demo.py --runs 5      # 成功率采样：同上
```

设计上的预期：热启动的用户可见延迟 ≈ 激活 + `paste_delay`（默认 300ms），
`render_delay`（500ms）是**粘贴之后**的安全等待、不是延迟的一部分。
这一点要靠 `--live` 的实测确认，不能只靠推理。

### 热键自定义 UI（M5 新增）

托盘菜单「设置热键…」→ 对话框里直接**按键捕获**（QKeySequenceEdit）→
确认时用 `parse_hotkey` 校验（与启动时**同一套规则**，不在 UI 里另写一份）→
先全部注册成功再注销旧的，**任一步失败整体回滚**（半新半旧的热键状态用户没法理解）
→ 成功后写回 `config.yaml`。

落盘是**逐行替换**，不重新序列化整个文件——否则会把用户的注释和排版全抹掉。
找不到 `app.hotkeys` 锚点时**拒绝写入**并说明（硬塞一个 `app:` 块会造成重复键、
把配置写坏）；此时热键仍在本次运行内生效，只是重启会回到默认值——这一点会明确告知。
