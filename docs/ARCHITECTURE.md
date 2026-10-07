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
├── src/
│   ├── main.py
│   ├── core/
│   │   ├── hotkey.py
│   │   ├── clipboard.py
│   │   ├── window.py
│   │   ├── process.py
│   │   ├── state_machine.py
│   │   └── dispatcher.py
│   ├── injectors/
│   │   ├── base.py
│   │   ├── clipboard_injector.py
│   │   ├── uia_injector.py
│   │   └── sendinput_injector.py
│   ├── ui/
│   │   ├── radial_menu.py
│   │   ├── settings.py
│   │   └── tray.py
│   ├── screenshot/
│   │   └── capture.py
│   └── utils/
│       ├── logger.py
│       └── notification.py
├── tests/
├── requirements.txt
└── MOCK.md
```
