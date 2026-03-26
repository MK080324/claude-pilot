# Claude Pilot v2 — Rebuild Plan

## 设计原则

1. **统一执行路径**：所有 Claude 会话都通过 tmux 启动，终端和 TG 不再是两条代码路径
2. **单一职责分层**：每个文件只做一件事，没有文件超过 200 行
3. **安装即用**：`curl | bash` 完成所有依赖安装和配置收集
4. **重启无感**：关键状态持久化到 `.state.json`，Bot 重启后自动恢复
5. **可观测性**：崩溃自动通知、自动重启、`/status` 主动查询

---

## 用户体验流程

### 阶段一：install.sh（纯终端，不需要手机）

```
curl -fsSL https://raw.githubusercontent.com/.../install.sh | bash
```

脚本执行逻辑：

1. 检测 `claude` CLI → 没有则打印安装链接，**礼貌退出**
2. 检测 `tmux` → 没有则自动 `brew install tmux`
3. 检测 Python 3.10+ → 没有则提示，退出
4. clone 项目到 `~/.claude-pilot`，`pip install` 依赖
5. 收集 `BOT_TOKEN` → 必填，不填过不去
6. 收集 `ALLOWED_USERS`（用户 ID）→ 必填，不填过不去
7. 写入 `.env`
8. 合并写入 Claude Code hooks（不覆盖用户已有配置）
9. 安装 `claude-pilot` CLI 命令到 PATH
10. 打印完成提示：**"现在打开 Telegram，找到你的 Bot，发送 /setup"**

安装结束时 Bot 已在后台运行，等待用户完成 TG 侧配置。

### 阶段二：/setup 引导（Telegram，需要手机，约 30 秒）

Bot 不能自动创建群组（Telegram Bot API 限制），采用**引导式建群**：

```
用户私聊 Bot → /setup

Bot 第一条消息：
  "第 1 步：新建一个 Telegram 群组
   [📱 点我开始建群]  ← 按钮（跳转到 TG 建群界面，预填群名）"

用户建好群后，Bot 第二条消息：
  "第 2 步：将我加入群组并设为管理员
   需要开启「管理话题」权限
   加好后点这里：[✅ 已添加]"

用户点击后，Bot 轮询检测自己是否出现在新群组
检测成功，自动写入 GROUP_CHAT_ID 到 .state.json

Bot 第三条消息：
  "✅ 配置完成！去群里发 /help 开始使用。"
```

**GROUP_CHAT_ID 持久化到 `.state.json`，Bot 重启后无需重新执行 /setup。**

### 日常使用

```
场景 A：手机发起新任务
  在群组建话题 → /projects 选目录 → 发消息开始

场景 B：电脑跑任务，手机远程监控
  电脑：tmux → claude
  手机：TG 群组自动出现新话题，实时看输出，审批权限，发消息打断

场景 C：多任务并行
  每个话题独立跑一个 Claude session，互不干扰
```

---

## 文件结构

```
~/.claude-pilot/
├── install.sh              # 一键安装脚本
├── claude-pilot            # CLI 入口（start/stop/status/enable/logs）
│
├── bot.py                  # 启动入口 + Application 组装（~60 行）
├── config.py               # 配置加载与持久化（~50 行）
├── session.py              # 会话状态 + tmux 操作（~150 行）
├── renderer.py             # Markdown → Telegram HTML（~80 行）
├── api.py                  # aiohttp HTTP 路由，绑定 127.0.0.1（~80 行）
│
├── handlers/
│   ├── commands.py         # /start /setup /projects /resume /quit /status
│   ├── messages.py         # 普通消息处理
│   └── callbacks.py        # InlineKeyboard 回调
│
├── hooks/
│   ├── _common.py          # 共享工具：read_port(), post_to_bot()
│   ├── session_start.py
│   ├── permission.py
│   ├── notification.py
│   └── stop.py
│
├── crash_reporter.py       # 独立守护进程，监控 Bot 并上报崩溃
│
├── .env                    # 用户配置（BOT_TOKEN, ALLOWED_USERS 等）
├── .state.json             # 运行时持久化（GROUP_CHAT_ID 等），不提交 git
├── .pid                    # Bot 进程 PID
├── bot.log                 # 运行日志
└── requirements.txt
```

---

## 核心架构决策

### 统一会话模型：tmux window

所有 Claude 进程都运行在同一个 tmux session 下的独立 window 里：

```
tmux session: claude-pilot
  ├── window: bot          ← bot.py 运行在这里
  ├── window: cp-a3f2b1    ← Claude session #1（~/project-a）
  ├── window: cp-7c9e4d    ← Claude session #2（~/project-b）
  └── window: cp-...
```

- 终端用户自己启动的 claude，通过 `SessionStart` hook 被感知，走相同的状态注册流程
- TG 用户发起的会话，Bot 调用统一的 `launch_session()` 函数，在新 window 里启动
- 两条来源，**同一套代码路径**
- `tmux list-windows -t claude-pilot` 即可查看所有活跃会话，调试方便

### 配置分层

`.env`（用户手动配置，安装时写入，不变）：
```
BOT_TOKEN=...
ALLOWED_USERS=123456789
BOT_PORT=5000
PROJECT_DIR=~/workspace
```

`.state.json`（运行时自动写入，不提交 git）：
```json
{
  "group_chat_id": -1001234567890,
  "notify_chat_id": 123456789
}
```

---

## 可观测性设计（三层）

### 层 1：launchd 自动重启（macOS）

`claude-pilot enable` 写入 launchd plist：

```xml
<key>KeepAlive</key>     <true/>
<key>RunAtLoad</key>     <true/>
<key>ThrottleInterval</key> <integer>30</integer>
```

- Bot 崩溃后 30 秒内自动拉起
- 机器断电重启后开机自启
- `ThrottleInterval=30` 防止断网期间疯狂重启刷日志

### 层 2：crash_reporter.py（独立守护进程）

独立进程，**不依赖 python-telegram-bot**，只用内置 `urllib` 直接调 TG HTTP API。即使 Bot 的 venv 环境损坏，通知依然能发出。自身也由 launchd 托管。

工作流程：
```
每 10 秒检查 Bot PID 是否存活
  ↓ Bot 挂了
读取 bot.log 最后 50 行，提取 Traceback
  ↓
urllib 直接调 TG API → 私聊发给 ALLOWED_USERS[0]（管理员）
  ↓
等待 launchd 把 Bot 拉起（通常 30 秒内）
  ↓
Bot 恢复 → 发第二条消息："✅ Bot 已恢复，停机 23 秒"
```

崩溃通知格式：
```
⚠️ Claude Pilot 崩溃

时间：2024-03-24 14:32:11
今日重启次数：第 2 次

错误信息：
KeyError: 'session_id'
  File "session.py", line 87, in get_session
  File "handlers/messages.py", line 34, in handle_message

Bot 正在自动重启...
```

恢复通知：
```
✅ Claude Pilot 已恢复
停机时长：约 23 秒
```

**通知走私聊**，原因：群组状态在崩溃时未知，私聊是最可靠的通道。崩溃通知是系统级消息，语义上也不属于任何 Claude 会话。

### 层 3：/status 命令（主动查询）

```
🟢 Claude Pilot 运行中

⏱  运行时长：3 天 14 小时
💬 活跃会话：2 个
🔄 今日重启：0 次
📋 最后崩溃：无

会话列表：
  • ~/project-a（话题 #3，运行 2h）
  • ~/project-b（话题 #7，运行 45m）
```

---

## 故障场景覆盖

| 场景 | 自动恢复 | 用户感知 |
|------|----------|----------|
| Bot 进程崩溃 | ✅ launchd 30 秒内拉起 | 私聊收到崩溃通知 + 恢复通知 |
| 机器断电重启 | ✅ 开机自启 | 私聊收到"异常重启，会话已中断"通知 |
| 网络中断 | ✅ 网络恢复后自动恢复 | 断网期间无通知（正常，手机也没网） |
| 断网后消息积压 | ⚠️ watcher 需合并逻辑 | 否则触发 TG 429 限速 |
| 断电导致 tmux 会话消失 | ✅ Bot 恢复，发通知告知 | 私聊："所有会话已中断，可 /resume 恢复" |

**断网期间消息积压的处理**：watcher 检测到积压超过 N 条时，合并为一条摘要发出，而不是逐条推送。

---

## 安全修复（相比原项目）

| 问题 | 原项目 | v2 |
|------|--------|-----|
| HTTP API 暴露 | 绑定 `0.0.0.0` | 绑定 `127.0.0.1` |
| 路径穿越 | session_id 未校验 | 白名单正则 `[a-f0-9\-]{8,36}` |
| tmux 注入 | 只过滤换行 | 过滤全部控制字符 `[\x00-\x1f\x7f]` |
| TG 会话并发 | 无锁 | per-topic `asyncio.Lock` |

---

## CLI 命令设计

```
claude-pilot start    # 后台启动 Bot
claude-pilot stop     # 停止 Bot
claude-pilot status   # 查看运行状态
claude-pilot enable   # 注册 launchd 开机自启
claude-pilot disable  # 取消开机自启
claude-pilot logs     # tail -f bot.log
```

---

## Telegram 命令列表

| 命令 | 作用 |
|------|------|
| `/setup` | 引导配置群组（首次使用） |
| `/projects` | 选择项目目录，发起新会话 |
| `/resume [名称或ID]` | 恢复历史会话 |
| `/rename <名称>` | 重命名当前会话 |
| `/interrupt` | 中断 Claude 当前回复 |
| `/quit` | 暂停当前会话（可 resume） |
| `/delete <名称或ID>` | 删除会话（二次确认） |
| `/bypass` | 开关权限审批 |
| `/status` | 查看 Bot 运行状态 |
| `/info` | 查看当前会话信息 |

---

## 与原项目的差异对比

| 方面 | 原项目 | v2 |
|------|--------|-----|
| 会话启动 | 两条路径（终端/TG） | 统一 tmux window 模型 |
| 文件结构 | 单文件 1372 行 | 分层，每文件 < 200 行 |
| 状态持久化 | 内存，重启丢失 | `.state.json`，重启恢复 |
| HTTP API | 绑定 `0.0.0.0` | 绑定 `127.0.0.1` |
| 并发保护 | TG 会话无锁 | per-topic `asyncio.Lock` |
| hook 工具函数 | 4 文件重复 | 共享 `_common.py` |
| 安装方式 | 手动克隆 + 手动配置 | `curl \| bash` 一键完成 |
| 群组配置 | 全手动，无引导 | `/setup` 三步引导 |
| 崩溃处理 | 无 | crash_reporter + launchd |
| 可观测性 | 无 | `/status` + 崩溃私聊通知 |
| 断电恢复 | 需手动重启 | launchd 开机自启 |
