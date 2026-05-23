# core/monitor_manager.py - 精简极速版
import os
import pandas as pd
import logging
from sqlalchemy import text
from datetime import datetime
from config import MONITOR_POOL_FILE

logger = logging.getLogger(__name__)

class MonitorPoolManager:
    def __init__(self, db_engine):
        self.db = db_engine
        self.engine = db_engine.get_engine() if hasattr(db_engine, 'get_engine') else db_engine.engine

    def _convert_code(self, ths_code):
        code = str(ths_code).strip().upper()
        if code.startswith('SZ'): return f"{code[2:]}.SZ"
        if code.startswith('SH'): return f"{code[2:]}.SH"
        if code.startswith('BJ'): return f"{code[2:]}.BJ"
        return code 

    def init_tables(self):
        sql_stock = text("""
        CREATE TABLE IF NOT EXISTS monitor_pool_stock (
            ts_code VARCHAR(20) PRIMARY KEY,
            name VARCHAR(50),
            remark TEXT,
            sort_order INT DEFAULT 0,
            update_time DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        sql_concept = text("""
        CREATE TABLE IF NOT EXISTS monitor_pool_concept (
            concept_name VARCHAR(50) PRIMARY KEY,
            sort_order INT DEFAULT 0,
            update_time DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        try:
            with self.engine.begin() as conn:
                conn.execute(sql_stock)
                conn.execute(sql_concept)
                try: conn.execute(text("ALTER TABLE monitor_pool_stock ADD COLUMN sort_order INT DEFAULT 0"))
                except: pass
                try: conn.execute(text("ALTER TABLE monitor_pool_concept ADD COLUMN sort_order INT DEFAULT 0"))
                except: pass
        except Exception as e:
            logger.error(f"初始化表结构失败: {e}")

    def sync_from_excel(self, file_path=MONITOR_POOL_FILE):
        self.init_tables()
        if not os.path.exists(file_path): return

        try:
            df_stock = pd.read_excel(file_path, sheet_name='stock')
            df_concept = pd.read_excel(file_path, sheet_name='concept')

            if '备注' in df_stock.columns:
                df_stock['remark'] = df_stock['备注'].fillna('').astype(str).replace('nan', '')
            else:
                df_stock['remark'] = ''

            df_stock['ts_code'] = df_stock['股票代码'].apply(self._convert_code)
            df_stock['name'] = df_stock['股票名称']
            df_stock['update_time'] = datetime.now()
            df_stock['sort_order'] = range(len(df_stock))
            df_stock = df_stock[['ts_code', 'name', 'remark', 'sort_order', 'update_time']]

            # 【新增这一行】：去除重复的股票代码，保留第一条
            df_stock = df_stock.drop_duplicates(subset=['ts_code'], keep='first')

            concept_col = '概念名称' if '概念名称' in df_concept.columns else df_concept.columns[0]
            df_concept = df_concept[[concept_col]].copy()
            df_concept.columns = ['concept_name']
            df_concept['concept_name'] = df_concept['concept_name'].astype(str).str.strip()
            df_concept['update_time'] = datetime.now()
            df_concept['sort_order'] = range(len(df_concept))

            # 【新增这一行】：去除重复的概念名称，保留第一条
            df_concept = df_concept.drop_duplicates(subset=['concept_name'], keep='first')

            with self.engine.begin() as conn:
                conn.execute(text("TRUNCATE TABLE monitor_pool_stock"))
                conn.execute(text("TRUNCATE TABLE monitor_pool_concept"))
                df_stock.to_sql('monitor_pool_stock', conn, index=False, if_exists='append')
                df_concept.to_sql('monitor_pool_concept', conn, index=False, if_exists='append')
        except Exception as e:
            logger.error(f"同步Excel监控池失败: {e}")

    def get_monitor_data(self, latest_date):
        # 1. 拿取监控的概念池
        sql_pool_c = text("SELECT concept_name FROM monitor_pool_concept ORDER BY sort_order ASC")
        try:
            with self.engine.connect() as conn:
                pool_concepts = [row[0] for row in conn.execute(sql_pool_c).fetchall()]
        except Exception: 
            return None, []

        concept_trends = {'dates': [], 'concepts': pool_concepts, 'data': [], 'series': []}  # 【修改后】：加一个 'series': [] 兜底
        if pool_concepts:
            sql_dates30 = text("SELECT DISTINCT trade_date FROM concept_daily ORDER BY trade_date DESC LIMIT 30")
            with self.engine.connect() as conn:
                dates_30 = [r[0] for r in conn.execute(sql_dates30).fetchall()]
            dates_30.reverse() 
            concept_trends['dates'] = [f"{d[4:6]}-{d[6:]}" for d in dates_30]

            start_date = dates_30[0] if dates_30 else '20000101'
            c_list_str = "'" + "','".join(pool_concepts) + "'"
            sql_c_data = text(f"""
                SELECT trade_date, concept_name, avg_pct 
                FROM concept_daily 
                WHERE trade_date >= '{start_date}' AND concept_name IN ({c_list_str})
            """)
            df_c_daily = pd.read_sql(sql_c_data, self.engine)

            for i, d in enumerate(dates_30):
                day_data = df_c_daily[df_c_daily['trade_date'] == d]
                day_map = day_data.set_index('concept_name')['avg_pct'].to_dict()
                for j, c in enumerate(pool_concepts):
                    pct = day_map.get(c, 0.0)
                    concept_trends['data'].append([i, j, round(pct, 2)])

        # 2. 提取个股并关联每日行情
        sql_pool_s = text("SELECT ts_code, name, remark, sort_order FROM monitor_pool_stock")
        df_stocks = pd.read_sql(sql_pool_s, self.engine)
        
        if df_stocks.empty: return concept_trends, []
            
        ts_codes = df_stocks['ts_code'].tolist()
        codes_str = "'" + "','".join(ts_codes) + "'"


        # 【重点修改】：同时取出 concept_str 和 total_score
        sql_daily = text(f"""
            SELECT ts_code, close, pct_chg, turnover_rate, market, total_mv, concept_str, total_score
            FROM daily_data 
            WHERE trade_date = '{latest_date}' AND ts_code IN ({codes_str})
        """)

        df_daily = pd.read_sql(sql_daily, self.engine)
        df_merged = pd.merge(df_stocks, df_daily, on='ts_code', how='left')

        # 3. 计算 "X天Y板"
        sql_dates15 = text(f"SELECT DISTINCT trade_date FROM daily_data WHERE trade_date <= '{latest_date}' ORDER BY trade_date DESC LIMIT 15")
        with self.engine.connect() as conn:
            dates_15 = [r[0] for r in conn.execute(sql_dates15).fetchall()]
        dates_15.reverse()

        if dates_15 and not df_merged.empty:
            sql_hist = text(f"""
                SELECT ts_code, trade_date, pct_chg 
                FROM daily_data 
                WHERE trade_date >= '{dates_15[0]}' 
                AND trade_date <= '{latest_date}' 
                AND ts_code IN ({codes_str})
            """)
            df_hist = pd.read_sql(sql_hist, self.engine)
            name_map = df_stocks.set_index('ts_code')['name'].to_dict()

            def calc_xy_boards(sub_df):
                code = sub_df['ts_code'].iloc[0]
                name = name_map.get(code, "")
                sub_df = sub_df.sort_values('trade_date').reset_index(drop=True)
                
                if 'ST' in name: limit = 4.8
                elif code.startswith(('300', '688')): limit = 19.5
                elif code.startswith(('8', '4', '9')): limit = 29.5
                else: limit = 9.5

                is_limit = sub_df['pct_chg'] >= limit
                if not is_limit.any(): return "-"
                
                first_idx = is_limit.idxmax()
                x_days = len(sub_df) - first_idx
                y_boards = is_limit.iloc[first_idx:].sum()
                
                if x_days == 1 and y_boards == 1: return "首板"
                return f"{x_days}天{y_boards}板"

            xy_map = df_hist.groupby('ts_code').apply(calc_xy_boards).to_dict()
            df_merged['xy_boards'] = df_merged['ts_code'].map(xy_map).fillna('-')
        else:
            df_merged['xy_boards'] = '-'

        df_merged = df_merged.fillna({'close': 0, 'pct_chg': 0, 'turnover_rate': 0, 'market': '未知', 'remark': '', 'total_mv': 0, 'concept_str': '-', 'total_score': 0})
        stock_list = []
        for _, row in df_merged.iterrows():
            stock_list.append({
                'ts_code': row['ts_code'],
                'name': row['name'],
                'board': row['market'], 
                'close': round(row['close'], 2) if row['close'] else '-',
                'pct_chg': round(row['pct_chg'], 2),
                'turnover_rate': round(row['turnover_rate'], 2),
                'total_mv_val': float(row['total_mv']) if row['total_mv'] else 0,
                'total_mv': round(row['total_mv'] / 10000, 2) if row['total_mv'] else '-',
                'xy_boards': row['xy_boards'],
                'concept_str': row['concept_str'],
                # 新增分数返回给前端
                'total_score': round(row['total_score'], 1) if pd.notnull(row['total_score']) else 0.0,
                'remark': row['remark'],
                'sort_order': row['sort_order'] 
            })

        stock_list = sorted(stock_list, key=lambda x: x['sort_order'])
        return concept_trends, stock_list
