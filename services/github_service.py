# services/github_service.py
import logging
import os
import base64
import requests
from config import GITHUB_DEPLOY_CONFIG, get_daily_review_html_path

logger = logging.getLogger(__name__)

def _put_file_to_github(encoded_content, target_path, commit_msg, config):
    """
    通用内部函数：将 Base64 内容推送到 GitHub 指定路径
    """
    token = config.get('token')
    owner = config.get('owner')
    repo = config.get('repo')
    branch = config.get('branch', 'main')

    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{target_path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    # 1. 检查文件是否已存在以获取 SHA (覆盖旧文件必须带 SHA)
    get_response = requests.get(api_url, headers=headers, params={"ref": branch})
    file_sha = None
    if get_response.status_code == 200:
        file_sha = get_response.json().get('sha')

    # 2. 组装提交数据
    data = {
        "message": commit_msg,
        "content": encoded_content,
        "branch": branch
    }
    if file_sha:
        data["sha"] = file_sha

    # 3. 提交 PUT 请求
    put_response = requests.put(api_url, headers=headers, json=data)
    
    if put_response.status_code in [200, 201]:
        logger.info(f"✅ 成功上传文件至仓库路径: {target_path}")
        return True
    else:
        logger.error(f"❌ 上传失败 [{target_path}], 状态码 {put_response.status_code}: {put_response.text}")
        return False


def deploy_review_to_github(date):
    """
    将生成的 daily_review.html 通过 API 上传到 GitHub (一式两份：首页覆盖 + 历史归档)
    """
    try:
        source_file = get_daily_review_html_path(date)
        if not os.path.exists(source_file):
            logger.error(f"未找到当天的复盘报告: {source_file}")
            return False

        logger.info(f"正在读取待上传文件: {source_file}")
        with open(source_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 转换为 Base64
        encoded_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        
        # 提取配置验证
        if not GITHUB_DEPLOY_CONFIG.get('token') or 'ghp_' not in GITHUB_DEPLOY_CONFIG.get('token'):
            logger.error("GitHub Token 配置不正确！")
            return False

        # ==========================================
        # 核心逻辑：双份上传
        # ==========================================
        
        # 任务 1：上传为最新的 index.html (覆盖昨日首页)
        index_path = GITHUB_DEPLOY_CONFIG.get('target_path', 'index.html')
        logger.info(f"==> 开始上传主页 ({index_path})...")
        success_index = _put_file_to_github(
            encoded_content=encoded_content,
            target_path=index_path,
            commit_msg=f"Auto update homepage for {date}",
            config=GITHUB_DEPLOY_CONFIG
        )

        # 任务 2：上传到历史归档文件夹 (永久保留)
        archive_path = f"history/daily_review_{date}.html"
        logger.info(f"==> 开始上传历史归档 ({archive_path})...")
        success_archive = _put_file_to_github(
            encoded_content=encoded_content,
            target_path=archive_path,
            commit_msg=f"Archive daily review for {date}",
            config=GITHUB_DEPLOY_CONFIG
        )

        if success_index and success_archive:
            logger.info("🎉 GitHub 部署全流程完成！Cloudflare 将在十几秒内自动刷新。")
            logger.info("--------------------------------------------------")
            logger.info(f"👉 最新今日报告: https://你的域名.pages.dev")
            logger.info(f"👉 本日历史归档: https://你的域名.pages.dev/{archive_path}")
            logger.info("--------------------------------------------------")
            return True
        else:
            logger.warning("部署完成，但部分文件上传失败，请查看上方日志。")
            return False

    except Exception as e:
        logger.error(f"GitHub 部署过程中发生异常: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False
