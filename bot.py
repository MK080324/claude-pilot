import os
import json
import asyncio
import signal
import uuid
import time
import html as html_lib
import mistune
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters


# ============ Telegram HTML 渲染器 ============

class TelegramHTMLRenderer(mistune.HTMLRenderer):
    """将 Markdown AST 渲染为 Telegram 支持的 HTML 子集。
    支持: <b>, <i>, <code>, <pre>, <s>, <a>, <blockquote>
    不支持的格式做优雅降级。"""

    def text(self, text):
        return html_lib.escape(text)

    def strong(self, text):
        return f"<b>{text}</b>"

    def emphasis(self, text):
        return f"<i>{text}</i>"

    def codespan(self, text):
        return f"<code>{html_lib.escape(text)}</code>"

    def block_code(self, code, info=None):
        return f"<pre>{html_lib.escape(code)}</pre>\n"

    def link(self, text, url, title=None):
        url = html_lib.escape(url)
        return f'<a href="{url}">{text}</a>'

    def image(self, text, url, title=None):
        # TG 不支持 <img>，降级为链接
        url = html_lib.escape(url)
        alt = text or "image"
        return f'🖼 <a href="{url}">{alt}</a>'

    def block_quote(self, text):
        return f"<blockquote>{text}</blockquote>\n"

    def heading(self, text, level, **attrs):
        # TG 没有 <h1>~<h6>，降级为加粗
        return f"\n<b>{text}</b>\n\n"

    def thematic_break(self):
        # TG 没有 <hr>，用分割线字符代替
        return "———\n"

    def paragraph(self, text):
        return f"{text}\n\n"

    def linebreak(self):
        return "\n"

    def softbreak(self):
        return "\n"

    def list(self, text, ordered, **attrs):
        if ordered:
            # 给有序列表加上数字编号
            lines = text.strip().split("\n")
            numbered = []
            n = attrs.get("start", 1)
            for line in lines:
                if line.startswith("• "):
                    numbered.append(f"{n}. {line[2:]}")
                    n += 1
                else:
                    numbered.append(line)
            return "\n".join(numbered) + "\n\n"
        return f"{text}\n"

    def list_item(self, text):
        # 移除末尾多余换行，加项目符号
        text = text.strip()
        return f"• {text}\n"

    def blank_line(self):
        return "\n"

    def inline_html(self, html):
        return html_lib.escape(html)

    def block_html(self, html):
        return html_lib.escape(html)

    # strikethrough 插件注册的方法名
    def strikethrough(self, text):
        return f"<s>{text}</s>"

    # table 插件注册的方法名
    def table(self, text):
        return f"<pre>{text.strip()}</pre>\n"

    def table_head(self, text):
        return text

    def table_body(self, text):
        return text

    def table_row(self, text):
        # 去掉末尾的分隔符
        return f"{text.rstrip(' |')}\n"

    def table_cell(self, text, align=None, head=False):
        padding = text.strip()
        if head:
            padding = f"<b>{padding}</b>"
        return f"{padding} | "


# 创建 Markdown 解析器（含 strikethrough + table 插件）
_tg_markdown = mistune.create_markdown(
    renderer=TelegramHTMLRenderer(escape=False),
    plugins=["strikethrough", "table"],
)

# ============ 配置加载 ============

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        print(f"⚠️  未找到 {env_path}，请复制 .env.example 为 .env 并填入配置")
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                key = key.strip()
                if key not in os.environ:
                    os.environ[key] = value.strip()

load_env()
BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED_USERS = [int(x) for x in os.environ["ALLOWED_USERS"].split(",")]
BOT_PORT = int(os.environ.get("BOT_PORT", "5000"))
DEFAULT_PROJECT_DIR = os.path.expanduser(os.environ.get("PROJECT_DIR", "~/workspace"))
CLAUDE_PROJECTS_DIR = os.path.expanduser(os.environ.get("CLAUDE_PROJECTS_DIR", "~/.claude/projects"))

# ============ 全局状态 ============

NOTIFY_CHAT_ID = None      # 私聊通知 ID（后备）
GROUP_CHAT_ID = None       # 群组 ID（用于创建话题）
tg_app = None              # Telegram app 引用
permission_enabled = True  # 权限审批开关
sessions = {}              # topic_id → session info
session_topics = {}        # session_id → topic_id（反向映射）
pending_permissions = {}   # 权限请求存储
tmux_locks = {}            # tmux_pane → asyncio.Lock（防止并发注入交叉）
topic_locks = {}           # topic_id → asyncio.Lock（防止 TG 会话并发启动多个 claude -p）
BOT_START_TIME = time.time()  # Bot 启动时间戳
message_count = 0          # 消息计数（收到的用户消息总数）
conversation_count = 0     # 对话计数（启动过的 Claude 会话数）

# ============ 工具函数 ============

def get_topic_id(update):
    if update.message and update.message.message_thread_id:
        return update.message.message_thread_id
    return 0

def gfm_to_html(text):
    """GitHub-flavored Markdown → Telegram HTML（基于 mistune 解析）"""
    result = _tg_markdown(text)
    # 清理多余的连续空行
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result.strip()

def _split_html_chunks(text, limit=4096):
    """先将 Markdown 整体转为 HTML，再在段落边界处分段，确保每段 HTML 不超过 limit。
    返回 (html_chunk, raw_chunk) 元组列表，raw_chunk 用于 fallback。"""
    html_full = gfm_to_html(text)

    # 短文本直接返回
    if len(html_full) <= limit:
        return [(html_full, text)]

    # 按双换行拆分原始 Markdown 为段落块，逐块转 HTML 并累积
    blocks = text.split("\n\n")
    results = []
    cur_md = ""
    cur_html = ""

    for block in blocks:
        candidate_md = (cur_md + "\n\n" + block) if cur_md else block
        candidate_html = gfm_to_html(candidate_md)

        if len(candidate_html) <= limit:
            cur_md = candidate_md
            cur_html = candidate_html
        else:
            # 累积的内容先保存
            if cur_html:
                results.append((cur_html, cur_md))

            # 单个 block 转 HTML 后是否也超长
            block_html = gfm_to_html(block)
            if len(block_html) <= limit:
                cur_md = block
                cur_html = block_html
            else:
                # 单块超长，按纯文本 4096 硬切（放弃 HTML 格式）
                for i in range(0, len(block), limit):
                    piece = block[i:i + limit]
                    results.append((piece, piece))
                cur_md = ""
                cur_html = ""

    if cur_html:
        results.append((cur_html, cur_md))
    return results

async def send_reply(update, text, markdown=False):
    """回复用户消息，自动分段。markdown=True 时确保每段 HTML 不超过 TG 限制"""
    if markdown:
        for html_chunk, raw_chunk in _split_html_chunks(text):
            try:
                await update.message.reply_text(html_chunk, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(raw_chunk)
    else:
        for i in range(0, len(text), 4096):
            await update.message.reply_text(text[i:i + 4096])

async def send_to_topic(chat_id, topic_id, text, markdown=False):
    """发送消息到指定话题，自动分段。markdown=True 时确保每段 HTML 不超过 TG 限制"""
    if markdown:
        for html_chunk, raw_chunk in _split_html_chunks(text):
            kwargs = {"chat_id": chat_id, "text": html_chunk}
            if topic_id:
                kwargs["message_thread_id"] = topic_id
            try:
                await tg_app.bot.send_message(**kwargs, parse_mode="HTML")
            except Exception:
                kwargs["text"] = raw_chunk
                await tg_app.bot.send_message(**kwargs)
    else:
        for i in range(0, len(text), 4096):
            kwargs = {"chat_id": chat_id, "text": text[i:i + 4096]}
            if topic_id:
                kwargs["message_thread_id"] = topic_id
            await tg_app.bot.send_message(**kwargs)

def format_tool_use(name, input_data):
    if name == "Bash":
        return f"执行命令: {input_data.get('command', '?')}"
    elif name == "Read":
        return f"读取文件: {input_data.get('file_path', '?')}"
    elif name == "Edit":
        return f"编辑文件: {input_data.get('file_path', '?')}"
    elif name == "Write":
        return f"写入文件: {input_data.get('file_path', '?')}"
    elif name == "Glob":
        return f"搜索文件: {input_data.get('pattern', '?')}"
    elif name == "Grep":
        return f"搜索内容: {input_data.get('pattern', '?')}"
    elif name == "Task":
        return f"启动子任务: {input_data.get('description', '?')}"
    else:
        return f"{name}"

async def tmux_send_message(tmux_pane, text):
    """通过 tmux 向指定 pane 注入消息。
    Escape(打断) → 等待 → i(进入输入模式) → 输入文本 → Enter(提交)"""
    # 换行替换为空格，避免被当作 Enter 提交
    text = text.replace("\n", " ").replace("\r", " ")

    async def send_keys(*keys):
        proc = await asyncio.create_subprocess_exec(
            "tmux", "send-keys", "-t", tmux_pane, *keys,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"tmux send-keys {keys} 失败: {stderr.decode()}")

    # 1. Escape 打断当前生成（空闲时无害）
    await send_keys("Escape")

    # 2. 等待 Claude 回到普通模式
    await asyncio.sleep(1.0)

    # 3. 按 i 进入输入模式（类似 vim）
    await send_keys("i")
    await asyncio.sleep(0.3)

    # 4. 输入文本（-l = literal，不解释特殊键名）
    await send_keys("-l", text)

    # 5. Enter 提交
    await send_keys("Enter")

def resolve_topic(data):
    """从 HTTP 请求数据中解析出目标 chat_id 和 topic_id。
    优先用 session_id 查映射，其次用请求中的 chat_id/thread_id。"""
    session_id = data.get("session_id")
    if session_id and session_id in session_topics:
        topic_id = session_topics[session_id]
        session = sessions.get(topic_id, {})
        chat_id = session.get("chat_id", GROUP_CHAT_ID or NOTIFY_CHAT_ID)
        return chat_id, topic_id
    chat_id = data.get("chat_id", GROUP_CHAT_ID or NOTIFY_CHAT_ID)
    thread_id = data.get("thread_id")
    return chat_id, thread_id

# ============ 会话历史回显 ============

async def show_context(chat_id, topic_id, session_id):
    """读取 JSONL 文件，在话题中显示会话历史"""
    transcript_path = None
    for pdir in os.listdir(CLAUDE_PROJECTS_DIR):
        fpath = os.path.join(CLAUDE_PROJECTS_DIR, pdir, f"{session_id}.jsonl")
        if os.path.exists(fpath):
            transcript_path = fpath
            break
    if not transcript_path:
        return

    messages = []
    with open(transcript_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            if etype == "user" and event.get("userType") == "external":
                content = event.get("message", {}).get("content", "")
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
                if text.strip():
                    messages.append(("👤", text.strip()))

            elif etype == "assistant":
                content = event.get("message", {}).get("content", [])
                texts = []
                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text" and block.get("text", "").strip():
                            texts.append(block["text"].strip())
                if texts:
                    messages.append(("🤖", "\n".join(texts)))

    if not messages:
        await send_to_topic(chat_id, topic_id, "📜 没有找到历史对话记录")
        return

    parts = ["📜 会话历史:"]
    for role, text in messages:
        truncated = text[:500] + "..." if len(text) > 500 else text
        parts.append(f"\n{role} {truncated}")

    await send_to_topic(chat_id, topic_id, "\n".join(parts))

# ============ JSONL 文件监听器（终端会话流式输出） ============

async def watch_transcript(session_id, transcript_path, chat_id, topic_id):
    """监听 JSONL 对话文件，实时转发新内容到 Telegram 话题"""
    last_pos = 0
    seen_uuids = set()
    buffer = ""

    while True:
        try:
            if not os.path.exists(transcript_path):
                await asyncio.sleep(0.5)
                continue

            current_size = os.path.getsize(transcript_path)
            if current_size > last_pos:
                with open(transcript_path, "r") as f:
                    f.seek(last_pos)
                    new_data = f.read()
                    last_pos = f.tell()

                buffer += new_data
                lines = buffer.split("\n")
                buffer = lines[-1]  # 最后一个可能不完整，留到下次

                for line in lines[:-1]:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg_uuid = event.get("uuid")
                    if msg_uuid:
                        if msg_uuid in seen_uuids:
                            continue
                        seen_uuids.add(msg_uuid)

                    await process_transcript_event(event, chat_id, topic_id)

        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"[Watcher {session_id[:8]}] 错误: {e}")

        await asyncio.sleep(0.5)

async def process_transcript_event(event, chat_id, topic_id):
    """处理 JSONL 文件中的单个事件"""
    event_type = event.get("type")

    if event_type == "assistant":
        message = event.get("message", {})
        content = message.get("content", [])

        if isinstance(content, str):
            if content.strip():
                # 记录最后一条 assistant 文本，供 /retry 使用
                session = sessions.get(topic_id)
                if session:
                    session["last_result"] = content
                await send_to_topic(chat_id, topic_id, content, markdown=True)
        elif isinstance(content, list):
            for block in content:
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if text.strip():
                        session = sessions.get(topic_id)
                        if session:
                            session["last_result"] = text
                        await send_to_topic(chat_id, topic_id, text, markdown=True)
                elif block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    input_data = block.get("input", {})
                    desc = format_tool_use(name, input_data)
                    await send_to_topic(chat_id, topic_id, f"🔧 {desc}")

# ============ 权限按钮 ============

async def send_permission_request(chat_id, request_id, description, bot, thread_id=None):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 允许", callback_data=f"allow:{request_id}"),
            InlineKeyboardButton("❌ 拒绝", callback_data=f"deny:{request_id}"),
        ]
    ])
    kwargs = {
        "chat_id": chat_id,
        "text": f"🔐 权限请求:\n{description}",
        "reply_markup": keyboard,
    }
    if thread_id:
        kwargs["message_thread_id"] = thread_id
    await bot.send_message(**kwargs)

# ============ 按钮回调 ============

async def handle_button(update: Update, context):
    query = update.callback_query
    if query.from_user.id not in ALLOWED_USERS:
        await query.answer("未授权")
        return
    data = query.data

    # 项目选择: proj:dirname
    if data.startswith("proj:"):
        dirname = data[5:]
        path = os.path.join(DEFAULT_PROJECT_DIR, dirname)
        thread_id = query.message.message_thread_id or 0
        if thread_id not in sessions:
            sessions[thread_id] = {"session_id": None, "project_dir": path}
        else:
            sessions[thread_id]["project_dir"] = path
        await query.edit_message_text(f"✅ 已设置项目目录: {path}")
        await query.answer()
        return

    # 恢复会话: resume:session_id
    if data.startswith("resume:"):
        session_id = data[7:]
        # 拦截已绑定的会话
        if session_id in session_topics:
            await query.edit_message_text("❌ 该会话已绑定话题，暂无法恢复。")
            await query.answer()
            return
        thread_id = query.message.message_thread_id or 0
        project_dir = DEFAULT_PROJECT_DIR
        custom_title = ""
        # 找到 JSONL 文件，只读头部取 cwd，读尾部取 custom-title
        for pdir in os.listdir(CLAUDE_PROJECTS_DIR):
            fpath = os.path.join(CLAUDE_PROJECTS_DIR, pdir, f"{session_id}.jsonl")
            if os.path.exists(fpath):
                try:
                    # 头部取 cwd
                    with open(fpath, "r") as f:
                        head = f.read(8192)
                    for line in head.split("\n"):
                        try:
                            obj = json.loads(line)
                            if obj.get("cwd"):
                                project_dir = obj["cwd"]
                                break
                        except json.JSONDecodeError:
                            continue
                    # 尾部取 custom-title
                    file_size = os.path.getsize(fpath)
                    tail_size = min(4096, file_size)
                    with open(fpath, "rb") as f:
                        f.seek(max(0, file_size - tail_size))
                        tail = f.read().decode("utf-8", errors="ignore")
                    for line in reversed(tail.split("\n")):
                        if '"custom-title"' not in line:
                            continue
                        try:
                            obj = json.loads(line)
                            if obj.get("type") == "custom-title":
                                custom_title = obj.get("customTitle", "")
                                break
                        except json.JSONDecodeError:
                            continue
                except Exception:
                    pass
                break
        sessions[thread_id] = {
            "session_id": session_id,
            "project_dir": project_dir,
            "chat_id": query.message.chat.id,
            "source": "telegram",
        }
        session_topics[session_id] = thread_id
        display_name = custom_title or "未命名对话"
        await query.edit_message_text(f"✅ 已恢复会话\n📛 {display_name}\n🆔 {session_id[:8]}...")
        await query.answer()
        # 自动改话题名
        chat_id = query.message.chat.id
        if custom_title and thread_id:
            try:
                await tg_app.bot.edit_forum_topic(
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    name=custom_title[:128],
                )
            except Exception as e:
                print(f"[Resume] 修改话题名失败: {e}")
        await show_context(query.message.chat.id, thread_id, session_id)
        return

    # 删除确认: delete_yes:session_id / delete_no:session_id
    if data.startswith("delete_yes:"):
        sid = data[11:]
        # 断开话题绑定
        topic_id_to_clear = session_topics.pop(sid, None)
        if topic_id_to_clear is not None:
            s = sessions.pop(topic_id_to_clear, None)
            if s and s.get("watcher_task"):
                s["watcher_task"].cancel()
        # 删除 JSONL 文件
        deleted = False
        for pdir in os.listdir(CLAUDE_PROJECTS_DIR):
            fpath = os.path.join(CLAUDE_PROJECTS_DIR, pdir, f"{sid}.jsonl")
            if os.path.exists(fpath):
                os.remove(fpath)
                deleted = True
                break
        # 关闭话题
        chat_id = query.message.chat.id
        thread_id = query.message.message_thread_id
        if thread_id and chat_id:
            try:
                await tg_app.bot.close_forum_topic(chat_id=chat_id, message_thread_id=thread_id)
            except Exception:
                pass
        status = "文件已删除" if deleted else "文件未找到，但已断开绑定"
        await query.edit_message_text(f"🗑️ 会话已删除 ({sid[:8]}...)\n{status}\n请删除当前话题，当前话题已不可继续对话。")
        await query.answer()
        return

    if data.startswith("delete_no:"):
        await query.edit_message_text("已取消删除")
        await query.answer()
        return

    # 权限按钮: allow:xxx / deny:xxx
    action, request_id = data.split(":", 1)
    if request_id not in pending_permissions:
        await query.answer("该请求已过期")
        await query.edit_message_text("该请求已过期")
        return
    permission = pending_permissions[request_id]
    if action == "allow":
        permission["decision"] = "allow"
        await query.edit_message_text("✅ 已允许")
    else:
        permission["decision"] = "deny"
        await query.edit_message_text("❌ 已拒绝")
    permission["event"].set()
    await query.answer()

# ============ HTTP API ============

async def http_session_start(request):
    """SessionStart hook: 终端会话开始，创建话题并启动监听"""
    global conversation_count
    conversation_count += 1
    if not GROUP_CHAT_ID:
        return web.json_response({"status": "no_group"})
    data = await request.json()
    session_id = data.get("session_id")
    transcript_path = data.get("transcript_path", "")
    cwd = data.get("cwd", "")
    tmux_pane = data.get("tmux_pane")
    if not session_id:
        return web.json_response({"error": "no session_id"}, status=400)
    if session_id in session_topics:
        return web.json_response({"status": "exists"})

    project_name = os.path.basename(cwd) if cwd else "unknown"
    try:
        topic = await tg_app.bot.create_forum_topic(
            chat_id=GROUP_CHAT_ID,
            name=f"🖥️ {project_name}",
            icon_color=0x6FB9F0,
        )
        topic_id = topic.message_thread_id
    except Exception as e:
        print(f"创建话题失败: {e}")
        return web.json_response({"error": str(e)}, status=500)

    sessions[topic_id] = {
        "session_id": session_id,
        "project_dir": cwd,
        "chat_id": GROUP_CHAT_ID,
        "transcript_path": transcript_path,
        "source": "terminal",
        "watcher_task": None,
        "tmux_pane": tmux_pane,
    }
    session_topics[session_id] = topic_id

    await tg_app.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        message_thread_id=topic_id,
        text=f"🖥️ 终端会话已开始\n📁 {cwd}\n🆔 {session_id[:8]}...\n📡 tmux: {tmux_pane or '未检测到'}",
    )

    task = asyncio.create_task(
        watch_transcript(session_id, transcript_path, GROUP_CHAT_ID, topic_id)
    )
    sessions[topic_id]["watcher_task"] = task

    return web.json_response({"status": "created", "topic_id": topic_id})

async def http_session_stop(request):
    """Stop hook: 会话结束，发通知并停止监听"""
    data = await request.json()
    session_id = data.get("session_id")
    message = data.get("message", "Claude 已停止")
    chat_id, thread_id = resolve_topic(data)

    if chat_id:
        kwargs = {"chat_id": chat_id, "text": f"📢 {message}"}
        if thread_id:
            kwargs["message_thread_id"] = thread_id
        try:
            await tg_app.bot.send_message(**kwargs)
        except Exception:
            pass

    if session_id and session_id in session_topics:
        topic_id = session_topics[session_id]
        session = sessions.get(topic_id)
        # tmux 交互式会话：每轮结束都会触发 Stop，但 Claude 还活着，不要停监听
        if session and session.get("watcher_task") and not session.get("tmux_pane"):
            session["watcher_task"].cancel()
            session["watcher_task"] = None

    return web.json_response({"status": "ok"})

async def http_permission(request):
    """Hook 脚本调用: 请求权限审批"""
    data = await request.json()
    force_approval = data.get("force_approval", False)
    # 非敏感文件 + bypass 模式 = 直接放行
    if not permission_enabled and not force_approval:
        return web.json_response({"decision": "allow"})
    if not (NOTIFY_CHAT_ID or GROUP_CHAT_ID):
        return web.json_response({"error": "请先在 Telegram 中发 /start"}, status=503)
    description = data.get("description", "未知操作")
    chat_id, thread_id = resolve_topic(data)
    if not chat_id:
        chat_id = NOTIFY_CHAT_ID

    request_id = str(uuid.uuid4())[:8]
    event = asyncio.Event()
    pending_permissions[request_id] = {
        "description": description,
        "event": event,
        "decision": None,
    }
    await send_permission_request(chat_id, request_id, description, tg_app.bot, thread_id=thread_id)
    try:
        await asyncio.wait_for(event.wait(), timeout=120)
    except asyncio.TimeoutError:
        pending_permissions.pop(request_id, None)
        return web.json_response({"decision": "deny", "reason": "超时未回复"})
    decision = pending_permissions.pop(request_id)["decision"]
    return web.json_response({"decision": decision})

async def http_notification(request):
    """Hook 脚本调用: 发送通知"""
    if not (NOTIFY_CHAT_ID or GROUP_CHAT_ID):
        return web.json_response({"error": "请先在 Telegram 中发 /start"}, status=503)
    data = await request.json()
    message = data.get("message", "来自 Claude 的通知")
    chat_id, thread_id = resolve_topic(data)
    if not chat_id:
        chat_id = NOTIFY_CHAT_ID
    kwargs = {"chat_id": chat_id, "text": f"📢 {message}"}
    if thread_id:
        kwargs["message_thread_id"] = thread_id
    await tg_app.bot.send_message(**kwargs)
    return web.json_response({"status": "ok"})

async def http_health(request):
    return web.json_response({"status": "running"})

# ============ Telegram 命令 ============

async def cmd_start(update: Update, context):
    global NOTIFY_CHAT_ID, GROUP_CHAT_ID
    if update.effective_user.id not in ALLOWED_USERS:
        return
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        GROUP_CHAT_ID = chat_id
        NOTIFY_CHAT_ID = chat_id
        mode = "群组模式（话题自动创建）"
    else:
        NOTIFY_CHAT_ID = chat_id
        mode = "私聊模式"
    await update.message.reply_text(
        f"Claude Code Remote Bot — {mode}\n\n"
        "命令:\n"
        "/projects - 选择项目目录\n"
        "/resume - 恢复历史会话\n"
        "/rename - 重命名当前会话\n"
        "/interrupt - 中断 Claude 回复\n"
        "/quit - 暂停当前会话\n"
        "/delete - 删除对话（不可恢复）\n"
        "/info - 查看会话信息\n"
        "/retry - 重发 Claude 最后一条回复\n"
        "/bypass - 切换权限审批\n"
        "/setdir - 手动设置项目目录\n"
        "/status - 查看 Bot 运行状态"
    )

async def cmd_bypass(update: Update, context):
    global permission_enabled
    if update.effective_user.id not in ALLOWED_USERS:
        return
    permission_enabled = not permission_enabled
    if permission_enabled:
        await update.message.reply_text("🔐 权限审批: 开启")
    else:
        await update.message.reply_text("🔓 权限审批: 关闭")

async def cmd_projects(update: Update, context):
    if update.effective_user.id not in ALLOWED_USERS:
        return
    try:
        dirs = sorted([
            d for d in os.listdir(DEFAULT_PROJECT_DIR)
            if os.path.isdir(os.path.join(DEFAULT_PROJECT_DIR, d)) and not d.startswith(".")
        ])
    except OSError:
        await update.message.reply_text(f"无法读取目录: {DEFAULT_PROJECT_DIR}")
        return
    if not dirs:
        await update.message.reply_text(f"{DEFAULT_PROJECT_DIR} 下没有子目录")
        return
    buttons = []
    row = []
    for d in dirs:
        row.append(InlineKeyboardButton(d, callback_data=f"proj:{d}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    await update.message.reply_text("选择项目目录:", reply_markup=InlineKeyboardMarkup(buttons))

def _scan_sessions():
    """扫描所有 JSONL 文件，只读尾部 4KB 获取 custom-title。返回会话列表。"""
    results = []
    for pdir in os.listdir(CLAUDE_PROJECTS_DIR):
        full_dir = os.path.join(CLAUDE_PROJECTS_DIR, pdir)
        if not os.path.isdir(full_dir):
            continue
        for fname in os.listdir(full_dir):
            if not fname.endswith(".jsonl"):
                continue
            fpath = os.path.join(full_dir, fname)
            session_id = fname[:-6]
            try:
                title = ""
                file_size = os.path.getsize(fpath)
                tail_size = min(4096, file_size)
                with open(fpath, "rb") as f:
                    f.seek(max(0, file_size - tail_size))
                    tail = f.read().decode("utf-8", errors="ignore")
                for line in reversed(tail.split("\n")):
                    if '"custom-title"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("type") == "custom-title":
                            title = obj.get("customTitle", "")
                            break
                    except json.JSONDecodeError:
                        continue
                results.append({
                    "session_id": session_id,
                    "title": title,
                    "path": fpath,
                    "mtime": os.path.getmtime(fpath),
                })
            except Exception:
                continue
    return results

async def cmd_resume(update: Update, context):
    if update.effective_user.id not in ALLOWED_USERS:
        return
    topic_id = get_topic_id(update)
    if topic_id in sessions and sessions[topic_id].get("session_id"):
        await update.message.reply_text(
            "当前话题已绑定对话，请执行 /quit 解除绑定后再继续。\n"
            "详细状态信息请输入 /info 查看"
        )
        return
    try:
        all_sessions = _scan_sessions()
    except OSError:
        await update.message.reply_text("无法读取会话目录")
        return

    # 标记已绑定的会话，但不过滤掉
    bound_sids = set(session_topics.keys())
    all_sessions.sort(key=lambda x: x["mtime"], reverse=True)

    query_str = " ".join(context.args) if context.args else ""

    if query_str:
        # 按 ID 前缀或名称模糊匹配（排除已绑定的）
        matches = []
        for s in all_sessions:
            if s["session_id"] in bound_sids:
                continue
            if s["session_id"] == query_str or s["session_id"].startswith(query_str):
                matches.append(s)
            elif s["title"] and query_str.lower() in s["title"].lower():
                matches.append(s)
        if not matches:
            await update.message.reply_text(f"未找到匹配的会话: {query_str}")
            return
        if len(matches) == 1:
            # 直接恢复
            target = matches[0]
            topic_id = get_topic_id(update)
            project_dir = DEFAULT_PROJECT_DIR
            # 从头部读取 cwd
            try:
                with open(target["path"], "r") as f:
                    head = f.read(8192)
                for line in head.split("\n"):
                    try:
                        obj = json.loads(line)
                        if obj.get("cwd"):
                            project_dir = obj["cwd"]
                            break
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass
            sid = target["session_id"]
            sessions[topic_id] = {
                "session_id": sid,
                "project_dir": project_dir,
                "chat_id": update.effective_chat.id,
                "source": "telegram",
            }
            session_topics[sid] = topic_id
            display = target["title"] or "未命名对话"
            await update.message.reply_text(f"✅ 已恢复会话\n📛 {display}\n🆔 {sid[:8]}...")
            if target["title"] and topic_id:
                try:
                    await tg_app.bot.edit_forum_topic(
                        chat_id=update.effective_chat.id,
                        message_thread_id=topic_id,
                        name=target["title"][:128],
                    )
                except Exception:
                    pass
            await show_context(update.effective_chat.id, topic_id, sid)
            return
        # 多个匹配，提示用户精确指定
        lines = [f"找到 {len(matches)} 个匹配，请更精确地指定:"]
        for m in matches[:8]:
            label = m["title"] or "未命名对话"
            lines.append(f"  - {label} ({m['session_id'][:8]}...)")
        await update.message.reply_text("\n".join(lines))
        return

    # 无参数：显示按钮列表（最多 5 条）
    if not all_sessions:
        await update.message.reply_text("没有找到可恢复的会话")
        return

    buttons = []
    for s in all_sessions[:5]:
        is_bound = s["session_id"] in bound_sids
        if s["title"]:
            label = s["title"][:30] + ("（已绑定话题）" if is_bound else "")
        else:
            label = "未命名对话" + ("（已绑定话题）" if is_bound else "")
        buttons.append([InlineKeyboardButton(label, callback_data=f"resume:{s['session_id']}")])
    await update.message.reply_text("选择要恢复的会话:", reply_markup=InlineKeyboardMarkup(buttons))

async def cmd_quit(update: Update, context):
    if update.effective_user.id not in ALLOWED_USERS:
        return
    topic_id = get_topic_id(update)
    session = sessions.get(topic_id)
    if not session or not session.get("session_id"):
        await update.message.reply_text("当前话题没有活跃会话")
        return
    session_id = session["session_id"]
    if session.get("watcher_task"):
        session["watcher_task"].cancel()
    proc = session.get("proc")
    if proc and proc.returncode is None:
        proc.terminate()
    session_topics.pop(session_id, None)
    del sessions[topic_id]
    await update.message.reply_text(f"⏸️ 会话已暂停 ({session_id[:8]}...)\n用 /resume 可以恢复")

async def cmd_delete(update: Update, context):
    """删除对话：/delete <会话ID或自定义名称>"""
    if update.effective_user.id not in ALLOWED_USERS:
        return
    if not context.args:
        await update.message.reply_text("用法: /delete <会话ID 或 自定义名称>")
        return
    query_str = " ".join(context.args)

    # 搜索匹配的 JSONL 文件
    matches = []
    try:
        for pdir in os.listdir(CLAUDE_PROJECTS_DIR):
            full_dir = os.path.join(CLAUDE_PROJECTS_DIR, pdir)
            if not os.path.isdir(full_dir):
                continue
            for fname in os.listdir(full_dir):
                if not fname.endswith(".jsonl"):
                    continue
                fpath = os.path.join(full_dir, fname)
                session_id = fname[:-6]
                # 精确匹配 session_id
                if session_id == query_str or session_id.startswith(query_str):
                    matches.append({"session_id": session_id, "path": fpath, "match": "id"})
                    continue
                # 匹配 custom-title（读尾部）
                try:
                    file_size = os.path.getsize(fpath)
                    tail_size = min(4096, file_size)
                    with open(fpath, "rb") as f:
                        f.seek(max(0, file_size - tail_size))
                        tail = f.read().decode("utf-8", errors="ignore")
                    for line in reversed(tail.split("\n")):
                        if '"custom-title"' not in line:
                            continue
                        try:
                            obj = json.loads(line)
                            if obj.get("type") == "custom-title" and query_str in obj.get("customTitle", ""):
                                matches.append({"session_id": session_id, "path": fpath, "match": obj["customTitle"]})
                                break
                        except json.JSONDecodeError:
                            continue
                except Exception:
                    continue
    except OSError:
        await update.message.reply_text("无法读取会话目录")
        return

    if not matches:
        await update.message.reply_text(f"未找到匹配的会话: {query_str}")
        return
    if len(matches) > 1:
        lines = [f"找到 {len(matches)} 个匹配，请更精确地指定:"]
        for m in matches[:5]:
            label = m["match"] if m["match"] != "id" else m["session_id"][:16]
            lines.append(f"  - {label} ({m['session_id'][:8]}...)")
        await update.message.reply_text("\n".join(lines))
        return

    target = matches[0]
    sid = target["session_id"]
    label = target["match"] if target["match"] != "id" else sid[:16]
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 确认删除", callback_data=f"delete_yes:{sid}"),
            InlineKeyboardButton("❌ 取消", callback_data=f"delete_no:{sid}"),
        ]
    ])
    await update.message.reply_text(
        f"⚠️ 确认删除会话?\n🆔 {sid[:16]}...\n📛 {label}\n\n此操作不可恢复！",
        reply_markup=keyboard,
    )

async def cmd_rename(update: Update, context):
    if update.effective_user.id not in ALLOWED_USERS:
        return
    if not context.args:
        await update.message.reply_text("用法: /rename <名称>")
        return
    topic_id = get_topic_id(update)
    session = sessions.get(topic_id)
    if not session or not session.get("session_id"):
        await update.message.reply_text("当前话题没有活跃会话")
        return
    new_name = " ".join(context.args)
    session_id = session["session_id"]
    try:
        # 找到对应的 JSONL 文件并追加 custom-title 事件
        jsonl_path = None
        for pdir in os.listdir(CLAUDE_PROJECTS_DIR):
            fpath = os.path.join(CLAUDE_PROJECTS_DIR, pdir, f"{session_id}.jsonl")
            if os.path.exists(fpath):
                jsonl_path = fpath
                break
        if not jsonl_path:
            await update.message.reply_text("❌ 找不到会话文件，无法重命名")
            return
        event = {"type": "custom-title", "customTitle": new_name, "sessionId": session_id}
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(event) + "\n")
        # 同步修改 TG 话题名称
        if topic_id:
            try:
                await tg_app.bot.edit_forum_topic(
                    chat_id=update.effective_chat.id,
                    message_thread_id=topic_id,
                    name=new_name[:128],
                )
            except Exception as e:
                print(f"[Rename] 修改话题名失败: {e}")
        await update.message.reply_text(f"✅ 会话已重命名为: {new_name}")
    except Exception as e:
        await update.message.reply_text(f"❌ 重命名失败: {e}")

async def cmd_interrupt(update: Update, context):
    if update.effective_user.id not in ALLOWED_USERS:
        return
    topic_id = get_topic_id(update)
    session = sessions.get(topic_id)
    if not session or not session.get("session_id"):
        await update.message.reply_text("当前话题没有活跃会话")
        return

    source = session.get("source")

    # 终端会话：通过 tmux 发送 Escape 中断
    if source == "terminal":
        tmux_pane = session.get("tmux_pane")
        if not tmux_pane:
            await update.message.reply_text("⚠️ 该终端会话未在 tmux 中运行，无法中断")
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux", "send-keys", "-t", tmux_pane, "Escape",
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                await update.message.reply_text(f"❌ 中断失败: {stderr.decode()}")
                return
            await update.message.reply_text("✅ 已发送中断信号到终端")
        except Exception as e:
            await update.message.reply_text(f"❌ 中断失败: {e}")
        return

    # TG 会话：向子进程发送 SIGINT
    if source == "telegram":
        proc = session.get("proc")
        if not proc or proc.returncode is not None:
            await update.message.reply_text("当前没有正在运行的 Claude 进程")
            return
        try:
            proc.send_signal(signal.SIGINT)
            await update.message.reply_text("✅ 已中断 Claude 回复")
        except Exception as e:
            await update.message.reply_text(f"❌ 中断失败: {e}")
        return

    await update.message.reply_text("⚠️ 无法识别当前会话类型")

async def cmd_setdir(update: Update, context):
    if update.effective_user.id not in ALLOWED_USERS:
        return
    if not context.args:
        await update.message.reply_text("用法: /setdir <路径>")
        return
    path = os.path.expanduser(" ".join(context.args))
    if not os.path.isdir(path):
        await update.message.reply_text(f"目录不存在: {path}")
        return
    topic_id = get_topic_id(update)
    if topic_id not in sessions:
        sessions[topic_id] = {"session_id": None, "project_dir": path}
    else:
        sessions[topic_id]["project_dir"] = path
    await update.message.reply_text(f"已设置项目目录: {path}")

async def cmd_info(update: Update, context):
    if update.effective_user.id not in ALLOWED_USERS:
        return
    topic_id = get_topic_id(update)
    session = sessions.get(topic_id, {})
    project_dir = session.get("project_dir", DEFAULT_PROJECT_DIR)
    session_id = session.get("session_id", "无")
    source = session.get("source", "-")
    watching = "是" if session.get("watcher_task") else "否"
    tmux_pane = session.get("tmux_pane", "无")
    bypass = "yes" if not permission_enabled else "no"
    await update.message.reply_text(
        f"话题 ID: {topic_id}\n"
        f"项目目录: {project_dir}\n"
        f"会话 ID: {session_id}\n"
        f"来源: {source}\n"
        f"监听中: {watching}\n"
        f"tmux pane: {tmux_pane}\n"
        f"bypass: {bypass}"
    )

async def cmd_retry(update: Update, context):
    if update.effective_user.id not in ALLOWED_USERS:
        return
    topic_id = get_topic_id(update)
    session = sessions.get(topic_id)
    if not session or not session.get("last_result"):
        await update.message.reply_text("没有可重发的消息")
        return
    text = session["last_result"]
    if session.get("source") == "terminal":
        await send_to_topic(session["chat_id"], topic_id, text, markdown=True)
    else:
        await send_reply(update, text, markdown=True)

async def cmd_status(update: Update, context):
    """查看 Bot 运行状态：运行时长、会话数、消息数"""
    if update.effective_user.id not in ALLOWED_USERS:
        return
    uptime = int(time.time() - BOT_START_TIME)
    days = uptime // 86400
    hours = (uptime % 86400) // 3600
    mins = (uptime % 3600) // 60
    secs = uptime % 60
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0:
        parts.append(f"{hours}小时")
    if mins > 0:
        parts.append(f"{mins}分钟")
    parts.append(f"{secs}秒")
    uptime_str = " ".join(parts)

    active_sessions = len([s for s in sessions.values() if s.get("session_id")])
    terminal_sessions = len([s for s in sessions.values() if s.get("source") == "terminal"])
    tg_sessions = len([s for s in sessions.values() if s.get("source") == "telegram"])
    bypass_status = "关闭" if permission_enabled else "开启"

    await update.message.reply_text(
        f"📊 Claude Pilot 状态\n\n"
        f"⏱ 运行时长: {uptime_str}\n"
        f"💬 收到消息数: {message_count}\n"
        f"🔄 启动对话数: {conversation_count}\n"
        f"📡 当前活跃会话: {active_sessions}\n"
        f"  ├ 终端会话: {terminal_sessions}\n"
        f"  └ TG 会话: {tg_sessions}\n"
        f"🔐 bypass: {bypass_status}"
    )

# ============ 消息处理（TG 会话 stream-json） ============

async def handle_message(update: Update, context):
    global message_count
    user = update.effective_user
    text = update.message.text
    if user.id not in ALLOWED_USERS:
        return
    message_count += 1
    topic_id = get_topic_id(update)
    if topic_id not in sessions:
        sessions[topic_id] = {"session_id": None, "project_dir": DEFAULT_PROJECT_DIR}
    session = sessions[topic_id]

    # ========== 终端会话：tmux 注入 ==========
    if session.get("source") == "terminal":
        tmux_pane = session.get("tmux_pane")
        if not tmux_pane:
            await update.message.reply_text(
                "⚠️ 该终端会话未在 tmux 中运行，无法注入消息。\n"
                "请在 tmux 中启动 Claude 以启用远程消息注入。"
            )
            return
        try:
            # 检查 pane 是否存在
            check = await asyncio.create_subprocess_exec(
                "tmux", "list-panes", "-a", "-F", "#{pane_id}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await check.communicate()
            if tmux_pane not in stdout.decode().strip().split("\n"):
                await update.message.reply_text("❌ tmux pane 已不存在，终端会话可能已关闭。")
                return

            # 加锁防止并发注入交叉
            if tmux_pane not in tmux_locks:
                tmux_locks[tmux_pane] = asyncio.Lock()
            async with tmux_locks[tmux_pane]:
                await tmux_send_message(tmux_pane, text)
            await update.message.reply_text(f"📨 已注入消息到终端")
        except Exception as e:
            await update.message.reply_text(f"❌ 注入失败: {e}")
        return

    # ========== TG 会话：启动 claude -p 进程 ==========

    # 加锁防止同一话题并发启动多个 claude -p 进程
    if topic_id not in topic_locks:
        topic_locks[topic_id] = asyncio.Lock()
    if topic_locks[topic_id].locked():
        await update.message.reply_text("⏳ 上一条消息还在处理中，请稍候...")
        return

    async with topic_locks[topic_id]:
        await _handle_tg_session(update, context, topic_id, session, text)


async def _handle_tg_session(update, context, topic_id, session, text):
    """TG 会话的实际处理逻辑（在 topic_lock 保护下执行）"""
    global conversation_count
    conversation_count += 1
    user = update.effective_user
    print(f"[话题 {topic_id}] 用户: {user.first_name}, 目录: {session['project_dir']}, 内容: {text}")

    status_msg = await update.message.reply_text("⏳ 正在让 Claude 处理...")
    cmd = ["claude", "-p", text, "--output-format", "stream-json", "--verbose"]
    if session["session_id"]:
        cmd.extend(["--resume", session["session_id"]])

    try:
        env = os.environ.copy()
        env["TELEGRAM_CHAT_ID"] = str(update.effective_chat.id)
        if topic_id:
            env["TELEGRAM_THREAD_ID"] = str(topic_id)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=session["project_dir"],
            env=env,
        )
        session["proc"] = proc

        turn_count = 0
        async for raw_line in proc.stdout:
            line = raw_line.decode().strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")

            if event_type == "system":
                new_sid = event.get("session_id")
                if new_sid:
                    session["session_id"] = new_sid
                    session["source"] = "telegram"
                    session["chat_id"] = update.effective_chat.id
                    session_topics[new_sid] = topic_id
                model = event.get("model", "?")
                try:
                    await status_msg.edit_text(f"⏳ Claude 已连接 (模型: {model})")
                except Exception:
                    pass

            elif event_type == "assistant":
                content_blocks = event.get("message", {}).get("content", [])
                for block in content_blocks:
                    if block.get("type") == "text":
                        reply_text = block["text"]
                        if reply_text.strip():
                            session["last_result"] = reply_text
                            await send_reply(update, reply_text, markdown=True)
                    elif block.get("type") == "tool_use":
                        tool_name = block.get("name", "?")
                        tool_input = block.get("input", {})
                        desc = format_tool_use(tool_name, tool_input)
                        turn_count += 1
                        await update.message.reply_text(f"🔧 [{turn_count}] {desc}")

            elif event_type == "result":
                new_sid = event.get("session_id")
                if new_sid:
                    session["session_id"] = new_sid
                    session_topics[new_sid] = topic_id
                is_error = event.get("is_error", False)
                if is_error:
                    await update.message.reply_text(f"❌ 出错了: {event.get('result', '未知错误')}")

        await proc.wait()
        session.pop("proc", None)

    except Exception as e:
        session.pop("proc", None)
        await update.message.reply_text(f"❌ 出错了: {str(e)}")

# ============ 启动 ============

async def main():
    global tg_app

    tg_app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("bypass", cmd_bypass))
    tg_app.add_handler(CommandHandler("projects", cmd_projects))
    tg_app.add_handler(CommandHandler("resume", cmd_resume))
    tg_app.add_handler(CommandHandler("quit", cmd_quit))
    tg_app.add_handler(CommandHandler("delete", cmd_delete))
    tg_app.add_handler(CommandHandler("rename", cmd_rename))
    tg_app.add_handler(CommandHandler("interrupt", cmd_interrupt))
    tg_app.add_handler(CommandHandler("setdir", cmd_setdir))
    tg_app.add_handler(CommandHandler("info", cmd_info))
    tg_app.add_handler(CommandHandler("retry", cmd_retry))
    tg_app.add_handler(CommandHandler("status", cmd_status))
    tg_app.add_handler(CallbackQueryHandler(handle_button))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    http_app = web.Application()
    http_app.router.add_post("/session_start", http_session_start)
    http_app.router.add_post("/session_stop", http_session_stop)
    http_app.router.add_post("/permission", http_permission)
    http_app.router.add_post("/notification", http_notification)
    http_app.router.add_get("/health", http_health)
    runner = web.AppRunner(http_app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", BOT_PORT)

    print("Bot 已启动，等待消息...")
    print(f"HTTP API 运行在 http://localhost:{BOT_PORT}")
    await tg_app.initialize()
    await tg_app.start()

    from telegram import BotCommand
    await tg_app.bot.set_my_commands([
        BotCommand("start", "初始化 Bot"),
        BotCommand("projects", "选择项目目录"),
        BotCommand("resume", "恢复历史会话"),
        BotCommand("rename", "重命名当前会话"),
        BotCommand("interrupt", "中断 Claude 回复"),
        BotCommand("quit", "暂停当前会话"),
        BotCommand("delete", "删除对话（不可恢复）"),
        BotCommand("info", "查看会话信息"),
        BotCommand("retry", "重发 Claude 最后一条回复"),
        BotCommand("bypass", "切换权限审批"),
        BotCommand("setdir", "手动设置项目目录"),
        BotCommand("status", "查看 Bot 运行状态"),
    ])

    await tg_app.updater.start_polling()
    await site.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, stop_event.set)
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)

    try:
        await stop_event.wait()
    finally:
        print("\n正在退出中...")
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        await runner.cleanup()
        print("已退出")

if __name__ == "__main__":
    asyncio.run(main())
