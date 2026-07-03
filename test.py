# test_tushare.py
import tushare as ts
import pandas as pd
from config import TUSHARE_TOKEN

def test_interface():
    print("正在尝试连接 Tushare API 并获取基础数据...")
    try:
        # 初始化 API
        pro = ts.pro_api(TUSHARE_TOKEN)
        
        # 尝试拉取包含 ts_code, list_date, industry 的股票列表
        df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,list_date,industry')
        
        if df is None or df.empty:
            print("❌ 获取成功，但返回的数据为空。")
            return
            
        print("\n✅ 获取成功！")
        print(f"数据总量: {len(df)} 行")
        print("数据字段:", df.columns.tolist())
        print("\n--- 前 10 行数据样例 ---")
        print(df.head(10))
        
        # 检查关键字段
        if 'list_date' in df.columns:
            # 抽样打印前几个不为空的上市日期
            sample_dates = df['list_date'].dropna().head().tolist()
            print(f"\n'list_date' 数据样本: {sample_dates}")
        else:
            print("❌ 返回的数据中缺少 'list_date' 字段")
            
    except Exception as e:
        print(f"❌ 调用 Tushare 接口失败，错误信息如下：\n{e}")

if __name__ == "__main__":
    test_interface()