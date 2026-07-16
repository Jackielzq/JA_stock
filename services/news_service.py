# news_service.py - 多源财经新闻抓取 + AI精选Top50版
# 数据源：同花顺(10jqka) + 华尔街见闻(wallstreetcn) + 新浪财经 备选
import requests
import time
import json
import logging
import urllib3
import re
from datetime import datetime, timedelta
from openai import OpenAI
from config import LLM_CONFIG

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")


class NewsManager:
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
        }

        if LLM_CONFIG.get("api_key"):
            self.client = OpenAI(
                api_key=LLM_CONFIG["api_key"],
                base_url=LLM_CONFIG["base_url"],
            )
        else:
            self.client = None
            logger.warning("未配置 LLM API Key，将跳过 AI 分析")

    # ──────────────── 数据源1：同花顺 7x24 快讯 ────────────────
    def fetch_10jqka_news(self, hours=24):
        """抓取同花顺 A 股快讯（含关联个股标识，数据质量最高）"""
        logger.info(f"[同花顺] 正在抓取最近 {hours} 小时的快讯...")
        news_list = []
        start_ts = int((datetime.now() - timedelta(hours=hours)).timestamp())
        page = 1
        max_pages = 120  # 安全上限

        try:
            while page <= max_pages:
                r = self.session.get(
                    "https://news.10jqka.com.cn/tapp/news/push/stock/",
                    params={"page": str(page), "pagesize": "20", "type": "0"},
                    headers=self.headers,
                    timeout=15,
                    verify=False,
                )
                if r.status_code != 200:
                    logger.warning(f"[同花顺] 请求失败，状态码: {r.status_code}")
                    break

                data = r.json()
                if data.get("code") != "200":
                    logger.warning(f"[同花顺] API 异常: {data.get('msg', '')}")
                    break

                items = data.get("data", {}).get("list", [])
                if not items:
                    logger.info("[同花顺] 无更多数据，停止翻页")
                    break

                for item in items:
                    ctime = int(item.get("ctime", 0))
                    if ctime < start_ts:
                        logger.info(
                            f"[同花顺] 已回溯至 {datetime.fromtimestamp(ctime)}，停止抓取"
                        )
                        return news_list

                    title = item.get("title", "")
                    digest = item.get("digest", "")
                    tags = [t.get("name", "") for t in item.get("tags", [])]
                    stocks = item.get("stock", [])
                    stock_names = ", ".join(
                        [f"{s.get('name', '')}({s.get('stockCode', '')})" for s in stocks]
                    )

                    content = f"【{title}】{digest}"
                    if stock_names:
                        content += f" [关联: {stock_names}]"
                    if tags:
                        content += f" [标签: {', '.join(tags)}]"

                    news_list.append({
                        "time_str": datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M"),
                        "timestamp": ctime,
                        "content": content,
                    })

                page += 1
                time.sleep(0.15)

        except Exception as e:
            logger.error(f"[同花顺] 网络错误: {e}")

        logger.info(f"[同花顺] 共收集 {len(news_list)} 条快讯")
        return news_list

    # ──────────────── 数据源2：华尔街见闻 live ────────────────
    def fetch_wallstcn_news(self, hours=24):
        """抓取华尔街见闻 7x24 快讯（宏观/全球视角，信息角度更广）"""
        logger.info(f"[华尔街见闻] 正在抓取最近 {hours} 小时的快讯...")
        news_list = []
        start_ts = int((datetime.now() - timedelta(hours=hours)).timestamp())
        cursor = None
        max_pages = 120

        try:
            for _ in range(max_pages):
                params = {
                    "channel": "global-channel",
                    "client": "pc",
                    "limit": "20",
                    "first_page": "true" if cursor is None else "false",
                }
                if cursor:
                    params["cursor"] = str(cursor)

                r = self.session.get(
                    "https://api-one.wallstcn.com/apiv1/content/lives",
                    params=params,
                    headers={**self.headers, "Referer": "https://wallstreetcn.com/"},
                    timeout=15,
                    verify=False,
                )
                if r.status_code != 200:
                    logger.warning(f"[华尔街见闻] 请求失败: {r.status_code}")
                    break

                data = r.json()
                if data.get("code") != 20000:
                    logger.warning(f"[华尔街见闻] API 异常: {data.get('message', '')}")
                    break

                items = data.get("data", {}).get("items", [])
                if not items:
                    break

                for item in items:
                    display_time = item.get("display_time", 0)
                    if display_time < start_ts:
                        return news_list

                    title = item.get("title", "")
                    content_text = item.get("content_text", "").strip()
                    if not title and content_text:
                        title = content_text[:40] + "..."

                    content = f"【{title}】{content_text}"

                    news_list.append({
                        "time_str": datetime.fromtimestamp(display_time).strftime("%Y-%m-%d %H:%M"),
                        "timestamp": display_time,
                        "content": content,
                    })

                next_cursor = data.get("data", {}).get("next_cursor")
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
                time.sleep(0.15)

        except Exception as e:
            logger.error(f"[华尔街见闻] 网络错误: {e}")

        logger.info(f"[华尔街见闻] 共收集 {len(news_list)} 条快讯")
        return news_list

    # ──────────────── 数据源3：新浪财经 滚动新闻（备选） ────────────────
    def fetch_sina_news(self, hours=24):
        """抓取新浪财经滚动新闻（备选源）"""
        logger.info(f"[新浪财经] 正在抓取最近 {hours} 小时的滚动新闻...")
        news_list = []
        start_ts = int((datetime.now() - timedelta(hours=hours)).timestamp())
        page = 1
        max_pages = 120

        try:
            while page <= max_pages:
                r = self.session.get(
                    "https://feed.mix.sina.com.cn/api/roll/get",
                    params={
                        "pageid": "153", "lid": "2516", "k": "",
                        "num": "20", "page": str(page),
                    },
                    headers=self.headers,
                    timeout=15,
                    verify=False,
                )
                if r.status_code != 200:
                    break
                r.encoding = "utf-8"
                data = r.json()
                items = data.get("result", {}).get("data", [])
                if not items:
                    break

                for item in items:
                    ctime = int(item.get("ctime", 0))
                    if ctime < start_ts:
                        return news_list

                    title = item.get("title", "")
                    intro = item.get("intro", "")
                    content = f"【{title}】{intro}"

                    news_list.append({
                        "time_str": datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M"),
                        "timestamp": ctime,
                        "content": content,
                    })

                page += 1
                time.sleep(0.15)

        except Exception as e:
            logger.error(f"[新浪财经] 网络错误: {e}")

        logger.info(f"[新浪财经] 共收集 {len(news_list)} 条快讯")
        return news_list

    # ──────────────── 旧 CLS 接口（保留兼容，但标记弃用） ────────────────
    def fetch_cls_news(self, hours=24):
        """
        [已弃用] 财联社电报 — 原始 API 端点已 404。
        请改用 fetch_multi_source_news()。
        """
        logger.warning("[CLS] 旧版 API 已废弃 (404 / 签名验证)，自动回退到多源抓取...")
        return self.fetch_multi_source_news(hours)

    # ──────────────── 多源聚合 ────────────────
    def fetch_multi_source_news(self, hours=24):
        """从多个数据源聚合新闻，去重排序"""
        all_news = []

        # 按优先级依次抓取
        for fetcher, name in [
            (self.fetch_10jqka_news, "同花顺"),
            (self.fetch_wallstcn_news, "华尔街见闻"),
            (self.fetch_sina_news, "新浪财经"),
        ]:
            try:
                batch = fetcher(hours)
                all_news.extend(batch)
                logger.info(f"  -> {name}: {len(batch)} 条")
            except Exception as e:
                logger.error(f"  -> {name}: 抓取失败 ({e})")

            # 如果已收集到足够新闻，提前退出
            if len(all_news) >= 500:
                logger.info("已收集 500+ 条新闻，停止多源抓取")
                break

        if not all_news:
            logger.warning("所有数据源均未返回新闻！")
            return []

        # 去重（基于 content 的前 60 个字符）
        seen = set()
        unique_news = []
        for n in all_news:
            key = n["content"][:60]
            if key not in seen:
                seen.add(key)
                unique_news.append(n)

        # 按时间倒序排列
        unique_news.sort(key=lambda x: x["timestamp"], reverse=True)

        logger.info(f"多源聚合完成: {len(unique_news)} 条（去重前 {len(all_news)} 条）")
        return unique_news

    # ──────────────── LLM 分析（保持原有逻辑不变） ────────────────
    def analyze_news_with_llm(self, raw_news):
        """使用大模型筛选 Top 50"""
        if not self.client or not raw_news:
            return []

        process_news = raw_news[:200]
        logger.info(f"正在准备 AI 分析上下文，输入新闻数: {len(process_news)} 条...")

        news_text_lines = []
        for i, n in enumerate(process_news):
            short_content = n["content"][:80].replace("\n", " ")
            if len(n["content"]) > 80:
                short_content += "..."
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
                model=LLM_CONFIG["model"],
                messages=[
                    {"role": "system", "content": "你是一个金融分析专家，只输出 JSON，严禁输出其他废话。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=4000,
                stream=False,
            )

            content = response.choices[0].message.content

            # JSON 自动修复与清洗
            content = re.sub(r"```json\s*", "", content)
            content = re.sub(r"```\s*$", "", content)
            content = content.strip()

            try:
                analyzed_list = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"JSON 解析失败: {e}，尝试自动修复...")
                try:
                    r_idx = content.rfind("}")
                    if r_idx != -1:
                        fixed_content = content[: r_idx + 1] + "]"
                        analyzed_list = json.loads(fixed_content)
                        logger.info("JSON 自动修复成功！")
                    else:
                        raise Exception("无法找到有效的 JSON 结束符")
                except Exception as fix_err:
                    logger.error(f"JSON 修复彻底失败: {fix_err}")
                    logger.error(f"错误内容片段: {content[-100:]}")
                    raise fix_err

            logger.info(f"AI 分析完成，筛选出 {len(analyzed_list)} 条核心新闻")
            analyzed_list.sort(key=lambda x: x.get("score", 0), reverse=True)
            return analyzed_list

        except Exception as e:
            logger.error(f"LLM 分析过程出错: {e}")
            fallback = []
            for n in process_news[:20]:
                fallback.append({
                    "time": n["time_str"],
                    "title": "AI解析异常-降级显示",
                    "summary": n["content"][:100],
                    "score": 0,
                    "sentiment": "中性",
                    "sector": "-",
                    "stocks": "-",
                })
            return fallback

    # ──────────────── 主入口 ────────────────
    def get_top_news(self):
        """主入口：多源抓取 + LLM 精选"""
        raw_news = self.fetch_multi_source_news(hours=24)

        if not raw_news:
            logger.warning("未抓取到新闻")
            return []

        return self.analyze_news_with_llm(raw_news)


if __name__ == "__main__":
    nm = NewsManager()
    news = nm.get_top_news()
    print(f"最终获取新闻条数: {len(news)}")
    for n in news[:5]:
        print(f"  [{n.get('score')}] {n.get('title')} - {n.get('sector')} - {n.get('stocks')}")
