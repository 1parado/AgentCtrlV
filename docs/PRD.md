# AgentCtrlV PRD v0.1

## 一句话定位
Windows 上 AI Agent 的剪贴板路由器——把用户手里的图片/文本，以最快速度、最低摩擦、最可靠地送进一个或多个 Agent 的输入框。

## 核心概念

| 概念 | 定义 | 例子 |
|------|------|------|
| Payload | 要分发的内容 | 截图图片、剪贴板文本 |
| Agent | 一个注入目标 | ChatGPT Desktop、Cursor、Windows Terminal |
| Trigger | 触发分发的事件 | 热键、截图完成、剪贴板变化 |
| Session Policy | 冷/热启动的会话策略 | 新会话 / 复用当前 |

核心流程：`Trigger → Payload → Selector → 对每个选中 Agent 执行 Session Policy + Inject`

## 用户场景

1. **快速提问**：复制一段代码/截图 → Alt+V → 选 ChatGPT → 图片/文本出现在输入框
2. **多 Agent 对比**：同一问题同时发给 ChatGPT + Cursor
3. **冷启动新对话**：Agent 未运行时一键启动并开新会话
4. **截图即问**：Alt+S 截图后直接选 Agent 注入

## v0.1 做什么

- 托盘常驻，单实例
- Alt+V 分发剪贴板、Alt+S 分发截图、Alt+Shift+V 重发
- 热键自定义 + 冲突检测 + 降级提示
- 环形菜单：单选 + Ctrl 多选（最多 5 个）
- 3 个内置 Agent：ChatGPT Desktop、Cursor、Windows Terminal
- 冷启动（可强制新会话）/ 热启动（复用当前）
- 剪贴板完整保存/恢复
- 三层注入降级（Clipboard → UIA → SendInput / 盲粘）
- 失败通知（托盘气泡）
- YAML 配置 + pydantic 校验

## v0.1 明确不做什么

- 截图标注
- 浏览器 Web 版 Agent
- API 直连 / CLI 直接调用
- 云同步
- 自动回车发送
- 管理员权限目标窗口
- 多图 / 滚动截图
- 社区配置仓库（先本地）

## 成功指标（M5）

- 菜单弹出 < 150ms
- 热启动注入 < 800ms
- 成功率 > 95%
