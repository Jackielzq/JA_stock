# renderer.py
from jinja2 import Environment, FileSystemLoader
import logging
import os
from config import ensure_runtime_dirs

logger = logging.getLogger(__name__)

class ReportRenderer:
    def __init__(self):
        # 模板目录定位到当前文件的同级 templates 目录
        template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
        self.env = Environment(loader=FileSystemLoader(template_dir))

    def render(self, template_name, data, output_filename):
        """
        :param template_name: 'daily_review.html'
        :param data: 字典数据
        :param output_filename: 输出文件路径或文件名
        """
        try:
            ensure_runtime_dirs()
            full_output_path = output_filename if os.path.isabs(output_filename) else os.path.abspath(output_filename)
            os.makedirs(os.path.dirname(full_output_path), exist_ok=True)

            template = self.env.get_template(template_name)
            html_content = template.render(**data)
            
            # [修改] 使用新的完整路径写入
            with open(full_output_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            logger.info(f"报告生成成功: {full_output_path}")
            # [修改] 返回文件的完整绝对路径，方便其他模块调用
            return full_output_path
        except Exception as e:
            logger.error(f"渲染报告失败: {e}")
            return None
