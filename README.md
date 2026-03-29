# Claude Pilot

通过 Telegram Bot 远程操控 Claude Code，手机上也能看输出、批权限、发消息。

## 安装

```bash
git clone git@github-mk:MK080324/claude-pilot.git
cd claude-pilot
bash install.sh
```

安装脚本会自动完成所有配置（Python 环境、依赖、.env、Claude hooks），按提示输入 Bot Token 和用户 ID 即可。

## 使用

```bash
crc start       # 启动
crc stop        # 停止
crc status      # 查看状态
crc logs        # 查看日志
crc logs -f     # 实时跟踪日志
crc restart     # 重启
crc uninstall   # 卸载（自动恢复 Claude 原始设置）
```

启动后在 Telegram 群组的通用话题里发 `/start` 初始化。

## 前置条件

- macOS / Linux
- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- Telegram Bot Token（从 @BotFather 获取）
- 开启话题功能的 Telegram 群组，Bot 加为管理员（需"管理话题"权限）
- tmux（`brew install tmux`）

## License

MIT
