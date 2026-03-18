#!/usr/bin/env python3
"""
Claude Code SessionStart Hook
终端启动 Claude 时通知 Bot 创建话题并开始监听
如果是 Bot 启动的（有 TELEGRAM_CHAT_ID 环境变量），则跳过
"""
import sys
import os
import json
import urllib.request

BOT_API = "http://localhost:5000/session_start"

# 如果是 Bot 启动的 Claude，不需要通知（Bot 自己处理流式输出）
if os.environ.get("TELEGRAM_CHAT_ID"):
    sys.exit(0)

input_data = json.loads(sys.stdin.read())

# 检测 tmux 环境
tmux_pane = None
if os.environ.get("TMUX"):
    tmux_pane = os.environ.get("TMUX_PANE")

payload = json.dumps({
    "session_id": input_data.get("session_id", ""),
    "transcript_path": input_data.get("transcript_path", ""),
    "cwd": input_data.get("cwd", ""),
    "tmux_pane": tmux_pane,
}).encode()
req = urllib.request.Request(BOT_API, data=payload, headers={"Content-Type": "application/json"})

try:
    urllib.request.urlopen(req, timeout=10)
except Exception:
    pass
