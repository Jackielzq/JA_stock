# core/strategies.py - 策略引擎模块 (V8.2: 概念对齐监控池，增加策略4、策略5)

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from sqlalchemy import text, inspect, MetaData, Table, Column, Integer, String, Float
import logging
import time
import sys

# 模块化导入
try:
    from utils.market_utils import infer_market, print_progress
except ImportError:
    def infer_market(code):
        if code.startswith('6'): return '主板'
        if code.startswith('0'): return '主板'
        if code.startswith('3'): return '创业板'
        return '未知'
    def print_progress(current, total, prefix):
        pass

from config import DB_CONFIG, DAILY_DATA_TABLE

logger = logging.getLogger(__name__)

class StrategyEngine:
    def __init__(self, db_engine):
        self.db = db_engine
        self.engine = db_engine.get_engine()
        self.selection_table = 'selection_history'
        self._init_selection_table()
        
        # 策略列表更新为最新五个策略
        self.ALL_STRATEGIES = [
            "策略1",  #三连击突破
            "策略2",  #分歧弱转强
            "策略3",  #半年天量突破
            "策略4",  #十字星高位整理
            "策略5"   #长周期双底突破
        ]

    def _init_selection_table(self):
        metadata = MetaData()
        insp = inspect(self.engine)
        columns_def = [
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('trade_date', String(20), index=True),
            Column('ts_code', String(20), index=True),
            Column('stock_name', String(50)),
            Column('strategy_name', String(50)),
            Column('initial_price', Float),
            Column('total_score', Float),
            Column('total_mv', Float),
            Column('industry', String(50)),
            Column('market', String(20)),
            Column('processed_time', String(50))
        ]
        if not insp.has_table(self.selection_table):
            Table(self.selection_table, metadata, *columns_def)
            metadata.create_all(self.engine)
        else:
            existing_cols = [c['name'] for c in insp.get_columns(self.selection_table)]
            with self.engine.connect() as conn:
                if 'total_mv' not in existing_cols: conn.execute(text(f"ALTER TABLE {self.selection_table} ADD COLUMN total_mv FLOAT"))
                if 'industry' not in existing_cols: conn.execute(text(f"ALTER TABLE {self.selection_table} ADD COLUMN industry VARCHAR(50)"))
                if 'market' not in existing_cols: conn.execute(text(f"ALTER TABLE {self.selection_table} ADD COLUMN market VARCHAR(20)"))

    def get_latest_date(self):
        with self.engine.connect() as conn:
            res = conn.execute(text("SELECT MAX(trade_date) FROM daily_data")).scalar()
        return res

    def get_bulk_history(self, ts_codes, end_date, lookback=250):
        if not ts_codes: return {}
        logger.info(f"正在预加载 {len(ts_codes)} 只股票的历史数据...")
        start_date = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=lookback + 45)).strftime('%Y%m%d')
        
        query = f"""
            SELECT ts_code, trade_date, close, open, high, low, pct_chg, vol, volume_ratio, turnover_rate, amount
            FROM daily_data 
            WHERE trade_date >= '{start_date}' 
            AND trade_date <= '{end_date}'
        """
        start_t = time.time()
        try:
            df_all = pd.read_sql(text(query), self.engine)
        except Exception as e:
            logger.error(f"加载历史数据失败: {e}")
            return {}
        
        df_all = df_all[df_all['ts_code'].isin(ts_codes)]
        df_all = df_all.sort_values('trade_date')
        
        logger.info(f"数据加载完成，耗时 {time.time()-start_t:.2f}秒，共 {len(df_all)} 行记录")
        return {code: data for code, data in df_all.groupby('ts_code')}

    def _get_trend_class(self, pct):
        if pct > 1.0: return "trend-deep-red"
        elif 0 < pct <= 1.0: return "trend-light-red"
        elif pct < 0: return "trend-green"
        return ""

    def _enrich_results(self, results, date):
        """为结果注入行业涨跌幅，并格式化概念字符串(对齐monitor样式)"""
        if not results: return results
        
        def get_real_date(table_name, target_date):
            try:
                sql = text(f"SELECT MAX(trade_date) FROM {table_name} WHERE trade_date <= '{target_date}'")
                with self.engine.connect() as conn:
                    res = conn.execute(sql).scalar()
                if res: return str(res)
            except Exception: pass
            return target_date 

        real_ind_date = get_real_date('industry_daily', date)
        
        ind_map = {}
        try:
            sql_ind = text(f"SELECT industry_name, avg_pct FROM industry_daily WHERE trade_date = '{real_ind_date}'")
            df_ind = pd.read_sql(sql_ind, self.engine)
            if not df_ind.empty:
                df_ind['industry_name'] = df_ind['industry_name'].astype(str).str.strip()
                ind_map = df_ind.set_index('industry_name')['avg_pct'].to_dict()
        except Exception: pass

        for item in results:
            # 1. 处理行业字段(带当日涨幅和颜色)
            raw_ind = str(item.get('industry', ''))
            if raw_ind == 'None' or raw_ind == 'nan': raw_ind = ''
            raw_ind = raw_ind.strip()
            
            item['industry_html'] = raw_ind 
            if raw_ind and raw_ind in ind_map:
                pct = ind_map[raw_ind]
                sign = '+' if pct > 0 else ''
                css_class = self._get_trend_class(pct)
                item['industry_html'] = f"<span class='concept-item {css_class}'>{raw_ind}({sign}{pct:.1f}%)</span>"
            elif raw_ind:
                item['industry_html'] = raw_ind
            
            # 2. 核心修改：不再查全部概念排名，直接使用注入的监控池概念
            concept_str = item.get('concept_str', '-')
            if concept_str and concept_str != '-':
                # 这里注入了与 monitor.html 完全一致的紫色加粗字体样式
                item['concepts_str'] = f"<span style='color: #8b5cf6; font-weight: 600; font-size: 13px; text-align: left; line-height: 1.5;'>{concept_str}</span>"
            else:
                item['concepts_str'] = "-"
                
        return results

    def _inject_history_stats(self, results, current_date):
        if not results: return results
        ts_codes = [r['ts_code'] for r in results]
        code_str = "'" + "','".join(ts_codes) + "'"
        
        sql = text(f"""
            SELECT ts_code, MIN(trade_date) as first_date_val, COUNT(*) as cnt 
            FROM {self.selection_table} 
            WHERE ts_code IN ({code_str})
            GROUP BY ts_code
        """)
        
        stats_map = {}
        try:
            df_stats = pd.read_sql(sql, self.engine)
            if not df_stats.empty:
                stats_map = df_stats.set_index('ts_code').to_dict('index')
        except Exception as e:
            logger.error(f"查询历史统计失败: {e}")

        for item in results:
            code = item['ts_code']
            stat = stats_map.get(code, {})
            db_cnt = stat.get('cnt', 0)
            db_first_date = stat.get('first_date_val')
            
            total_count = db_cnt + 1
            first_date_raw = db_first_date if db_first_date else current_date
            
            s_date = str(first_date_raw).strip()
            if len(s_date) == 8:
                formatted_date = f"{s_date[:4]}-{s_date[4:6]}-{s_date[6:]}"
            else:
                formatted_date = s_date
            
            item['first_date'] = formatted_date
            item['selection_count'] = total_count
            
        return results

    def run_selection(self, date):
        if not date: return []
        logger.info(f"--- 启动选股引擎 ({date}) ---")
        
        sql = text(f"SELECT * FROM daily_data WHERE trade_date = '{date}'")
        df = pd.read_sql(sql, self.engine)
        if df.empty: return []

        df.columns = df.columns.str.strip().str.lower()
        df = df[~df['ts_code'].str.startswith(('688', '8', '4', '9'))]
        df = df[~df['stock_name'].str.contains('ST')]
        df = df[df['vol'] > 0]
        
        total_stocks = len(df)
        logger.info(f"进入策略筛选池: {total_stocks} 只股票 (已过滤北交/ST)")

        valid_codes = df['ts_code'].tolist()
        history_map = self.get_bulk_history(valid_codes, date, 250) 

        results = []
        logger.info("正在执行策略计算...")
        
        for idx, (i, row) in enumerate(df.iterrows()):
            if idx % 50 == 0: print_progress(idx + 1, total_stocks, "筛选进度")

            ts_code = row['ts_code']
            hist_df = history_map.get(ts_code, pd.DataFrame())
            
            def get_val(key, default=0):
                val = row.get(key)
                return val if val is not None else default

            close = get_val('close')
            ma60 = get_val('ma_60')
            ma120 = get_val('ma_120')

            # ===================================================
            # 【全局通用前置拦截器】
            # 收盘价必须同时大于 60 日均线和 120 日均线
            # ===================================================
            if not (ma60 > 0 and ma120 > 0 and close > ma60 and close > ma120):
                continue

            hist_full = hist_df.reset_index(drop=True)
            total_days = len(hist_full)
            

            # 公共辅助变量：判定涨停与获取历史涨停索引
            is_startup = ts_code.startswith(('30', '68'))
            lu_limit = 19.5 if is_startup else 9.5
            lu_indices = hist_full.index[hist_full['pct_chg'] >= lu_limit].tolist()

            strategies_hit = []

            pct_chg = get_val('pct_chg')
            vol_ratio = get_val('volume_ratio')
            current_vol = get_val('vol')
            turnover = get_val('turnover_rate')
            open_price = get_val('open')
            total_mv = get_val('total_mv')
            ma5 = get_val('ma_5')

            # ===================================================
            # 策略1（三连击突破）：
            # 1、连续三天放量 (T > T-1 > T-2)
            # 2、连续三天上涨 (最低价逐渐升高、最高价逐渐升高)
            # 3、当天收盘价突破半年(60日)新高
            # 4、近三天无涨停
            # 5、市值小于800亿 (total_mv单位为万元，300亿=3,000,000万)
            # 6、股价不高于60
            # 7、半年内(120日)涨停次数不超过4次
            # 8、当日换手率大于5%
            # 9、当天涨幅：主板<=7%，创业板<=12%
            # ===================================================
            if total_days >= 120:
                limit_pct = 12.0 if is_startup else 7.0
                if turnover > 5.0 and total_mv <= 8000000 and close <= 60.0 and pct_chg <= limit_pct:
                    d_t0 = hist_full.iloc[-1]
                    d_t1 = hist_full.iloc[-2]
                    d_t2 = hist_full.iloc[-3]
                    
                    # 1. 连续三天放量
                    cond_vol = (d_t0['vol'] > d_t1['vol']) and (d_t1['vol'] > d_t2['vol'])
                    if cond_vol:
                        # 2. 连续三天上涨 (最低价抬高且最高价抬高)
                        cond_low = (d_t0['low'] > d_t1['low']) and (d_t1['low'] > d_t2['low'])
                        cond_high = (d_t0['high'] > d_t1['high']) and (d_t1['high'] > d_t2['high'])
                        
                        if cond_low and cond_high:
                            # 3. 突破60日新高
                            past_60_max = hist_full.iloc[max(0, total_days-61):total_days-1]['high'].max()
                            if close > past_60_max:
                                # 4. 近三天无涨停
                                recent_3_lus = [j for j in lu_indices if j >= total_days - 3]
                                if len(recent_3_lus) == 0:
                                    # 7. 半年内(120日)涨停次数不超过4次
                                    half_year_lus = [j for j in lu_indices if j >= total_days - 120]
                                    if len(half_year_lus) <= 4:
                                        strategies_hit.append(("策略1", "tag-s1"))
                                        

            # ===================================================
            # 策略2（分歧弱转强）：
            # 1、T-2日涨停
            # 2、T-1日成交量大于T-2
            # 3、T-1日和当日都必须收红，且当日收盘价要高于T-1日
            # 4、当日成交量不低于T-1日的70%
            # 5、T-1日和当日收红（涨幅>0）且收阳（收盘>开盘，非真阴假阳），且未涨停
            # 6、当日换手率不小于5%
            # ===================================================
            if total_days >= 3:
                d_t0 = hist_full.iloc[-1]
                d_t1 = hist_full.iloc[-2]
                d_t2 = hist_full.iloc[-3]
                
                # 6. 当日换手率不小于5%
                if turnover >= 5.0:
                    # 1. T-2日涨停
                    if d_t2['pct_chg'] >= lu_limit:
                        # 2. T-1日成交量大于T-2
                        if d_t1['vol'] > d_t2['vol']:
                            # 5. T-1日和当日收红且收阳（非真阴假阳），且未涨停
                            cond_t1_red = d_t1['pct_chg'] > 0
                            cond_t1_yang = d_t1['close'] > d_t1['open']
                            cond_t1_not_lu = d_t1['pct_chg'] < lu_limit
                            
                            cond_t0_red = d_t0['pct_chg'] > 0
                            cond_t0_yang = d_t0['close'] > d_t0['open']
                            cond_t0_not_lu = d_t0['pct_chg'] < lu_limit
                            
                            if (cond_t1_red and cond_t1_yang and cond_t1_not_lu and 
                                cond_t0_red and cond_t0_yang and cond_t0_not_lu):
                                # 3. 当日收盘价要高于T-1日
                                if d_t0['close'] > d_t1['close']:
                                    # 4. 当日成交量不低于T-1日的70%
                                    if d_t0['vol'] >= d_t1['vol'] * 0.7:
                                        strategies_hit.append(("策略2", "tag-s2"))

            # ===================================================
            # 策略3(半年天量刚性突破 & 180日新高):
            # 1、当前放量收涨（非涨停）
            # 2、昨天未突破，今天刚突破（昨收 <= 120日天量最高价，且 今收 > 120日天量最高价）
            # 3、收盘价高于5、10、20、60、120日均线
            # 4、近5个交易日内涨幅不超过30%
            # 5、当日收盘价创 180 日新高
            # 6、近半年(120日)内涨停次数不超过3次
            # ===================================================
            if total_days >= 180:
                d_t1 = hist_full.iloc[-2]
                
                # 1. 当前放量收涨（非涨停）
                cond_vol = current_vol > d_t1['vol']
                cond_up = pct_chg > 0
                cond_not_lu = pct_chg < lu_limit
                
                if cond_vol and cond_up and cond_not_lu:
                    
                    # 6. 半年(120日)内涨停次数不超过3次 (利用前置算好的 lu_indices 数组)
                    half_year_lus = [j for j in lu_indices if j >= total_days - 120]
                    if len(half_year_lus) <= 3:
                        
                        # 从当天数据库字段中读取现成的均线
                        ma10 = get_val('ma_10')
                        ma20 = get_val('ma_20')
                        
                        # 3. 收盘价高于5、10、20、60、120日均线
                        if ma5 > 0 and ma10 > 0 and ma20 > 0 and close > ma5 and close > ma10 and close > ma20:
                            
                            # 4. 近5个交易日内涨幅不超过30%
                            d_t5 = hist_full.iloc[-6] 
                            pct_5d = (close / d_t5['close'] - 1) * 100
                            
                            if pct_5d <= 30.0:
                                
                                # 5. 当日收盘价创180日新高
                                start_180_idx = max(0, total_days - 181)
                                past_180_max = hist_full.iloc[start_180_idx : total_days - 1]['high'].max()
                                
                                if close > past_180_max:
                                
                                    # 2. 寻找近半年(120日)成交量最高那天
                                    start_120_idx = max(0, total_days - 121)
                                    past_120_df = hist_full.iloc[start_120_idx : total_days - 1]
                                    
                                    if not past_120_df.empty:
                                        max_vol_idx = past_120_df['vol'].idxmax()
                                        max_vol_high = past_120_df.loc[max_vol_idx, 'high']
                                        
                                        # 核心精准过滤：昨天还没突破，今天才刚刚突破！
                                        if close > max_vol_high and d_t1['close'] <= max_vol_high:
                                            strategies_hit.append(("策略3", "tag-s3"))


# ===================================================
            # 策略4（涨停强整理突破）：
            # 1、近一个月（20个交易日）只有一次涨停，其余日期都没涨停
            # 2、涨停后的所有交易日，股价（收盘价）都在这次涨停价的97%以上
            # 3、当日成交量大于昨日
            # 4、收盘价破30日新高
            # 5、近3天的涨幅不大于15%（排除涨停当天）
            # ===================================================
            if total_days >= 35:
                d_t0 = hist_full.iloc[-1]
                d_t1 = hist_full.iloc[-2]
                
                # 1. 近一个月（20个交易日）内只有一次涨停
                one_month_indices = range(total_days - 20, total_days)
                month_lus = [idx for idx in one_month_indices if idx in lu_indices]
                
                if len(month_lus) == 1:
                    lu_idx = month_lus[0]
                    # 确保涨停日发生在今天之前（即存在“涨停后”的交易日）
                    if lu_idx < total_days - 1:
                        lu_price = hist_full.loc[lu_idx, 'close']
                        
                        # 2. 涨停后的所有交易日（含今天），股价（收盘价）都在这次涨停价的97%以上
                        post_lu_indices = range(lu_idx + 1, total_days)
                        cond_above_97 = all(hist_full.loc[j, 'close'] >= lu_price * 0.97 for j in post_lu_indices)
                        
                        if cond_above_97:
                            # 3. 当日成交量大于昨日
                            if d_t0['vol'] > d_t1['vol']:
                                
                                # 5. 近3天的累计涨幅不大于15%（剔除涨停当天的波动）
                                comp_return = 1.0
                                for offset in range(3):
                                    idx = total_days - 1 - offset
                                    # 如果该交易日是涨停当天，则不计入这3天的累计涨幅计算
                                    if idx != lu_idx:
                                        day_pct = hist_full.loc[idx, 'pct_chg']
                                        comp_return *= (1 + day_pct / 100.0)
                                pct_3d_excluded = (comp_return - 1) * 100
                                
                                if pct_3d_excluded <= 15.0:
                                    # 4. 当天收盘价突破30日新高（不含今天的前30个交易日的最高价）
                                    past_30_max = hist_full.iloc[total_days - 31 : total_days - 1]['high'].max()
                                    if close > past_30_max:
                                        strategies_hit.append(("策略4", "tag-s4"))

            # ===================================================
            # 策略5（长周期双底突破）：
            # 1、当日收红 (pct_chg > 0)
            # 2、当日收盘价创 120 日新高
            # 3、近 3 日内（T-2, T-1, T）有一次涨停
            # 4、本次涨停距离上一次涨停相隔半年（120个交易日）以上
            # 5、这两次涨停的价格（收盘价）差异不超过 20%
            # ===================================================
            if total_days >= 120 and pct_chg > 0:
                # 创180日新高
                start_120_idx = max(0, total_days - 121)
                past_120_max = hist_full.iloc[start_120_idx : total_days - 1]['high'].max()
                
                if close > past_120_max:
                    s5_hit = False
                    # 近3日内包含的索引范围：[total_days - 3, total_days - 1]
                    recent_3_days_indices = range(total_days - 3, total_days)
                    for lu_idx1 in recent_3_days_indices:
                        if lu_idx1 in lu_indices:
                            # 检索该次涨停之前的历史涨停
                            prior_lus = [idx for idx in lu_indices if idx < lu_idx1]
                            if prior_lus:
                                lu_idx2 = prior_lus[-1]  # 上一次涨停
                                # 相隔120个交易日以上 (半年)
                                if (lu_idx1 - lu_idx2) >= 120:
                                    p1 = hist_full.loc[lu_idx1, 'close']
                                    p2 = hist_full.loc[lu_idx2, 'close']
                                    # 两次价格对比差异在 20% 以内
                                    if p2 > 0 and (abs(p1 - p2) / p2) <= 0.20:
                                        s5_hit = True
                                        break
                    if s5_hit:
                        strategies_hit.append(("策略5", "tag-s5"))


            if strategies_hit:
                score = row.get('total_score', 0)
                
                # 核心获取概念字符串
                concept_val = row.get('concept_str', '-')
                if pd.isna(concept_val) or not str(concept_val).strip():
                    concept_val = '-'
                else:
                    concept_val = str(concept_val).strip()
                
                market_val = row.get('market')
                if not market_val: market_val = infer_market(ts_code)
                for s_name, s_class in strategies_hit:
                    results.append({
                        'trade_date': date,
                        'ts_code': ts_code,
                        'stock_name': row['stock_name'],
                        'industry': row['industry'], 
                        'market': market_val,
                        'close': row['close'],
                        'pct_chg': round(row['pct_chg'], 2),
                        'turnover_rate': round(row['turnover_rate'], 2) if row.get('turnover_rate') else 0,
                        'volume_ratio': round(row['volume_ratio'], 2) if row.get('volume_ratio') else 0,
                        'total_mv': round(row['total_mv'] / 10000, 2) if row.get('total_mv') else 0,
                        'strategy_name': s_name,
                        'tag_class': s_class,
                        'total_score': score,
                        'concept_str': concept_val
                    })
        
        print_progress(total_stocks, total_stocks, "筛选进度")
        print("")
        results.sort(key=lambda x: x['total_score'], reverse=True)
        
        logger.info("正在注入行业涨幅与热点概念数据...")
        results = self._enrich_results(results, date)
        logger.info("正在计算历史入选统计...")
        results = self._inject_history_stats(results, date)
        return results

    def save_to_db(self, results, date):
        with self.engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {self.selection_table} WHERE trade_date = '{date}'"))
        if not results: return
        df_save = pd.DataFrame(results)
        df_save = df_save.drop_duplicates(subset=['trade_date', 'ts_code', 'strategy_name'])
        cols_map = {
            'trade_date': 'trade_date',
            'ts_code': 'ts_code',
            'stock_name': 'stock_name',
            'strategy_name': 'strategy_name', 
            'close': 'initial_price', 
            'total_score': 'total_score',
            'total_mv': 'total_mv', 
            'industry': 'industry', 
            'market': 'market'
        }
        df_db = df_save[list(cols_map.keys())].rename(columns=cols_map)
        df_db['processed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        df_db.to_sql(self.selection_table, self.engine, index=False, if_exists='append')
        logger.info(f"入库成功: {len(df_db)} 条")

    def run_backtest(self):
        logger.info("--- 启动历史回测引擎---")
        history = pd.read_sql(text(f"SELECT * FROM {self.selection_table} ORDER BY trade_date ASC"), self.engine)
        if history.empty: return [], [], [], []

        history = history[~history['ts_code'].str.startswith(('8', '4', '9'))]
        history = history[~history['stock_name'].str.contains('ST')]
        
        min_date = history['trade_date'].min()
        logger.info(f"预加载价格数据 (从 {min_date} 至今)...")
        p_sql = text(f"SELECT trade_date, ts_code, close FROM daily_data WHERE trade_date >= '{min_date}'")
        price_df = pd.read_sql(p_sql, self.engine)
        price_matrix = price_df.pivot(index='trade_date', columns='ts_code', values='close')
        all_dates = sorted(price_matrix.index.tolist())
        date_to_idx = {d: i for i, d in enumerate(all_dates)}
        
        logger.info("正在计算全量历史胜率...")
        summary_stats = {s: {'total':0, 'wins':0, 'sum_1d':0, 'dates':{}} for s in self.ALL_STRATEGIES}
        trend_map = {} 
        stock_total_counts = {} 

        total_recs = len(history)
        for i, row in history.iterrows():
            sel_date = row['trade_date']
            code = row['ts_code']
            strat = row['strategy_name']
            init_p = row['initial_price']
            
            stock_total_counts[code] = stock_total_counts.get(code, 0) + 1

            if sel_date not in date_to_idx: continue
            curr_idx = date_to_idx[sel_date]
            target_idx_1d = curr_idx + 1
            
            ret_1d = 0.0
            is_win = 0
            has_valid_data = False
            
            if target_idx_1d < len(all_dates):
                date_1d = all_dates[target_idx_1d]
                if code in price_matrix.columns:
                    p_1d = price_matrix.at[date_1d, code]
                    if p_1d is not None and not pd.isna(p_1d) and init_p > 0:
                        ret_1d = (p_1d - init_p) / init_p * 100
                        is_win = 1 if ret_1d > 0 else 0
                        has_valid_data = True

            if strat not in summary_stats: 
                summary_stats[strat] = {'total':0, 'wins':0, 'sum_1d':0.0, 'dates':{}}
            
            if has_valid_data:
                summary_stats[strat]['total'] += 1
                summary_stats[strat]['wins'] += is_win
                summary_stats[strat]['sum_1d'] += ret_1d
                
                if sel_date not in trend_map: trend_map[sel_date] = {}
                if strat not in trend_map[sel_date]: trend_map[sel_date][strat] = {'total':0, 'wins':0}
                trend_map[sel_date][strat]['total'] += 1
                trend_map[sel_date][strat]['wins'] += is_win

        summary_list = []
        for name in self.ALL_STRATEGIES + [k for k in summary_stats.keys() if k not in self.ALL_STRATEGIES]:
            if name not in summary_stats: continue
            data = summary_stats[name]
            win_rate = round(data['wins']/data['total']*100, 1) if data['total']>0 else 0
            avg_1d = round(data['sum_1d']/data['total'], 2) if data['total']>0 else 0
            summary_list.append({
                'name': name, 'count': data['total'], 'wins': data['wins'],
                'win_rate_val': win_rate, 'win_rate_str': f"{win_rate}%", 'avg_1d': avg_1d
            })

        sorted_trend_dates = sorted(trend_map.keys(), reverse=True)[:10]
        matrix_dates = sorted(sorted_trend_dates) 
        matrix_data = []
        for strat in self.ALL_STRATEGIES:
            row = {'name': strat, 'cells': []}
            for d in matrix_dates:
                if d in trend_map and strat in trend_map[d]:
                    s_data = trend_map[d][strat]
                    cnt = s_data['total']
                    val = round(s_data['wins']/cnt*100, 0) if cnt > 0 else 0
                    row['cells'].append({'has_data': True, 'str': f"{val:.0f}%", 'val': val, 'wins': s_data['wins'], 'total': cnt})
                else:
                    row['cells'].append({'has_data': False})
            matrix_data.append(row)

        logger.info("正在生成最近10日个股明细...")
        all_unique_dates = sorted(history['trade_date'].unique(), reverse=True)
        display_dates = all_unique_dates[1:11] 
        
        final_stock_list = []
        display_history = history[history['trade_date'].isin(display_dates)].copy()
        
        for i, row in display_history.iterrows():
            sel_date = row['trade_date']
            code = row['ts_code']
            strat_name = row['strategy_name']
            init_p = row['initial_price']
            
            if sel_date not in date_to_idx: continue
            start_idx = date_to_idx[sel_date]
            
            current_price = init_p
            if code in price_matrix.columns:
                last_valid = price_matrix[code].dropna()
                if not last_valid.empty:
                    current_price = last_valid.iloc[-1]
            
            def get_ret(n_days):
                target = start_idx + n_days
                if target < len(all_dates):
                    d = all_dates[target]
                    if code in price_matrix.columns:
                        p = price_matrix.at[d, code]
                        if p and not pd.isna(p):
                            return (p - init_p) / init_p * 100
                return None

            ret_1d = get_ret(1)
            ret_5d = get_ret(5)
            ret_10d = get_ret(10)
            
            is_win = (ret_1d > 0) if ret_1d is not None else False
            
            market_val = row.get('market')
            if not market_val: market_val = infer_market(code)
            
            total_cnt = stock_total_counts.get(code, 1)

            final_stock_list.append({
                'ts_code': code,
                'stock_name': row['stock_name'],
                'first_date': sel_date,      
                'market': market_val,
                'industry': row.get('industry', '-'),
                'strategies_list': [strat_name], 
                'selection_count': total_cnt,
                'total_score': row.get('total_score', 0),
                'total_mv': row.get('total_mv', 0),
                'initial_price': init_p,
                'latest_price': current_price,
                'ret_1d': f"{ret_1d:.2f}%" if ret_1d is not None else "-",
                'ret_5d': f"{ret_5d:.0f}%" if ret_5d is not None else "-",
                'ret_10d': f"{ret_10d:.0f}%" if ret_10d is not None else "-",
                'style_1d': 'win' if ret_1d and ret_1d > 0 else ('loss' if ret_1d and ret_1d < 0 else ''),
                'is_win': is_win
            })

        final_stock_list.sort(key=lambda x: x['total_score'], reverse=True)
        final_stock_list.sort(key=lambda x: x['first_date'], reverse=True)
        
        # 加上大于0的保护判断，防止清理数据库后历史为空导致的除0报错
        if len(display_history) > 0:
            print_progress(len(display_history), len(display_history), "列表生成")
        print("")
        
        return summary_list, final_stock_list, matrix_dates, matrix_data