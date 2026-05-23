import sys

def infer_market(ts_code):
    """根据股票代码推断市场板块"""
    if ts_code.startswith(('600', '601', '603', '605', '000', '001', '002', '003')): 
        return "主板"
    elif ts_code.startswith(('300', '301')): 
        return "创业板"
    elif ts_code.startswith(('688')):
        return "科创板"
    elif ts_code.startswith(('920')):
        return "北交所"
    else:    
        return "其他"

def print_progress(current, total, prefix=""):
    """控制台进度条"""
    percent = 100 * (current / float(total))
    bar = '█' * int(percent/2) + '-' * (50 - int(percent/2))
    sys.stdout.write(f'\r{prefix} |{bar}| {percent:.1f}% ({current}/{total})')
    sys.stdout.flush()