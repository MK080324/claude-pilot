# 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Telegram 群组（话题模式）                   │
│                                                             │
│  话题 A (终端会话)    话题 B (TG会话)    话题 C (resumed)     │
└──────┬──────────────────┬──────────────────┬────────────────┘
       │                  │                  │
       ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────────┐
│                      bot.py                                 │
│                                                             │
│  Telegram Bot (polling)  +  HTTP API (:BOT_PORT)            │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ handle_msg  │  │ http_session │  │ watch_transcript  │  │
│  │ 发消息→Claude│  │ _start/stop │  │ JSONL 文件监听    │  │
│  └──────┬──────┘  └──────┬───────┘  └────────┬──────────┘  │
└─────────┼────────────────┼───────────────────┼──────────────┘
          │                │                   │
          ▼                ▼                   ▼
┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐
│ claude -p    │  │ Hook 脚本    │  │ ~/.claude/projects/    │
│ --stream-json│  │              │  │   xxx/session.jsonl    │
│ (TG 会话)    │  │ session_start│  │                        │
│              │  │ permission   │  │  终端 Claude 写入       │
│ Bot 读 stdout│  │ notification │  │  Bot 监听新行           │
└──────────────┘  │ stop         │  └────────────────────────┘
                  │              │
                  │  HTTP→Bot    │
                  └──────┬───────┘
                         │
                         ▼
                  ┌──────────────┐
                  │ 终端 Claude  │
                  │ (tmux 中)    │
                  │              │
                  │ Bot 可通过    │
                  │ tmux send-   │
                  │ keys 注入输入 │
                  └──────────────┘
```

## 数据流

| 场景 | 输入 | 输出 | 权限审批 |
|------|------|------|---------|
| TG 发消息 | Bot 启动 `claude -p` | 读 stdout (stream-json) | Hook → Bot HTTP → TG 按钮 |
| 终端 Claude | tmux send-keys 注入 | 监听 JSONL 文件 | Hook → Bot HTTP → TG 按钮 |

## 文件结构

```
bot.py                  主程序：Telegram Bot + HTTP API
hooks/
  session_start.py      终端启动 Claude 时通知 Bot（含 tmux 检测）
  permission.py         工具执行前请求审批
  notification.py       转发 Claude 通知
  stop.py               Claude 停止时通知 Bot
.env                    BOT_TOKEN, ALLOWED_USERS
```
