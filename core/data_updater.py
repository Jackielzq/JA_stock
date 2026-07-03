# core/data_updater.py - 核心数据管理模块 - 概念增强版 v8
# 修复与新增：
# 1. 新增 concept_daily 表，存储概念板块的日线及多周期涨幅
# 2. 新增 update_concept_metrics 方法，自动计算概念合成指数
# 3. 集成到增量/全量更新流程中
# 4. [修复] 增量更新增加“防重复机制(幂等)”，彻底解决重复数据导致的成交量翻倍崩溃问题
# 5. [修复] 修复高版本 Pandas (2.2.0+) 带来的 FutureWarning 红色警告报错问题

import tushare as ts
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text, inspect, MetaData, Table, Column, Integer, String, Float, DateTime
from datetime import datetime, timedelta
import time
import logging
from threading import Lock
import sys
import warnings

# ==========================================
# [新增配置] 解决高版本 Pandas 产生的控制台红色警告
# ==========================================
warnings.simplefilter(action='ignore', category=FutureWarning)
pd.set_option('future.no_silent_downcasting', True)

# 尝试导入 pywencai
try:
    import pywencai
    HAS_WENCAI = True
except ImportError:
    HAS_WENCAI = False

from config import DB_CONFIG, TUSHARE_TOKEN, DAILY_DATA_TABLE, REQUEST_INTERVAL, TUSHARE_MAX_THREADS, TUSHARE_MAX_CALLS_PER_MINUTE, HISTORY_LOOKBACK_DAYS

# 配置日志
logger = logging.getLogger(__name__)

class RateLimiter:
    """API请求频率限制器"""
    def __init__(self, max_calls_per_minute, max_threads):
        self.max_calls_per_minute = max_calls_per_minute
        self.calls_made = 0
        self.start_time = time.time()
        self.lock = Lock()
        self.min_interval = 60.0 / max_calls_per_minute
    
    def wait_if_needed(self):
        with self.lock:
            current_time = time.time()
            elapsed = current_time - self.start_time
            if elapsed >= 60:
                self.calls_made = 0
                self.start_time = current_time
            if self.calls_made >= self.max_calls_per_minute:
                wait_time = 60 - elapsed
                if wait_time > 0:
                    time.sleep(wait_time)
                    self.calls_made = 0
                    self.start_time = time.time()
            self.calls_made += 1
    
    def get_sleep_time(self):
        return max(self.min_interval, REQUEST_INTERVAL)

class TechnicalCalculator:
    """技术指标计算引擎"""
    @staticmethod
    def calculate_metrics(df):
        if df.empty: return df
        df = df.sort_values('trade_date').reset_index(drop=True)
        raw_close = df['close']
        
        if 'adj_factor' in df.columns:
            # [修改] 增加 infer_objects(copy=False) 消除下转型警告
            df['adj_factor'] = df['adj_factor'].ffill().fillna(1.0).infer_objects(copy=False)
            adj_close = df['close'] * df['adj_factor']
        else:
            adj_close = df['close']

        ma_periods = [5, 10, 20, 30, 60, 120, 250]
        for p in ma_periods:
            df[f'ma_{p}'] = raw_close.rolling(window=p).mean().round(2)

        if 'vol' in df.columns:
            vol_ma_5 = df['vol'].shift(1).rolling(window=5).mean()
            df['calc_vol_ratio'] = df['vol'] / vol_ma_5
            df['calc_vol_ratio'] = df['calc_vol_ratio'].replace([np.inf, -np.inf], 0).infer_objects(copy=False).round(2)
        else:
            df['calc_vol_ratio'] = np.nan

        high_periods = [30, 60, 90, 120, 250]
        for p in high_periods:
            df[f'high_{p}'] = df['high'].rolling(window=p).max().round(2)

        pct_periods = {'1m': 20, '3m': 60, '6m': 120}
        for name, p in pct_periods.items():
            df[f'pct_chg_{name}'] = adj_close.pct_change(periods=p).round(4) * 100

        if 'up_limit' in df.columns:
            is_limit_up = (df['close'] >= df['up_limit']) & (df['pct_chg'] > 0)
        else:
            is_limit_up = df['pct_chg'] >= 9.8 
        is_limit_up_col = is_limit_up.astype(int)
        
        limit_counts = [10, 30, 90, 120, 180]
        for p in limit_counts:
            df[f'limit_up_count_{p}'] = is_limit_up_col.rolling(window=p).sum()

        up_condition = df['pct_chg'] > 0
        up_groups = (~up_condition).cumsum()
        df['up_streak'] = df.groupby(up_groups).cumcount()
        df.loc[~up_condition, 'up_streak'] = 0

        down_condition = df['pct_chg'] < 0
        down_groups = (~down_condition).cumsum()
        df['down_streak'] = df.groupby(down_groups).cumcount()
        df.loc[~down_condition, 'down_streak'] = 0
        
        df = df.drop(columns=['is_limit_up'], errors='ignore')
        return df

class StockDataUpdater:
    def __init__(self):
        self.pro = ts.pro_api(TUSHARE_TOKEN)
        self.rate_limiter = RateLimiter(TUSHARE_MAX_CALLS_PER_MINUTE, TUSHARE_MAX_THREADS)
        
        db_url = f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset={DB_CONFIG['charset']}"
        self.engine = create_engine(db_url, pool_recycle=3600, pool_size=20, max_overflow=30)
        
        self.db_valid_columns = [
            'ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close', 'change', 'pct_chg', 'vol', 'amount',
            'adj_factor', 'adj_close',
            'turnover_rate', 'turnover_rate_f', 'volume_ratio', 'pe', 'pe_ttm', 'pb', 'total_mv', 'circ_mv',
            'ma_5', 'ma_10', 'ma_20', 'ma_30', 'ma_60', 'ma_120', 'ma_250',
            'high_30', 'high_60', 'high_90', 'high_120', 'high_250',
            'pct_chg_1m', 'pct_chg_3m', 'pct_chg_6m',
            'up_streak', 'down_streak',
            'limit_up_count_10', 'limit_up_count_30', 'limit_up_count_90', 'limit_up_count_120', 'limit_up_count_180',
            'stock_name', 'industry', 'area', 'market', 'processed_time'
        ]

        self._ensure_table_structure()
    
    def _ensure_table_structure(self):
        metadata = MetaData()
        
        # 1. 个股日线表
        columns = [
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('ts_code', String(20), nullable=False, index=True),
            Column('trade_date', String(10), nullable=False, index=True),
            Column('open', Float), Column('high', Float), Column('low', Float), Column('close', Float),
            Column('pre_close', Float), Column('change', Float), Column('pct_chg', Float),
            Column('vol', Float), Column('amount', Float),
            Column('adj_factor', Float), Column('adj_close', Float),
            Column('turnover_rate', Float), Column('turnover_rate_f', Float), Column('volume_ratio', Float),
            Column('pe', Float), Column('pe_ttm', Float), Column('pb', Float),
            Column('total_mv', Float), Column('circ_mv', Float),
            Column('ma_5', Float), Column('ma_10', Float), Column('ma_20', Float),
            Column('ma_30', Float), Column('ma_60', Float), Column('ma_120', Float), Column('ma_250', Float),
            Column('high_30', Float), Column('high_60', Float), Column('high_90', Float),
            Column('high_120', Float), Column('high_250', Float),
            Column('pct_chg_1m', Float), Column('pct_chg_3m', Float), Column('pct_chg_6m', Float),
            Column('up_streak', Integer), Column('down_streak', Integer),
            Column('limit_up_count_10', Integer), Column('limit_up_count_30', Integer),
            Column('limit_up_count_90', Integer), Column('limit_up_count_120', Integer),
            Column('limit_up_count_180', Integer),
            Column('stock_name', String(50)),
            Column('industry', String(50)),
            Column('area', String(50)),
            Column('market', String(20)),
            Column('processed_time', DateTime, default=datetime.now)
        ]
        Table(DAILY_DATA_TABLE, metadata, *columns)

        # 2. 行业日线数据表
        Table('industry_daily', metadata,
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('trade_date', String(10), nullable=False, index=True),
            Column('industry_name', String(50), nullable=False, index=True),
            Column('avg_pct', Float, comment='当日平均涨幅'),
            Column('pct_5d', Float, comment='5日累计涨幅'),
            Column('pct_10d', Float, comment='10日累计涨幅'),
            Column('pct_20d', Float, comment='20日累计涨幅'),
            Column('processed_time', DateTime, default=datetime.now)
        )

        # 3. 概念明细表 (映射关系)
        Table('concept_detail', metadata,
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('ts_code', String(20), nullable=False, index=True),
            Column('stock_name', String(50)),
            Column('concept_name', String(100), nullable=False, index=True),
            Column('src', String(20), default='wencai'),
            Column('processed_time', DateTime, default=datetime.now)
        )

        # 4. [新增] 概念日线数据表 (时间序列数据)
        Table('concept_daily', metadata,
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('trade_date', String(10), nullable=False, index=True),
            Column('concept_name', String(100), nullable=False, index=True),
            Column('avg_pct', Float, comment='当日平均涨幅'),
            Column('pct_5d', Float, comment='5日'),
            Column('pct_10d', Float, comment='10日'),
            Column('pct_20d', Float, comment='20日'),
            Column('pct_30d', Float, comment='30日'),
            Column('pct_60d', Float, comment='60日'),
            Column('pct_90d', Float, comment='90日'),
            Column('pct_120d', Float, comment='120日'),
            Column('pct_250d', Float, comment='250日'),
            Column('processed_time', DateTime, default=datetime.now)
        )

        try:
            metadata.create_all(self.engine)
            # 自动修复 concept_detail 表缺少 src 的问题
            with self.engine.connect() as conn:
                insp = inspect(self.engine)
                if insp.has_table('concept_detail'):
                    cols = [c['name'] for c in insp.get_columns('concept_detail')]
                    if 'src' not in cols:
                        logger.info("自动修复: concept_detail 表缺少 src 列，正在添加...")
                        conn.execute(text("ALTER TABLE concept_detail ADD COLUMN src VARCHAR(20) DEFAULT 'wencai'"))
        except Exception as e:
            logger.error(f"初始化表结构失败: {e}")

    def get_stock_info_dict(self):
        self.rate_limiter.wait_if_needed()
        df = self.pro.stock_basic(fields='ts_code,name,industry,area,market')
        return df.set_index('ts_code').to_dict('index')

    def get_merged_daily_data(self, trade_date):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.rate_limiter.wait_if_needed()
                df_daily = self.pro.daily(trade_date=trade_date)
                if df_daily.empty: return pd.DataFrame()
                
                self.rate_limiter.wait_if_needed()
                df_basic = self.pro.daily_basic(trade_date=trade_date, 
                                              fields='ts_code,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,total_mv,circ_mv')
                
                self.rate_limiter.wait_if_needed()
                df_adj = self.pro.adj_factor(trade_date=trade_date, fields='ts_code,adj_factor')
                
                df_merge = pd.merge(df_daily, df_basic, on='ts_code', how='left', suffixes=('', '_basic'))
                df_merge = pd.merge(df_merge, df_adj, on='ts_code', how='left')
                
                if 'trade_date_basic' in df_merge.columns:
                    df_merge.drop(columns=['trade_date_basic'], inplace=True)
                return df_merge
            except Exception as e:
                logger.warning(f"获取 {trade_date} 数据失败 (第{attempt+1}次): {e}")
                time.sleep(2 ** attempt)
        return pd.DataFrame()
    
    def _clean_data_before_insert(self, df):
        if df.empty: return df
        valid_cols = [c for c in df.columns if c in self.db_valid_columns]
        df_clean = df[valid_cols].copy()
        df_clean = df_clean.replace([np.inf, -np.inf], np.nan).infer_objects(copy=False)
        return df_clean

    def fix_history_data(self):
        logger.info("=== 开始修复历史数据 (均线重算) ===")
        with self.engine.connect() as conn:
            codes = pd.read_sql(f"SELECT DISTINCT ts_code FROM {DAILY_DATA_TABLE}", conn)['ts_code'].tolist()
        
        total_stocks = len(codes)
        batch_size = 50 
        for i in range(0, total_stocks, batch_size):
            batch_codes = codes[i : i + batch_size]
            codes_str = "'" + "','".join(batch_codes) + "'"
            logger.info(f"处理进度 {i+1}-{min(i+batch_size, total_stocks)}...")
            
            query = f"""
                SELECT ts_code, trade_date, `open`, high, low, close, pre_close, `change`, pct_chg, vol, amount,
                       adj_factor, turnover_rate, turnover_rate_f, volume_ratio, pe, pe_ttm, pb, total_mv, circ_mv,
                       stock_name, industry, area, market
                FROM {DAILY_DATA_TABLE}
                WHERE ts_code IN ({codes_str})
                ORDER BY trade_date
            """
            try:
                df_raw = pd.read_sql(query, self.engine)
            except Exception as e:
                logger.error(f"读取数据失败: {e}")
                continue
            
            if df_raw.empty: continue
            
            df_fixed = df_raw.groupby('ts_code', group_keys=False).apply(TechnicalCalculator.calculate_metrics)
            
            if 'calc_vol_ratio' in df_fixed.columns:
                # [修改] 增加 infer_objects 消除 fillna 的 FutureWarning
                df_fixed['volume_ratio'] = df_fixed['volume_ratio'].fillna(df_fixed['calc_vol_ratio']).infer_objects(copy=False)
            
            df_fixed['processed_time'] = datetime.now()
            df_final = self._clean_data_before_insert(df_fixed)
            try:
                with self.engine.begin() as conn:
                    conn.execute(text(f"DELETE FROM {DAILY_DATA_TABLE} WHERE ts_code IN ({codes_str})"))
                    df_final.to_sql(DAILY_DATA_TABLE, conn, index=False, if_exists='append', method='multi', chunksize=2000)
            except Exception as e:
                logger.error(f"写入失败: {e}")
        logger.info("=== 历史数据修复完成！ ===")

    def update_industry_metrics(self, trade_dates=None):
        logger.info("=== 开始更新行业板块涨幅数据 ===")
        if not trade_dates:
            with self.engine.connect() as conn:
                last_date = conn.execute(text(f"SELECT MAX(trade_date) FROM {DAILY_DATA_TABLE}")).scalar()
            if not last_date: return
            trade_dates = [last_date] 
        
        calc_start_date = sorted(trade_dates)[0]
        # 回溯 45 天计算 20日线
        buffer_date = (datetime.strptime(calc_start_date, '%Y%m%d') - timedelta(days=45)).strftime('%Y%m%d')

        logger.info(f"加载行业基础数据 (从 {buffer_date})...")
        sql = text(f"""
            SELECT trade_date, industry, AVG(pct_chg) as avg_pct
            FROM {DAILY_DATA_TABLE}
            WHERE trade_date >= '{buffer_date}' 
            AND industry IS NOT NULL AND industry != ''
            AND stock_name NOT LIKE '%%ST%%'
            AND ts_code NOT LIKE '688%%' AND ts_code NOT LIKE '8%%' AND ts_code NOT LIKE '4%%'
            GROUP BY trade_date, industry
            ORDER BY trade_date
        """)
        try:
            df_ind = pd.read_sql(sql, self.engine)
        except Exception as e:
            logger.error(f"读取行业数据失败: {e}")
            return

        if df_ind.empty: return
        df_ind = df_ind.sort_values(['industry', 'trade_date'])
        
        def calc_rolling_ret(x, window):
            returns = 1 + x / 100
            res = returns.rolling(window=window).apply(np.prod, raw=True)
            return (res - 1) * 100

        df_ind['pct_5d'] = df_ind.groupby('industry')['avg_pct'].transform(lambda x: calc_rolling_ret(x, 5))
        df_ind['pct_10d'] = df_ind.groupby('industry')['avg_pct'].transform(lambda x: calc_rolling_ret(x, 10))
        df_ind['pct_20d'] = df_ind.groupby('industry')['avg_pct'].transform(lambda x: calc_rolling_ret(x, 20))

        df_to_save = df_ind[df_ind['trade_date'].isin(trade_dates)].copy()
        if df_to_save.empty:
            logger.info("没有新的行业数据需要保存")
            return

        df_to_save = df_to_save.rename(columns={'industry': 'industry_name'})
        df_to_save['processed_time'] = datetime.now()
        for col in ['avg_pct', 'pct_5d', 'pct_10d', 'pct_20d']:
            df_to_save[col] = df_to_save[col].round(2)

        dates_str = "'" + "','".join(trade_dates) + "'"
        logger.info(f"正在写入 {len(df_to_save)} 条行业趋势数据...")
        try:
            with self.engine.begin() as conn:
                conn.execute(text(f"DELETE FROM industry_daily WHERE trade_date IN ({dates_str})"))
                df_to_save.to_sql('industry_daily', conn, index=False, if_exists='append', chunksize=2000)
        except Exception as e:
            logger.error(f"行业数据写入失败: {e}")

    # =========================================================================
    # [新增] 概念板块计算模块
    # =========================================================================
    def update_concept_metrics(self, trade_dates=None):
        """
        计算概念板块指数
        逻辑：个股pct_chg + 概念映射 -> 概念日涨幅 -> 概念滚动涨幅 -> concept_daily表
        """
        logger.info("=== 开始更新概念板块涨幅数据 (Synthetic Index) ===")
        
        # 1. 确定日期范围
        if not trade_dates:
            with self.engine.connect() as conn:
                last_date = conn.execute(text(f"SELECT MAX(trade_date) FROM {DAILY_DATA_TABLE}")).scalar()
            if not last_date: return
            trade_dates = [last_date]
        
        calc_start_date = sorted(trade_dates)[0]
        # 回溯 400 天以确保能计算 250日线
        buffer_date = (datetime.strptime(calc_start_date, '%Y%m%d') - timedelta(days=400)).strftime('%Y%m%d')

        logger.info(f"加载概念合成所需的历史数据 (从 {buffer_date})...")

        # 2. 读取数据 (Pandas处理，避免复杂SQL Join)
        try:
            # 2.1 读取个股行情
            sql_daily = text(f"""
                SELECT ts_code, trade_date, pct_chg 
                FROM {DAILY_DATA_TABLE}
                WHERE trade_date >= '{buffer_date}'
                AND ts_code NOT LIKE '8%%' AND ts_code NOT LIKE '4%%'
            """)
            df_daily = pd.read_sql(sql_daily, self.engine)
            
            # 2.2 读取概念映射
            sql_map = text("SELECT ts_code, concept_name FROM concept_detail")
            df_map = pd.read_sql(sql_map, self.engine)
        except Exception as e:
            logger.error(f"读取数据失败: {e}")
            return

        if df_daily.empty or df_map.empty:
            logger.warning("个股数据或概念映射为空，无法计算概念指数")
            return

        # 3. 数据合并与计算
        logger.info(f"正在合成概念指数 (Daily Rows: {len(df_daily)}, Map Rows: {len(df_map)})...")
        
        # Merge: (ts_code, date, pct) + (ts_code, concept) -> (date, concept, pct)
        # 注意: 一个股票对应多个概念，merge后行数会膨胀
        df_merged = pd.merge(df_daily, df_map, on='ts_code', how='inner')
        
        # 聚合: 按日期和概念分组，计算平均涨跌幅
        df_concept_daily = df_merged.groupby(['trade_date', 'concept_name'])['pct_chg'].mean().reset_index()
        df_concept_daily.rename(columns={'pct_chg': 'avg_pct'}, inplace=True)
        
        # 4. 计算滚动涨幅
        logger.info("正在计算多周期滚动涨幅 (Rolling)...")
        df_concept_daily = df_concept_daily.sort_values(['concept_name', 'trade_date'])
        
        def calc_rolling_ret(x, window):
            returns = 1 + x / 100
            res = returns.rolling(window=window).apply(np.prod, raw=True)
            return (res - 1) * 100

        periods = [5, 10, 20, 30, 60, 90, 120, 250]
        for p in periods:
            df_concept_daily[f'pct_{p}d'] = df_concept_daily.groupby('concept_name')['avg_pct'].transform(lambda x: calc_rolling_ret(x, p))

        # 5. 过滤需要保存的日期
        df_to_save = df_concept_daily[df_concept_daily['trade_date'].isin(trade_dates)].copy()
        
        if df_to_save.empty:
            logger.info("没有新的概念指数数据需要保存")
            return

        # 6. 入库
        df_to_save['processed_time'] = datetime.now()
        # 四舍五入
        float_cols = ['avg_pct'] + [f'pct_{p}d' for p in periods]
        for col in float_cols:
            df_to_save[col] = df_to_save[col].round(2)

        # 写入 concept_daily 表
        dates_str = "'" + "','".join(trade_dates) + "'"
        logger.info(f"正在写入 {len(df_to_save)} 条概念日线数据...")
        
        try:
            with self.engine.begin() as conn:
                conn.execute(text(f"DELETE FROM concept_daily WHERE trade_date IN ({dates_str})"))
                df_to_save.to_sql('concept_daily', conn, index=False, if_exists='append', chunksize=5000)
            logger.info("✅ 概念板块数据更新完成！")
        except Exception as e:
            logger.error(f"概念数据写入失败: {e}")

    def update_concepts_wencai(self):
        """
        全量更新/增量覆盖同花顺问财概念数据 (已集成：代理直连绕过 + 股票级局部安全覆盖逻辑)
        """
        logger.info("=== 开始更新概念板块 (Source: Wencai - 智能覆盖版) ===")
        
        # 1. 强行屏蔽代理环境变量，防止 VPN 干扰同花顺接口
        import os
        for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
            os.environ[key] = ''
        os.environ['NO_PROXY'] = '*'
        os.environ['no_proxy'] = '*'
        
        pd.options.mode.chained_assignment = None

        if not HAS_WENCAI:
            logger.error("❌ 未检测到 pywencai 库，无法更新概念。请运行: pip install pywencai pandas")
            return

        try:
            logger.info("正在请求问财接口(需要一点时间)...")
            res = pywencai.get(query='A股股票的所属概念', loop=True)
            if res is None or res.empty:
                logger.warning("问财接口返回为空")
                return

            concept_col = next((c for c in res.columns if '所属概念' in c), None)
            code_col = next((c for c in res.columns if 'code' in c or '代码' in c), None)
            name_col = next((c for c in res.columns if '股票简称' in c or '名称' in c), None)

            if not concept_col or not code_col:
                logger.error(f"列名解析失败，当前列: {res.columns.tolist()}")
                return

            df = res[[code_col, name_col, concept_col]].copy()
            df.columns = ['ts_code', 'stock_name', 'concepts']

            def normalize_code(c):
                c = str(c).strip()
                if '.' in c: return c
                if c.startswith('6'): return f"{c}.SH"
                if c.startswith(('0', '3')): return f"{c}.SZ"
                if c.startswith(('8', '4')): return f"{c}.BJ"
                return c
            
            df['ts_code'] = df['ts_code'].apply(normalize_code)
            df['concepts'] = df['concepts'].astype(str).str.split(r'[;；]') 
            df_exploded = df.explode('concepts')
            
            df_exploded['concepts'] = df_exploded['concepts'].str.strip()
            df_exploded = df_exploded[df_exploded['concepts'] != '']
            df_exploded = df_exploded[df_exploded['concepts'] != 'nan']

            df_final = df_exploded.rename(columns={'concepts': 'concept_name'})
            df_final['src'] = 'wencai'
            df_final['processed_time'] = datetime.now()
            
            df_final = df_final.drop_duplicates(subset=['ts_code', 'concept_name'])
            
            fetched_stocks = df_final['ts_code'].unique().tolist()
            logger.info(f"解析完成，共获取到 {len(df_final)} 条概念映射关系，涉及 {len(fetched_stocks)} 只个股。")

            # 2. 获取数据库中当前的记录行数，用于比对
            try:
                with self.engine.connect() as conn:
                    before_count = conn.execute(text("SELECT COUNT(*) FROM concept_detail")).scalar()
            except Exception:
                before_count = 0

            # 3. 智能覆盖机制：不再 TRUNCATE，只删除本次成功获取到的股票的历史数据
            if len(fetched_stocks) > 0:
                logger.info("准备安全写入数据库 (增量覆盖模式)...")
                try:
                    with self.engine.begin() as conn:
                        # 批量分段删除已拉取股票的旧映射（分批以防代码过多导致 SQL 超长报错）
                        batch_size = 500
                        for i in range(0, len(fetched_stocks), batch_size):
                            batch = fetched_stocks[i : i + batch_size]
                            codes_str = "'" + "','".join(batch) + "'"
                            conn.execute(text(f"DELETE FROM concept_detail WHERE ts_code IN ({codes_str})"))
                        
                        # 追加写入新拉取到的关联数据
                        df_final.to_sql('concept_detail', conn, index=False, if_exists='append', chunksize=5000)
                    
                    # 4. 计算写入后的最新汇总信息
                    with self.engine.connect() as conn:
                        after_count = conn.execute(text("SELECT COUNT(*) FROM concept_detail")).scalar()
                        unique_stocks = conn.execute(text("SELECT COUNT(DISTINCT ts_code) FROM concept_detail")).scalar()
                    
                    logger.info(f"✅ 概念数据增量覆盖成功！")
                    logger.info(f"📊 数据库原有关系数：{before_count} 条 -> 最新关系数：{after_count} 条。")
                    logger.info(f"📊 数据库当前具有概念映射关系的个股数：{unique_stocks} 只。")
                except Exception as e:
                    logger.error(f"安全覆盖写入数据库失败: {e}")
            else:
                logger.warning("未获取到任何有效的个股，本次未对数据库进行写入操作。")

        except Exception as e:
            logger.error(f"概念更新失败: {e}")


    # def update_concepts_wencai(self):
    #     """
    #     更新概念板块数据 (断点续传补全版：强力代理绕过 + 自动断点补全 + 失败重试)
    #     """
    #     logger.info("=== 开始更新概念板块 (已自动切换至 AkShare 智能补全模式) ===")
        
    #     # 1. 强行屏蔽代理环境变量，防止 VPN 干扰
    #     import os
    #     for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
    #         os.environ[key] = ''
    #     os.environ['NO_PROXY'] = '*'
    #     os.environ['no_proxy'] = '*'
        
    #     pd.options.mode.chained_assignment = None

    #     try:
    #         import akshare as ak
    #     except ImportError:
    #         logger.error("❌ 未检测到 akshare 库，无法更新概念。请运行: pip install akshare")
    #         return

    #     try:
    #         logger.info("正在获取全市场概念板块列表...")
    #         df_concepts = None
    #         for attempt in range(3):
    #             try:
    #                 df_concepts = ak.stock_board_concept_name_em()
    #                 if df_concepts is not None and not df_concepts.empty:
    #                     break
    #             except Exception as e:
    #                 if attempt == 2: raise e
    #                 time.sleep(1.5)
            
    #         if df_concepts is None or df_concepts.empty:
    #             logger.warning("获取概念板块列表为空")
    #             return

    #         all_concept_names = df_concepts['板块名称'].tolist()
            
    #         # 2. 读取本地数据库已成功保存的概念，实现【断点续传 / 缺啥补啥】
    #         existing_concepts = []
    #         try:
    #             with self.engine.connect() as conn:
    #                 # 检查表是否存在且有数据
    #                 insp = inspect(self.engine)
    #                 if insp.has_table('concept_detail'):
    #                     res_df = pd.read_sql("SELECT DISTINCT concept_name FROM concept_detail", conn)
    #                     if not res_df.empty:
    #                         existing_concepts = res_df['concept_name'].tolist()
    #         except Exception as e:
    #             logger.warning(f"尝试读取已有概念缓存失败 (将进行全量更新): {e}")

    #         # 过滤出未成功获取的缺失概念
    #         concept_names = [c for c in all_concept_names if c not in existing_concepts]
            
    #         if not concept_names:
    #             logger.info("🎉 检查完成！数据库中的概念数据已经是 100% 完整状态，无需任何补全。")
    #             return

    #         if len(existing_concepts) > 0:
    #             logger.info(f"📊 检测到数据库中已存在 {len(existing_concepts)} 个概念。")
    #             logger.info(f"🔍 本次将开启【智能补全模式】，仅针对缺失的 {len(concept_names)} 个概念进行精准修补...")
    #         else:
    #             logger.info(f"📊 本地无缓存，将开启【全新全量同步】，共需处理 {len(concept_names)} 个概念板块...")

    #         all_relations = []
    #         total = len(concept_names)
    #         success_count = 0

    #         for idx, name in enumerate(concept_names, 1):
    #             df_cons = pd.DataFrame()
                
    #             # 3. 单板块抓取失败重试逻辑 (3次机会)
    #             max_retries = 3
    #             is_success = False
    #             for attempt in range(max_retries):
    #                 try:
    #                     df_cons = ak.stock_board_concept_cons_em(symbol=name)
    #                     is_success = True
    #                     break 
    #                 except Exception as e:
    #                     if attempt < max_retries - 1:
    #                         sleep_time = 2.0 * (attempt + 1)
    #                         time.sleep(sleep_time)
    #                     else:
    #                         logger.warning(f"⚠️ 概念 [{name}] 成分股在重试 {max_retries} 次后仍失败 (此概念本次跳过，下次运行可继续补全): {e}")

    #             if not is_success or df_cons.empty:
    #                 continue
                
    #             success_count += 1
    #             try:
    #                 for _, row in df_cons.iterrows():
    #                     code = str(row['代码']).strip().zfill(6)
    #                     if code.startswith('6'):
    #                         ts_code = f"{code}.SH"
    #                     elif code.startswith(('0', '3')):
    #                         ts_code = f"{code}.SZ"
    #                     elif code.startswith(('8', '4', '9')):
    #                         ts_code = f"{code}.BJ"
    #                     else:
    #                         ts_code = code

    #                     all_relations.append({
    #                         'ts_code': ts_code,
    #                         'stock_name': row.get('名称', row.get('股票简称', '-')),
    #                         'concept_name': name,
    #                         'src': 'akshare',
    #                         'processed_time': datetime.now()
    #                     })
                    
    #                 if idx % 20 == 0 or idx == total:
    #                     logger.info(f"进度: {idx}/{total} - 正在补全: {name}, 本次累计新增映射: {len(all_relations)}")
                    
    #                 time.sleep(0.08)
    #             except Exception as e:
    #                 logger.warning(f"解析概念 [{name}] 成分股异常: {e}")

    #         if not all_relations:
    #             logger.info("ℹ️ 本轮未能成功新增任何板块。如果 VPN 仍旧干扰严重，可尝试暂时关闭 VPN 客户端后重试。")
    #             return

    #         df_final = pd.DataFrame(all_relations).drop_duplicates(subset=['ts_code', 'concept_name'])
            
    #         # 4. 安全追加写入数据库 (非 TRUNCATE 模式，实现幂等局部更新)
    #         logger.info(f"准备将本次成功补全的 {success_count} 个概念（共 {len(df_final)} 条个股映射）追加写入数据库...")
            
    #         try:
    #             with self.engine.begin() as conn:
    #                 # 如果是全量更新（原本表为空），直接清空；如果是断点补全，则只删除本次要写入的这些概念名，防止重复
    #                 if len(existing_concepts) == 0:
    #                     conn.execute(text("TRUNCATE TABLE concept_detail"))
    #                 else:
    #                     names_to_delete = df_final['concept_name'].unique().tolist()
    #                     names_str = "'" + "','".join([n.replace("'", "\\'") for n in names_to_delete]) + "'"
    #                     conn.execute(text(f"DELETE FROM concept_detail WHERE concept_name IN ({names_str})"))
                    
    #                 df_final.to_sql('concept_detail', conn, index=False, if_exists='append', chunksize=5000)
                
    #             # 重新计算最新状态
    #             with self.engine.connect() as conn:
    #                 final_count = conn.execute(text("SELECT COUNT(DISTINCT concept_name) FROM concept_detail")).scalar()
                
    #             logger.info(f"✅ 概念数据更新/补全成功！当前数据库已完整拥有 {final_count}/{len(all_concept_names)} 个概念板块数据。")
                
    #             if final_count < len(all_concept_names):
    #                 logger.warning(f"💡 提示：目前还有 {len(all_concept_names) - final_count} 个板块因网络原因未获取成功。您无需担心，直接再次执行命令即可，程序会自动检测并继续下载缺失的板块！")
            
    #         except Exception as e:
    #             logger.error(f"写入数据库失败: {e}")

    #     except Exception as e:
    #         logger.error(f"概念数据同步失败: {e}")



    def init_all_daily_data(self, start_date='20200101'):
        end_date = datetime.now().strftime('%Y%m%d')
        logger.info(f"=== 开始全量初始化: {start_date} - {end_date} ===")
        
        trade_dates = self.pro.trade_cal(exchange='SSE', is_open='1', start_date=start_date, end_date=end_date)['cal_date'].tolist()
        stock_info = self.get_stock_info_dict()
        
        all_raw_data = []
        for i, date in enumerate(trade_dates):
            if i % 10 == 0:
                logger.info(f"下载进度: {i}/{len(trade_dates)} ({date})")
            df_day = self.get_merged_daily_data(date)
            if not df_day.empty:
                all_raw_data.append(df_day)
            time.sleep(self.rate_limiter.get_sleep_time())
            
        if not all_raw_data: return
        logger.info("数据下载完成，正在合并...")
        full_df = pd.concat(all_raw_data, ignore_index=True)
        
        logger.info("正在计算衍生技术指标...")
        full_df['stock_name'] = full_df['ts_code'].map(lambda x: stock_info.get(x, {}).get('name', ''))
        full_df['industry'] = full_df['ts_code'].map(lambda x: stock_info.get(x, {}).get('industry', ''))
        full_df['area'] = full_df['ts_code'].map(lambda x: stock_info.get(x, {}).get('area', ''))
        full_df['market'] = full_df['ts_code'].map(lambda x: stock_info.get(x, {}).get('market', ''))
        
        full_df = full_df.groupby('ts_code', group_keys=False).apply(TechnicalCalculator.calculate_metrics)
        if 'calc_vol_ratio' in full_df.columns:
            # [修改] 增加 infer_objects 消除 fillna 的 FutureWarning
            full_df['volume_ratio'] = full_df['volume_ratio'].fillna(full_df['calc_vol_ratio']).infer_objects(copy=False)

        full_df['processed_time'] = datetime.now()
        full_df = self._clean_data_before_insert(full_df)
        
        logger.info(f"计算完成，准备写入数据库 (共 {len(full_df)} 行)...")
        chunksize = 5000
        try:
            full_df.to_sql(DAILY_DATA_TABLE, self.engine, index=False, if_exists='replace', chunksize=chunksize, method='multi')
            logger.info("个股全量初始化成功！")
            
            # 更新行业
            self.update_industry_metrics(trade_dates)
            # [新增] 更新概念
            self.update_concept_metrics(trade_dates)
            
        except Exception as e:
            logger.error(f"写入数据库失败: {e}")
            
    def incremental_update(self):
        logger.info("=== 开始增量更新 ===")
        with self.engine.connect() as conn:
            if not inspect(self.engine).has_table(DAILY_DATA_TABLE):
                logger.error("表不存在，请先运行 init")
                return
            res = conn.execute(text(f"SELECT MAX(trade_date) FROM {DAILY_DATA_TABLE}")).scalar()
            if not res:
                self.init_all_daily_data(start_date=(datetime.now() - timedelta(days=365)).strftime('%Y%m%d'))
                return
            last_date = str(res)
            today = datetime.now().strftime('%Y%m%d')
            if last_date >= today:
                logger.info("已经是最新数据")
                return

        start_date = (datetime.strptime(last_date, '%Y%m%d') + timedelta(days=1)).strftime('%Y%m%d')
        trade_dates = self.pro.trade_cal(exchange='SSE', is_open='1', start_date=start_date, end_date=today)['cal_date'].tolist()
        
        if not trade_dates:
            logger.info("没有新的交易日数据")
            return

        logger.info(f"需要更新日期: {trade_dates}")
        stock_info = self.get_stock_info_dict()

        lookback_date = (datetime.strptime(start_date, '%Y%m%d') - timedelta(days=HISTORY_LOOKBACK_DAYS)).strftime('%Y%m%d')
        logger.info(f"加载历史数据 (从 {lookback_date})...")
        
        req_cols_sql = "ts_code, trade_date, close, adj_factor, high, pct_chg, vol"
        history_query = f"SELECT {req_cols_sql} FROM {DAILY_DATA_TABLE} WHERE trade_date >= '{lookback_date}'"
        
        try:
            df_history = pd.read_sql(history_query, self.engine)
        except Exception as e:
            logger.warning(f"历史数据加载重试... {e}")
            df_history = pd.DataFrame()

        new_data_buffer = []
        for date in trade_dates:
            logger.info(f"获取 {date} 数据...")
            df_today = self.get_merged_daily_data(date)
            if df_today.empty: continue
            new_data_buffer.append(df_today)
        
        if not new_data_buffer: return

        df_new_raw = pd.concat(new_data_buffer, ignore_index=True)
        for col in ['ts_code', 'trade_date', 'close', 'high', 'pct_chg', 'adj_factor', 'vol']:
            if col not in df_new_raw.columns:
                df_new_raw[col] = np.nan
        
        df_for_calc = pd.concat([df_history, df_new_raw[df_history.columns]], ignore_index=True)
        logger.info("正在计算新数据的技术指标...")
        
        df_calculated = df_for_calc.groupby('ts_code', group_keys=False).apply(TechnicalCalculator.calculate_metrics)
        
        update_dates_set = set(trade_dates)
        df_final_metrics = df_calculated[df_calculated['trade_date'].isin(update_dates_set)].copy()
        
        df_new_raw['stock_name'] = df_new_raw['ts_code'].map(lambda x: stock_info.get(x, {}).get('name', ''))
        df_new_raw['industry'] = df_new_raw['ts_code'].map(lambda x: stock_info.get(x, {}).get('industry', ''))
        df_new_raw['area'] = df_new_raw['ts_code'].map(lambda x: stock_info.get(x, {}).get('area', ''))
        df_new_raw['market'] = df_new_raw['ts_code'].map(lambda x: stock_info.get(x, {}).get('market', ''))
        df_new_raw['processed_time'] = datetime.now()

        cols_in_raw = set(df_new_raw.columns)
        cols_to_use_from_metrics = [c for c in df_final_metrics.columns if c not in cols_in_raw and c not in ['id']]
        
        result = pd.merge(df_new_raw, df_final_metrics[['ts_code', 'trade_date'] + cols_to_use_from_metrics], 
                         on=['ts_code', 'trade_date'], how='left')
        
        if 'calc_vol_ratio' in result.columns:
            if 'volume_ratio' in result.columns:
                # [修改] 增加 infer_objects 消除 fillna 的 FutureWarning
                result['volume_ratio'] = result['volume_ratio'].fillna(result['calc_vol_ratio']).infer_objects(copy=False)
            else:
                result['volume_ratio'] = result['calc_vol_ratio']

        result = self._clean_data_before_insert(result)
        logger.info(f"准备保存 {len(result)} 条新记录到数据库...")

        # =========================================================================
        # [新增核心防重复机制]
        # 在执行 to_sql(..., if_exists='append') 之前，先将目标日期的数据从数据库中彻底抹除。
        # 这样即使本脚本中途报错中断、或者用户重复执行多次更新，也绝对不会产生重复数据！
        # =========================================================================
        dates_to_delete_str = "'" + "','".join(trade_dates) + "'"
        try:
            with self.engine.begin() as conn:
                conn.execute(text(f"DELETE FROM {DAILY_DATA_TABLE} WHERE trade_date IN ({dates_to_delete_str})"))
            logger.info(f"✅ 防重复拦截：已清空数据库中 {trade_dates} 可能存在的旧数据")
        except Exception as e:
            logger.error(f"清理旧数据失败: {e}")
        # =========================================================================

        result.to_sql(DAILY_DATA_TABLE, self.engine, index=False, if_exists='append', method='multi', chunksize=2000)
        logger.info("个股日线数据追加写入成功！")
        
        # [更新] 同时更新行业和概念
        self.update_industry_metrics(trade_dates)
        self.update_concept_metrics(trade_dates)
        
        logger.info("增量更新全部完成")

    def run(self, mode='init', days=None):
        if mode == 'init':
            self.init_all_daily_data(start_date='20200101')
        elif mode == 'update':
            self.incremental_update()
        elif mode == 'fix':
            self.fix_history_data()
        elif mode == 'concept':
            self.update_concepts_wencai()