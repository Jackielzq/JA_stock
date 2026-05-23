import importlib.util
import os

# 项目根目录：config/ 的上一级目录
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
MONITOR_POOL_LOCAL_FILE = os.path.join(INPUT_DIR, "monitor_pool.local.xlsx")
MONITOR_POOL_SAMPLE_FILE = os.path.join(INPUT_DIR, "monitor_pool.sample.xlsx")
MONITOR_POOL_FILE = (
    MONITOR_POOL_LOCAL_FILE
    if os.path.exists(MONITOR_POOL_LOCAL_FILE)
    else MONITOR_POOL_SAMPLE_FILE
)

# 数据表配置
STOCK_BASIC_TABLE = "stock_basic"
DAILY_DATA_TABLE = "daily_data"
INDUSTRY_DETAIL_TABLE = "industry_detail"
CONCEPT_DETAIL_TABLE = "concept_detail"

# 通用运行参数
BATCH_SIZE = 500
REQUEST_INTERVAL = 0.05
HISTORY_LOOKBACK_DAYS = 300
TUSHARE_TIMEOUT = 30
TUSHARE_MAX_CALLS_PER_MINUTE = 190
TUSHARE_MAX_THREADS = 5

def _load_local_settings():
    settings_path = os.path.join(os.path.dirname(__file__), "settings.local.py")
    if not os.path.exists(settings_path):
        raise FileNotFoundError(
            f"Missing local settings file: {settings_path}. "
            "Create it from config/settings.example.py before running the project."
        )

    spec = importlib.util.spec_from_file_location("config_settings_local", settings_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load local settings from: {settings_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_local = _load_local_settings()

DB_CONFIG = _local.DB_CONFIG
TUSHARE_TOKEN = _local.TUSHARE_TOKEN
EMAIL_CONFIG = _local.EMAIL_CONFIG
LLM_CONFIG = _local.LLM_CONFIG
GITHUB_DEPLOY_CONFIG = _local.GITHUB_DEPLOY_CONFIG


def ensure_runtime_dirs():
    for path in [
        OUTPUT_DIR,
        IMG_DIR,
        DAILY_REVIEW_DIR,
        DAILY_NEWS_DIR,
        MONITOR_DIR,
        STOCK_SELECTION_DIR,
        BACKTEST_DIR,
        PDF_DIR,
        DATA_DIR,
        INPUT_DIR,
    ]:
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
