#!/usr/bin/env bash
set -euo pipefail

# ============ Claude Pilot 一键安装脚本 ============
# 自动配置 Python 虚拟环境、依赖、.env、Claude hooks、crc 命令

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CLAUDE_SETTINGS_BACKUP="$HOME/.claude/settings.backup.json"
CRC_BIN="/usr/local/bin/crc"
PLIST_NAME="com.claude-pilot.bot"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/$PLIST_NAME.plist"
LOG_DIR="$SCRIPT_DIR/logs"
PID_FILE="$SCRIPT_DIR/.bot.pid"
STOP_FLAG="$SCRIPT_DIR/.manual_stop"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ---- 检查前置条件 ----
check_prerequisites() {
    info "检查前置条件..."

    # Python 3.10+
    if command -v python3 &>/dev/null; then
        PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
            err "需要 Python 3.10+，当前版本: $PY_VERSION"
            exit 1
        fi
        ok "Python $PY_VERSION"
    else
        err "未找到 python3，请先安装 Python 3.10+"
        echo "  brew install python3"
        exit 1
    fi

    # Claude Code CLI
    if command -v claude &>/dev/null; then
        ok "Claude Code CLI 已安装"
    else
        warn "未找到 claude 命令，请确保已安装 Claude Code CLI"
        echo "  参考: https://docs.anthropic.com/en/docs/claude-code"
    fi

    # tmux
    if command -v tmux &>/dev/null; then
        ok "tmux 已安装"
    else
        warn "未找到 tmux，远程消息注入功能需要 tmux"
        echo "  brew install tmux"
    fi
}

# ---- 创建虚拟环境 ----
setup_venv() {
    if [ -d "$VENV_DIR" ]; then
        ok "虚拟环境已存在: $VENV_DIR"
    else
        info "创建 Python 虚拟环境..."
        python3 -m venv "$VENV_DIR"
        ok "虚拟环境已创建"
    fi

    info "安装 Python 依赖..."
    "$VENV_DIR/bin/pip" install -q --upgrade pip
    "$VENV_DIR/bin/pip" install -q \
        'python-telegram-bot>=22.6,<23.0' \
        'aiohttp>=3.9,<4.0' \
        'mistune>=3.2,<4.0'
    ok "依赖安装完成"
}

# ---- 配置 .env ----
setup_env() {
    if [ -f "$ENV_FILE" ]; then
        ok ".env 文件已存在，跳过配置"
        # 检查是否有空的必填项
        local token
        token=$(grep -E '^BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2-)
        if [ -z "$token" ] || [ "$token" = "你的telegram_bot_token" ]; then
            warn "BOT_TOKEN 未配置，请手动编辑 $ENV_FILE"
        fi
        return
    fi

    info "配置 .env 文件..."
    echo ""
    echo -e "${YELLOW}请准备以下信息:${NC}"
    echo "  1. Telegram Bot Token（从 @BotFather 获取）"
    echo "  2. 你的 Telegram 用户 ID（从 @userinfobot 获取）"
    echo ""

    read -rp "请输入 Telegram Bot Token: " bot_token
    if [ -z "$bot_token" ]; then
        err "Bot Token 不能为空"
        exit 1
    fi

    read -rp "请输入你的 Telegram 用户 ID（多个用逗号分隔）: " allowed_users
    if [ -z "$allowed_users" ]; then
        err "用户 ID 不能为空"
        exit 1
    fi

    read -rp "工作目录 [默认: ~/workspace]: " project_dir
    project_dir="${project_dir:-~/workspace}"

    read -rp "HTTP API 端口 [默认: 5000]: " bot_port
    bot_port="${bot_port:-5000}"

    cat > "$ENV_FILE" <<EOF
BOT_TOKEN=$bot_token
ALLOWED_USERS=$allowed_users

# 工作目录
PROJECT_DIR=$project_dir

# Claude Code 项目数据目录
# CLAUDE_PROJECTS_DIR=~/.claude/projects

# Bot HTTP API 端口
BOT_PORT=$bot_port
EOF

    ok ".env 配置完成"
}

# ---- 备份并配置 Claude settings ----
setup_claude_settings() {
    info "配置 Claude Code hooks..."

    # 确保 .claude 目录存在
    mkdir -p "$HOME/.claude"

    # 备份原始 settings（只在第一次安装时备份）
    if [ -f "$CLAUDE_SETTINGS" ]; then
        if [ ! -f "$CLAUDE_SETTINGS_BACKUP" ]; then
            cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS_BACKUP"
            ok "已备份原始 settings 到 $CLAUDE_SETTINGS_BACKUP"
        else
            ok "备份文件已存在，跳过备份"
        fi
    else
        # settings.json 不存在，创建空的备份标记
        echo '{}' > "$CLAUDE_SETTINGS_BACKUP"
        ok "未找到现有 settings.json，已创建空备份"
    fi

    # 生成 hooks 配置
    local hooks_json
    hooks_json=$(cat <<HOOKEOF
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$VENV_DIR/bin/python3 $SCRIPT_DIR/hooks/permission.py",
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
            "command": "$VENV_DIR/bin/python3 $SCRIPT_DIR/hooks/notification.py",
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
            "command": "$VENV_DIR/bin/python3 $SCRIPT_DIR/hooks/stop.py",
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
            "command": "$VENV_DIR/bin/python3 $SCRIPT_DIR/hooks/session_start.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
HOOKEOF
)

    # 合并到现有 settings（如果有 python3 + json 模块就用它来合并）
    if [ -f "$CLAUDE_SETTINGS" ]; then
        # 用 Python 智能合并 JSON
        "$VENV_DIR/bin/python3" -c "
import json, sys

with open('$CLAUDE_SETTINGS', 'r') as f:
    existing = json.load(f)

new_hooks = json.loads('''$hooks_json''')

# 合并 hooks：新的覆盖旧的同名 key
if 'hooks' not in existing:
    existing['hooks'] = {}
existing['hooks'].update(new_hooks['hooks'])

with open('$CLAUDE_SETTINGS', 'w') as f:
    json.dump(existing, f, indent=2, ensure_ascii=False)
    f.write('\n')
"
        ok "hooks 已合并到 $CLAUDE_SETTINGS"
    else
        echo "$hooks_json" > "$CLAUDE_SETTINGS"
        ok "已创建 $CLAUDE_SETTINGS"
    fi
}

# ---- 创建日志目录 ----
setup_logs() {
    mkdir -p "$LOG_DIR"
    ok "日志目录: $LOG_DIR"
}

# ---- 安装 crc 命令 ----
install_crc() {
    info "安装 crc 命令..."

    # 生成 crc 脚本
    cat > "$SCRIPT_DIR/crc.sh" <<'CRCEOF'
#!/usr/bin/env bash
set -euo pipefail

# ============ Claude Pilot 管理工具 (crc) ============

INSTALL_DIR="__INSTALL_DIR__"
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
CRCEOF

    # 替换安装路径
    sed -i '' "s|__INSTALL_DIR__|$SCRIPT_DIR|g" "$SCRIPT_DIR/crc.sh"
    chmod +x "$SCRIPT_DIR/crc.sh"

    # 创建符号链接到 /usr/local/bin
    if [ -w /usr/local/bin ] || sudo -n true 2>/dev/null; then
        if [ -L "$CRC_BIN" ] || [ -f "$CRC_BIN" ]; then
            sudo rm -f "$CRC_BIN" 2>/dev/null || rm -f "$CRC_BIN"
        fi
        if sudo ln -sf "$SCRIPT_DIR/crc.sh" "$CRC_BIN" 2>/dev/null || ln -sf "$SCRIPT_DIR/crc.sh" "$CRC_BIN" 2>/dev/null; then
            ok "crc 命令已安装到 $CRC_BIN"
        else
            warn "无法创建符号链接到 $CRC_BIN"
            echo "  请手动执行: sudo ln -sf $SCRIPT_DIR/crc.sh $CRC_BIN"
            echo "  或将以下路径添加到 PATH: $SCRIPT_DIR"
        fi
    else
        warn "需要 sudo 权限来安装 crc 命令"
        echo "  请手动执行: sudo ln -sf $SCRIPT_DIR/crc.sh $CRC_BIN"
    fi
}

# ---- 配置 launchd 防崩溃 (macOS) ----
setup_launchd() {
    if [ "$(uname)" != "Darwin" ]; then
        info "非 macOS 系统，使用 wrapper 脚本实现崩溃重启"
        return
    fi

    info "配置 launchd 防崩溃服务..."
    mkdir -p "$PLIST_DIR"

    cat > "$PLIST_FILE" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>$SCRIPT_DIR/crc-wrapper.sh</string>
    </array>
    <key>RunAtLoad</key>
    <false/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
        <key>PathState</key>
        <dict>
            <key>$STOP_FLAG</key>
            <false/>
        </dict>
    </dict>
    <key>ThrottleInterval</key>
    <integer>3</integer>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/bot.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/bot.log</string>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/local/bin:/opt/homebrew/bin</string>
        <key>HOME</key>
        <string>$HOME</string>
    </dict>
</dict>
</plist>
PLISTEOF

    # 创建 wrapper 脚本（launchd 用）
    cat > "$SCRIPT_DIR/crc-wrapper.sh" <<WRAPPEREOF
#!/usr/bin/env bash
# launchd wrapper：记录启动时间、写 PID、运行 bot

INSTALL_DIR="$SCRIPT_DIR"
PID_FILE="\$INSTALL_DIR/.bot.pid"
STARTUP_FILE="\$INSTALL_DIR/.startup_time"
STOP_FLAG="\$INSTALL_DIR/.manual_stop"

# 检查手动停止标记
if [ -f "\$STOP_FLAG" ]; then
    exit 0
fi

# 记录启动时间
date +%s > "\$STARTUP_FILE"

# 写入 PID
echo \$\$ > "\$PID_FILE"

# 运行 bot
exec "$VENV_DIR/bin/python3" "$SCRIPT_DIR/bot.py"
WRAPPEREOF
    chmod +x "$SCRIPT_DIR/crc-wrapper.sh"

    ok "launchd 防崩溃服务已配置"
}

# ---- 主流程 ----
main() {
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║   Claude Pilot 一键安装             ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
    echo ""

    check_prerequisites
    echo ""
    setup_venv
    echo ""
    setup_env
    echo ""
    setup_claude_settings
    echo ""
    setup_logs
    echo ""
    setup_launchd
    echo ""
    install_crc
    echo ""

    echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║   安装完成！                        ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
    echo ""
    echo "使用方法:"
    echo "  crc start      启动 Bot"
    echo "  crc stop       停止 Bot"
    echo "  crc status     查看状态"
    echo "  crc logs       查看日志"
    echo "  crc logs -f    实时跟踪日志"
    echo "  crc uninstall  卸载"
    echo ""
    echo -e "${YELLOW}首次启动前，请确认 .env 配置正确:${NC}"
    echo "  cat $ENV_FILE"
    echo ""
    echo -e "准备好后运行: ${GREEN}crc start${NC}"
    echo ""
}

main "$@"
