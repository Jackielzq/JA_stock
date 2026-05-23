# core/factor_calculator.py - 终极完整版 v11.1 (新增评分写库与概念拼接引擎)
import pandas as pd
import numpy as np
from sqlalchemy import text
from datetime import datetime, timedelta
from utils.market_utils import infer_market
import logging
import tushare as ts
from sqlalchemy.types import String, Float
from config import TUSHARE_TOKEN

class FactorCalculator:
    def __init__(self, db_engine):
        self.db = db_engine
        self._stock_basic_cache = None
        self._init_daily_data_columns() # 自动检查并添加新字段

    def _init_daily_data_columns(self):
        """初始化 daily_data 表，确保包含评分和概念拼接字段"""
        try:
            with self.db.get_engine().begin() as conn:
                try: conn.execute(text("ALTER TABLE daily_data ADD COLUMN total_score FLOAT DEFAULT 0.0"))
                except: pass
                try: conn.execute(text("ALTER TABLE daily_data ADD COLUMN concept_str VARCHAR(255) DEFAULT '-'"))
                except: pass
        except Exception as e:
            logging.error(f"检查 daily_data 字段失败: {e}")

    def _get_data(self, sql, conn=None):
        try:
            if conn:
                return pd.read_sql(sql, conn)
            with self.db.get_engine().connect() as conn:
                 return pd.read_sql(sql, conn)
        except Exception as e:
            logging.error(f"SQL执行错误: {e}")
            return pd.DataFrame()

    def update_daily_factors(self, date):
        """
        [全局通用因子引擎] 计算个股总分和监控概念拼接，并写入 daily_data 表。
        强烈建议：在每天下载完基础日线数据后，立刻调用此方法。
        """
        logging.info(f"正在计算全局基础因子并落库 (日期: {date})...")
        
        # 1. 取出当日所有股票基础数据
        sql = text(f"""
            SELECT ts_code, close, total_mv, turnover_rate, ma_5, ma_10, ma_20 
            FROM daily_data WHERE trade_date = '{date}'
        """)
        df = self._get_data(sql)
        if df.empty:
            logging.warning(f"{date} 无日线数据，跳过因子计算。")
            return

        # 2. 获取监控概念池 (两步走，避免字符集冲突)
        concept_map = {}
        try:
            sql_pool = text("SELECT concept_name FROM monitor_pool_concept")
            with self.db.get_engine().connect() as conn:
                monitor_concepts = [str(r[0]).strip() for r in conn.execute(sql_pool).fetchall() if r[0]]
            
            if monitor_concepts:
                c_list_str = "'" + "','".join([c.replace("'", "\\'") for c in monitor_concepts]) + "'"
                sql_rel = text(f"SELECT ts_code, concept_name FROM concept_detail WHERE concept_name IN ({c_list_str})")
                df_rel = self._get_data(sql_rel)
                if not df_rel.empty:
                    df_rel['concept_name'] = df_rel['concept_name'].astype(str).str.strip()
                    # 聚合成字典：ts_code -> [概念1, 概念2...]
                    concept_map = df_rel.groupby('ts_code')['concept_name'].apply(list).to_dict()
        except Exception as e:
            logging.error(f"加载监控概念用于打分时失败: {e}")

        # 3. 计算评分与拼接概念
        updates = []
        for _, row in df.iterrows():
            code = row['ts_code']
            c = row['close']
            if pd.isna(c) or c == 0: continue
            
            score = 0.0
            my_concepts = concept_map.get(code, [])
            concept_str_val = " + ".join(my_concepts) if my_concepts else "-"

            # --- 规则1：监控概念命中 (每命中一个+1分) ---
            score += float(len(my_concepts) * 1.0)

            # --- 规则2：市值偏好 ---
            mv = row['total_mv']
            if pd.notnull(mv):
                mv_100m = mv / 10000 
                if mv_100m < 50: score += 1.0
                elif 50 <= mv_100m < 100: score += 0.9
                elif 100 <= mv_100m < 200: score += 0.8
                else: score += 0.5

            # --- 规则3：均线多头打分 ---
            m5 = row['ma_5']
            m10 = row['ma_10']
            m20 = row['ma_20']
            if pd.notnull(m5) and pd.notnull(m10) and pd.notnull(m20):
                if c > m5 and m5 > m10 and m10 > m20:
                    score += 1.0
                elif c > m5 and c < m10:
                    score += 0.6
                elif c > m5 and c < m20:
                    score += 0.4

            # --- 规则4：换手率偏好 ---
            to = row['turnover_rate']
            if pd.notnull(to):
                if 5 < to <= 20: score += 1.0
                elif to > 20: score += 0.6
                else: score += 0.3

            # --- 封顶 10 分 ---
            final_score = round(min(score, 10.0), 1)

            updates.append({
                'b_score': final_score,
                'b_cstr': concept_str_val,
                'b_date': date,
                'b_code': code
            })


        # 4. 极速方案：借助临时表连表更新 (强制类型对齐版)
        if updates:
            df_updates = pd.DataFrame(updates)
            temp_table = 'temp_update_factors'
            try:
                with self.db.get_engine().begin() as conn:
                    # 1. 使用严格的数据类型，防止 MySQL 将字符串误认为 TEXT 导致全表扫描
                    dtype_mapping = {
                        'b_score': Float(),
                        'b_cstr': String(255),
                        'b_date': String(20),
                        'b_code': String(20)
                    }
                    df_updates.to_sql(temp_table, conn, index=False, if_exists='replace', dtype=dtype_mapping)
                    
                    # 2. 为临时表添加主键索引，确保匹配速度达到毫秒级
                    conn.execute(text(f"ALTER TABLE {temp_table} ADD PRIMARY KEY (b_date, b_code)"))
                    
                    # 3. 连表更新 (现在两边都是 VARCHAR 且有索引，瞬间完成)
                    update_sql = text(f"""
                        UPDATE daily_data d
                        INNER JOIN {temp_table} t 
                        ON d.trade_date = t.b_date AND d.ts_code = t.b_code
                        SET d.total_score = t.b_score, d.concept_str = t.b_cstr
                    """)
                    conn.execute(update_sql)
                    
                    # 4. 阅后即焚
                    conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))
                    
                logging.info(f"极速更新完成！成功将 {len(updates)} 只股票的评分与概念回写至 daily_data 库。")
            except Exception as e:
                logging.error(f"极速批量更新分数失败: {e}")


    # ==========================
    # 下方保留你原有的完整核心功能代码
    # ==========================
    def _get_valid_stock_pool(self, current_date, min_days=90):
        try:
            if self._stock_basic_cache is None:
                pro = ts.pro_api(TUSHARE_TOKEN)
                self._stock_basic_cache = pro.stock_basic(exchange='', list_status='L', fields='ts_code,list_date,industry')
            fmt_date = current_date.replace('-', '')
            cutoff_date = (datetime.strptime(fmt_date, '%Y%m%d') - timedelta(days=min_days)).strftime('%Y%m%d')
            valid_df = self._stock_basic_cache[self._stock_basic_cache['list_date'] <= cutoff_date]
            return set(valid_df['ts_code'].tolist())
        except Exception as e:
            logging.error(f"获取上市日期失败: {e}")
            return None

    def _try_get_latest_date(self, table_name, target_date, date_col='trade_date'):
        target_str = target_date.replace('-', '')
        target_dash = f"{target_str[:4]}-{target_str[4:6]}-{target_str[6:]}"
        for d in [target_str, target_dash]:
            try:
                sql = text(f"SELECT COUNT(*) FROM {table_name} WHERE {date_col} = '{d}'")
                with self.db.get_engine().connect() as conn:
                    count = conn.execute(sql).scalar()
                if count > 0: return d
            except: pass
        try:
            sql_max = text(f"SELECT MAX({date_col}) FROM {table_name} WHERE {date_col} <= '{target_str}'")
            with self.db.get_engine().connect() as conn:
                max_d = conn.execute(sql_max).scalar()
            if max_d: return max_d
        except: pass
        return target_date

    def _enrich_context_info(self, stock_list, date):
        if not stock_list: return []
        logging.info(f"=== 正在进行数据增强 (目标日期: {date}) ===")
        ind_date = self._try_get_latest_date('industry_daily', date)
        ind_map = {}
        sql_ind = text(f"SELECT industry_name, avg_pct FROM industry_daily WHERE trade_date = '{ind_date}'")
        df_ind = self._get_data(sql_ind)
        if not df_ind.empty:
            df_ind['industry_name'] = df_ind['industry_name'].astype(str).str.strip()
            ind_map = df_ind.set_index('industry_name')['avg_pct'].to_dict()

        codes = [s['ts_code'] for s in stock_list]
        codes_str = "'" + "','".join(codes) + "'"
        sql_rel = text(f"SELECT DISTINCT ts_code, concept_name FROM concept_detail WHERE ts_code IN ({codes_str})")
        df_rel = self._get_data(sql_rel)
        
        cp_map = {}
        if not df_rel.empty:
            df_rel['concept_name'] = df_rel['concept_name'].astype(str).str.strip()
            relevant_concepts = df_rel['concept_name'].unique().tolist()
            cp_date = self._try_get_latest_date('concept_daily', date)
            concepts_safe = [c.replace("'", "") for c in relevant_concepts]
            if concepts_safe:
                c_list_str = "'" + "','".join(concepts_safe) + "'"
                sql_cp = text(f"SELECT concept_name, avg_pct FROM concept_daily WHERE trade_date = '{cp_date}' AND concept_name IN ({c_list_str})")
                df_cp = self._get_data(sql_cp)
                if not df_cp.empty:
                    df_cp['concept_name'] = df_cp['concept_name'].astype(str).str.strip()
                    cp_map = df_cp.set_index('concept_name')['avg_pct'].to_dict()

        if self._stock_basic_cache is None:
            try:
                pro = ts.pro_api(TUSHARE_TOKEN)
                self._stock_basic_cache = pro.stock_basic(exchange='', list_status='L', fields='ts_code,industry')
            except: pass

        for stock in stock_list:
            code = stock['ts_code']
            ind_name = stock.get('industry')
            if not ind_name or ind_name == '-':
                if self._stock_basic_cache is not None:
                    row = self._stock_basic_cache[self._stock_basic_cache['ts_code'] == code]
                    if not row.empty:
                        ind_name = row.iloc[0]['industry']
                        stock['industry'] = ind_name 
            if ind_name:
                ind_clean = str(ind_name).strip()
                if ind_clean in ind_map:
                    pct = ind_map[ind_clean]
                    sign = '+' if pct > 0 else ''
                    stock['industry'] = f"{ind_clean}({sign}{pct:.1f}%)"
            
            stock['concepts_str'] = "-"
            if not df_rel.empty:
                my_concepts = df_rel[df_rel['ts_code'] == code]['concept_name'].tolist()
                scored = []
                for c in my_concepts:
                    if c in cp_map: scored.append((c, cp_map[c]))
                if scored:
                    scored.sort(key=lambda x: x[1], reverse=True)
                    top_3 = scored[:3]
                    html_parts = []
                    for name, pct in top_3:
                        sign = '+' if pct > 0 else ''
                        css = "concept-hot" if pct >= 3.0 else ""
                        part = f"<span class='concept-item {css}'>{name}({sign}{pct:.1f}%)</span>"
                        html_parts.append(part)
                    stock['concepts_str'] = "".join(html_parts)
        return stock_list

    def calculate_strict_streaks(self, date):
        logging.info(f"正在计算 {date} 的连板高度...")
        # 【修改】加上提取 total_score 和 concept_str
        sql = text(f"SELECT ts_code, stock_name, pct_chg, close, industry, turnover_rate, total_mv, area, market, vol, amount, total_score, concept_str FROM daily_data WHERE trade_date = '{date}'")
        df_today = self._get_data(sql)
        if df_today.empty: return {}, pd.DataFrame()

        df_today = df_today[~df_today['ts_code'].str.startswith(('688', '8', '4', '9')) & ~df_today['stock_name'].str.contains('ST')]
        valid_pool = self._get_valid_stock_pool(date)
        if valid_pool: df_today = df_today[df_today['ts_code'].isin(valid_pool)]

        potential_limit_ups = df_today[df_today['pct_chg'] >= 9.0]
        if potential_limit_ups.empty:
            df_today['real_streak'] = 0
            df_today['market'] = df_today.apply(lambda x: x['market'] if x['market'] else infer_market(x['ts_code']), axis=1)
            return {}, df_today

        codes_str = str(tuple(potential_limit_ups['ts_code'].tolist())) 
        if len(potential_limit_ups) == 1: codes_str = f"('{potential_limit_ups['ts_code'].iloc[0]}')"
        start_check = (datetime.strptime(date, '%Y%m%d') - timedelta(days=40)).strftime('%Y%m%d')
        hist_sql = text(f"SELECT ts_code, trade_date, pct_chg FROM daily_data WHERE trade_date >= '{start_check}' AND trade_date <= '{date}' AND ts_code IN {codes_str} ORDER BY trade_date DESC")
        df_hist = self._get_data(hist_sql)

        streak_map = {}
        for _, row in potential_limit_ups.iterrows():
            code = row['ts_code']
            hist = df_hist[df_hist['ts_code'] == code]
            limit_threshold = 19.5 if code.startswith(('300', '301')) else 9.5
            streak = 0
            for _, r in hist.iterrows():
                if r['pct_chg'] >= limit_threshold: streak += 1
                else: break
            if streak > 0: streak_map[code] = streak

        df_today['real_streak'] = df_today['ts_code'].map(streak_map).fillna(0).astype(int)
        df_today['market'] = df_today.apply(lambda x: x['market'] if x['market'] else infer_market(x['ts_code']), axis=1)
        return streak_map, df_today

    def calculate_sentiment_score(self, sh_pct, total_amount, up_count, total_count, limit_up_count, high_board, promo_rate=0, vol_change_pct=0, limit_down_count=0):
        s_vol = 10 if total_amount > 30000 else (8 if total_amount >= 25000 else (7 if total_amount >= 20000 else (4 if total_amount >= 15000 else (2 if total_amount >= 10000 else 0))))
        s_idx = 10 if sh_pct > 3.0 else (8 if sh_pct >= 2.0 else (6 if sh_pct >= 1.0 else (5 if sh_pct >= 0.0 else (0 if sh_pct >= -1.0 else (-1 if sh_pct >= -2.0 else -5)))))
        ratio = (up_count / total_count * 100) if total_count > 0 else 0
        s_up = 10 if ratio >= 90 else (8 if ratio >= 75 else (5 if ratio >= 50 else (2.5 if ratio >= 25 else 0)))
        s_limit = 10 if limit_up_count >= 120 else (7.5 if limit_up_count >= 80 else (5 if limit_up_count >= 50 else (2.5 if limit_up_count >= 20 else 0)))
        s_high = 10 if high_board >= 10 else (8 if high_board >= 8 else (6 if high_board >= 5 else (2 if high_board >= 3 else 0)))
        final_score = s_vol * 6 + s_idx * 1 + s_up * 0.5 + s_limit * 1 + s_high * 1.5
        return {'total': int(max(0, min(100, final_score)))}

    def get_trend_history(self, current_date, pro_api=None):
        logging.info("🚀 正在计算历史连板趋势 (扩展为30日)...")
        dates = self.db.get_recent_trading_days(current_date, 60) 
        if len(dates) < 30: return []
        dates.sort()
        start_date, end_date = dates[0], dates[-1]
        target_dates = dates[-30:]
        
        sql = text(f"SELECT ts_code, trade_date, pct_chg, close, amount, stock_name FROM daily_data WHERE trade_date >= '{start_date}' AND trade_date <= '{end_date}'")
        df_all = self._get_data(sql)
        if df_all.empty: return []

        df_clean = df_all[~df_all['ts_code'].str.startswith(('688', '8', '4', '9')) & ~df_all['stock_name'].str.contains('ST')].copy()
        df_clean = df_clean.sort_values(['ts_code', 'trade_date'])
        
        is_startup = df_clean['ts_code'].str.startswith('30')
        limit_threshold = np.where(is_startup, 19.5, 9.5)
        df_clean['is_limit'] = (df_clean['pct_chg'] >= limit_threshold).astype(int)
        df_clean['grp'] = (df_clean['is_limit'] == 0).groupby(df_clean['ts_code']).cumsum()
        df_clean['streak'] = df_clean.groupby(['ts_code', 'grp'])['is_limit'].cumsum()
        
        index_map = {}
        try:
            if pro_api is None: pro_api = ts.pro_api(TUSHARE_TOKEN)
            df_index = pro_api.index_daily(ts_code='000001.SH', start_date=start_date, end_date=end_date)
            for _, row in df_index.iterrows():
                index_map[row['trade_date']] = {'open': row['open'], 'close': row['close'], 'high': row['high'], 'low': row['low'], 'pct_chg': row['pct_chg']}
        except: pass

        history = []
        prev_amount, prev_limit_ups = 0, set()
        
        for date in dates:
            if date not in target_dates and date < target_dates[0]:
                day_raw = df_all[df_all['trade_date'] == date]
                prev_amount = (day_raw['amount'].sum() if not day_raw.empty else 0) / 100000
                # prev_limit_ups = set(df_clean[df_clean['trade_date'] == date][df_clean['is_limit'] == 1]['ts_code'])
                prev_limit_ups = set(df_clean[(df_clean['trade_date'] == date) & (df_clean['is_limit'] == 1)]['ts_code'])
                continue
                
            day_data = df_clean[df_clean['trade_date'] == date]
            day_all_raw = df_all[df_all['trade_date'] == date]
            if day_all_raw.empty: continue
            
            amount_yi = day_all_raw['amount'].sum() / 100000
            limit_up_stocks = day_data[day_data['is_limit'] == 1]
            curr_limit_ups = set(limit_up_stocks['ts_code'])
            
            up_count = len(day_all_raw[day_all_raw['pct_chg'] > 0])
            down_count = len(day_all_raw) - up_count
            limit_up_count = len(limit_up_stocks)
            limit_down_count = len(day_data[day_data['pct_chg'] < -9.0])
            height = limit_up_stocks['streak'].max() if not limit_up_stocks.empty else 1
            if pd.isna(height): height = 1
            
            promo_rate = ((len(curr_limit_ups & prev_limit_ups) / len(prev_limit_ups)) * 100) if prev_limit_ups else 0
            vol_pct = ((amount_yi - prev_amount) / prev_amount * 100) if prev_amount > 0 else 0
                
            idx_data = index_map.get(date, {})
            score_res = self.calculate_sentiment_score(idx_data.get('pct_chg', 0), amount_yi, up_count, len(day_all_raw), limit_up_count, height, promo_rate, vol_pct, limit_down_count)
            
            history.append({
                'date': date, 'date_str': f"{date[4:6]}-{date[6:]}", 'height': int(height), 'score': int(score_res['total']),
                'sh_open': idx_data.get('open', 0), 'sh_close': idx_data.get('close', 0), 'sh_high': idx_data.get('high', 0),
                'sh_low': idx_data.get('low', 0), 'sh_pct': idx_data.get('pct_chg', 0), 'amount': amount_yi,
                'vol_pct': round(vol_pct, 2), 'limit_up': limit_up_count, 'limit_down': limit_down_count,
                'up_count': up_count, 'down_count': down_count, 'ad_ratio': round(up_count/down_count, 2) if down_count > 0 else 99,
                'promo_rate': round(promo_rate, 1)
            })
            prev_amount, prev_limit_ups = amount_yi, curr_limit_ups
            
        history.sort(key=lambda x: x['date'], reverse=True)
        return history

    def get_limit_down_stocks(self, date):
        start_date = (datetime.strptime(date, '%Y%m%d') - timedelta(days=25)).strftime('%Y%m%d')
        sql = text(f"SELECT ts_code, trade_date, close, pct_chg, stock_name, industry, total_mv, market FROM daily_data WHERE trade_date >= '{start_date}' AND trade_date <= '{date}'")
        df = self._get_data(sql)
        if df.empty: return []
            
        candidates = df[(df['trade_date'] == date) & (df['pct_chg'] <= -9.5)]
        res = []
        for _, curr in candidates.iterrows():
            code = curr['ts_code']
            if code.startswith(('688', '8', '4', '9')) or 'ST' in curr['stock_name']: continue
            hist = df[df['ts_code'] == code].sort_values('trade_date')
            tail_5, tail_10 = hist.tail(5), hist.tail(10)
            res.append({
                'ts_code': code, 'stock_name': curr['stock_name'], 'industry': curr['industry'],
                'market': curr['market'] if curr['market'] else infer_market(code),
                'mv': round(curr['total_mv'] / 10000, 2) if curr['total_mv'] else 0,
                'close': curr['close'], 'pct_chg': round(curr['pct_chg'], 2),
                'pct_5d': round((np.prod(1 + tail_5['pct_chg']/100) - 1) * 100, 2) if len(tail_5) > 0 else 0,
                'pct_10d': round((np.prod(1 + tail_10['pct_chg']/100) - 1) * 100, 2) if len(tail_10) > 0 else 0,
                'up_streak': 0 
            })
        return sorted(res, key=lambda x: x['mv'], reverse=True)[:15]

    def get_sector_data(self, date, ascending=False):
        ind_date = self._try_get_latest_date('industry_daily', date)
        sql = text(f"SELECT industry_name as industry, avg_pct, pct_5d, pct_10d FROM industry_daily WHERE trade_date = '{ind_date}'")
        df = self._get_data(sql)
        if df.empty: return []

        df_today = self._get_data(text(f"SELECT industry, pct_chg FROM daily_data WHERE trade_date = '{date}' AND industry IS NOT NULL"))
        limit_counts = {}
        if not df_today.empty:
            limit_counts = df_today[df_today['pct_chg'] >= 9.0].groupby('industry').size() if not ascending else df_today[df_today['pct_chg'] <= -9.0].groupby('industry').size()

        res = [{'industry': r['industry'], 'pct_1d': round(r['avg_pct'], 2), 'pct_5d': round(r['pct_5d'], 2) if pd.notnull(r['pct_5d']) else 0, 'pct_10d': round(r['pct_10d'], 2) if pd.notnull(r['pct_10d']) else 0, 'limit_count': int(limit_counts.get(r['industry'], 0)), 'sort_val': r['avg_pct']} for _, r in df.iterrows()]
        return sorted(res, key=lambda x: x['sort_val'], reverse=not ascending)[:10]

    def get_concept_data(self):
        logging.info("正在获取概念板块数据...")
        cp_date = self._try_get_latest_date('concept_daily', datetime.now().strftime('%Y%m%d'))
        df_concepts = self._get_data(text(f"SELECT * FROM concept_daily WHERE trade_date = '{cp_date}'"))
        if df_concepts.empty: return [], []

        df_top, df_btm = df_concepts.sort_values('avg_pct', ascending=False).head(10), df_concepts.sort_values('avg_pct', ascending=True).head(10)

        def get_limit_counts(concept_names, is_limit_up=True):
            if not concept_names: return {}
            names_str = "'" + "','".join([c.replace("'", "") for c in concept_names]) + "'"
            op, val = (">=", "9.5") if is_limit_up else ("<=", "-9.5")
            df_cnt = self._get_data(text(f"SELECT d.concept_name, COUNT(*) as cnt FROM daily_data s JOIN concept_detail d ON s.ts_code = d.ts_code WHERE s.trade_date = '{cp_date}' AND d.concept_name IN ({names_str}) AND s.pct_chg {op} {val} GROUP BY d.concept_name"))
            if df_cnt.empty: return {}
            df_cnt['concept_name'] = df_cnt['concept_name'].astype(str).str.strip()
            return df_cnt.set_index('concept_name')['cnt'].to_dict()

        def attach_leaders(df_target, is_top=True):
            concepts = df_target['concept_name'].tolist()
            if not concepts: return []
            limit_map = get_limit_counts(concepts, is_limit_up=is_top)
            concepts_str = "'" + "','".join([c.replace("'", "") for c in concepts]) + "'"
            df_stocks = self._get_data(text(f"SELECT d.concept_name, s.stock_name, s.pct_chg FROM daily_data s JOIN concept_detail d ON s.ts_code = d.ts_code WHERE s.trade_date = '{cp_date}' AND d.concept_name IN ({concepts_str}) AND s.ts_code NOT LIKE '8%%' AND s.ts_code NOT LIKE '4%%' AND s.stock_name NOT LIKE '%%ST%%'"))
            df_leaders = df_stocks.sort_values('pct_chg', ascending=not is_top).drop_duplicates('concept_name')
            merged = pd.merge(df_target, df_leaders, on='concept_name', how='left')
            res = []
            for _, row in merged.iterrows():
                c_name = str(row['concept_name']).strip()
                pct_5, pct_10 = row.get('pct_5d', 0), row.get('pct_10d', 0)
                res.append({
                    'name': c_name, 'pct_chg': round(row['avg_pct'], 2),
                    'pct_5d': f"{pct_5:.2f}" if pd.notnull(pct_5) else "-", 'pct_5d_val': pct_5 if pd.notnull(pct_5) else 0,
                    'pct_10d': f"{pct_10:.2f}" if pd.notnull(pct_10) else "-", 'pct_10d_val': pct_10 if pd.notnull(pct_10) else 0,
                    'leader_name': row['stock_name'] if pd.notnull(row['stock_name']) else "-",
                    'leader_pct': round(row['pct_chg'], 2) if pd.notnull(row['pct_chg']) else 0,
                    'limit_count': int(limit_map.get(c_name, 0))
                })
            return res
        return attach_leaders(df_top, is_top=True), attach_leaders(df_btm, is_top=False)

    def get_strong_stocks(self, date, streak_map):
        logging.info("正在筛选核心强势股 (Matrix)...")
        start_date = (datetime.strptime(date, '%Y%m%d') - timedelta(days=25)).strftime('%Y%m%d')
        df = self._get_data(text(f"SELECT ts_code, trade_date, pct_chg, close, stock_name, industry, turnover_rate, total_mv, area, market FROM daily_data WHERE trade_date >= '{start_date}' AND trade_date <= '{date}'"))
        if df.empty: return []
        
        df = df[~df['ts_code'].str.startswith(('688', '8', '4', '9')) & ~df['stock_name'].str.contains('ST')].drop_duplicates(subset=['trade_date', 'ts_code'], keep='last')
        pivot_pct = df.pivot(index='trade_date', columns='ts_code', values='pct_chg').fillna(0)
        
        total_pct_series = (1 + pivot_pct.iloc[-10:] / 100).prod() - 1
        pct_5d_series = (1 + pivot_pct.iloc[-5:] / 100).prod() - 1
        mask = (total_pct_series > 0.30) | ((pivot_pct.iloc[-10:] > 9.5).sum() > 3)
        target_codes = mask[mask].index.tolist()
        
        latest_df = df[df['trade_date'] == date].set_index('ts_code')
        res = []
        for code in target_codes:
            if code not in latest_df.index: continue
            curr = latest_df.loc[code]
            total_pct = total_pct_series[code] * 100
            res.append({
                'ts_code': code, 'stock_name': curr['stock_name'], 'industry': curr['industry'],
                'market': curr['market'] if curr['market'] else infer_market(code), 'area': curr['area'],
                'mv': round(curr['total_mv'] / 10000, 2) if curr['total_mv'] else 0,
                'close': curr['close'], 'pct_chg': round(curr['pct_chg'], 2), 'turnover_rate': round(curr['turnover_rate'], 2),
                'volume_ratio': 1.0, 'total_score': int(total_pct), 'pct_5d': round(pct_5d_series[code] * 100, 2),
                'total_pct': round(total_pct, 2), 'sort_val': total_pct, 'up_streak': streak_map.get(code, 0)
            })
        return self._enrich_context_info(sorted(res, key=lambda x: x['sort_val'], reverse=True)[:15], date)

    def get_active_stocks(self, date):
        logging.info("正在筛选近期活跃异动股...")
        real_start_date = self._get_data(text(f"SELECT DISTINCT trade_date FROM daily_data WHERE trade_date <= '{date}' ORDER BY trade_date DESC LIMIT 3"))['trade_date'].iloc[-1]
        df = self._get_data(text(f"SELECT ts_code, trade_date, stock_name, close, open, high, low, pre_close, pct_chg, turnover_rate, amount, industry, market FROM daily_data WHERE trade_date >= '{real_start_date}' AND trade_date <= '{date}'"))
        if df.empty: return []
        
        df = df[~df['ts_code'].str.startswith(('688', '8', '4', '9')) & ~df['stock_name'].str.contains('ST')]
        df['amplitude'] = (df['high'] - df['low']) / df['pre_close'] * 100
        stats = df.groupby('ts_code').agg({'stock_name': 'first', 'industry': 'first', 'market': 'first', 'close': 'last', 'turnover_rate': 'mean', 'amplitude': 'mean', 'amount': 'mean', 'pct_chg': 'sum'}).reset_index()
        
        active_df = stats[(stats['turnover_rate'] >= 7.0) & (stats['amplitude'] >= 4.0) & (stats['pct_chg'] >= 3.0)].copy()
        active_df['score'] = active_df['turnover_rate'] * 0.6 + active_df['amplitude'] * 0.4
        active_df = active_df.sort_values('score', ascending=False).head(15)
        
        res = []
        for _, row in active_df.iterrows():
            res.append({
                'ts_code': row['ts_code'], 'stock_name': row['stock_name'], 'industry': row['industry'] if row['industry'] else '-', 
                'market': row['market'] if row['market'] else infer_market(row['ts_code']), 
                'close': row['close'], 'pct_chg': round(row['pct_chg'], 2), 'turnover_rate': round(row['turnover_rate'], 2),
                'total_mv': 0, 'volume_ratio': 1.0, 'total_score': int(row['score']),
                'avg_amp': round(row['amplitude'], 2), 'avg_amount': round(row['amount'] / 10000, 1) if row['amount'] else 0
            })
        return self._enrich_context_info(res, date)

    def _get_index_data_from_api(self, start_date, end_date):
        try:
            pro = ts.pro_api(TUSHARE_TOKEN)
            df_list = [pro.index_daily(ts_code=code, start_date=start_date, end_date=end_date)[['ts_code', 'trade_date', 'pct_chg']] for code in ['000001.SH', '399001.SZ', '399006.SZ', '000688.SH', '899050.BJ']]
            return pd.concat([d for d in df_list if not d.empty], ignore_index=True) if df_list else pd.DataFrame()
        except: return pd.DataFrame()

    def _map_stock_to_benchmark(self, ts_code):
        if ts_code.startswith(('60', '900')): return '000001.SH'
        elif ts_code.startswith(('00', '200')): return '399107.SZ'
        elif ts_code.startswith(('300', '301')): return '399006.SZ'
        elif ts_code.startswith('688'): return '000688.SH'
        elif ts_code.startswith(('8', '4', '92')): return '899050.BJ'
        else: return '000001.SH'

    def get_regulatory_abnormal_stocks(self, date):
        dates = self.db.get_recent_trading_days(date, 45) 
        if len(dates) < 35: return []
        dates.sort()
        date_start, date_end = dates[0], dates[-1]
        
        df_index = self._get_index_data_from_api(date_start, date_end)
        if df_index.empty: return []
        df_index = df_index.rename(columns={'ts_code': 'benchmark_code', 'pct_chg': 'idx_pct'})

        df = self._get_data(text(f"SELECT ts_code, trade_date, pct_chg, stock_name, industry, close FROM daily_data WHERE trade_date >= '{date_start}' AND trade_date <= '{date_end}' AND ts_code NOT LIKE '4%%' AND ts_code NOT LIKE '8%%'"))
        if df.empty: return []
        
        df = df[~df['stock_name'].str.contains('ST')]
        valid_pool = self._get_valid_stock_pool(date, min_days=90)
        if valid_pool: df = df[df['ts_code'].isin(valid_pool)]
        
        df['benchmark_code'] = df['ts_code'].apply(self._map_stock_to_benchmark)
        df = pd.merge(df, df_index, on=['trade_date', 'benchmark_code'], how='left')
        df['idx_pct'] = df['idx_pct'].fillna(0)
        
        df = df.drop_duplicates(subset=['trade_date', 'ts_code'], keep='last')
        pivot_stock = df.pivot(index='trade_date', columns='ts_code', values='pct_chg').fillna(0)
        pivot_bench = df.pivot(index='trade_date', columns='ts_code', values='idx_pct').fillna(0)
        
        if len(pivot_stock) < 30: return []
        
        dev_30_curr = ((1 + pivot_stock.iloc[-30:] / 100).prod() - 1) - ((1 + pivot_bench.iloc[-30:] / 100).prod() - 1)
        dev_10_curr = ((1 + pivot_stock.iloc[-10:] / 100).prod() - 1) - ((1 + pivot_bench.iloc[-10:] / 100).prod() - 1)
        
        mask = (dev_10_curr >= 0.60) | (dev_30_curr >= 1.50)
        target_codes = mask[mask].index.tolist()
        if not target_codes: return []

        results = []
        info_df = df[df['trade_date'] == date].set_index('ts_code')[['stock_name', 'industry', 'close', 'pct_chg']]
        
        for code in target_codes:
            if code not in info_df.index: continue
            d10_val, d30_val = dev_10_curr[code] * 100, dev_30_curr[code] * 100
            
            level, trigger_msg, status_msg = 1, "趋势监控", ""
            if d30_val >= 200:
                status_msg, trigger_msg, level = f"30日偏离 {d30_val:.2f}%", "已触发严重异动", 3
            elif d10_val >= 100:
                status_msg, trigger_msg, level = f"10日偏离 {d10_val:.2f}%", "已触发严重异动", 3
            else:
                level, trigger_msg = 2, "临界预警"
                status_msg = f"10日累计偏离 {d10_val:.2f}%" if d10_val > d30_val else f"30日累计偏离 {d30_val:.2f}%"
            
            results.append({
                'ts_code': code, 'stock_name': info_df.loc[code, 'stock_name'], 'industry': info_df.loc[code, 'industry'],
                'close': info_df.loc[code, 'close'], 'pct_today': info_df.loc[code, 'pct_chg'],
                'd10_curr': round(d10_val, 2), 'd30_curr': round(d30_val, 2),
                'status_info': status_msg, 'trigger_info': trigger_msg, 'level': level,
                'turnover_rate': 0, 'volume_ratio': 1.0, 'total_score': int(d10_val),
                'pct_chg': info_df.loc[code, 'pct_chg'], 'market': infer_market(code)
            })
            
        return self._enrich_context_info(sorted(results, key=lambda x: (x['level'], x['d10_curr']), reverse=True), date)