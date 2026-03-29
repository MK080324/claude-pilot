#!/usr/bin/env bash
set -euo pipefail

# ============ Claude Pilot 管理工具 (crc) ============

INSTALL_DIR="/Users/mserver/workspace/claude-pilot"
VENV_DIR="$INSTALL_DIR/venv"
LOG_DIR="$INSTALL_DIR/logs"
PID_FILE="$INSTALL_DIR/.bot.pid"
STOP_FLAG="$INSTALL_DIR/.manual_stop"
PLIST_NAME="com.claude-pilot.bot"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/$PLIST_NAME.plist"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CLAUDE_SETTINGS_BACKUP="$HOME/.claude/settings.backup.json"
STARTUP_FILE="$INSTALL_DIR/.startup_time"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

cmd_start() {
    if is_running; then
        echo -e "${YELLOW}Bot 已经在运行中 (PID: $(cat "$PID_FILE"))${NC}"
        return 0
    fi

    echo -e "${BLUE}启动 Claude Pilot Bot...${NC}"

    # 清除手动停止标记
    rm -f "$STOP_FLAG"

    mkdir -p "$LOG_DIR"

    # 记录启动时间
    date +%s > "$STARTUP_FILE"

    # 如果是 macOS 且 launchd plist 存在，用 launchctl 启动（支持崩溃自动重启）
    if [ "$(uname)" = "Darwin" ] && [ -f "$PLIST_FILE" ]; then
        launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$PLIST_FILE"
        sleep 1
        if is_running; then
            echo -e "${GREEN}Bot 已启动 (launchd 托管，崩溃自动重启)${NC}"
            echo -e "PID: $(cat "$PID_FILE")"
        else
            echo -e "${RED}启动失败，请查看日志: crc logs${NC}"
            return 1
        fi
    else
        # 直接后台启动（带 wrapper 实现重启）
        nohup bash -c "
            while true; do
                if [ -f \"$STOP_FLAG\" ]; then
                    exit 0
                fi
                date +%s > \"$STARTUP_FILE\"
                \"$VENV_DIR/bin/python3\" \"$INSTALL_DIR/bot.py\" >> \"$LOG_DIR/bot.log\" 2>&1
                EXIT_CODE=\$?
                if [ -f \"$STOP_FLAG\" ]; then
                    exit 0
                fi
                echo \"[\$(date)] Bot 异常退出 (code: \$EXIT_CODE)，3秒后重启...\" >> \"$LOG_DIR/bot.log\"
                sleep 3
            done
        " > /dev/null 2>&1 &
        local wrapper_pid=$!
        echo "$wrapper_pid" > "$PID_FILE"
        sleep 1
        echo -e "${GREEN}Bot 已启动 (PID: $wrapper_pid)${NC}"
    fi
}

cmd_stop() {
    if ! is_running; then
        echo -e "${YELLOW}Bot 未在运行${NC}"
        return 0
    fi

    echo -e "${BLUE}停止 Claude Pilot Bot...${NC}"

    # 设置手动停止标记（防止重启机制拉起）
    touch "$STOP_FLAG"

    # 如果是 macOS launchd 托管
    if [ "$(uname)" = "Darwin" ] && [ -f "$PLIST_FILE" ]; then
        launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null || true
    fi

    # 也发送信号终止进程
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        # 终止进程树
        kill "$pid" 2>/dev/null || true
        # 等待退出
        for i in $(seq 1 10); do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 0.5
        done
        # 还没退出就强杀
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi

    # 清理可能残留的 python bot.py 进程
    pkill -f "python.*bot\\.py" 2>/dev/null || true

    rm -f "$STARTUP_FILE"
    echo -e "${GREEN}Bot 已停止${NC}"
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_status() {
    echo -e "${BLUE}=== Claude Pilot 状态 ===${NC}"
    if is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        echo -e "状态: ${GREEN}运行中${NC}"
        echo -e "PID:  $pid"

        # 运行时长
        if [ -f "$STARTUP_FILE" ]; then
            local start_ts now_ts diff days hours mins secs
            start_ts=$(cat "$STARTUP_FILE")
            now_ts=$(date +%s)
            diff=$((now_ts - start_ts))
            days=$((diff / 86400))
            hours=$(( (diff % 86400) / 3600 ))
            mins=$(( (diff % 3600) / 60 ))
            secs=$((diff % 60))
            local uptime_str=""
            [ "$days" -gt 0 ] && uptime_str="${days}天 "
            [ "$hours" -gt 0 ] && uptime_str="${uptime_str}${hours}小时 "
            [ "$mins" -gt 0 ] && uptime_str="${uptime_str}${mins}分钟 "
            uptime_str="${uptime_str}${secs}秒"
            echo -e "运行时长: $uptime_str"
        fi

        # 健康检查
        local port
        port=$(grep -E '^BOT_PORT=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || echo "5000")
        port="${port:-5000}"
        if curl -s "http://localhost:$port/health" >/dev/null 2>&1; then
            echo -e "HTTP API: ${GREEN}正常${NC} (端口 $port)"
        else
            echo -e "HTTP API: ${YELLOW}未响应${NC} (端口 $port)"
        fi

        # launchd 状态
        if [ "$(uname)" = "Darwin" ] && [ -f "$PLIST_FILE" ]; then
            echo -e "崩溃重启: ${GREEN}已启用${NC} (launchd)"
        else
            echo -e "崩溃重启: ${GREEN}已启用${NC} (wrapper)"
        fi
    else
        echo -e "状态: ${RED}未运行${NC}"
    fi
    echo ""
}

cmd_logs() {
    local log_file="$LOG_DIR/bot.log"
    if [ ! -f "$log_file" ]; then
        echo -e "${YELLOW}暂无日志${NC}"
        return 0
    fi
    # 支持 -f 参数实时跟踪
    if [ "${1:-}" = "-f" ]; then
        tail -f "$log_file"
    else
        tail -100 "$log_file"
    fi
}

cmd_uninstall() {
    echo -e "${RED}=== 卸载 Claude Pilot ===${NC}"
    echo ""
    read -rp "确认卸载？这将停止 Bot 并恢复 Claude 设置 (y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "已取消"
        return 0
    fi

    # 1. 停止 Bot
    cmd_stop

    # 2. 卸载 launchd 服务
    if [ "$(uname)" = "Darwin" ] && [ -f "$PLIST_FILE" ]; then
        launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null || true
        rm -f "$PLIST_FILE"
        echo -e "${GREEN}已移除 launchd 服务${NC}"
    fi

    # 3. 恢复 Claude settings
    if [ -f "$CLAUDE_SETTINGS_BACKUP" ]; then
        cp "$CLAUDE_SETTINGS_BACKUP" "$CLAUDE_SETTINGS"
        rm -f "$CLAUDE_SETTINGS_BACKUP"
        echo -e "${GREEN}已恢复 Claude settings${NC}"
    fi

    # 4. 移除 crc 命令
    if [ -L "/usr/local/bin/crc" ] || [ -f "/usr/local/bin/crc" ]; then
        sudo rm -f "/usr/local/bin/crc" 2>/dev/null || rm -f "/usr/local/bin/crc" 2>/dev/null || true
        echo -e "${GREEN}已移除 crc 命令${NC}"
    fi

    # 5. 清理运行时文件
    rm -f "$PID_FILE" "$STOP_FLAG" "$STARTUP_FILE"
    rm -rf "$LOG_DIR"

    echo ""
    echo -e "${GREEN}卸载完成！${NC}"
    echo -e "项目文件保留在 $INSTALL_DIR，如需完全删除请手动执行:"
    echo -e "  rm -rf $INSTALL_DIR"
}

cmd_help() {
    echo "Claude Pilot 管理工具"
    echo ""
    echo "用法: crc <命令>"
    echo ""
    echo "命令:"
    echo "  start       启动 Bot（支持崩溃自动重启）"
    echo "  stop        停止 Bot"
    echo "  restart     重启 Bot"
    echo "  status      查看运行状态"
    echo "  logs        查看最近日志"
    echo "  logs -f     实时跟踪日志"
    echo "  uninstall   卸载（恢复 Claude 设置）"
    echo "  help        显示帮助"
}

case "${1:-help}" in
    start)      cmd_start ;;
    stop)       cmd_stop ;;
    restart)    cmd_restart ;;
    status)     cmd_status ;;
    logs)       cmd_logs "${2:-}" ;;
    uninstall)  cmd_uninstall ;;
    help|--help|-h) cmd_help ;;
    *)
        echo -e "${RED}未知命令: $1${NC}"
        cmd_help
        exit 1
        ;;
esac
