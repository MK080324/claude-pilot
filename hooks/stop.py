#!/usr/bin/env python3
"""
Claude Code Stop Hook
Claude 完成工作时通知 Bot 停止监听
"""
import sys
import os
import json
import urllib.request

BOT_API = "http://localhost:5000/session_stop"

input_data = json.loads(sys.stdin.read())
reason = input_data.get("stop_reason", "完成")
session_id = input_data.get("session_id", "")

chat_id = os.environ.get("TELEGRAM_CHAT_ID")
thread_id = os.environ.get("TELEGRAM_THREAD_ID")

payload_data = {"message": f"Claude 已停止 ({reason})", "session_id": session_id}
if chat_id:
    payload_data["chat_id"] = int(chat_id)
if thread_id:
    payload_data["thread_id"] = int(thread_id)
payload = json.dumps(payload_data).encode()
req = urllib.request.Request(BOT_API, data=payload, headers={"Content-Type": "application/json"})

try:
    urllib.request.urlopen(req, timeout=10)
except Exception:
    pass
