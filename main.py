# main.py - 模块化入口 (完整版 v7：支持 concept/fix/select 等全模式，增加自动截图)
import argparse
import logging
import sys
import os
import webbrowser
from datetime import datetime

# 路径设置
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 导入模块
from config import (
    MONITOR_POOL_FILE,
    ensure_runtime_dirs,
    get_backtest_path,
    get_daily_news_path,
    get_daily_review_html_path,
    get_daily_review_pdf_path,
    get_monitor_path,
    get_stock_selection_path,
)
from core.db_engine import DBEngine
from core.data_updater import StockDataUpdater
from core.factor_calculator import FactorCalculator
from core.strategies import StrategyEngine
from core.monitor_manager import MonitorPoolManager
from report.renderer import ReportRenderer
from services.news_service import NewsManager
from services.mail_service import send_report_email
from services.pdf_service import convert_html_to_pdf

# 尝试导入 Hexo
try:
    from services.deploy_hexo import deploy_reports
except ImportError:
    deploy_reports = None

logger = logging.getLogger(__name__)
# 强制配置日志，避免被其他库覆盖
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', force=True)
ensure_runtime_dirs()


def run_review(db, renderer):
    """执行每日复盘 (已合并量化选股，移除强势股/跌停股)"""
    logger.info(">>> 启动每日复盘模块 (行情篇 + 选股篇)...")
    date = db.get_latest_date()
    if not date:
        logger.error("数据库无数据")
        return

    # 1. 计算因子
    calc = FactorCalculator(db)
    trend_history = calc.get_trend_history(date, db.pro)
    if not trend_history: return
    latest = trend_history[0]

    # 移除强势股和跌停股的计算，节省时间
    # streak_map, df_limit_real = calc.calculate_strict_streaks(date)
    # strong_stocks = calc.get_strong_stocks(date, streak_map)
    # limit_down_stocks = calc.get_limit_down_stocks(date)

    top_sectors = calc.get_sector_data(date, ascending=False)
    bottom_sectors = calc.get_sector_data(date, ascending=True)
    active_stocks = calc.get_active_stocks(date)
    regulatory_stocks = calc.get_regulatory_abnormal_stocks(date)
    top_concepts, bottom_concepts = calc.get_concept_data()

    # --- 新增：调用选股引擎获取策略数据 ---
    logger.info(">>> 正在合并计算选股策略数据...")
    strategy_engine = StrategyEngine(db)
    selection_results = strategy_engine.run_selection(date)
    # 为了防止只在复盘里跑而不入库，如果你想在此处也将选股结果入库，可以解除下面这行的注释
    strategy_engine.save_to_db(selection_results, date)

    grouped_stocks = {s: [] for s in strategy_engine.ALL_STRATEGIES}
    for item in selection_results:
        s_name = item.get('strategy_name')
        if s_name in grouped_stocks: grouped_stocks[s_name].append(item)
        else: grouped_stocks[s_name] = [item]
    # -----------------------------------

    # 2. 构造数据
    score_data = {'total': latest['score']}
    market_core = {
        'sh_close': latest['sh_close'], 'sh_pct': latest['sh_pct'],
        'total_amount_str': f"{latest['amount']:.0f}亿", 'vol_chg_pct': latest['vol_pct'],
        'ad_ratio': latest['ad_ratio'], 'up_count': latest['up_count'], 'down_count': latest['down_count'],
        'limit_up_count': latest['limit_up'], 'limit_down_count': latest['limit_down']
    }

    data = {
        'date': date,
        'generate_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'market_core': market_core, 'score_data': score_data,
        'promo_rate': latest['promo_rate'], 'high_board': latest['height'],
        'trend_history': trend_history, 'top_sectors': top_sectors, 'bottom_sectors': bottom_sectors,
        # 'strong_stocks': strong_stocks, 'limit_down_stocks': limit_down_stocks, # 已移除
        'active_stocks': active_stocks, 'regulatory_stocks': regulatory_stocks,
        'latest': latest, 'top_concepts': top_concepts, 'bottom_concepts': bottom_concepts,

        # 传入选股数据给模板
        'grouped_stocks': grouped_stocks,
        'total_count': len(selection_results)
    }

    # 3. 渲染生成 HTML，并获取真实绝对路径
    outfile = get_daily_review_html_path(date)
    final_html_path = renderer.render('daily_review.html', data, outfile)

    if final_html_path:
        # 4. 生成对应的 PDF 文件
        pdf_outfile = get_daily_review_pdf_path(date)
        convert_html_to_pdf(final_html_path, pdf_outfile)
        # 5. 打开浏览器
        webbrowser.open(f'file://{final_html_path}')


def run_news(db, renderer):
    """执行新闻吹哨"""
    logger.info(">>> 启动AI新闻智能分析板块...")
    date = db.get_latest_date()
    if not date: date = datetime.now().strftime('%Y%m%d')

    news_list = []
    try:
        news_mgr = NewsManager()
        news_list = news_mgr.get_top_news()
    except Exception as e:
        logger.error(f"新闻抓取失败: {e}")

    data = {
        'date': date,
        'generate_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'news_list': news_list
    }

    outfile = get_daily_news_path(date)
    final_html_path = renderer.render('daily_news.html', data, outfile)

    if final_html_path:
        # 打开网页
        webbrowser.open(f'file://{final_html_path}')


def run_selection(db, renderer):
    """执行选股与回测"""
    logger.info(">>> 启动选股策略模块...")
    strategy_engine = StrategyEngine(db)
    date = db.get_latest_date()
    if not date: return

    # 1. 选股
    results = strategy_engine.run_selection(date)
    strategy_engine.save_to_db(results, date)

    grouped_stocks = {s: [] for s in strategy_engine.ALL_STRATEGIES}
    for item in results:
        s_name = item.get('strategy_name')
        if s_name in grouped_stocks: grouped_stocks[s_name].append(item)
        else: grouped_stocks[s_name] = [item]

    sel_filename = get_stock_selection_path(date)
    final_sel_path = renderer.render('stock_selection.html', {
        'date': date, 'grouped_stocks': grouped_stocks,
        'total_count': len(results), 'generate_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }, sel_filename)

    if final_sel_path:
        # 打开网页
        webbrowser.open(f'file://{final_sel_path}')

    # 2. 回测
    summary, consolidated_list, trend_dates, matrix_data = strategy_engine.run_backtest()
    bt_filename = get_backtest_path(datetime.now().strftime('%Y%m%d'))
    final_bt_path = renderer.render('back_test.html', {
        'summary': summary,
        'consolidated_list': consolidated_list,
        'trend_dates': trend_dates,
        'matrix_data': matrix_data,
        'generate_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }, bt_filename)

    if final_bt_path:
        # 打开网页
        webbrowser.open(f'file://{final_bt_path}')


def run_email(db):
    """发送邮件 (纯净版：无长篇正文，只发 PDF 附件)"""
    logger.info(">>> 启动邮件发送服务...")
    date = db.get_latest_date()
    if not date: return

    # 定义附件路径
    review_pdf = get_daily_review_pdf_path(date)
    select_file = get_stock_selection_path(date)
    backtest_file = get_backtest_path(datetime.now().strftime('%Y%m%d'))
    news_file = get_daily_news_path(date)

    # 邮件正文改为极其精简的提示语（防垃圾邮件拦截，不能完全为空）
    mail_content = f"""
    <div style='font-family: sans-serif; padding: 20px; color: #333;'>
        <h2>📈 A股量化复盘日报 - {date}</h2>
        <p>各位股神好！，今日的深度复盘报告已生成完毕。</p>
        <p style='color: #e74c3c; font-weight: bold;'>为了保证图表的完美展示，请直接打开附件中的 PDF 文件查看。</p>
        <hr style='border: none; border-top: 1px solid #eee;' />
        <p style='font-size: 12px; color: #999;'>System Generated by AI Quant</p>
    </div>
    """

    # 组装附件 (优先加上刚才生成的复盘 PDF)
    attachments = []
    if os.path.exists(review_pdf): attachments.append(review_pdf)
    # if os.path.exists(select_file): attachments.append(select_file)
    # if os.path.exists(backtest_file): attachments.append(backtest_file)
    # if os.path.exists(news_file): attachments.append(news_file)

    if attachments:
        send_report_email(mail_content, date, "A股量化复盘日报", attachments)
    else:
        logger.warning("没有找到任何报告附件，邮件未发送。")


def run_github_deploy(db):
    """自动上传复盘报告到指定 GitHub 仓库"""
    logger.info(">>> 启动 GitHub 静态页面部署服务...")
    date = db.get_latest_date()
    if not date:
        logger.error("数据库无数据，无法部署。")
        return

    # 动态导入，避免其他模式不必要的依赖加载
    from services.github_service import deploy_review_to_github
    deploy_review_to_github(date)


# main.py

def run_monitor(db, renderer):
    """运行监控池功能 (Excel同步入库 + 静态网页生成)"""
    logger.info(">>> 启动核心监控池服务...")

    # 获取数据库里最新的交易日
    latest_date = db.get_latest_date()
    if not latest_date:
        logger.error("数据库无行情数据，无法生成监控报告。")
        return

    # 1. 初始化 Manager
    from core.monitor_manager import MonitorPoolManager
    monitor_manager = MonitorPoolManager(db)

    # 2. 优先从 Web 配置同步监控池；若 Web 配置无数据则回退到 Excel
    synced = monitor_manager.sync_from_web_config()
    if not synced:
        logger.info("Web配置监控池为空，尝试从 Excel 同步...")
        monitor_file = MONITOR_POOL_FILE
        monitor_manager.sync_from_excel(monitor_file)

    # 3. 从数据库中抽取报告所需数据 (不依赖Excel)
    concept_trends, stock_list = monitor_manager.get_monitor_data(latest_date)

    if not stock_list and not concept_trends.get('series') and not concept_trends.get('data'):
        logger.warning(f"监控池数据为空。如果你有Excel，请确保放在 {MONITOR_POOL_FILE} 且格式正确。")
        return

    # 4. 构造数据对象
    data = {
        'date': latest_date,
        'generate_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'concept_trends': concept_trends,
        'stock_list': stock_list
    }

    # 5. 渲染生成HTML并自动打开
    outfile = get_monitor_path(latest_date)
    final_html_path = renderer.render('monitor.html', data, outfile)

    if final_html_path:
        # logger.info(f"监控报告已生成: {final_html_path}")
        webbrowser.open(f'file://{final_html_path}')


def main():
    parser = argparse.ArgumentParser(description='A股量化分析系统')

    # 【修改1】在 choices 里新增了一个 'factor' 模式，方便你单独手工触发算分
    parser.add_argument('--mode',
        choices=['init', 'update', 'review', 'news', 'select', 'deploy', 'email', 'fix', 'concept', 'github', 'monitor', 'factor'],
        default='select',
        help='运行模式'
    )

    parser.add_argument('--days', type=int, default=1, help='增量更新天数')
    args = parser.parse_args()

    # --- 1. 独立运行的数据维护模式 (Fix/Concept) ---

    if args.mode == 'fix':
        # 修复模式：修复均线等历史数据
        logger.info(">>> 启动历史数据修复模式 (重算 MA 均线)...")
        updater = StockDataUpdater()
        updater.run('fix')
        return

    if args.mode == 'concept':
        # [新增] 概念更新模式：调用问财更新概念表
        logger.info(">>> 启动概念数据更新 (Source: Wencai)...")
        updater = StockDataUpdater()
        updater.run('concept')
        return

    # --- 2. 需要常规 DB 引擎的模式 ---

    db = DBEngine()
    renderer = ReportRenderer()

    if args.mode == 'update':
        updater = StockDataUpdater()
        updater.run('update', args.days)
        
        # 【修改2】：在这里加上，每次 update 更新完每日数据后，自动触发计算分数并落库
        logger.info(">>> 日线数据更新完毕，正在自动计算全局因子 (评分与概念)...")
        latest_date = db.get_latest_date()
        if latest_date:
            calc = FactorCalculator(db)
            calc.update_daily_factors(latest_date)
            logger.info(">>> 全局因子写入完成！")

    elif args.mode == 'init':
        updater = StockDataUpdater()
        updater.run('init')
        
    # 【修改3】：单独的手工算分模式（如果你今天不想重新下载数据，只想跑一下刚写好的算分逻辑，可以直接用这个）
    elif args.mode == 'factor':
        logger.info(">>> 手动触发：全局基础因子计算与落库...")
        latest_date = db.get_latest_date()
        if latest_date:
            calc = FactorCalculator(db)
            calc.update_daily_factors(latest_date)
            logger.info(">>> 计算与落库完成！")
            
    elif args.mode == 'review':
        run_review(db, renderer)
    elif args.mode == 'news':
        run_news(db, renderer)
    elif args.mode == 'select':
        run_selection(db, renderer)
    elif args.mode == 'email':
        run_email(db)
    elif args.mode == 'deploy':
        logger.info(">>> 启动博客同步...")
        if deploy_reports:
            target_date = db.get_latest_date()
            if target_date: deploy_reports(target_date)
        else: logger.error("未找到 Hexo 部署模块")
    elif args.mode == 'github':
        run_github_deploy(db)
    elif args.mode == 'monitor':
        run_monitor(db, renderer)


if __name__ == "__main__":
    main()