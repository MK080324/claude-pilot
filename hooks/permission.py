#!/usr/bin/env python3
"""
Claude Code PreToolUse Hook
在 Claude 执行工具之前，转发到 Telegram Bot 让用户审批
如果 Bot 没在运行，不干预，让 Claude 按正常流程走
"""
import sys
import os
import json
import urllib.request

BOT_API = "http://localhost:5000/permission"

# 从 stdin 读取 Claude 传来的 Hook 数据
input_data = json.loads(sys.stdin.read())

# 提取工具名和参数，组成描述
tool_name = input_data.get("tool_name", "未知工具")
tool_input = input_data.get("tool_input", {})

if tool_name == "Bash":
    description = f"执行命令: {tool_input.get('command', '?')}"
elif tool_name == "Edit":
    description = f"编辑文件: {tool_input.get('file_path', '?')}"
elif tool_name == "Write":
    description = f"写入文件: {tool_input.get('file_path', '?')}"
elif tool_name == "Read":
    description = f"读取文件: {tool_input.get('file_path', '?')}"
else:
    description = f"{tool_name}: {json.dumps(tool_input, ensure_ascii=False)[:200]}"

# 读取环境变量（场景 A：由 bot 启动 Claude 时设置）
chat_id = os.environ.get("TELEGRAM_CHAT_ID")
thread_id = os.environ.get("TELEGRAM_THREAD_ID")

# 提取 session_id（用于路由到正确的话题）
session_id = input_data.get("session_id", "")

# 向 Bot 的 HTTP API 发请求，等待用户审批
payload_data = {"description": description, "session_id": session_id}
if chat_id:
    payload_data["chat_id"] = int(chat_id)
if thread_id:
    payload_data["thread_id"] = int(thread_id)
payload = json.dumps(payload_data).encode()
req = urllib.request.Request(BOT_API, data=payload, headers={"Content-Type": "application/json"})

try:
    resp = urllib.request.urlopen(req, timeout=130)
    result = json.loads(resp.read())
    decision = result.get("decision", None)
except Exception:
    # Bot 没运行或网络错误，不干预，让 Claude 按正常流程走
    decision = None

# 只有拿到明确的决定时才输出
if decision == "allow":
    output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}
    print(json.dumps(output))
elif decision == "deny":
    output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "用户拒绝"}}
    print(json.dumps(output))
# decision 为 None 时什么都不输出，Claude 回退到正常流程
