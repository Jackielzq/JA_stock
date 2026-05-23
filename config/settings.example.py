"""示例配置模板。

后续会把根目录 config.py 中的敏感配置迁移到本目录。
本文件只保留结构和占位符，不放真实账号、密码、token、key。
"""

# 数据库配置
DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "your_user",
    "password": "your_password",
    "database": "stock_data",
    "charset": "utf8mb4",
}

# Tushare
TUSHARE_TOKEN = "your_tushare_token"

# 邮件配置
EMAIL_CONFIG = {
    "smtp_server": "smtp.qq.com",
    "smtp_port": 465,
    "sender": "your_email@qq.com",
    "password": "your_smtp_password",
    "receivers": ["receiver@example.com"],
    "subject_prefix": "【A股每日复盘】",
}

# 大模型配置
LLM_CONFIG = {
    "api_key": "your_llm_api_key",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "temperature": 1.0,
    "top_n": 20,
}

# GitHub 发布配置
GITHUB_DEPLOY_CONFIG = {
    "token": "your_github_token",
    "owner": "your_github_username",
    "repo": "your_repo_name",
    "branch": "main",
    "target_path": "index.html",
}
