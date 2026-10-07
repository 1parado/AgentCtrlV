# AgentCtrlV 配置契约（CONFIG_SCHEMA）

使用 PyYAML + pydantic 做强校验。所有字段必须有默认值或明确必填。

## 顶层结构

```yaml
app:
  hotkeys: ...
  triggers: ...
  behavior: ...
agents:
  - id: ...
    name: ...
    # 完整 Agent 配置
```

## app 字段

| 字段 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| hotkeys.dispatch_clipboard | str | "Alt+V" | 分发剪贴板 |
| hotkeys.dispatch_screenshot | str | "Alt+S" | 分发截图 |
| hotkeys.resend_last | str | "Alt+Shift+V" | 重发上一次 |
| triggers.listen_screenshot_dir | bool | false | 监听系统截图目录 |
| triggers.listen_clipboard_image | bool | false | 监听剪贴板图片变化 |
| triggers.listen_clipboard_text | bool | false | 监听剪贴板文本变化 |
| behavior.restore_clipboard | bool | true | 发送后恢复原剪贴板 |
| behavior.auto_enter | bool | false | 全局自动回车（默认关） |
| behavior.multi_target_delay | int | 800 | 多目标间隔（ms） |
| behavior.multi_target_max | int | 5 | 多目标上限 |

## Agent 字段

| 字段 | 类型 | 必填 | 默认/说明 |
|------|------|------|-----------|
| id | str | 是 | 唯一标识 |
| name | str | 是 | 显示名称 |
| type | str | 是 | `gui` / `cli` |
| enabled | bool | 否 | true |
| process | str | 是 | 进程名（如 ChatGPT.exe） |
| launch | str | 否 | 启动命令（shell:AppsFolder 或 exe 路径） |
| window.title_pattern | str | 是 | 窗口标题匹配（支持 *） |
| window.multi_instance | str | 否 | `recent` / `first` / `all` |
| locate.method | str | 否 | `uia` / `click` / `blind` |
| locate.uia_control | str | 否 | UIA 控件类型 |
| locate.uia_search_depth | int | 否 | 10 |
| locate.uia_timeout | int | 否 | 1000 |
| locate.click_coords | list[int] | 否 | [x, y] |
| inject.method | str | 否 | `clipboard` / `sendinput` / `uia` |
| inject.paste_delay | int | 否 | 300 |
| inject.render_delay | int | 否 | 500 |
| inject.auto_enter | bool | 否 | false |
| cold_start.new_session | bool | 否 | true |
| cold_start.new_session_method | str | 否 | `hotkey` / `uia_button` / `none` |
| cold_start.new_session_hotkey | str | 否 | "Ctrl+N" |
| cold_start.ready_timeout | int | 否 | 8000 |
| cold_start.ui_ready_timeout | int | 否 | 3000 |
| hot_start.reuse_session | bool | 否 | true |
| hot_start.clear_input | bool | 否 | false |
| permissions | str | 否 | `normal` / `elevated` |
| supported_payloads | list[str] | 否 | `["image", "text"]` 或 `["text"]` |
| icon | str | 否 | 图标路径 |

## 约束

- 热键必须包含至少一个修饰键（Alt/Ctrl/Shift/Win）
- 禁止系统保留组合（Ctrl+Alt+Del、Win+L 等）
- CLI 类型 Agent 默认 `supported_payloads: ["text"]`
- `new_session` 是每个 Agent 的配置项，不是全局能力
