import importlib.util
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 输出目录
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
IMG_DIR = os.path.join(OUTPUT_DIR, "img")
DAILY_REVIEW_DIR = os.path.join(OUTPUT_DIR, "daily_review")
DAILY_NEWS_DIR = os.path.join(OUTPUT_DIR, "daily_news")
MONITOR_DIR = os.path.join(OUTPUT_DIR, "monitor")
STOCK_SELECTION_DIR = os.path.join(OUTPUT_DIR, "stock_selection")
BACKTEST_DIR = os.path.join(OUTPUT_DIR, "backtest")
PDF_DIR = os.path.join(OUTPUT_DIR, "pdf")
DATA_DIR = os.path.join(BASE_DIR, "data")
INPUT_DIR = os.path.join(DATA_DIR, "input")
MONITOR_POOL_FILE = os.path.join(INPUT_DIR, "monitor_pool.local.xlsx")

STOCK_BASIC_TABLE = "stock_basic"
DAILY_DATA_TABLE = "daily_data"
INDUSTRY_DETAIL_TABLE = "industry_detail"
CONCEPT_DETAIL_TABLE = "concept_detail"

BATCH_SIZE = 500
REQUEST_INTERVAL = 0.05
HISTORY_LOOKBACK_DAYS = 300
TUSHARE_TIMEOUT = 30
TUSHARE_MAX_CALLS_PER_MINUTE = 190
TUSHARE_MAX_THREADS = 5


def _load_config():
    """统一从 web_settings.json 加载配置，不存在则从 settings.local.py 迁移"""
    web_path = os.path.join(os.path.dirname(__file__), "web_settings.json")

    if not os.path.exists(web_path):
        # 首次启动：从 web_config_manager 触发迁移
        from web_config_manager import migrate_from_legacy
        migrate_from_legacy()

    if os.path.exists(web_path):
        with open(web_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        # 兜底：读取 settings.local.py
        sp = os.path.join(os.path.dirname(__file__), "settings.local.py")
        spec = importlib.util.spec_from_file_location("_sl", sp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cfg = {
            "db": mod.DB_CONFIG,
            "tushare_token": mod.TUSHARE_TOKEN,
            "email": mod.EMAIL_CONFIG,
            "recipients": [{"email": r, "remark": "", "enabled": True} for r in mod.EMAIL_CONFIG.get("receivers", [])],
            "llm": mod.LLM_CONFIG,
            "github_deploy": mod.GITHUB_DEPLOY_CONFIG,
            "monitor_pool": {"stocks": [], "concepts": []},
        }

    return cfg


_cfg = _load_config()

DB_CONFIG = _cfg["db"]
TUSHARE_TOKEN = _cfg["tushare_token"]
EMAIL_CONFIG = _cfg["email"]
LLM_CONFIG = _cfg["llm"]
GITHUB_DEPLOY_CONFIG = _cfg["github_deploy"]

# 提取启用的收件人邮箱列表（兼容旧代码）
_recipients_data = _cfg.get("recipients", [])
EMAIL_CONFIG["receivers"] = [r["email"] for r in _recipients_data if r.get("enabled", True)]


def ensure_runtime_dirs():
    for path in [OUTPUT_DIR, IMG_DIR, DAILY_REVIEW_DIR, DAILY_NEWS_DIR,
                 MONITOR_DIR, STOCK_SELECTION_DIR, BACKTEST_DIR, PDF_DIR,
                 DATA_DIR, INPUT_DIR]:
        os.makedirs(path, exist_ok=True)


def get_daily_review_html_path(date):
    return os.path.join(DAILY_REVIEW_DIR, f"daily_review_{date}.html")

def get_daily_review_pdf_path(date):
    return os.path.join(PDF_DIR, f"daily_review_{date}.pdf")

def get_daily_news_path(date):
    return os.path.join(DAILY_NEWS_DIR, f"daily_news_{date}.html")

def get_stock_selection_path(date):
    return os.path.join(STOCK_SELECTION_DIR, f"stock_selection_{date}.html")

def get_backtest_path(date):
    return os.path.join(BACKTEST_DIR, f"backtest_report_{date}.html")

def get_monitor_path(date):
    return os.path.join(MONITOR_DIR, f"monitor_{date}.html")