# Claude Code Remote Control

通过 Telegram Bot 远程操控 Claude Code，手机上也能看输出、批权限、发消息。

## 前置条件

- macOS / Linux
- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- Telegram Bot Token（从 @BotFather 获取）
- 一个开启了**话题功能**的 Telegram 群组，Bot 加为管理员（需"管理话题"权限）
- tmux（`brew install tmux`，远程发消息功能需要）

## 安装

```bash
git clone <repo-url> && cd claudecode-remote-control
python3 -m venv venv && source venv/bin/activate
pip install 'python-telegram-bot>=22.6,<23.0' 'aiohttp>=3.9,<4.0' 'mistune>=3.2,<4.0'
```

创建 `.env` 文件（参考 `.env.example`）：

```
BOT_TOKEN=你的token
ALLOWED_USERS=你的telegram用户id
```

根据你的环境，按需设置以下可选配置（不填则使用默认值）：

```
# 你的工作目录，bot 通过扫描此目录列出项目（/projects 命令）
# 不填则默认为 ~/workspace，如果该目录不存在，/projects 命令会报错
PROJECT_DIR=~/your-workspace

# Claude Code 的项目数据目录，bot 用来读取历史会话记录（/resume 命令）
# 一般不需要修改，除非你自定义了 Claude Code 的数据目录
# CLAUDE_PROJECTS_DIR=~/.claude/projects

# Bot HTTP API 端口（hooks 通过此端口与 bot 通信）
# 如果默认的 5000 端口被占用（如 macOS 的 AirPlay Receiver），可以改为其他端口
# BOT_PORT=5000
```

配置 Claude Code hooks。将以下内容合并到你的 `~/.claude/settings.json` 中（仅为 hooks 部分，不要覆盖你已有的其他配置项）。把 `/你的路径/claudecode-remote-control` 替换成你实际的 clone 路径：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /你的路径/claudecode-remote-control/hooks/permission.py",
            "timeout": 130
          }
        ]
      }
    ],
    "Notification": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /你的路径/claudecode-remote-control/hooks/notification.py",
            "timeout": 10
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /你的路径/claudecode-remote-control/hooks/stop.py",
            "timeout": 10
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /你的路径/claudecode-remote-control/hooks/session_start.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

> **注意**: hooks 的路径必须是**绝对路径**，不能用相对路径。

## 启动

```bash
source venv/bin/activate
python3 bot.py
```

然后在 TG 群组的通用话题里发 `/start`。

## 使用

### 场景 A：电脑上跑长任务，手机远程监控

```bash
tmux new -s work        # 创建 tmux 工作区
cd ~/your-project
claude                  # 启动 Claude，布置任务
```

TG 群组自动出现新话题，你可以：
- 实时看到 Claude 的输出
- 审批权限请求
- 直接发消息打断 Claude 并给新指令

### 场景 B：纯手机操控

在 TG 群组里新建一个话题（或选已有的空话题）：

```
/projects          → 选择项目目录
直接发消息          → 开始对话
```

### 会话管理

| 命令 | 作用 |
|------|------|
| `/resume` | 恢复历史会话（显示上下文，自动改话题名） |
| `/rename <名称>` | 重命名当前会话（同步改话题名） |
| `/quit` | 暂停当前会话，可以 resume |
| `/clear` | 清除当前会话，关闭话题 |
| `/projects` | 选择项目目录 |
| `/setdir <路径>` | 手动指定项目目录 |
| `/info` | 查看会话信息（来源、tmux、bypass 状态等） |
| `/bypass` | 开关权限审批 |

输入框输入 `/` 会弹出命令菜单。

### 消息格式

Claude 的输出会自动渲染 Markdown（粗体、斜体、代码块、引用等）。Telegram 不支持的格式（标题、表格）会做优雅降级。

## 注意事项

- **不要在 General 话题中关联 Claude 会话。** General 是群组的公共区域，`/start` 的欢迎消息和系统通知都会发到这里。建议新建独立话题来使用，保持 General 整洁。
- **Bot 运行期间，所有 Claude 会话的权限请求都会转发到 Telegram 审批，终端侧不会弹出确认提示。**
