# core/factor_calculator.py - 终极完整版 v11.4 (引入多因子基本面打分、未来日期占位保护与数值强制对齐)
import pandas as pd
import numpy as np
from sqlalchemy import text, MetaData, Table, Column, Integer, String, Float, Index, inspect
from sqlalchemy.types import String as SQLString, Float as SQLFloat
from datetime import datetime, timedelta
from utils.market_utils import infer_market
import logging
import tushare as ts
from config import TUSHARE_TOKEN

class FactorCalculator:
    def __init__(self, db_engine):
        self.db = db_engine
        self._stock_basic_cache = None
        self._init_daily_data_columns()   # 自动检查并添加新字段
        self._init_financials_table()     # 初始化基础财务表
        self._init_rankings_table()       # 自动初始化综合排名表


    def _load_stock_basic_cache(self):    
        """
        [安全数据加载器] 确保 stock_basic 缓存被统一加载，且字段结构（ts_code, list_date, industry）完全一致。
        """
        # 1. 检查缓存是否存在，若存在，验证核心字段是否完整
        if self._stock_basic_cache is not None:
            required_cols = {'ts_code', 'list_date', 'industry'}
            if required_cols.issubset(self._stock_basic_cache.columns):
                return self._stock_basic_cache
            else:
                logging.warning("检测到非标准的 stock_basic 缓存结构，将清空并重新初始化...")
                self._stock_basic_cache = None

        # 2. 统一向 Tushare 发起完整数据请求
        try:
            logging.info("🚀 正在通过 Tushare API 获取全市场股票基础信息（包含上市日期与行业）...")
            pro = ts.pro_api(TUSHARE_TOKEN)
            df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,list_date,industry')
            if df is not None and not df.empty:
                self._stock_basic_cache = df
                return self._stock_basic_cache
        except Exception as e:
            logging.error(f"⚠️ 通过 Tushare 接口获取股票基础信息失败: {e}")
        
        return None



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

    def _init_financials_table(self):
        """初始化 stock_financials 表，用于缓存从 AkShare 获取的个股基础财务数据"""
        try:
            engine = self.db.get_engine()
            insp = inspect(engine)
            if not insp.has_table('stock_financials'):
                metadata = MetaData()
                financials_table = Table(
                    'stock_financials', metadata,
                    Column('id', Integer, primary_key=True, autoincrement=True),
                    Column('ts_code', SQLString(20), index=True),
                    Column('period', SQLString(20), index=True),        # 报告期，如 '20240930'
                    Column('ann_date', SQLString(20)),                 # 公告日期
                    Column('revenue', Float, default=0.0),             # 营业总收入 (元)
                    Column('net_profit', Float, default=0.0),          # 净利润 (元)
                    Column('roe', Float, default=0.0),                 # 净资产收益率 (%)
                    Column('processed_time', SQLString(50)),
                    Index('idx_code_period', 'ts_code', 'period', unique=True)
                )
                metadata.create_all(engine)
                logging.info("成功创建并初始化 stock_financials 财务基础数据表。")
        except Exception as e:
            logging.error(f"初始化 stock_financials 表失败: {e}")

    def _init_rankings_table(self):
        """初始化 daily_rankings 表，用于存储个股每日综合评分、多因子行业地位及全市排名"""
        try:
            engine = self.db.get_engine()
            insp = inspect(engine)
            if not insp.has_table('daily_rankings'):
                metadata = MetaData()
                rankings_table = Table(
                    'daily_rankings', metadata,
                    Column('id', Integer, primary_key=True, autoincrement=True),
                    Column('trade_date', SQLString(20), index=True),
                    Column('ts_code', SQLString(20), index=True),
                    Column('stock_name', SQLString(50)),
                    Column('industry', SQLString(50)),
                    Column('total_mv', Float),          # 单位：亿元
                    Column('total_score', Float),       # 综合评分 (0~10分)
                    Column('ind_mv_rank', Integer),      # 同行业内市值排名
                    Column('ind_stock_count', Integer),  # 同行业成分股总数
                    Column('ind_mv_ratio', Float),       # 行业龙头系数 (0.0~1.0)
                    Column('overall_rank', Integer),     # 全市综合排名
                    Column('processed_time', SQLString(50)),
                    Index('idx_date_code', 'trade_date', 'ts_code', unique=True)
                )
                metadata.create_all(engine)
                logging.info("成功创建并初始化 daily_rankings 综合排名数据表。")
        except Exception as e:
            logging.error(f"初始化 daily_rankings 表失败: {e}")

    def _get_data(self, sql, conn=None):
        try:
            if conn:
                return pd.read_sql(sql, conn)
            with self.db.get_engine().connect() as conn:
                 return pd.read_sql(sql, conn)
        except Exception as e:
            logging.error(f"SQL执行错误: {e}")
            return pd.DataFrame()

    def _get_latest_report_period(self, target_date_str):
        """
        根据当前交易日期，智能推算已完整披露的最新财务报告期。
        规避财报披露时间差导致的未来函数偏误。
        """
        try:
            dt = datetime.strptime(target_date_str.replace('-', ''), '%Y%m%d')
            year = dt.year
            month = dt.month
            if month >= 11:    # 11月及以后，三季报（09-30）已强制披露完毕
                return f"{year}0930"
            elif month >= 9:   # 9月至10月，半年报（06-30）已强制披露完毕
                return f"{year}0630"
            elif month >= 5:   # 5月至8月，一季报（03-31）已强制披露完毕
                return f"{year}0331"
            else:              # 1月至4月，使用上个年度的年报（12-31）
                return f"{year-1}1231"
        except Exception:
            return f"{datetime.now().year - 1}1231"


    def sync_financial_data(self, target_date):
        """
        自动判断并同步目标日期所属周期的财务数据 (使用 AkShare 免 Token 批量获取)
        安全防护机制：如果接口抓取失败（如网络波动或未来日期回测无数据），绝不污染数据库，内存中会自动以0值安全计算。
        """
        period = self._get_latest_report_period(target_date)
        
        # 1. 检查本地是否已有该周期数据
        try:
            with self.db.get_engine().connect() as conn:
                count = conn.execute(text(f"SELECT COUNT(*) FROM stock_financials WHERE period = '{period}'")).scalar()
            if count > 1000:
                logging.info(f"本地已有 {period} 财务数据共 {count} 条，无需同步。")
                return
        except Exception as e:
            logging.error(f"检查本地财务数据存在性失败: {e}")

        logging.info(f"🚀 本地缺少 {period} 财务数据，正在通过 AkShare 同步全市场季报指标...")
        
        df_ak = None
        try:
            import akshare as ak
            period_dash = f"{period[:4]}-{period[4:6]}-{period[6:]}"
            # 尝试拉取财务数据
            df_ak = ak.stock_yjbb_em(date=period_dash)
        except Exception as e:
            logging.error(f"通过 AkShare 接口获取财务数据时发生异常: {e}")

        # 核心修改：如果抓取失败（返回 None、空表或未来日期无数据），绝不向数据库写入任何脏数据，直接安全返回
        if df_ak is None or not isinstance(df_ak, pd.DataFrame) or df_ak.empty:
            logging.warning(f"⚠️ 无法从数据接口获取到 {period} 周期的财务数据（回测未来日期时属于正常现象）。"
                            f"系统将维持本地数据库原样，并在内存计算中自动以 0 值对齐，确保安全平稳运行。")
            return

        # 2. 只有在成功获取到非空真实数据时，才进行数据库的安全覆盖写入
        try:
            # 弹性列名匹配 (防止东财网页调整列名导致报错)
            rename_map = {}
            for col in df_ak.columns:
                if '代码' in col:
                    rename_map[col] = 'code'
                elif '营业收入' in col and '同比' not in col and '季度' not in col:
                    rename_map[col] = 'revenue'
                elif '净利润' in col and '同比' not in col and '季度' not in col:
                    rename_map[col] = 'net_profit'
                elif '净资产收益率' in col or 'ROE' in col:
                    rename_map[col] = 'roe'
                elif '公告日期' in col:
                    rename_map[col] = 'ann_date'

            df_ak = df_ak.rename(columns=rename_map)

            # 过滤并保留所需字段
            required_cols = ['code', 'ann_date', 'revenue', 'net_profit', 'roe']
            for col in required_cols:
                if col not in df_ak.columns:
                    df_ak[col] = 0.0 if col != 'code' and col != 'ann_date' else '-'

            df_clean = df_ak[required_cols].copy()

            # 将纯数字代码转换为带后缀的 ts_code
            def format_to_ts_code(code_val):
                c_str = str(code_val).strip().zfill(6)
                if c_str.startswith('6'):
                    return f"{c_str}.SH"
                elif c_str.startswith(('0', '3')):
                    return f"{c_str}.SZ"
                elif c_str.startswith(('8', '4', '9')):
                    return f"{c_str}.BJ"
                return c_str

            df_clean['ts_code'] = df_clean['code'].apply(format_to_ts_code)
            df_clean['period'] = period
            
            # 数据类型清洗
            df_clean['revenue'] = pd.to_numeric(df_clean['revenue'], errors='coerce').fillna(0.0)
            df_clean['net_profit'] = pd.to_numeric(df_clean['net_profit'], errors='coerce').fillna(0.0)
            df_clean['roe'] = pd.to_numeric(df_clean['roe'], errors='coerce').fillna(0.0)
            df_clean['processed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            df_save = df_clean[['ts_code', 'period', 'ann_date', 'revenue', 'net_profit', 'roe', 'processed_time']].copy()
            df_save = df_save.drop_duplicates(subset=['ts_code', 'period'])

            # 写入本地数据库
            with self.db.get_engine().begin() as conn:
                conn.execute(text(f"DELETE FROM stock_financials WHERE period = '{period}'"))
                df_save.to_sql('stock_financials', conn, index=False, if_exists='append')
                
            logging.info(f"AkShare 成功获取真实财报！已将 {len(df_save)} 条 {period} 周期的财务快照保存至 stock_financials。")
            
        except Exception as e:
            logging.error(f"解析并保存 AkShare 财务数据时发生异常: {e}")


    def update_daily_factors(self, date):
        """
        [全局通用因子与排名引擎] 融合多因子归一化评分，计算个股总分、拼接概念，并生成个股行业与全市综合排名。
        """
        logging.info(f"正在计算全局因子与多维度排名并落库 (日期: {date})...")
        
        # 1. 自动同步并加载最新季报基本面数据
        self.sync_financial_data(date)
        period = self._get_latest_report_period(date)
        
        # 2. 取出当日所有股票行情数据
        sql_daily = text(f"""
            SELECT ts_code, stock_name, industry, close, total_mv, turnover_rate, ma_5, ma_10, ma_20 
            FROM daily_data WHERE trade_date = '{date}'
        """)
        df_daily = self._get_data(sql_daily)
        if df_daily.empty:
            logging.warning(f"{date} 无日线数据，跳过因子计算。")
            return

        # 3. 读取本地已同步的财务快照
        sql_fin = text(f"SELECT ts_code, revenue, net_profit, roe FROM stock_financials WHERE period = '{period}'")
        df_fin = self._get_data(sql_fin)
        
        # 4. 行情数据与财务数据在 Pandas 内部向量化合并
        df_merged = pd.merge(df_daily, df_fin, on='ts_code', how='left')
        
        # 核心修复 2：强制将所有待运算及比较的字段显式转换为 numeric 浮点型，规避各种数据库特殊驱动返回 object 的情况
        df_merged['total_mv'] = pd.to_numeric(df_merged['total_mv'], errors='coerce').fillna(0.0)
        df_merged['revenue'] = pd.to_numeric(df_merged['revenue'], errors='coerce').fillna(0.0)
        df_merged['net_profit'] = pd.to_numeric(df_merged['net_profit'], errors='coerce').fillna(0.0)
        df_merged['roe'] = pd.to_numeric(df_merged['roe'], errors='coerce').fillna(0.0)
        df_merged['industry'] = df_merged['industry'].fillna('未知').astype(str).str.strip()

        # 5. 行业分组下的多因子极值归一化 (Min-Max Normalization) 算法
        def min_max_normalize(series):
            s_min = series.min()
            s_max = series.max()
            if s_max == s_min:
                return pd.Series(50.0, index=series.index)
            return ((series - s_min) / (s_max - s_min)) * 100.0

        # 对各行业内部的四大财务数据分别进行极值转化 (0 ~ 100分)
        df_merged['mv_score'] = df_merged.groupby('industry')['total_mv'].transform(min_max_normalize)
        df_merged['rev_score'] = df_merged.groupby('industry')['revenue'].transform(min_max_normalize)
        df_merged['prof_score'] = df_merged.groupby('industry')['net_profit'].transform(min_max_normalize)
        df_merged['roe_score'] = df_merged.groupby('industry')['roe'].transform(min_max_normalize)

        # 加权合成行业地位分 (规模大 35% + 营收 30% + 利润 20% + ROE 15%)
        df_merged['industry_status_score'] = (
            df_merged['mv_score'] * 0.35 +
            df_merged['rev_score'] * 0.30 +
            df_merged['prof_score'] * 0.20 +
            df_merged['roe_score'] * 0.15
        )

        # 行业内排名与成分股总数
        df_merged['ind_mv_rank'] = df_merged.groupby('industry')['total_mv'].rank(ascending=False, method='min').astype(int)
        df_merged['ind_stock_count'] = df_merged.groupby('industry')['ts_code'].transform('count').astype(int)
        df_merged['ind_max_mv'] = df_merged.groupby('industry')['total_mv'].transform('max')
        df_merged['ind_mv_ratio'] = (df_merged['total_mv'] / df_merged['ind_max_mv'].replace(0, 1.0)).round(4)

        # 6. 获取监控概念池
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
                    concept_map = df_rel.groupby('ts_code')['concept_name'].apply(list).to_dict()
        except Exception as e:
            logging.error(f"加载监控概念用于打分时失败: {e}")

        # 7. 逐股结合技术指标与多维度行业地位，合成最终总分
        calculated_records = []
        updates_for_daily = []
        
        for row in df_merged.itertuples(index=False):
            code = row.ts_code
            c = row.close
            if pd.isna(c) or c == 0: continue
            
            score = 0.0
            my_concepts = concept_map.get(code, [])
            concept_str_val = " + ".join(my_concepts) if my_concepts else "-"

            # --- 规则1：监控概念命中 (每命中一个+1分) ---
            score += float(len(my_concepts) * 1.0)

            # --- 规则2：市值偏好 ---
            mv = row.total_mv
            if pd.notnull(mv):
                mv_100m = mv / 10000 
                if mv_100m < 50: score += 1.0
                elif 50 <= mv_100m < 100: score += 0.9
                elif 100 <= mv_100m < 200: score += 0.8
                else: score += 0.5

            # --- 规则3：均线多头打分 ---
            m5 = row.ma_5
            m10 = row.ma_10
            m20 = row.ma_20
            if pd.notnull(m5) and pd.notnull(m10) and pd.notnull(m20):
                if c > m5 and m5 > m10 and m10 > m20:
                    score += 1.0
                elif c > m5 and c < m10:
                    score += 0.6
                elif c > m5 and c < m20:
                    score += 0.4

            # --- 规则4：换手率偏好 ---
            to = row.turnover_rate
            if pd.notnull(to):
                if 5 < to <= 20: score += 1.0
                elif to > 20: score += 0.6
                else: score += 0.3

            # --- 规则5：基本面行业地位分加权叠加 ---
            # 我们将上述专业算法算出的“行业地位分 (0~100)”进行线性折算，折合权重为 2.0 分
            status_bonus = (row.industry_status_score / 100.0) * 2.0
            score += status_bonus

            # 统一封顶 10 分
            final_score = round(min(score, 10.0), 1)

            calculated_records.append({
                'trade_date': date,
                'ts_code': code,
                'stock_name': row.stock_name,
                'industry': row.industry,
                'total_mv': float(mv),
                'total_score': final_score,
                'ind_mv_rank': int(row.ind_mv_rank),
                'ind_stock_count': int(row.ind_stock_count),
                'ind_mv_ratio': float(row.ind_mv_ratio),
                'concept_str': concept_str_val
            })

            updates_for_daily.append({
                'b_score': final_score,
                'b_cstr': concept_str_val,
                'b_date': date,
                'b_code': code
            })

        if not calculated_records:
            return

        # 8. 计算全市综合排名 (排序规则：分数降序为主，若分数相同按市值降序)
        df_rank_save = pd.DataFrame(calculated_records)
        df_rank_save = df_rank_save.sort_values(by=['total_score', 'total_mv'], ascending=[False, False]).reset_index(drop=True)
        df_rank_save['overall_rank'] = df_rank_save.index + 1  # 综合排名从1开始

        # 9. 写入 daily_rankings 表
        try:
            # 市值转换为 “亿元”
            df_rank_save['total_mv'] = (df_rank_save['total_mv'] / 10000).round(2)
            df_rank_save['processed_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # 剔除概念字段以符合排名表结构
            df_rank_db = df_rank_save.drop(columns=['concept_str'])

            with self.db.get_engine().begin() as conn:
                conn.execute(text(f"DELETE FROM daily_rankings WHERE trade_date = '{date}'"))
                df_rank_db.to_sql('daily_rankings', conn, index=False, if_exists='append')
            logging.info(f"成功将 {len(df_rank_db)} 只股票的多因子地位及综合排名写入 daily_rankings 表。")
        except Exception as e:
            logging.error(f"保存每日排名数据到 daily_rankings 失败: {e}")

        # 10. 极速连表回写 daily_data 表中的评分与概念
        if updates_for_daily:
            df_updates = pd.DataFrame(updates_for_daily)
            temp_table = 'temp_update_factors'
            try:
                with self.db.get_engine().begin() as conn:
                    dtype_mapping = {
                        'b_score': SQLFloat(),
                        'b_cstr': SQLString(255),
                        'b_date': SQLString(20),
                        'b_code': SQLString(20)
                    }
                    df_updates.to_sql(temp_table, conn, index=False, if_exists='replace', dtype=dtype_mapping)
                    conn.execute(text(f"ALTER TABLE {temp_table} ADD PRIMARY KEY (b_date, b_code)"))
                    
                    update_sql = text(f"""
                        UPDATE daily_data d
                        INNER JOIN {temp_table} t 
                        ON d.trade_date = t.b_date AND d.ts_code = t.b_code
                        SET d.total_score = t.b_score, d.concept_str = t.b_cstr
                    """)
                    conn.execute(update_sql)
                    conn.execute(text(f"DROP TABLE IF EXISTS {temp_table}"))
                logging.info(f"极速更新完成！评分与概念已成功回写至 daily_data 库。")
            except Exception as e:
                logging.error(f"批量更新 daily_data 评分与概念失败: {e}")


    # ==========================
    # 下方保留你原有的完整核心功能代码
    # ==========================
    def _get_valid_stock_pool(self, current_date, min_days=90):
        """
        获取合法的股票池（过滤上市时间不足 min_days 的新股与次新股）。
        优先：采用 Tushare API 真实 list_date 字段进行过滤。
        兜底：若 API 异常或无积分，自动启用本地数据库历史交易记录，确保新股无法穿透过滤器。
        """
        fmt_date = current_date.replace('-', '')
        try:
            cutoff_dt = datetime.strptime(fmt_date, '%Y%m%d') - timedelta(days=min_days)
            cutoff_date_str = cutoff_dt.strftime('%Y%m%d')
        except Exception as e:
            logging.error(f"日期格式解析失败 ({current_date}): {e}")
            return set()  # 返回空集合，防止脏数据通过

        # 1. 尝试使用 Tushare API 的真实上市日期进行筛选
        cache = self._load_stock_basic_cache()
        if cache is not None and 'list_date' in cache.columns:
            try:
                valid_df = cache[cache['list_date'] <= cutoff_date_str]
                valid_codes = set(valid_df['ts_code'].tolist())
                logging.info(f"📊 [API 过滤] 成功使用真实上市日期进行过滤，合规老股共 {len(valid_codes)} 只（已排除近 {min_days} 天上市新股）。")
                return valid_codes
            except Exception as e:
                logging.error(f"使用 API 缓存过滤上市日期时发生异常: {e}")

        # 2. 兜底保护逻辑：如果接口获取完全失败，绝不返回 None（防止外层过滤被直接跳过）
        # 降级切换为本地数据库历史分析：查找在 cutoff_date 之前已经在本地生成过交易记录的股票
        logging.warning("⚠️ API 上市日期数据获取不完整，启动本地数据库历史交易深度检测机制...")
        try:
            db_cutoff_date = cutoff_dt.strftime('%Y-%m-%d') if '-' in current_date else cutoff_date_str
            sql = text(f"SELECT DISTINCT ts_code FROM daily_data WHERE trade_date <= '{db_cutoff_date}'")
            with self.db.get_engine().connect() as conn:
                res = conn.execute(sql).fetchall()
            valid_codes = {r[0] for r in res}
            logging.info(f"💾 [本地兜底] 成功通过本地历史记录兜底，共保留 {len(valid_codes)} 只老股，安全拦截了新股穿透。")
            return valid_codes
        except Exception as db_err:
            logging.critical(f"❌ [严重警报] 接口过滤与本地数据库兜底同时失败: {db_err}")
            
        # 极端异常情况下，返回空集合，确保不会有新股混入计算
        return set()



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

        self._load_stock_basic_cache()
        

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

    def calculate_sentiment_score(self, sh_pct, total_amount, up_count, total_count, limit_up_count, high_board, 
                                      sh_close=0, sh_ma5=0, sh_ma10=0, sh_ma20=0,
                                      promo_rate=0, vol_change_pct=0, limit_down_count=0):
            """
            [量能统治版情绪分算法] 将成交量权重提升至 40%，实现放量即活跃、有成交量就有高分的逻辑，总分上限 100 分。
            """
            # 1. 交易额因子 (s_vol) - 量能即生命线，权重系数拉满至 4.0 (最高直接贡献 40 分)
            s_vol = 10 if total_amount >= 35000 else (
                9 if total_amount >= 30000 else (
                    8 if total_amount >= 25000 else (
                        7 if total_amount >= 20000 else (
                            5 if total_amount >= 15000 else (
                                2 if total_amount >= 10000 else 0
                            )
                        )
                    )
                )
            )

            # 2. 均线趋势因子 (s_avg) - 权重系数下调至 1.0 (最高 10 分)
            s_avg = 0.0
            if sh_close > 0 and sh_ma5 > 0 and sh_ma10 > 0 and sh_ma20 > 0:
                # A. 均线多头排列且价格在 5 日线之上 
                if sh_close >= sh_ma5 and sh_ma5 > sh_ma10 and sh_ma10 > sh_ma20:
                    s_avg = 10.0
                # B. 均线多头排列，价格合理回踩 5 日至 10 日线之间
                elif sh_ma5 > sh_close >= sh_ma10 and sh_ma10 > sh_ma20:
                    s_avg = 8.0
                # C. 价格全线上穿 5, 10, 20 日线，但均线金叉还未完全对齐
                elif sh_close >= sh_ma5 and sh_close >= sh_ma10 and sh_close >= sh_ma20:
                    s_avg = 7.0
                # D. 仅守住 20 日生命线 (中期安全防线)
                elif sh_close >= sh_ma20:
                    s_avg = 5.0
                # E. 价格在 5 日线之上开始反弹
                elif sh_close >= sh_ma5:
                    s_avg = 3.0
                # F. 空头压制
                else:
                    s_avg = 0.0

            # 3. 指数涨跌因子 (s_idx) - 去除负值影响，范围 (0 ~ 10分)，权重系数 1.0 (最高 10 分)
            s_idx = 10 if sh_pct >= 3.0 else (
                9 if sh_pct >= 2.0 else (
                    8 if sh_pct >= 1.0 else (
                        7 if sh_pct >= 0.5 else (
                            6 if sh_pct >= 0.0 else (
                                4 if sh_pct >= -0.5 else (
                                    2 if sh_pct >= -1.0 else (
                                        1 if sh_pct >= -2.0 else 0
                                    )
                                )
                            )
                        )
                    )
                )
            )
            
            # 4. 上涨家数占比因子 (s_up) - 赚钱效应核心，权重系数 2.0 (最高 20 分)
            ratio = (up_count / total_count * 100) if total_count > 0 else 0
            s_up = 10 if ratio >= 90 else (8 if ratio >= 75 else (5 if ratio >= 50 else (2.5 if ratio >= 25 else 0)))
            
            # 5. 涨停家数因子 (s_limit) - 权重系数下调至 0.5 (最高 5 分)
            s_limit = 10 if limit_up_count >= 120 else (7.5 if limit_up_count >= 80 else (5 if limit_up_count >= 50 else (2.5 if limit_up_count >= 20 else 0)))
            
            # 6. 跌停家数因子 (s_limit_d) - 权重系数下调至 0.5 (最高 5 分)
            s_limit_d = 10 if limit_down_count < 5 else (
                8 if limit_down_count < 15 else (
                    6 if limit_down_count < 30 else (
                        4 if limit_down_count < 50 else (
                            2 if limit_down_count < 80 else 0
                        )
                    )
                )
            )

            # 7. 最高连板因子 (s_high) - 阶梯平滑，权重系数 1.0 (最高 10 分)
            s_high = 10 if high_board >= 7 else (
                8 if high_board >= 5 else (
                    6 if high_board >= 4 else (
                        4 if high_board >= 3 else 0
                    )
                )
            )
            
            # 按照“量能统治版”权重合成总分：
            # 情绪分 = (s_vol * 4) + (s_avg * 1) + (s_idx * 1) + (s_up * 2) + (s_limit * 0.5) + (s_limit_d * 0.5) + (s_high * 1)
            final_score = (s_vol * 4) + (s_avg * 1) + (s_idx * 1) + (s_up * 2) + (s_limit * 0.5) + (s_limit_d * 0.5) + (s_high * 1)
            
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
        # 1. 获取近45日的交易日期序列
        dates = self.db.get_recent_trading_days(date, 45) 
        if len(dates) < 35: return []
        dates.sort()
        date_start, date_end = dates[0], dates[-1]
        
        # 2. 从 Tushare 获取对应的指数日线
        df_index = self._get_index_data_from_api(date_start, date_end)
        if df_index.empty: return []
        df_index = df_index.rename(columns={'ts_code': 'benchmark_code', 'pct_chg': 'idx_pct'})

        # 3. 读取本地个股历史日线数据
        df = self._get_data(text(f"SELECT ts_code, trade_date, pct_chg, stock_name, industry, close FROM daily_data WHERE trade_date >= '{date_start}' AND trade_date <= '{date_end}'"))
        if df.empty: return []
        
        # 【核心过滤 1】：过滤掉科创板(688)、北交所(8, 4, 9开头)的股票，同时剔除 ST 股
        df = df[~df['ts_code'].str.startswith(('688', '8', '4', '9')) & ~df['stock_name'].str.contains('ST')]
        
        # 【核心过滤 2】：过滤新股与次新股。将 min_days 设定为 180 天（即上市未满半年的新股/次新股不纳入计算体系）
        valid_pool = self._get_valid_stock_pool(date, min_days=180)
        if valid_pool: 
            df = df[df['ts_code'].isin(valid_pool)]
        
        # 4. 个股与指数基准关联并计算偏离度
        df['benchmark_code'] = df['ts_code'].apply(self._map_stock_to_benchmark)
        df = pd.merge(df, df_index, on=['trade_date', 'benchmark_code'], how='left')
        df['idx_pct'] = df['idx_pct'].fillna(0)
        
        df = df.drop_duplicates(subset=['trade_date', 'ts_code'], keep='last')
        pivot_stock = df.pivot(index='trade_date', columns='ts_code', values='pct_chg').fillna(0)
        pivot_bench = df.pivot(index='trade_date', columns='ts_code', values='idx_pct').fillna(0)
        
        if len(pivot_stock) < 30: return []
        
        # 计算 30日 与 10日 偏离值
        dev_30_curr = ((1 + pivot_stock.iloc[-30:] / 100).prod() - 1) - ((1 + pivot_bench.iloc[-30:] / 100).prod() - 1)
        dev_10_curr = ((1 + pivot_stock.iloc[-10:] / 100).prod() - 1) - ((1 + pivot_bench.iloc[-10:] / 100).prod() - 1)
        
        # 筛选符合异常波动边界的个股（10日偏离 >= 60% 或 30日偏离 >= 150%）
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