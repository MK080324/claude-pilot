"""Hook 脚本的公共工具函数"""
import os


def read_bot_port():
    """从 .env 文件或环境变量读取 BOT_PORT"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("BOT_PORT="):
                    return line.split("=", 1)[1].strip()
    return os.environ.get("BOT_PORT", "5000")
