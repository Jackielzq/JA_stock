# core/strategies.py - 策略引擎模块

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
            "策略3",  #N字反包
            "策略4",  #均线多头趋势
            "策略5"   #双涨停平台确认
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
            # 近 5 日平均换手率必须大于 3%
            # 收盘价不得高于 30 元
            # 总市值不得高于 300 亿元（total_mv 单位：万元）
            # ===================================================
            if not (ma60 > 0 and ma120 > 0 and close > ma60 and close > ma120):
                continue

            total_mv = get_val('total_mv')
            if total_mv <= 0 or total_mv > 3000000:
                continue

            hist_full = hist_df.reset_index(drop=True)
            total_days = len(hist_full)

            if total_days < 5 or close > 30:
                continue

            recent_5_turnover = pd.to_numeric(
                hist_full.tail(5)['turnover_rate'], errors='coerce'
            ).fillna(0).mean()
            if recent_5_turnover <= 3.0:
                continue
            

            # 公共辅助变量：判定涨停与获取历史涨停索引
            is_startup = ts_code.startswith(('30', '68'))
            lu_limit = 19.5 if is_startup else 9.5
            lu_indices = hist_full.index[hist_full['pct_chg'] >= lu_limit].tolist()

            # 主板股票近半年（120 个交易日）至少要出现过一次涨停
            # 创业板不应用此限制
            if not is_startup:
                half_year_start = max(0, total_days - 120)
                recent_half_year_lus = [j for j in lu_indices if j >= half_year_start]
                if not recent_half_year_lus:
                    continue

            strategies_hit = []

            pct_chg = get_val('pct_chg')
            vol_ratio = get_val('volume_ratio')
            current_vol = get_val('vol')
            turnover = get_val('turnover_rate')
            open_price = get_val('open')
            ma5 = get_val('ma_5')

            # ===================================================
            # 策略1（三连击突破）：
            # 1、连续三天放量 (T > T-1 > T-2)
            # 2、连续三天上涨 (最低价逐渐升高、最高价逐渐升高)
            # 3、当天收盘价突破半年(60日)新高
            # 4、近三天无涨停
            # 5、半年内(120日)涨停次数不超过4次
            # 6、当日换手率大于5%
            # 7、当天涨幅：主板<=7%，创业板<=12%
            # 注：股价和市值上限由全局过滤统一控制（股价<=30元、总市值<=300亿元）
            # ===================================================
            if total_days >= 120:
                limit_pct = 12.0 if is_startup else 7.0
                if turnover > 5.0 and pct_chg <= limit_pct:
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
            # 2、T-1日收阳（收盘高于 T-2 收盘），且实体完全位于 T-2 涨停价上方
            # 3、T日收阳（收盘高于 T-1 收盘），且实体完全位于 T-1 日实体上方
            # 4、T-1日成交量大于T-2日
            # 5、T日成交量不低于T-1日成交量的65%
            # 6、T-1日不能涨停
            # ===================================================
            if total_days >= 3:
                d_t0 = hist_full.iloc[-1]
                d_t1 = hist_full.iloc[-2]
                d_t2 = hist_full.iloc[-3]

                t2_limit_price = d_t2['close']
                cond_t2_limit_up = d_t2['pct_chg'] >= lu_limit
                cond_t1_bullish = d_t1['close'] > d_t2['close']
                cond_t1_body_above_t2 = min(d_t1['open'], d_t1['close']) > t2_limit_price
                cond_t0_bullish = d_t0['close'] > d_t1['close']
                cond_t0_body_above_t1 = min(d_t0['open'], d_t0['close']) > d_t1['close']
                cond_t1_volume = d_t1['vol'] > d_t2['vol']
                cond_t0_volume = d_t0['vol'] >= d_t1['vol'] * 0.65
                cond_t1_not_limit_up = d_t1['pct_chg'] < lu_limit

                if (cond_t2_limit_up and cond_t1_bullish and cond_t1_body_above_t2
                        and cond_t0_bullish and cond_t0_body_above_t1
                        and cond_t1_volume and cond_t0_volume and cond_t1_not_limit_up):
                    strategies_hit.append(("策略2", "tag-s2"))

            # ===================================================
            # 策略3（N字反包）：
            # 1、近 5 日内首日涨停
            # 2、涨停后连续 2~3 日收阴；涨停次日可放量，之后成交量逐日缩小
            # 3、第 4 或第 5 日（当日）收阳
            # 4、当日收盘价不低于首个涨停日的最低价
            # ===================================================
            if total_days >= 5:
                d_t0 = hist_full.iloc[-1]
                d_t1 = hist_full.iloc[-2]
                cond_t0_bullish = d_t0['close'] > d_t1['close']

                def is_shrinking_bearish(day, previous_day):
                    return (
                        day['close'] < previous_day['close']
                        and day['vol'] < previous_day['vol']
                    )

                def is_bearish(day, previous_day):
                    return day['close'] < previous_day['close']

                n_reversal_hit = False
                # 4 日形态：涨停 + 阴线（可放量）+ 缩量阴 + 当日收阳
                first_day_4 = hist_full.iloc[-4]
                pullback_4_1 = hist_full.iloc[-3]
                pullback_4_2 = hist_full.iloc[-2]
                if (
                    first_day_4['pct_chg'] >= lu_limit
                    and is_bearish(pullback_4_1, first_day_4)
                    and is_shrinking_bearish(pullback_4_2, pullback_4_1)
                    and cond_t0_bullish
                    and d_t0['close'] >= first_day_4['low']
                ):
                    n_reversal_hit = True

                # 5 日形态：涨停 + 阴线（可放量）+ 缩量阴 + 缩量阴 + 当日收阳
                if not n_reversal_hit:
                    first_day_5 = hist_full.iloc[-5]
                    pullback_5_1 = hist_full.iloc[-4]
                    pullback_5_2 = hist_full.iloc[-3]
                    pullback_5_3 = hist_full.iloc[-2]
                    if (
                        first_day_5['pct_chg'] >= lu_limit
                        and is_bearish(pullback_5_1, first_day_5)
                        and is_shrinking_bearish(pullback_5_2, pullback_5_1)
                        and is_shrinking_bearish(pullback_5_3, pullback_5_2)
                        and cond_t0_bullish
                        and d_t0['close'] >= first_day_5['low']
                    ):
                        n_reversal_hit = True

                if n_reversal_hit:
                    strategies_hit.append(("策略3", "tag-s3"))

            # ===================================================
            # 策略4（均线多头趋势）：
            # 1、收盘价高于5日均线
            # 2、5日均线 > 10日均线 > 20日均线
            # 3、连续5天收盘价均在各自5日均线上方
            # 4、近5个交易日未出现主板涨幅>=5%、创业板涨幅>=10%的交易日
            # 5、最近5天的最低价均高于各自前一日最低价
            # ===================================================
            if total_days >= 20:
                close_series = pd.to_numeric(hist_full['close'], errors='coerce')
                ma_5_series = close_series.rolling(window=5).mean()
                ma_10_series = close_series.rolling(window=10).mean()
                ma_20_series = close_series.rolling(window=20).mean()

                current_ma5 = ma_5_series.iloc[-1]
                current_ma10 = ma_10_series.iloc[-1]
                current_ma20 = ma_20_series.iloc[-1]
                cond_close_above_ma5 = close > current_ma5
                cond_ma_bullish = current_ma5 > current_ma10 > current_ma20

                recent_5_closes = close_series.tail(5)
                recent_5_ma5 = ma_5_series.tail(5)
                cond_five_days_above_ma5 = (
                    recent_5_ma5.notna().all()
                    and (recent_5_closes > recent_5_ma5).all()
                )

                rapid_rise_limit = 10.0 if is_startup else 5.0
                recent_5_pct = pd.to_numeric(
                    hist_full.tail(5)['pct_chg'], errors='coerce'
                ).fillna(0)
                cond_no_rapid_rise = (recent_5_pct < rapid_rise_limit).all()

                recent_6_lows = pd.to_numeric(
                    hist_full.tail(6)['low'], errors='coerce'
                )
                cond_five_days_higher_lows = (
                    recent_6_lows.notna().all()
                    and (recent_6_lows.iloc[1:].to_numpy()
                         > recent_6_lows.iloc[:-1].to_numpy()).all()
                )

                if (cond_close_above_ma5 and cond_ma_bullish
                        and cond_five_days_above_ma5 and cond_no_rapid_rise
                        and cond_five_days_higher_lows):
                    strategies_hit.append(("策略4", "tag-s4"))

            # ===================================================
            # 策略5（双涨停平台确认）：
            # 1、最近一次涨停发生在近 5 个交易日内
            # 2、最近一次涨停与上一次涨停至少间隔 3 个月（60 个交易日）
            # 3、两次涨停价的差异不大于 20%
            # 4、两次涨停之间的整体振幅不大于 50%
            # 5、当日收盘价不低于最近一次涨停日的最低价
            # ===================================================
            if len(lu_indices) >= 2:
                previous_limit_idx = lu_indices[-2]
                latest_limit_idx = lu_indices[-1]
                previous_limit = hist_full.iloc[previous_limit_idx]
                latest_limit = hist_full.iloc[latest_limit_idx]

                # 1. 最近一次涨停必须发生在近 5 个交易日内（含当日）
                cond_recent_limit = (total_days - 1 - latest_limit_idx) <= 4

                # 2. 两次涨停至少间隔 60 个交易日
                cond_interval = (latest_limit_idx - previous_limit_idx) >= 60

                # 3. 以涨停日收盘价作为涨停价，限制两次价格偏差
                previous_limit_price = previous_limit['close']
                latest_limit_price = latest_limit['close']
                price_diff_pct = (
                    abs(latest_limit_price - previous_limit_price) / previous_limit_price * 100
                    if previous_limit_price > 0 else float('inf')
                )
                cond_price_diff = price_diff_pct <= 20.0

                # 4. 统计两次涨停日（含）之间的最高价与最低价，计算整体振幅
                interval_df = hist_full.iloc[previous_limit_idx : latest_limit_idx + 1]
                interval_low = interval_df['low'].min()
                interval_high = interval_df['high'].max()
                amplitude_pct = (
                    (interval_high - interval_low) / interval_low * 100
                    if interval_low > 0 else float('inf')
                )
                cond_amplitude = amplitude_pct <= 50.0

                # 5. 当日收盘不低于最近一次涨停日的最低价
                cond_close_support = close >= latest_limit['low']

                if (cond_recent_limit and cond_interval and cond_price_diff
                        and cond_amplitude and cond_close_support):
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
        """以可执行交易口径生成回测数据。

        信号在 T 日收盘后形成，统一以 T+1 开盘买入；各持有期均在最后一日收盘卖出。
        同一股票 10 个交易日内的重复信号只保留首个，避免把同一段行情重复计为成功交易。
        """
        logger.info("--- 启动真实交易口径回测引擎---")
        # 保持空报告也能被模板安全渲染，方便数据库尚无历史信号时直接打开报告。
        empty = {
            'summary': [], 'holding_summary': [], 'trades': [],
            'assumptions': {
                'buy_cost': 0.08, 'sell_cost': 0.08, 'cooldown': 10,
                'raw_signals': 0, 'merged_signals': 0, 'duplicate_skipped': 0, 'eligible_trades': 0,
            },
            'portfolio': {
                'period': 5, 'trade_count': 0, 'nav': None, 'total_return': None, 'max_drawdown': None,
                'benchmark_nav': None, 'benchmark_return': None, 'benchmark_days': 0,
                'strategy_chart_points': '', 'benchmark_chart_points': '',
                'start_date': '-', 'end_date': '-',
            },
        }
        history = pd.read_sql(text(f"SELECT * FROM {self.selection_table} ORDER BY trade_date ASC, id ASC"), self.engine)
        if history.empty:
            return empty

        history['trade_date'] = history['trade_date'].astype(str)
        history = history[~history['ts_code'].astype(str).str.startswith(('8', '4', '9'))]
        history = history[~history['stock_name'].fillna('').str.contains('ST', case=False)]
        if history.empty:
            return empty

        min_date = history['trade_date'].min()
        logger.info(f"预加载回测行情（从 {min_date} 至今）...")
        prices = pd.read_sql(text(
            f"SELECT trade_date, ts_code, open, high, low, close FROM {DAILY_DATA_TABLE} "
            f"WHERE trade_date >= '{min_date}'"
        ), self.engine)
        if prices.empty:
            return empty
        prices['trade_date'] = prices['trade_date'].astype(str)
        all_dates = sorted(prices['trade_date'].unique().tolist())
        date_to_idx = {d: i for i, d in enumerate(all_dates)}
        matrices = {
            field: prices.pivot(index='trade_date', columns='ts_code', values=field).reindex(all_dates)
            for field in ('open', 'high', 'low', 'close')
        }

        # 同日同股多个策略信号合并为一笔候选交易，保留所有触发策略用于展示。
        grouped = history.sort_values(['trade_date', 'ts_code', 'total_score'], ascending=[True, True, False]).groupby(
            ['trade_date', 'ts_code'], as_index=False
        )
        candidates, raw_counts = [], history['strategy_name'].value_counts().to_dict()
        for (_, _), group in grouped:
            primary = group.iloc[0].to_dict()
            primary['strategies'] = list(dict.fromkeys(group['strategy_name'].dropna().tolist()))
            candidates.append(primary)

        # 10 个交易日冷却：同一股票的相邻重复信号不重复开仓。
        candidates.sort(key=lambda x: (x['trade_date'], -float(x.get('total_score') or 0)))
        eligible, last_signal_idx, skipped_duplicates = [], {}, 0
        for item in candidates:
            idx = date_to_idx.get(item['trade_date'])
            if idx is None:
                continue
            previous = last_signal_idx.get(item['ts_code'])
            if previous is not None and idx - previous < 10:
                skipped_duplicates += 1
                continue
            last_signal_idx[item['ts_code']] = idx
            item['signal_idx'] = idx
            eligible.append(item)

        buy_cost = 0.0008       # 单边：佣金、滑点等统一的保守估计
        sell_cost = 0.0008
        holding_periods = [1, 3, 5, 10, 20]

        def number(matrix, date, code):
            if code not in matrix.columns or date not in matrix.index:
                return None
            value = matrix.at[date, code]
            return float(value) if pd.notna(value) and float(value) > 0 else None

        def pct(value):
            return f"{value:+.2f}%" if value is not None else '-'

        def make_trade(item):
            signal_idx = item['signal_idx']
            entry_idx = signal_idx + 1
            if entry_idx >= len(all_dates):
                return None
            code = item['ts_code']
            entry_date = all_dates[entry_idx]
            entry_price = number(matrices['open'], entry_date, code)
            if not entry_price:
                return None
            returns, paths = {}, {}
            for days in holding_periods:
                exit_idx = entry_idx + days - 1
                if exit_idx >= len(all_dates):
                    continue
                exit_date = all_dates[exit_idx]
                exit_price = number(matrices['close'], exit_date, code)
                if not exit_price:
                    continue
                window_dates = all_dates[entry_idx:exit_idx + 1]
                lows = [number(matrices['low'], d, code) for d in window_dates]
                highs = [number(matrices['high'], d, code) for d in window_dates]
                closes = [number(matrices['close'], d, code) for d in window_dates]
                if any(v is None for v in lows + highs + closes):
                    continue  # 停牌或缺失行情不把收益当成可执行结果
                gross = (exit_price / entry_price - 1) * 100
                net = ((exit_price / entry_price) * (1 - buy_cost) * (1 - sell_cost) - 1) * 100
                mark_path = [entry_price] + closes
                running_high = np.maximum.accumulate(mark_path)
                drawdown = (np.asarray(mark_path) / running_high - 1).min() * 100
                returns[days] = {
                    'gross': gross, 'net': net, 'exit_date': exit_date, 'exit_price': exit_price,
                    'mae': (min(lows) / entry_price - 1) * 100,
                    'mfe': (max(highs) / entry_price - 1) * 100,
                    'drawdown': drawdown,
                }
                paths[days] = {'dates': window_dates, 'closes': closes}
            if not returns:
                return None
            return {
                'signal_date': item['trade_date'], 'entry_date': entry_date, 'entry_idx': entry_idx,
                'ts_code': code, 'stock_name': item.get('stock_name', '-'),
                'strategies': item['strategies'], 'industry': item.get('industry', '-') or '-',
                'market': item.get('market') or infer_market(code), 'score': float(item.get('total_score') or 0),
                'entry_price': entry_price, 'returns': returns, 'paths': paths,
            }

        trades = [trade for item in eligible if (trade := make_trade(item))]

        def metric(values):
            if not values:
                return {'count': 0, 'win_rate': None, 'avg': None, 'median': None, 'profit_factor': None, 'payoff': None}
            arr = np.asarray(values, dtype=float)
            gains, losses = arr[arr > 0], arr[arr < 0]
            profit_factor = gains.sum() / abs(losses.sum()) if len(losses) and abs(losses.sum()) > 0 else None
            payoff = gains.mean() / abs(losses.mean()) if len(gains) and len(losses) else None
            return {
                'count': len(arr), 'win_rate': (arr > 0).mean() * 100, 'avg': arr.mean(), 'median': np.median(arr),
                'profit_factor': profit_factor, 'payoff': payoff,
            }

        summary = []
        for strategy in self.ALL_STRATEGIES:
            strategy_trades = [t for t in trades if strategy in t['strategies']]
            item = {'name': strategy, 'raw_signals': int(raw_counts.get(strategy, 0)), 'executed': len(strategy_trades), 'periods': {}}
            for days in holding_periods:
                item['periods'][days] = metric([t['returns'][days]['net'] for t in strategy_trades if days in t['returns']])
            summary.append(item)

        holding_summary = []
        for days in holding_periods:
            holding_summary.append({'days': days, **metric([t['returns'][days]['net'] for t in trades if days in t['returns']])})

        # 合并组合：每笔最多使用 20% 资金，最多 5 个并行仓位；以 5 日持有期为主口径。
        portfolio_period = 5
        candidates_5d = [t for t in trades if portfolio_period in t['returns']]
        candidates_5d.sort(key=lambda t: (t['entry_idx'], -t['score']))
        active, portfolio_trades = [], []
        for trade in candidates_5d:
            active = [t for t in active if t['exit_idx'] >= trade['entry_idx']]
            if len(active) >= 5:
                continue
            trade['exit_idx'] = date_to_idx[trade['returns'][portfolio_period]['exit_date']]
            active.append(trade)
            portfolio_trades.append(trade)

        portfolio_returns = {}
        for trade in portfolio_trades:
            path = trade['paths'][portfolio_period]
            for position, date in enumerate(path['dates']):
                close = path['closes'][position]
                if position == 0:
                    day_return = (close / trade['entry_price']) * (1 - buy_cost) - 1
                else:
                    day_return = close / path['closes'][position - 1] - 1
                if position == len(path['dates']) - 1:
                    day_return = (1 + day_return) * (1 - sell_cost) - 1
                portfolio_returns[date] = portfolio_returns.get(date, 0) + day_return / 5
        nav, peak, max_dd, nav_points = 1.0, 1.0, 0.0, []
        for date in sorted(portfolio_returns):
            nav *= 1 + portfolio_returns[date]
            peak = max(peak, nav)
            max_dd = min(max_dd, nav / peak - 1)
            nav_points.append({'date': date, 'nav': nav, 'return': portfolio_returns[date] * 100})

        # 基准使用沪深300。首次回测时补齐所需历史，之后直接复用 index_daily 缓存。
        benchmark = {}
        try:
            portfolio_start = nav_points[0]['date'] if nav_points else None
            portfolio_end = nav_points[-1]['date'] if nav_points else None
            index_df = pd.read_sql(text(
                "SELECT trade_date, close FROM index_daily WHERE ts_code = '000300.SH' "
                f"AND trade_date >= '{portfolio_start}' AND trade_date <= '{portfolio_end}' ORDER BY trade_date"
            ), self.engine) if portfolio_start else pd.DataFrame()

            # 当前 index_daily 过去只按日写入，历史报告第一次生成时需补齐基准区间。
            if portfolio_start and len(index_df) < max(2, len(nav_points) * 0.8):
                import tushare as ts
                from config import TUSHARE_TOKEN
                df_benchmark = ts.pro_api(TUSHARE_TOKEN).index_daily(
                    ts_code='000300.SH', start_date=portfolio_start, end_date=portfolio_end,
                    fields='ts_code,trade_date,open,close,change,pct_chg'
                )
                if df_benchmark is not None and not df_benchmark.empty:
                    df_benchmark['index_name'] = '沪深300'
                    df_benchmark['processed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    records = df_benchmark[['trade_date', 'ts_code', 'index_name', 'open', 'close', 'change', 'pct_chg', 'processed_time']].to_dict('records')
                    upsert = text("""
                        INSERT INTO index_daily (trade_date, ts_code, index_name, open, close, `change`, pct_chg, processed_time)
                        VALUES (:trade_date, :ts_code, :index_name, :open, :close, :change, :pct_chg, :processed_time)
                        ON DUPLICATE KEY UPDATE open=VALUES(open), close=VALUES(close), `change`=VALUES(`change`),
                            pct_chg=VALUES(pct_chg), processed_time=VALUES(processed_time)
                    """)
                    with self.engine.begin() as conn:
                        conn.execute(upsert, records)
                    index_df = pd.read_sql(text(
                        "SELECT trade_date, close FROM index_daily WHERE ts_code = '000300.SH' "
                        f"AND trade_date >= '{portfolio_start}' AND trade_date <= '{portfolio_end}' ORDER BY trade_date"
                    ), self.engine)
                    logger.info(f"沪深300基准历史已补齐：{len(records)} 个交易日")
            if not index_df.empty:
                index_df['trade_date'] = index_df['trade_date'].astype(str)
                index_df = index_df.set_index('trade_date')['close'].astype(float)
                benchmark = index_df.to_dict()
        except Exception as exc:
            logger.warning(f"读取沪深300基准失败: {exc}")
        common_points = [point for point in nav_points if point['date'] in benchmark]
        benchmark_points = []
        if common_points:
            benchmark_start = benchmark[common_points[0]['date']]
            benchmark_points = [
                {'date': point['date'], 'nav': benchmark[point['date']] / benchmark_start}
                for point in common_points
            ]
        benchmark_nav = benchmark_points[-1]['nav'] if benchmark_points else None

        # 图表坐标在后端生成，报告无需依赖外部 JavaScript。
        chart_series = common_points[-180:]
        if len(chart_series) >= 2:
            benchmark_map = {p['date']: p['nav'] for p in benchmark_points}
            values = [p['nav'] for p in chart_series] + [benchmark_map[p['date']] for p in chart_series]
            lo, hi = min(values), max(values)
            span = hi - lo or 0.01
            strategy_points = ' '.join(f"{i / (len(chart_series) - 1) * 100:.2f},{100 - (p['nav'] - lo) / span * 100:.2f}" for i, p in enumerate(chart_series))
            benchmark_chart_points = ' '.join(f"{i / (len(chart_series) - 1) * 100:.2f},{100 - (benchmark_map[p['date']] - lo) / span * 100:.2f}" for i, p in enumerate(chart_series))
        else:
            strategy_points, benchmark_chart_points = '', ''

        rendered_trades = []
        for trade in sorted(trades, key=lambda t: (t['signal_date'], t['score']), reverse=True)[:100]:
            r5 = trade['returns'].get(5)
            rendered_trades.append({
                **trade,
                'strategies_text': '、'.join(trade['strategies']),
                'entry_price_str': f"{trade['entry_price']:.2f}",
                'ret_1': pct(trade['returns'].get(1, {}).get('net')),
                'ret_3': pct(trade['returns'].get(3, {}).get('net')),
                'ret_5': pct(r5.get('net')) if r5 else '-',
                'ret_10': pct(trade['returns'].get(10, {}).get('net')),
                'ret_20': pct(trade['returns'].get(20, {}).get('net')),
                'exit_date': r5['exit_date'] if r5 else '-',
                'mae': pct(r5['mae']) if r5 else '-', 'mfe': pct(r5['mfe']) if r5 else '-',
                'drawdown': pct(r5['drawdown']) if r5 else '-',
                'is_win': bool(r5 and r5['net'] > 0),
            })

        return {
            'summary': summary, 'holding_summary': holding_summary, 'trades': rendered_trades,
            'assumptions': {
                'buy_cost': buy_cost * 100, 'sell_cost': sell_cost * 100, 'cooldown': 10,
                'raw_signals': len(history), 'merged_signals': len(candidates), 'duplicate_skipped': skipped_duplicates,
                'eligible_trades': len(trades),
            },
            'portfolio': {
                'period': portfolio_period, 'trade_count': len(portfolio_trades), 'nav': nav,
                'total_return': (nav - 1) * 100 if nav_points else None, 'max_drawdown': max_dd * 100 if nav_points else None,
                'benchmark_nav': benchmark_nav,
                'benchmark_return': (benchmark_nav - 1) * 100 if benchmark_nav is not None else None,
                'benchmark_days': len(benchmark_points),
                'strategy_chart_points': strategy_points, 'benchmark_chart_points': benchmark_chart_points,
                'start_date': nav_points[0]['date'] if nav_points else '-', 'end_date': nav_points[-1]['date'] if nav_points else '-',
            },
        }
