"""
Web 配置管理器 v2 — 统一配置，单一数据源 web_settings.json
首次启动自动从 settings.local.py 和 Excel 迁移数据
"""
import json
import os
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_CONFIG_FILE = os.path.join(BASE_DIR, "config", "web_settings.json")
_lock = threading.Lock()

DEFAULT_CONFIG = {
    "db": {"host": "localhost", "port": 3306, "user": "root", "password": "", "database": "stock_data", "charset": "utf8mb4"},
    "tushare_token": "",
    "email": {"smtp_server": "smtp.qq.com", "smtp_port": 465, "sender": "", "password": "", "subject_prefix": "【每日复盘】"},
    "recipients": [],
    "llm": {"api_key": "", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "temperature": 1.0, "top_n": 20},
    "monitor_pool": {"stocks": [], "concepts": []},
    "github_deploy": {"token": "", "owner": "", "repo": "", "branch": "main", "target_path": "index.html"},
}


def _load():
    if os.path.exists(WEB_CONFIG_FILE):
        try:
            with open(WEB_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return _deep_merge(DEFAULT_CONFIG, data)
        except:
            pass
    return dict(DEFAULT_CONFIG)


def _save(cfg):
    os.makedirs(os.path.dirname(WEB_CONFIG_FILE), exist_ok=True)
    with open(WEB_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _deep_merge(base, override):
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


_migrated = False

def migrate_from_legacy():
    global _migrated
    if _migrated or os.path.exists(WEB_CONFIG_FILE):
        _migrated = True
        return True

    cfg = _load()
    try:
        import importlib.util
        sp = os.path.join(os.path.dirname(__file__), "config", "settings.local.py")
        if not os.path.exists(sp):
            _migrated = True
            return True

        spec = importlib.util.spec_from_file_location("_tmp_sl_migrate", sp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        cfg["db"] = mod.DB_CONFIG
        cfg["tushare_token"] = mod.TUSHARE_TOKEN
        cfg["email"] = {
            "smtp_server": mod.EMAIL_CONFIG.get("smtp_server", ""),
            "smtp_port": mod.EMAIL_CONFIG.get("smtp_port", 465),
            "sender": mod.EMAIL_CONFIG.get("sender", ""),
            "password": mod.EMAIL_CONFIG.get("password", ""),
            "subject_prefix": mod.EMAIL_CONFIG.get("subject_prefix", "【每日复盘】"),
        }
        old_recv = mod.EMAIL_CONFIG.get("receivers", [])
        cfg["recipients"] = [{"email": r, "remark": "", "enabled": True} for r in old_recv]

        cfg["llm"] = {
            "api_key": mod.LLM_CONFIG.get("api_key", ""),
            "base_url": mod.LLM_CONFIG.get("base_url", "https://api.deepseek.com"),
            "model": mod.LLM_CONFIG.get("model", "deepseek-chat"),
            "temperature": mod.LLM_CONFIG.get("temperature", 1.0),
            "top_n": mod.LLM_CONFIG.get("top_n", 20),
        }
        cfg["github_deploy"] = {
            "token": mod.GITHUB_DEPLOY_CONFIG.get("token", ""),
            "owner": mod.GITHUB_DEPLOY_CONFIG.get("owner", ""),
            "repo": mod.GITHUB_DEPLOY_CONFIG.get("repo", ""),
            "branch": mod.GITHUB_DEPLOY_CONFIG.get("branch", "main"),
            "target_path": mod.GITHUB_DEPLOY_CONFIG.get("target_path", "index.html"),
        }

        _import_monitor_excel(cfg)
        _save(cfg)
        _migrated = True
        print("[Config] 迁移完成！")
        return True
    except Exception as e:
        import traceback
        print("[Config] 迁移失败: " + str(e))
        traceback.print_exc()
        return False


def _import_monitor_excel(cfg):
    import pandas as pd
    excel_path = os.path.join(BASE_DIR, "data", "input", "monitor_pool.local.xlsx")
    if not os.path.exists(excel_path):
        return
    try:
        xls = pd.ExcelFile(excel_path)
        if "stock" in xls.sheet_names:
            df = pd.read_excel(xls, "stock")
            stocks = []
            for _, row in df.iterrows():
                s = {"code": str(row.get("股票代码", "")).strip(),
                     "name": str(row.get("股票名称", "")).strip(),
                     "remark": str(row.get("备注", "")).strip()}
                if s["code"] and s["code"] != "nan":
                    if s["remark"] in ("--", "nan"): s["remark"] = ""
                    stocks.append(s)
            cfg["monitor_pool"]["stocks"] = stocks
        if "concept" in xls.sheet_names:
            df = pd.read_excel(xls, "concept")
            concepts = []
            for _, row in df.iterrows():
                c = str(row.get("概念名称", "")).strip()
                if c and c != "nan":
                    concepts.append({"name": c})
            cfg["monitor_pool"]["concepts"] = concepts
        print("[Config] 从 Excel 导入: " + str(len(cfg["monitor_pool"]["stocks"])) + " 只股票, " + str(len(cfg["monitor_pool"]["concepts"])) + " 个概念")
    except Exception as e:
        print("[Config] Excel 导入失败: " + str(e))


# ==================== 全量 ====================

def get_all():
    with _lock: return _load()

def update_all(data):
    with _lock: _save(data)
    return data


# ==================== DB / Tushare ====================

def get_db_config():
    with _lock: return _load().get("db", {})

def update_db_config(data):
    with _lock:
        cfg = _load(); cfg["db"].update(data); _save(cfg)
    return cfg["db"]


# ==================== 邮件 ====================

def get_email_config():
    with _lock: return _load().get("email", {})

def update_email_config(data):
    with _lock:
        cfg = _load(); cfg["email"].update(data); _save(cfg)
    return cfg["email"]


# ==================== 收件人 ====================

def get_recipients():
    with _lock: return _load().get("recipients", [])

def add_recipient(email, remark=""):
    with _lock:
        cfg = _load()
        if not any(r["email"] == email for r in cfg["recipients"]):
            cfg["recipients"].append({"email": email, "remark": remark, "enabled": True})
            _save(cfg)
    return cfg["recipients"]

def remove_recipient(email):
    with _lock:
        cfg = _load()
        cfg["recipients"] = [r for r in cfg["recipients"] if r["email"] != email]
        _save(cfg)
    return cfg["recipients"]

def update_recipient(email, data):
    with _lock:
        cfg = _load()
        for r in cfg["recipients"]:
            if r["email"] == email:
                r.update(data)
        _save(cfg)
    return cfg["recipients"]

def toggle_all_recipients(enabled):
    with _lock:
        cfg = _load()
        for r in cfg["recipients"]:
            r["enabled"] = enabled
        _save(cfg)
    return cfg["recipients"]

def reorder_recipients(ordered_list):
    """按新顺序重排收件人"""
    with _lock:
        cfg = _load()
        cfg["recipients"] = ordered_list
        _save(cfg)
    return cfg["recipients"]


# ==================== LLM ====================

def get_llm_config():
    with _lock: return _load().get("llm", {})

def update_llm_config(data):
    with _lock:
        cfg = _load(); cfg["llm"].update(data); _save(cfg)
    return cfg["llm"]


# ==================== GitHub ====================

def get_github_config():
    with _lock: return _load().get("github_deploy", {})

def update_github_config(data):
    with _lock:
        cfg = _load(); cfg["github_deploy"].update(data); _save(cfg)
    return cfg["github_deploy"]


# ==================== 监控池 ====================

def get_monitor_pool():
    with _lock: return _load().get("monitor_pool", {"stocks": [], "concepts": []})

def add_stock(code, name="", remark=""):
    with _lock:
        cfg = _load()
        if not any(s["code"] == code for s in cfg["monitor_pool"]["stocks"]):
            cfg["monitor_pool"]["stocks"].append({"code": code, "name": name, "remark": remark})
            _save(cfg)
    return cfg["monitor_pool"]

def remove_stock(code):
    with _lock:
        cfg = _load()
        cfg["monitor_pool"]["stocks"] = [s for s in cfg["monitor_pool"]["stocks"] if s["code"] != code]
        _save(cfg)
    return cfg["monitor_pool"]

def update_stock(code, data):
    with _lock:
        cfg = _load()
        for s in cfg["monitor_pool"]["stocks"]:
            if s["code"] == code:
                s.update(data)
        _save(cfg)
    return cfg["monitor_pool"]

def import_stocks(stocks_list):
    with _lock:
        cfg = _load()
        existing = {s["code"] for s in cfg["monitor_pool"]["stocks"]}
        for item in stocks_list:
            if item.get("code") and item["code"] not in existing:
                cfg["monitor_pool"]["stocks"].append({"code": item["code"], "name": item.get("name", ""), "remark": item.get("remark", "")})
                existing.add(item["code"])
        _save(cfg)
    return cfg["monitor_pool"]

def reorder_stocks(ordered_list):
    with _lock:
        cfg = _load()
        cfg["monitor_pool"]["stocks"] = ordered_list
        _save(cfg)
    return cfg["monitor_pool"]

def add_concept(name):
    with _lock:
        cfg = _load()
        if not any(c["name"] == name for c in cfg["monitor_pool"]["concepts"]):
            cfg["monitor_pool"]["concepts"].append({"name": name})
            _save(cfg)
    return cfg["monitor_pool"]

def remove_concept(name):
    with _lock:
        cfg = _load()
        cfg["monitor_pool"]["concepts"] = [c for c in cfg["monitor_pool"]["concepts"] if c["name"] != name]
        _save(cfg)
    return cfg["monitor_pool"]

def import_concepts(concepts_list):
    with _lock:
        cfg = _load()
        existing = {c["name"] for c in cfg["monitor_pool"]["concepts"]}
        for item in concepts_list:
            name = item.get("name", "").strip()
            if name and name not in existing:
                cfg["monitor_pool"]["concepts"].append({"name": name})
                existing.add(name)
        _save(cfg)
    return cfg["monitor_pool"]

def reorder_concepts(ordered_list):
    with _lock:
        cfg = _load()
        cfg["monitor_pool"]["concepts"] = ordered_list
        _save(cfg)
    return cfg["monitor_pool"]

def export_monitor_pool(format="json"):
    with _lock:
        pool = _load().get("monitor_pool", {"stocks": [], "concepts": []})
    return pool

# ==================== Excel 导入导出 ====================

def import_monitor_from_excel(file_input):
    """从 Excel 文件导入监控池，完全覆盖现有数据（同时同步到数据库）"""
    import pandas as pd
    try:
        xls = pd.ExcelFile(file_input)
        result = {"stocks": 0, "concepts": 0}
        
        # 先清空再导入，实现完全覆盖
        with _lock:
            cfg = _load()
            cfg["monitor_pool"]["stocks"] = []
            cfg["monitor_pool"]["concepts"] = []
            _save(cfg)
        
        if "stock" in xls.sheet_names:
            df = pd.read_excel(xls, "stock")
            stocks = []
            for _, row in df.iterrows():
                code = str(row.get("股票代码", "")).strip()
                name = str(row.get("股票名称", "")).strip()
                remark = str(row.get("备注", "")).strip()
                if code and code != "nan":
                    if remark in ("--", "nan"):
                        remark = ""
                    stocks.append({"code": code, "name": name, "remark": remark})
            if stocks:
                import_stocks(stocks)
                result["stocks"] = len(stocks)
        
        if "concept" in xls.sheet_names:
            df = pd.read_excel(xls, "concept")
            concept_col = "概念名称" if "概念名称" in df.columns else df.columns[0]
            concepts = []
            for _, row in df.iterrows():
                name = str(row.get(concept_col, "")).strip()
                if name and name != "nan":
                    concepts.append({"name": name})
            if concepts:
                import_concepts(concepts)
                result["concepts"] = len(concepts)
        
        # 同步到数据库表，确保监控报告也使用最新数据
        try:
            from core.monitor_manager import MonitorPoolManager
            from core.db_engine import DBEngine
            db = DBEngine()
            mgr = MonitorPoolManager(db)
            mgr.sync_from_web_config()
        except Exception:
            pass  # 数据库不可用时静默跳过
        
        return result
    except Exception as e:
        raise ValueError(f"Excel 导入失败: {e}")


def export_monitor_to_excel():
    """导出监控池为 Excel 文件，返回文件路径"""
    import pandas as pd
    import tempfile
    
    pool = get_monitor_pool()
    output_path = os.path.join(tempfile.gettempdir(), "monitor_pool_export.xlsx")
    
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        stocks = pool.get("stocks", [])
        if stocks:
            df_stock = pd.DataFrame(stocks)
            df_stock = df_stock.rename(columns={"code": "股票代码", "name": "股票名称", "remark": "备注"})
            df_stock = df_stock[["股票代码", "股票名称", "备注"]]
        else:
            df_stock = pd.DataFrame(columns=["股票代码", "股票名称", "备注"])
        df_stock.to_excel(writer, sheet_name="stock", index=False)
        
        concepts = pool.get("concepts", [])
        if concepts:
            df_concept = pd.DataFrame(concepts)
            df_concept = df_concept.rename(columns={"name": "概念名称"})
        else:
            df_concept = pd.DataFrame(columns=["概念名称"])
        df_concept.to_excel(writer, sheet_name="concept", index=False)
    
    return output_path
