# services/pdf_service.py
import os
import logging
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

def convert_html_to_pdf(html_path, pdf_path):
    """
    使用 Playwright 将 HTML 渲染为 PDF
    支持 JS 渲染，完美兼容 Echarts 等动态图表
    """
    try:
        logger.info(f"正在生成 PDF 文件，请稍候: {pdf_path}")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # 【核心修改 1】强制设置一个宽屏视口，让图表和表格有足够的空间展开，不会被挤压变形
            page.set_viewport_size({"width": 1440, "height": 900})
            
            # 【核心修改 2】强制模拟屏幕(screen)模式，忽略掉浏览器自带的打印(@media print)排版，防止样式错乱
            page.emulate_media(media="screen")
            
            # 构造本地文件的绝对路径 URL
            file_url = f"file://{os.path.abspath(html_path)}"
            
            # 打开页面并等待网络空闲（确保 Echarts 动画和字体完全加载完毕）
            # page.goto(file_url, wait_until="networkidle")
            page.goto(file_url, wait_until="load", timeout=60000)
            
            # 【核心修改 3】生成 PDF 时加入 scale 等比缩放，0.52 的比例刚好能把 1440px 完美塞进 A4 宽度
            page.pdf(
                path=pdf_path, 
                format="A4", 
                scale=0.52,                 # 缩小网页比例以适应 A4 宽度
                print_background=True,      # 必须保留，否则背景色全变白
                margin={"top": "15mm", "bottom": "15mm", "left": "10mm", "right": "10mm"}
            )
            
            browser.close()
            
        logger.info(f"✅ PDF 生成成功: {pdf_path}")
        return pdf_path
    except Exception as e:
        logger.error(f"❌ 生成 PDF 失败: {e}")
        return None

# ==========================================
# 本地快速测试代码
# ==========================================
# if __name__ == "__main__":
#     logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
#     current_dir = os.path.dirname(os.path.abspath(__file__))
    
#     test_html = os.path.join(current_dir, "daily_review_20260408.html")
#     test_pdf = os.path.join(current_dir, "daily_review_20260408_test.pdf")
    
#     if not os.path.exists(test_html):
#         logger.error(f"❌ 找不到测试的 HTML 文件: {test_html}")
#     else:
#         logger.info(f"✅ 找到 HTML 文件，准备开始转换...")
#         result = convert_html_to_pdf(test_html, test_pdf)
#         if result:
#             logger.info(f"🎉 测试完成！请打开确认是否完整: {result}")