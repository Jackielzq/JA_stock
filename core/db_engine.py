# core/db_engine.py - 数据库与API连接引擎 (修复AttributeError版)
import tushare as ts
import pandas as pd
from sqlalchemy import create_engine, text
from config import DB_CONFIG, TUSHARE_TOKEN
import logging

logger = logging.getLogger(__name__)

class DBEngine:
    def __init__(self):
        # 1. 初始化数据库连接
        self.db_url = f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset={DB_CONFIG['charset']}"
        self.engine = create_engine(self.db_url)

        # 2. 初始化 Tushare API (修复：补充 self.pro 属性)
        try:
            self.pro = ts.pro_api(TUSHARE_TOKEN)
        except Exception as e:
            logger.error(f"Tushare 初始化失败: {e}")
            self.pro = None

    def get_engine(self):
        return self.engine

    def get_latest_date(self):
        """获取数据库中最新的交易日"""
        try:
            with self.engine.connect() as conn:
                res = conn.execute(text("SELECT MAX(trade_date) FROM daily_data")).scalar()
                return str(res) if res else None
        except Exception as e:
            logger.error(f"获取最新日期失败: {e}")
            return None

    def get_data(self, sql, params=None):
        """通用查询方法

        Args:
            sql: SQL语句
            params: SQL参数（字典形式），默认为None

        Returns:
            DataFrame: 查询结果
        """
        try:
            if params:
                return pd.read_sql(text(str(sql)), self.engine, params=params)
            else:
                return pd.read_sql(text(str(sql)), self.engine)
        except Exception as e:
            logger.error(f"SQL查询失败: {e}")
            return pd.DataFrame()

    def get_recent_trading_days(self, end_date, n=10):
        """
        基于 daily_data 表获取最近 n 个交易日
        """
        try:
            # 你的表里日期列名是 'trade_date'
            sql = f"SELECT DISTINCT trade_date FROM daily_data WHERE trade_date <= '{end_date}' ORDER BY trade_date DESC LIMIT {n}"
            df = pd.read_sql(sql, self.engine)

            # 转为字符串列表返回
            return [str(d) for d in df['trade_date'].tolist()]
        except Exception as e:
            print(f"获取交易日列表失败: {e}")
            return [end_date]

    def get_date_data(self, date):
        """
        获取某一日的全市场数据，用于计算市场情绪
        """
        import pandas as pd
        try:
            # 获取必要的列：代码、涨跌幅、连阳天数、成交额(如果有)
            # 注意：这里假设你的表里有 'amount' 列，如果没有请改为 'vol' 或去掉
            sql = f"SELECT ts_code, pct_chg, up_streak, amount FROM daily_data WHERE trade_date = '{date}'"
            return pd.read_sql(sql, self.engine)
        except Exception as e:
            # 如果没有 amount 列，尝试不带 amount 查询
            try:
                sql = f"SELECT ts_code, pct_chg, up_streak FROM daily_data WHERE trade_date = '{date}'"
                return pd.read_sql(sql, self.engine)
            except:
                print(f"获取单日数据失败 ({date}): {e}")
                return pd.DataFrame()
