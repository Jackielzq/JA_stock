# news_manager.py - 24小时全量抓取 + AI精选Top50版
import requests
import time
import json
import logging
import urllib3
import re
from datetime import datetime, timedelta
from openai import OpenAI
from config import LLM_CONFIG

# 禁用不安全的HTTPS警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

class NewsManager:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.cls.cn/telegraph",
            "Connection": "keep-alive"
        }
        self.cls_url = "https://www.cls.cn/nodeapi/telegraphList"
        
        # 保持原有的配置读取方式不变
        if LLM_CONFIG.get('api_key'):
            self.client = OpenAI(
                api_key=LLM_CONFIG['api_key'], 
                base_url=LLM_CONFIG['base_url']
            )
        else:
            self.client = None
            logger.warning("未配置 LLM API Key，将跳过 AI 分析")

    def fetch_cls_news(self, hours=24):
        """
        抓取财联社电报（以时间为唯一截止标准）
        :param hours: 回溯时间（默认24小时）
        """
        logger.info(f"正在全量抓取最近 {hours} 小时的快讯...")
        news_list = []
        end_time = datetime.now().timestamp()
        start_time = (datetime.now() - timedelta(hours=hours)).timestamp()
        
        params = {
            "rn": 100, 
            "lastTime": int(end_time)
        }
        
        session = requests.Session()
        session.trust_env = False
        
        page_count = 0
        has_more = True
        
        try:
            while has_more:
                page_count += 1
                response = session.get(
                    self.cls_url, 
                    params=params, 
                    headers=self.headers, 
                    timeout=10, 
                    verify=False 
                )
                
                if response.status_code != 200:
                    logger.warning(f"请求失败，状态码: {response.status_code}")
                    break
                
                try:
                    json_data = response.json()
                except:
                    logger.error("返回内容非JSON格式")
                    break

                data = json_data.get('data', {}).get('roll_data', [])
                if not data:
                    logger.info("未获取到更多数据，停止抓取")
                    break
                
                for item in data:
                    ctime = item.get('ctime', 0)
                    
                    if ctime < start_time:
                        logger.info(f"已回溯至 {datetime.fromtimestamp(ctime)}，超出 {hours} 小时范围，停止抓取")
                        has_more = False 
                        break 
                    
                    content = item.get('content', '')
                    title = item.get('title', '')
                    if not title and content:
                        title = content[:30] + "..."
                    
                    content = content.replace("<br>", "").replace("<strong>", "").replace("</strong>", "")
                    
                    news_list.append({
                        'time_str': datetime.fromtimestamp(ctime).strftime('%Y-%m-%d %H:%M'),
                        'timestamp': ctime,
                        'content': f"【{title}】{content}"
                    })
                
                if has_more:
                    params['lastTime'] = data[-1]['ctime']
                    time.sleep(0.2)
                
        except Exception as e:
            logger.error(f"抓取新闻网络错误: {e}")
        
        logger.info(f"抓取结束，24小时内共收集到 {len(news_list)} 条快讯")
        return news_list

    def analyze_news_with_llm(self, raw_news):
        """
        使用大模型筛选 Top 50
        """
        if not self.client or not raw_news:
            return []

        # === 核心修改1：减少输入量防止输出截断 ===
        # 800条太多了，很容易导致输出超过 max_tokens 而被截断
        # 建议取最近的 200 条即可覆盖大部分重要热点
        process_news = raw_news[:200]
        logger.info(f"正在准备 AI 分析上下文，输入新闻数: {len(process_news)} 条...")
        
        news_text_lines = []
        for i, n in enumerate(process_news):
            short_content = n['content'][:80].replace('\n', ' ') 
            if len(n['content']) > 80: short_content += "..."
            news_text_lines.append(f"id:{i}|{short_content}")
            
        news_text = "\n".join(news_text_lines)
        
        prompt = f"""
你是一名资深A股分析师。以下是最近24小时的财经快讯列表（格式为 id:序号|内容）。
请从这些信息中，严格筛选出**最有价值、对明日A股市场影响最大**的 30-50 条新闻。

筛选标准：
1. **优先级**：国家级政策 > 行业重磅利好/涨价 > 龙头股重大重组/业绩 > 知名游资/机构动向。
2. **过滤**：必须剔除美股日常、外汇、无关宏观、凑数的废话。
3. **关联**：必须能推理出明确的A股受益板块或个股。

请返回 JSON 格式列表，格式如下（不要Markdown标记）：
[
    {{
        "time": "MM-DD HH:mm", 
        "title": "简短标题",
        "summary": "一句话核心逻辑",
        "score": 10,
        "sentiment": "利好",
        "sector": "板块名",
        "stocks": "股名1,股名2"
    }}
]

注意：
- JSON 中 "time" 请根据内容预估或留空。
- "score" 范围 1-10，分值越高越重要。
- 必须严格返回合法的 JSON 数组。
- **输出必须是纯 JSON，不要包含 ```json 或其他文字**。

原始快讯数据：
{news_text}
"""
        
        try:
            logger.info("发送请求给大模型 (数据量大，预计耗时 30-60秒)...")
            response = self.client.chat.completions.create(
                model=LLM_CONFIG['model'],
                messages=[
                    {"role": "system", "content": "你是一个金融分析专家，只输出 JSON，严禁输出其他废话。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=4000,
                stream=False
            )
            
            content = response.choices[0].message.content
            
            # === 核心修改2：JSON 自动修复与清洗 ===
            # 1. 去除 Markdown 标记
            content = re.sub(r'```json\s*', '', content)
            content = re.sub(r'```\s*$', '', content)
            content = content.strip()
            
            # 2. 尝试解析
            try:
                analyzed_list = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"JSON 解析失败: {e}，尝试自动修复...")
                
                # 3. 自动修复逻辑：查找最后一个有效的对象结束符 '}'
                # 大模型输出被截断时，通常是 "...}, {..." 这种形式
                try:
                    r_idx = content.rfind('}')
                    if r_idx != -1:
                        # 截取到最后一个 '}'，并补上 ']'
                        fixed_content = content[:r_idx+1] + ']'
                        analyzed_list = json.loads(fixed_content)
                        logger.info("JSON 自动修复成功！")
                    else:
                        raise Exception("无法找到有效的 JSON 结束符")
                except Exception as fix_err:
                    logger.error(f"JSON 修复彻底失败: {fix_err}")
                    logger.error(f"错误内容片段: {content[-100:]}") # 打印最后100字符方便调试
                    # 抛出异常进入下面的 fallback
                    raise fix_err
            
            logger.info(f"AI 分析完成，筛选出 {len(analyzed_list)} 条核心新闻")
            
            # 按分数排序
            analyzed_list.sort(key=lambda x: x.get('score', 0), reverse=True)
            
            return analyzed_list
            
        except Exception as e:
            logger.error(f"LLM 分析过程出错: {e}")
            # 出错时返回最近的 20 条原始数据兜底，保证程序不崩
            fallback = []
            for n in process_news[:20]:
                fallback.append({
                    "time": n['time_str'],
                    "title": "AI解析异常-降级显示",
                    "summary": n['content'][:100],
                    "score": 0,
                    "sentiment": "中性",
                    "sector": "-",
                    "stocks": "-"
                })
            return fallback

    def get_top_news(self):
        """主入口"""
        # 1. 抓取24小时内所有新闻
        raw_news = self.fetch_cls_news(hours=24)
        
        if not raw_news:
            logger.warning("未抓取到新闻")
            return []
        
        # 2. AI 筛选 Top 50
        return self.analyze_news_with_llm(raw_news)

if __name__ == "__main__":
    nm = NewsManager()
    news = nm.get_top_news()
    print(f"最终获取新闻条数: {len(news)}")