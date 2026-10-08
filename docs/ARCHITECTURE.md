# AgentCtrlV 架构设计

## 模块划分

| 模块 | 职责 |
|------|------|
| HotkeyManager | 注册热键、冲突检测、热更新 |
| ClipboardManager | 读写剪贴板、完整保存/恢复所有格式 |
| WindowLocator | 找进程、找窗口、激活窗口（force_foreground） |
| ProcessManager | 启动进程、轮询窗口出现 |
| Injector（抽象） | 注入接口 |
| ├ ClipboardInjector | GUI 首选：写剪贴板 → 聚焦 → Ctrl+V |
| ├ UIAInjector | UIA SetFocus / ValuePattern |
| └ SendInputInjector | CLI / 兜底键盘模拟 |
| SessionPolicy | 冷启动/热启动策略 |
| Dispatcher | 多目标调度（间隔 800ms） |
| RadialMenu | 环形菜单 UI（PySide6） |
| Tray | 托盘图标 + 右键菜单 |
| Notification | 托盘气泡通知 |

## 状态机

```
NOT_RUNNING ──启动──> STARTING ──窗口出现──> WINDOW_READY ──UI就绪──> UI_READY ──注入──> INJECTING ──完成──> DONE
     │                    │                      │                    │                    │
     └── 启动失败 ─────────┴── 窗口超时 ──────────┴── UI超时 ───────────┴── 注入失败 ──────────┘
                              │                      │                    │
                              ▼                      ▼                    ▼
                          [通知+重试]            [降级盲粘]            [标记可能成功]
```

每个箭头都有超时和降级路径。

## 注入决策树

```
拿到 hwnd，激活窗口
    │
    ▼
config.locate.method == "uia" ?
    ├── 是 → UIA 搜索 Edit/Document 控件
    │         ├── 找到 → SetFocus → 进注入
    │         └── 超时 1s → 降级
    │
    ▼
config.locate.method == "click" ?
    ├── 是 → 模拟点击配置坐标 → 进注入
    │
    ▼
降级：盲粘到前台焦点（必须通知用户）
```

## 剪贴板注入完整时序

1. 保存原剪贴板（快照所有格式）
2. 写 Payload 到剪贴板（图片：PNG + CF_DIB + CF_BITMAP）
3. 激活目标窗口（SetForegroundWindow + AttachThreadInput 兜底）
4. 聚焦输入框（UIA SetFocus / 模拟点击 / 盲粘）
5. 等待 paste_delay（默认 300ms）
6. SendInput Ctrl+V
7. 等待 render_delay（默认 500ms）
8. 恢复原剪贴板

## 剪贴板实现的三个实测坑位（M1 踩到并已修复）

这三条都是"代码看起来对、结果静默错"的类型，写在这里避免 M2+ 重踩。

### 1. `CF_HDROP` 在 pywin32 里不走 bytes

`win32clipboard.GetClipboardData(CF_HDROP)` 返回的是**文件名元组**，不是 `bytes`；
而写入时必须传 `bytes`（传元组会直接 `TypeError`）。
只按 `str`/`bytes` 分支处理，会让"剪贴板里只有文件"时**整个快照变成空的**，
进而 `restore()` 拒绝恢复 → **用户的文件剪贴板被 Payload 永久覆盖**。

→ `_read_format` 必须单独处理 tuple/list 分支；`_write_entry` 用 `pack_dropfiles`。

### 2. `BITMAPV5HEADER` 后面还有 12 字节掩码

`biCompression == BI_BITFIELDS` 时，颜色掩码跟在头**后面**，
即使头是 124 字节的 `BITMAPV5HEADER`（掩码在头里也有一份）也一样。
实测：120×90 的 32bpp DIBV5 总长 `43336 = 124 + 12 + 43200`。
只按 `头长度 + 调色板` 推算会少 12 字节 → 整幅图错位。

→ `dib.pixel_data_offset()` 优先用**总长度反推**（`len(dib) - 像素字节数`），
静态推算只作兜底。注意 `read_image()` 优先选 CF_DIBV5，所以这个坑影响面很大。

### 3. Windows 会自动合成位图格式

只往剪贴板放 `CF_BITMAP` 时，枚举里照样出现 `CF_DIB` 和 `CF_DIBV5`。
所以不需要额外的 `GetDIBits` 兜底，`read_image()` 的 DIB 路径天然覆盖老程序。

## 失败矩阵（摘要）

| 失败场景 | 降级策略 | 用户反馈 |
|----------|----------|----------|
| 热键注册失败 | 提示冲突，允许改键 | 托盘气泡 |
| Agent 启动失败 | 保留 Payload，可重试 | 托盘通知 |
| 窗口未出现（8s） | 保留 Payload，可重试 | 托盘通知 |
| UI 未就绪 | 降级到盲粘 | 静默降级 |
| 输入框找不到 | 降级到坐标点击 → 盲粘 | 通知 |
| 剪贴板被占用 | 重试 3 次，间隔 100ms | 失败通知 |
| 管理员权限目标 | 不支持 | 通知需以管理员运行 |
| 多目标部分失败 | 继续剩余目标 | 汇总结果 |

## Agent 列表与 CLI Agent 的定位（2026-10 实测后重做）

### 为什么不再把 Agent 写死在代码里

实测一台真实开发机后确认：**Agent 的数量与种类是不可预设的**。同一台机器上
同时存在 6 个 GUI Agent（ZCode / WorkBuddy / Kimi Code / OpenCode / ChatGPT / Cursor）
和 7 个终端里的 CLI Agent（Claude Code / Codex / Gemini / Grok / Kimi / OpenCode / Windows Terminal）。

而环形菜单在半径固定时会随条目增多而重叠——所以能进菜单的条目数是有限的。
**哪些 Agent 进菜单是用户的选择**，因此 Agent 列表改为从 `config/agents/*.yaml` 读取
（契约见 CONFIG_SCHEMA.md），用 `enabled: false` 控制是否进菜单。

`scripts/list_windows.py` 用来把"现实"打出来：当前有哪些可见窗口、
每个 Agent 能不能定位到、为什么不能。

### GUI Agent：进程名 + 标题

Electron 应用（ZCode / WorkBuddy / Kimi Code / OpenCode 实测都是
`Chrome_WidgetWin_1`）主窗口标题稳定等于产品名，`process` + `title_pattern` 足够。

### CLI Agent：从进程树反推宿主终端，**不猜标题**

CLI Agent 没有自己的窗口，注入目标是**宿主终端**。看起来可以靠终端标题匹配，
但那是个陷阱：CLI 会动态改写标题（实测 Claude Code 在 Windows Terminal 里把标题
改成 `✳ Claude Code`，而同一个窗口之前是 `WorkBuddy2API Gateway`）。
靠标题猜，猜错就等于把失败伪装成成功。

所以改成：

1. 按 `cli_match` 找到**真正在运行的那个 CLI 进程**
2. 顺 `psutil` 父进程链往上走，找到第一个拥有可见顶层窗口的祖先
3. 该窗口就是宿主终端（可用 `process` 限定只接受某种终端宿主）

`cli_match` 支持两种写法，因为 npm 装的 CLI 多数以 `node.exe` 运行：

| CLI | 实测运行方式 | `cli_match` |
|-----|--------------|-------------|
| Claude Code | 原生 `claude.exe` | `claude.exe` |
| OpenCode CLI | 原生 `opencode.exe` | `opencode.exe` |
| Grok / Kimi CLI | 原生 exe | `grok.exe` / `kimi.exe` |
| **Codex CLI** | **node.exe 跑 bin/codex.js** | `codex.js` |
| **Gemini CLI** | **node.exe 跑 bundle** | `@google/gemini-cli` |

判定用 `cli_match` **有没有值**，而不是 `type`：`type: cli` 在 CONFIG_SCHEMA 里
表示"默认只收文本"，而 Windows Terminal 自己就是 `type: cli`，它却是那个窗口本身。

**已知限制**：Windows Terminal 的多个标签页共用一个窗口，两个 CLI 跑在同一窗口
的不同标签时无法区分。这与 Ctrl+V 的语义一致——粘贴只会进当前聚焦的窗格，
所以用户本来就需要先切到目标标签。

> ⚠️ `cli_match` 是 CONFIG_SCHEMA.md **尚未收录**的扩展字段（AGENTS.md 要求
> 改 CONFIG_SCHEMA 前先确认）。当前只落在代码与 YAML 注释里，待确认后补进契约文档。

### 提权目标：UIPI 会挡住，而且症状具有误导性

实测（2026-10）：**WorkBuddy 以管理员权限运行**，其窗口所属进程 `29564` 的
token 读不出来（`OpenProcessToken` → winerror 5），于是：

| 环节 | 观察到的现象 |
|------|--------------|
| `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` | 成功 |
| `OpenProcessToken(TOKEN_QUERY)` | **拒绝访问 (5)** |
| `AttachThreadInput` / `BringWindowToTop` | **拒绝访问 (5)** |
| `SetForegroundWindow` 实际效果 | 前台仍是别的窗口 |

如果只看目标进程本身，会得出"权限未知 → 按可注入处理"，然后失败在激活环节，
最后抛出一个**误导性的「窗口未能激活」**——用户完全猜不到真正原因是权限。

修复分两处：

1. `permissions.sibling_level()`：目标 token 读不到时，**用同名进程反推**。
   同一个程序的其他进程往往读得到（WorkBuddy 有多个 High 进程），
   足以判定"这个程序跑在更高权限上"，从而提前拦下并给出准确原因。
2. 激活失败的文案里明确点出"目标可能以管理员权限运行"，不再只说"未能激活"。

> 限制仍在：**v0.1 不支持向提权目标注入**（PRD 明确不做）。这里是把它
> *说清楚*，不是绕过它——绕过需要 AgentCtrlV 自身也以管理员运行。

### 图标：区分"有真实图标"和"系统给的通用图标"

菜单图标由 `scripts/extract_icons.py` 从**真实 exe** 提取。两个坑：

1. **不能用 offscreen 平台**：`QFileIconProvider` 走平台主题（Windows 上是 shell），
   offscreen 下 11 个 exe 提取出的 PNG **字节完全相同**（都是通用占位图）。
2. **没有内嵌图标的 exe 会拿到同一个通用图标**：用 `ExtractIconEx` 先探测是否
   真的带图标资源；不带的（实测 `grok.exe` / `codex.exe`）**干脆不生成图标文件**，
   让菜单退回字母头像——否则菜单里会出现好几个一模一样的图标，根本分不清。

## 注入的可观测性：SendInput 的返回值不是送达证明

**这是本项目最重要的可靠性认知。**

`SendInput` 返回成功只说明"事件被系统接受"，**不代表目标窗口收到了**。
它没有回执——目标应用不会告诉你它到底粘上没有。

### 怎么才能真正验证

唯一可靠的办法：让一个**我们拥有的、会回报自己收到了什么**的目标窗口来判定。
见 `scripts/paste_target.py`（探针）与 `scripts/selftest.py`（逐层判定）。

### 关键教训：探针必须走真实路径（2026-10 实测踩到）

同一环境下，两种探针给出**完全相反**的结论：

| 探针方式 | 结果 |
|----------|------|
| 发单个字母键，看目标有没有收到按键 | ❌ 稳定判定为"注入被拦截" |
| 走真实 `ClipboardInjector` 发 Ctrl+V，看目标有没有粘贴成功 | ✅ 连续通过（集成测试 7 passed，无 skip） |

也就是说，**用简化探针得出的"环境拦截输入注入"是假阴性**。
当时据此写下的"低级键盘钩子吞掉注入"结论**已作废**。

教训：验证要尽量贴近真实路径，否则会把自己的探针缺陷误判成环境问题，
进而写出错误的架构结论。普通按键与组合键的投递表现并不一致，
**不能用字母键代替 Ctrl+V 做注入探针**。

### 仍然成立的结论

1. `InjectionStatus.SUCCESS` 的准确含义是「已派发且无异常」，**不是**「目标已收到」。
   UI 文案不得断言"已经粘好了"。
2. 投递表现**可能是间歇性的**：同一台机器上既观察到过连续成功，
   也观察到过整轮失败。`scripts/selftest.py` 的实测采样：
   同一台机器、同一份代码，连续三次运行得到 **0/3、1/3、3/3** 的成功率。
   因此：
   - `selftest.py` 会把成功率报出来（而不是给一个薛定谔的 ✅/❌），
     单次全失败时会提示先重跑；
   - 集成测试的能力探针重试 3 次，一次失败不足以判定环境不可用。
3. 另一个已被排除的干扰源：**探针就绪竞态**。早先 `selftest` 稳定得到 2/3，
   看起来像"投递不稳定"，实际是窗口刚出现就注入（Qt 还在初始化控件与焦点）。
   现在会等探针回报 `focused` 再注入。**"稳定 2/3"这种过于整齐的数字要警惕，
   它通常指向自己代码里的竞态，而不是外部环境。**
4. 不要在 v0.1 用 `PostMessage` 兜底：直投消息绕过输入队列，
   多数现代应用（Electron/Qt/UWP）不认，且会破坏"用户输入"语义。

## 目录结构

```
AgentCtrlV/
├── AGENTS.md
├── README.md
├── docs/
│   ├── PRD.md
│   ├── ARCHITECTURE.md
│   ├── CONFIG_SCHEMA.md
│   ├── TASKS.md
│   └── ACCEPTANCE.md
├── config/
│   ├── config.example.yaml
│   └── agents/
│       ├── chatgpt.yaml
│       ├── cursor.yaml
│       └── windows-terminal.yaml
├── scripts/
│   ├── m1_demo.py             # M1 验收脚本：read / activate / inject / all
│   ├── selftest.py            # 一键自检：逐层判定链路是否可用（退出码 0/1）
│   ├── list_windows.py        # 列出可注入窗口 + 每个 Agent 的定位结果
│   ├── extract_icons.py       # 从本机 exe 提取图标到 assets/agents/（不入库）
│   ├── preview_menu.py        # 把环形菜单渲染成 PNG（设计评审用）
│   ├── e2e_real_app.py        # 对真实应用做端到端验证（含截图差比对）
│   ├── m3_demo.py             # M3 验收：冷启动 + 多选依次注入（用自有探针）
│   └── paste_target.py        # 会回报"收到了什么"的 GUI 粘贴目标探针
├── src/
│   ├── main.py                # ✅ M2 装配入口（含 --check 无界面自检）
│   ├── core/
│   │   ├── clipboard.py       # ✅ T1.1 会话/重试/全格式快照/文本
│   │   ├── clipboard_image.py # ✅ T1.1 图片编解码 PNG/CF_DIB/CF_BITMAP
│   │   ├── clipboard_snapshot.py # ✅ T1.1 快照模型 + DROPFILES 编解码
│   │   ├── dib.py             # ✅ T1.1 DIB <-> PIL Image
│   │   ├── payload.py         # ✅ Payload 模型
│   │   ├── permissions.py     # ✅ UIPI 完整性级别判定
│   │   ├── window.py          # ✅ T1.2 查找 + force_foreground
│   │   ├── hotkey.py          # ✅ T2.1 解析/校验/RegisterHotKey
│   │   ├── agents.py          # ✅ Agent 配置模型 + 从 config/agents/*.yaml 加载
│   │   ├── targeting.py       # ✅ 共用：选中的 Agent → 目标窗口 + 失败解释
│   │   ├── cold_start.py      # ✅ T3.2/T3.3 启动 → 等窗口 → 新会话
│   │   ├── controller.py      # ✅ T2.3 热键 → 菜单 → 注入（T3.1 多选调度）
│   │   ├── process.py         # ⬜ M3
│   │   ├── state_machine.py   # ⬜ M3
│   │   └── dispatcher.py      # ⬜ M3
│   ├── injectors/
│   │   ├── base.py            # ✅ 注入抽象（三态结果）
│   │   ├── clipboard_injector.py # ✅ T1.3 完整时序
│   │   ├── uia_injector.py    # ⬜ M3
│   │   └── sendinput_injector.py # ⬜ M3
│   ├── ui/
│   │   ├── radial_menu.py     # ✅ T2.2/T2.3 环形菜单
│   │   ├── qt_hotkey.py       # ✅ WM_HOTKEY → HotkeyManager 的桥
│   │   ├── tray.py            # ✅ T2.1 托盘图标与失败通知
│   │   └── settings.py        # ⬜ M5 热键自定义 UI
│   ├── screenshot/
│   │   └── capture.py         # ⬜ 尚未排期（PRD 的 Alt+S）
│   └── utils/
│       ├── logger.py          # ✅ 可配置日志 + 内容脱敏
│       ├── sendinput.py       # ✅ SendInput 原语
│       └── notification.py    # ⬜ M2 由 ui/tray.py 承担，暂不需要单独模块
├── assets/
│   ├── icons/                 # 项目自有品牌资产（入库）
│   │   ├── app.png            # 512×512 托盘/窗口图标
│   │   ├── app.ico            # 多尺寸，PyInstaller --icon
│   │   └── app-master.png     # 1254×1254 母版
│   └── agents/                # 从本机程序提取的第三方图标，**不入库**
│                              # 各家商标资产，由 extract_icons.py 本地生成
├── tests/
│   ├── conftest.py            # FakeClipboard 夹具（单元测试不碰真实剪贴板）
│   ├── test_*.py              # 单元测试：pytest tests/
│   └── test_integration_*.py  # 真实剪贴板/窗口：pytest -m integration
├── pytest.ini
├── requirements.txt
└── MOCK.md
```

✅ = M1 已实现；⬜ = 后续里程碑
