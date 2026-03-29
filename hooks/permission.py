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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import read_bot_port

import fnmatch
import re

BOT_PORT = read_bot_port()
BOT_API = f"http://localhost:{BOT_PORT}/permission"

# ============ 敏感文件保护规则 ============
# 匹配这些模式的文件操作，无论 bypass 是否开启，都强制弹出审批
SENSITIVE_PATTERNS = [
    "*/.claude/*",
    "*/.claude/**",
    "**/.claude/*",
    "**/.claude/**",
    "**/settings.json",
    "**/settings.local.json",
    "**/settings.backup.json",
    "**/.env",
    "**/.env.*",
]

def is_sensitive_file(file_path):
    """检查文件路径是否匹配敏感文件规则"""
    if not file_path:
        return False
    # 展开 ~ 路径
    file_path = os.path.expanduser(file_path)
    # 直接检查是否包含 .claude/ 路径段
    if "/.claude/" in file_path or file_path.endswith("/.claude"):
        return True
    # 检查文件名是否为 settings 相关
    basename = os.path.basename(file_path)
    if basename in ("settings.json", "settings.local.json", "settings.backup.json"):
        return True
    # 检查 .env 文件
    if basename == ".env" or basename.startswith(".env."):
        return True
    # fnmatch 检查
    for pattern in SENSITIVE_PATTERNS:
        if fnmatch.fnmatch(file_path, pattern):
            return True
    return False

def extract_file_paths(tool_name, tool_input):
    """从工具调用中提取涉及的文件路径"""
    paths = []
    if tool_name in ("Edit", "Write", "Read"):
        fp = tool_input.get("file_path", "")
        if fp:
            paths.append(fp)
    elif tool_name == "Bash":
        # 简单启发式：从命令中提取可能的文件路径
        cmd = tool_input.get("command", "")
        # 匹配包含 .claude 或 settings.json 的路径
        for token in re.split(r'[\s;|&]+', cmd):
            if ".claude" in token or "settings.json" in token or ".env" in token:
                paths.append(token.strip("'\""))
    elif tool_name == "Glob":
        pattern = tool_input.get("path", "")
        if pattern:
            paths.append(pattern)
    return paths

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

# 检查是否涉及敏感文件
force_approval = False
file_paths = extract_file_paths(tool_name, tool_input)
for fp in file_paths:
    if is_sensitive_file(fp):
        force_approval = True
        description = f"⚠️ [敏感文件] {description}"
        break

# 读取环境变量（场景 A：由 bot 启动 Claude 时设置）
chat_id = os.environ.get("TELEGRAM_CHAT_ID")
thread_id = os.environ.get("TELEGRAM_THREAD_ID")

# 提取 session_id（用于路由到正确的话题）
session_id = input_data.get("session_id", "")

# 向 Bot 的 HTTP API 发请求，等待用户审批
payload_data = {
    "description": description,
    "session_id": session_id,
    "force_approval": force_approval,
}
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
