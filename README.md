# AgentCtrlV

**Windows 上 AI Agent 的剪贴板路由器**

把图片/文本以最快速度、最低摩擦、最可靠地送进一个或多个 AI Agent 的输入框。

> 只做粘贴，不内置 AI。

## 核心流程

```
Alt+V → 读剪贴板 → 环形菜单 → 选 Agent → 注入输入框
```

- **Alt+V**：分发当前剪贴板（图片/文本）
- **Alt+S**：截图后分发
- **Alt+Shift+V**：重发上一次 Payload

支持单选 / Ctrl 多选，冷启动新会话 / 热启动当前输入框。

## v0.1 内置 Agent

| Agent | 类型 | 说明 |
|-------|------|------|
| ChatGPT Desktop | GUI | 支持图片 + 文本 |
| Cursor | GUI | 支持图片 + 文本 |
| Windows Terminal | CLI | 仅文本 |

## 快速开始（开发中）

```bash
git clone https://github.com/1parado/AgentCtrlV.git
cd AgentCtrlV
pip install -r requirements.txt
python src/main.py
```

## 开发交接包

本仓库已按 AI Agent 友好方式组织，包含完整交接文档：

| 文件 | 作用 |
|------|------|
| [AGENTS.md](AGENTS.md) | 给 AI 的开发规则（最重要） |
| [docs/PRD.md](docs/PRD.md) | 产品需求与边界 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 技术架构与状态机 |
| [docs/CONFIG_SCHEMA.md](docs/CONFIG_SCHEMA.md) | 配置契约 |
| [docs/TASKS.md](docs/TASKS.md) | 任务拆分与验收标准 |
| [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) | 手动验收清单 |
| [config/config.example.yaml](config/config.example.yaml) | 配置样例 |
| [MOCK.md](MOCK.md) | 测试环境说明 |
| requirements.txt | 精确依赖 |

## 设计哲学

热键是主路径，监听是辅助；
注入是分层降级，不是单点方案；
会话策略是配置项，不是通用能力；
失败必须可见，不能静默。

## License

MIT
