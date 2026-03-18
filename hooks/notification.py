#!/usr/bin/env python3
"""
Claude Code Notification Hook
把 Claude 的通知转发到 Telegram
"""
import sys
import os
import json
import urllib.request

BOT_API = "http://localhost:5000/notification"

input_data = json.loads(sys.stdin.read())
message = input_data.get("message", "Claude 发来通知")
session_id = input_data.get("session_id", "")

chat_id = os.environ.get("TELEGRAM_CHAT_ID")
thread_id = os.environ.get("TELEGRAM_THREAD_ID")

payload_data = {"message": message, "session_id": session_id}
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
