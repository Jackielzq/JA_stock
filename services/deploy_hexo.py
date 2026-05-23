# services/deploy_hexo.py - Hexo 博客自动同步模块 (包含: 复盘、新闻、自选监控)

import os
import shutil
import datetime
import sys

# 动态将项目根目录加入环境变量
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from config import get_daily_news_path, get_daily_review_html_path, get_monitor_path

# ================= 配置区域 =================
# 请确保这里是你真实的路径，保留 r 前缀
HEXO_ROOT_PATH = r"C:\Users\Admin\Nutstore\1\我的坚果云\Hexo_blog"

# 报告在 Hexo source 中的存放目录名称
REPORT_DIR_NAME = "stock-reports"

# ===========================================

def deploy_reports(trade_date_str):
    """
    执行同步流程 (同步 review, news, monitor)
    """
    print("\n" + "="*30)
    print("   开始执行同步 (复盘 & 新闻 & 监控)")
    print("="*30)

    # 1. 环境检查
    if not os.path.exists(HEXO_ROOT_PATH):
        print(f"    ❌ 错误: 找不到 Hexo 根目录！请检查路径配置是否正确。")
        return

    # 2. 准备目标路径
    target_base = os.path.join(HEXO_ROOT_PATH, "source", REPORT_DIR_NAME)
    target_date_dir = os.path.join(target_base, trade_date_str)
    
    print(f"\n[1] 准备同步到: {target_date_dir}")
    try:
        if not os.path.exists(target_date_dir):
            os.makedirs(target_date_dir)
    except Exception as e:
        print(f"    ❌ 创建目录失败: {e}")
        return

    # 3. 映射规则：加入 monitor.html
    file_mapping = {
        "review.html":  [f"daily_review_{trade_date_str}.html"],
        "news.html":    [f"daily_news_{trade_date_str}.html"],
        "monitor.html": [f"monitor_{trade_date_str}.html"]
    }
    
    print(f"\n[2] 开始复制文件 (目标日期: {trade_date_str}):")
    
    success_count = 0
    for dst_name, src_candidates in file_mapping.items():
        found = False
        for src_name in src_candidates:
            if dst_name == "review.html":
                src_path = get_daily_review_html_path(trade_date_str)
            elif dst_name == "news.html":
                src_path = get_daily_news_path(trade_date_str)
            else:
                src_path = get_monitor_path(trade_date_str)
            if os.path.exists(src_path):
                dst_path = os.path.join(target_date_dir, dst_name)
                try:
                    shutil.copy2(src_path, dst_path)
                    print(f"    ✅ 同步成功: {src_name} -> {dst_name}")
                    found = True
                    success_count += 1
                    break
                except Exception as e:
                    print(f"    ❌ 复制出错 ({src_name}): {e}")
        
        if not found:
            print(f"    ⚠️ 未找到对应的源文件，跳过: {dst_name}")

    # 4. 更新索引
    if success_count > 0:
        try:
            generate_index_page(target_base)
        except Exception as e:
            print(f"\n❌ 生成索引页失败: {e}")
    else:
        print("\n⚠️ 没有文件被复制，跳过生成索引页。")

    print("\n" + "="*30)


def generate_index_page(base_dir):
    print(f"\n[3] 更新索引页: {base_dir}")
    
    dates = []
    if os.path.exists(base_dir):
        for entry in os.listdir(base_dir):
            full_path = os.path.join(base_dir, entry)
            # 判断是否为日期文件夹 (8位数字)
            if os.path.isdir(full_path) and entry.isdigit() and len(entry) == 8:
                dates.append(entry)
    
    dates.sort(reverse=True)
    print(f"    - 发现历史日期: {len(dates)} 个")
    
    # 精简了表头和样式，新增了 monitor 专属样式与表格列
    md_content = f"""---
title: 量化复盘归档
date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
type: "page"
comments: false
---

<style>
.report-btn {{
    display: inline-block; padding: 4px 12px; margin: 0 4px; border-radius: 4px;
    color: white !important; font-size: 13px; text-decoration: none !important; border: none; font-weight: 500;
}}
.btn-review {{ background-color: #3498db; }}  /* 蓝色 - 复盘与选股 */
.btn-news {{ background-color: #9b59b6; }}    /* 紫色 - 每日新闻 */
.btn-monitor {{ background-color: #e67e22; }} /* 橙色 - 自选监控 */
.report-btn:hover {{ opacity: 0.8; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
</style>

| 📅 交易日期 | 📈 每日复盘 (含选股) | 📰 每日新闻 | 🎯 核心自选监控 |
| :--- | :---: | :---: | :---: |
"""
    
    for d in dates:
        display_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        dir_path = os.path.join(base_dir, d)
        
        # 检查三个文件是否存在
        has_review = os.path.exists(os.path.join(dir_path, "review.html"))
        has_news = os.path.exists(os.path.join(dir_path, "news.html"))
        has_monitor = os.path.exists(os.path.join(dir_path, "monitor.html"))
        
        # 生成按钮HTML
        link_review = f'<a href="/{REPORT_DIR_NAME}/{d}/review.html" class="report-btn btn-review" target="_blank">查看报告</a>' if has_review else "-"
        link_news = f'<a href="/{REPORT_DIR_NAME}/{d}/news.html" class="report-btn btn-news" target="_blank">查看新闻</a>' if has_news else "-"
        link_monitor = f'<a href="/{REPORT_DIR_NAME}/{d}/monitor.html" class="report-btn btn-monitor" target="_blank">查看监控</a>' if has_monitor else "-"
        
        md_content += f"| **{display_date}** | {link_review} | {link_news} | {link_monitor} |\n"

    index_path = os.path.join(base_dir, "index.md")
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    
    print(f"    ✅ 索引页 index.md 已更新 (已加入自选监控列)")

if __name__ == "__main__":
    # 测试代码
    print("手动测试模式...")
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y%m%d')
    deploy_reports(yesterday)
