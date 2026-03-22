# 代码审阅报告

## 整体评价

项目功能设计出色，双模式（终端/TG）会话架构合理，Hook 机制与 Claude Code 的集成方式巧妙。函数划分清晰，有注释分区。以下是经讨论确认的问题清单。

---

## P0 — 必须修复

### TG 会话无并发保护

**位置**: `bot.py:1104-1222`, `bot.py:1228`

由于启用了 `concurrent_updates=True`，同一话题中用户快速连发两条消息会同时启动两个 `claude -p` 进程。两个进程可能使用同一个 session_id 做 `--resume`，导致：
- `session["proc"]` 被后一个进程覆盖，前一个进程变成孤儿进程
- `session_topics` 映射被覆盖
- 输出交叉混乱

终端会话有 `tmux_locks` 做了保护，但 TG 会话完全没有。

**修复方案**: 增加 per-topic 的 `asyncio.Lock`，确保同一话题下同时只处理一条消息。

---

## P1 — 应该修复

### /quit 不杀进程

**位置**: `bot.py:888-901`

如果一个 TG 会话正在运行 `claude -p` 进程，执行 `/quit` 只清理了数据结构（`del sessions[topic_id]`），没有终止子进程，该进程会变成孤儿进程继续运行。

**修复方案**: 在现有代码的 `watcher_task` 清理（第 897-898 行）之后，补上 proc 终止：
```python
proc = session.get("proc")
if proc and proc.returncode is None:
    proc.terminate()
```

### 消息分段切断 Markdown

**位置**: `bot.py:180-186`

`send_reply` 在 markdown 模式下按原始文本每 4000 字符切片，然后每段单独调用 `gfm_to_html(chunk)`。如果一段 Markdown 中间有未闭合的代码块（\`\`\`），被切断后两段都无法正确解析。虽然有 fallback 到纯文本不会崩溃，但格式会退化。

**修复方案**: 在 Markdown 的段落/代码块边界处切割，或先整体转 HTML 再在标签边界切。

### Hook 代码 DRY

**位置**: 四个 Hook 文件中的 `_read_bot_port()` 函数完全相同

**修复方案**: 提取到 `hooks/common.py`。

另外，`hooks/permission.py:32-41` 和 `bot.py:212-228` 的 `format_tool_use` 逻辑重复（permission.py 处理 4 种工具，bot.py 处理 7 种）。两者用途不同（权限描述 vs TG 展示），统一未必合适，但需注意保持同步。

---

## P2 — 可以改进

### Telegram 速率控制

`watch_transcript` 每 0.5 秒轮询一次，如果积累了大量事件会瞬间发出多条消息，可能触发 Telegram 429 限制（约 30 条/秒/群组）。长输出场景需要注意。

**改进方案**: 增加消息合并逻辑（如在短时间内的多条文本合并为一条发送），或增加重试+退避机制。

### HTTP API 认证

**位置**: `bot.py:563-686`

HTTP 服务器绑定在 `localhost:5000`，单用户机器上问题不大。多用户服务器上任何本地进程都可以伪造请求。

**改进方案**: 如果部署在共享服务器上，增加简单的 shared secret 验证。个人机器上可忽略。

---

## P3 — 低优先级

### signal import 位置

`bot.py:1290` — `import signal` 放在 `if __name__ == "__main__"` 里，`main()` 函数中使用 `signal.SIGINT`。执行顺序上没有 bug，但风格上建议移到文件顶部。

### 单文件架构与全局变量

`bot.py` 1292 行全在一个文件中，`tg_app` 等全局变量作为隐式依赖被多个函数直接引用。当前规模下函数划分清晰，不是痛点，但如果功能继续膨胀，建议按职责拆分模块（renderer、config、api、handlers、session），届时一并消除全局变量依赖。

### 其他

- `seen_uuids` 理论上无限增长，但实际每个 watcher 独立且 UUID 数量有限（几百到几千），内存影响极小
- `callback_data` 长度接近 Telegram 64 字节限制但仍有余量，Claude Code 使用标准 UUID 不太可能变更长
- `.env` 解析不支持引号等高级格式，但当前配置足够简单，不是痛点

---

## 已排除的误判

### ~~fallback 发送也可能失败~~

第 186 行 `await update.message.reply_text(chunk)` 没有设置 `parse_mode`，默认是纯文本模式。Telegram 纯文本模式不会解析 `<` 等字符，任何内容都能发送成功。此条不成立。

### ~~状态全在内存，重启丢失~~

这是有意设计。每次重启直接取消所有 topic 和 session 的关联，`/resume` 命令提供手动恢复能力。不是缺陷。

