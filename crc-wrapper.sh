#!/usr/bin/env bash
# launchd wrapper：记录启动时间、写 PID、运行 bot

INSTALL_DIR="/Users/mserver/workspace/claude-pilot"
PID_FILE="$INSTALL_DIR/.bot.pid"
STARTUP_FILE="$INSTALL_DIR/.startup_time"
STOP_FLAG="$INSTALL_DIR/.manual_stop"

# 检查手动停止标记
if [ -f "$STOP_FLAG" ]; then
    exit 0
fi

# 记录启动时间
date +%s > "$STARTUP_FILE"

# 写入 PID
echo $$ > "$PID_FILE"

# 运行 bot
exec "/Users/mserver/workspace/claude-pilot/venv/bin/python3" "/Users/mserver/workspace/claude-pilot/bot.py"
