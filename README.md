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
pip install python-telegram-bot aiohttp mistune
```

创建 `.env` 文件：

```
BOT_TOKEN=你的token
ALLOWED_USERS=你的telegram用户id
```

将 `~/.claude/settings.json` 中的 hooks 配置指向 `hooks/` 目录下的脚本（参考项目中已有的 hooks 文件）。

## 启动

```bash
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
