# mail_service.py
import logging
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.header import Header
from email.utils import formataddr
from config import EMAIL_CONFIG, OUTPUT_DIR  # [新增] 引入 OUTPUT_DIR

logger = logging.getLogger(__name__)

def send_report_email(html_content, date, subject_suffix="报告", attachment_files=None):
    """
    发送 HTML 内容邮件（支持附件）
    :param html_content: 具体的 HTML 字符串内容
    :param date: 日期
    :param subject_suffix: 标题后缀
    :param attachment_files: 附件文件路径列表 (List[str])
    """
    try:
        logger.info(f"正在发送邮件 ({subject_suffix})...")
        msg = MIMEMultipart()
        subject = f"{EMAIL_CONFIG['subject_prefix']} {date} {subject_suffix}"
        msg['Subject'] = Header(subject, 'utf-8')
        msg['From'] = formataddr(("AI Quant", EMAIL_CONFIG['sender']))
        
        # 获取真实的收件人列表（用于 SMTP 传输）
        config_receivers = EMAIL_CONFIG['receivers']
        real_receivers = config_receivers if isinstance(config_receivers, list) else [config_receivers]


        # 在邮件标头中隐藏具体收件人
        # 方法：将标头里的 'To' 设置为“群发通知”或发件人自己，而不是真实的收件人列表
        msg['To'] = formataddr(("AI Quant 订阅者", EMAIL_CONFIG['sender'])) 

        # 1. 添加正文 (HTML)
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))

        # 2. 添加附件 (如果有)
        if attachment_files:
            for file_path in attachment_files:
                if not file_path:
                    continue
                
                # [新增] 智能判断：如果传入的是纯文件名，去 OUTPUT_DIR 找；如果是绝对路径，保持原样
                if not os.path.isabs(file_path):
                    real_file_path = os.path.join(OUTPUT_DIR, file_path)
                else:
                    real_file_path = file_path

                if not os.path.exists(real_file_path):
                    logger.warning(f"附件文件不存在，跳过: {real_file_path}")
                    continue
                
                try:
                    # 获取文件名
                    filename = os.path.basename(real_file_path)
                    # [修改] 使用拼装好的真实路径读取文件
                    with open(real_file_path, 'rb') as f:
                        # 读取文件内容
                        part = MIMEApplication(f.read())
                        # 设置头部，使其作为附件显示
                        part.add_header('Content-Disposition', 'attachment', filename=filename)
                        msg.attach(part)
                        logger.info(f"已添加附件: {filename}")
                except Exception as e:
                    logger.error(f"添加附件失败 [{real_file_path}]: {e}")

        # 3. 发送邮件
        server = smtplib.SMTP_SSL(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        server.login(EMAIL_CONFIG['sender'], EMAIL_CONFIG['password'])
        server.sendmail(EMAIL_CONFIG['sender'], real_receivers, msg.as_string())
        server.quit()
        logger.info(f"邮件发送成功！(含 {len(attachment_files) if attachment_files else 0} 个附件)")
        
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        import traceback
        logger.error(traceback.format_exc())