"""JA_Stock Web 服务端 v2"""
import sys, os, json, logging, threading, queue, re, glob
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory, send_file, Response

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from web_config_manager import (
    import_monitor_from_excel, export_monitor_to_excel,
    get_all, update_all, get_db_config, update_db_config,
    get_email_config, update_email_config,
    get_recipients, add_recipient, remove_recipient, update_recipient,
    toggle_all_recipients, reorder_recipients,
    get_llm_config, update_llm_config,
    get_github_config, update_github_config,
    get_monitor_pool, add_stock, remove_stock, update_stock,
    import_stocks, import_concepts, export_monitor_pool,
    reorder_stocks, reorder_concepts,
    add_concept, remove_concept, migrate_from_legacy,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

app = Flask(__name__, static_folder="web/static", template_folder="web/templates")
log_queue = queue.Queue()
task_status = {"running": False, "task": "", "logs": [], "result": None}

task_start_time = None

class QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            log_queue.put({"time": datetime.now().strftime("%H:%M:%S"),
                           "msg": msg, "level": record.levelname})
        except Exception:
            pass

class TeeHandler(logging.StreamHandler):
    """同时输出到 stderr 和 log_queue 的 Handler"""
    def emit(self, record):
        # 输出到 stderr（保持控制台可见）
        super().emit(record)
        # 同时推送到队列（供前端展示）
        try:
            msg = self.format(record)
            log_queue.put({"time": datetime.now().strftime("%H:%M:%S"),
                           "msg": msg, "level": record.levelname})
        except Exception:
            pass

def ensure_qh_attached():
    """替换根 logger 的 StreamHandler 为 TeeHandler，确保日志同时进入队列"""
    root = logging.getLogger()
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    
    # 检查是否已经替换过（通过检查是否有 TeeHandler）
    has_tee = any(isinstance(h, TeeHandler) for h in root.handlers)
    if has_tee:
        return
    
    # 移除普通的 StreamHandler，替换为 TeeHandler
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(h, TeeHandler):
            root.removeHandler(h)
    
    tee = TeeHandler()
    tee.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", "%H:%M:%S"))
    tee.setLevel(logging.DEBUG)
    root.addHandler(tee)
    
    # 移除旧的 QueueHandler（如果还存在的话），只用 TeeHandler 避免重复
    for h in list(root.handlers):
        if isinstance(h, QueueHandler):
            root.removeHandler(h)

qh = QueueHandler()
qh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", "%H:%M:%S"))
qh.setLevel(logging.DEBUG)  # 捕获更多级别的日志
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("web_server")

# 抑制 werkzeug 的 HTTP 请求日志（太吵了）
logging.getLogger("werkzeug").setLevel(logging.WARNING)

if not os.path.exists(os.path.join(BASE_DIR, "config", "web_settings.json")):
    logger.info("首次启动，正在迁移配置...")
    migrate_from_legacy()


# ====== 页面 ======
@app.route("/")
def index():
    return send_from_directory("web/templates", "index.html")

@app.route("/static/<path:fn>")
def static_files(fn):
    return send_from_directory("web/static", fn)


# ====== 报告服务 ======

REPORT_DIRS = {
    "review": os.path.join(OUTPUT_DIR, "daily_review"),
    "selection": os.path.join(OUTPUT_DIR, "stock_selection"),
    "backtest": os.path.join(OUTPUT_DIR, "backtest"),
    "monitor": os.path.join(OUTPUT_DIR, "monitor"),
}

REPORT_PATTERNS = {
    "review": r"daily_review_(\d{8})\.html",
    "selection": r"stock_selection_(\d{8})\.html",
    "backtest": r"backtest_report_(\d{8})\.html",
    "monitor": r"monitor_(\d{8})\.html",
}

@app.route("/api/reports")
def api_list_reports():
    """列出所有可用的报告日期"""
    result = {}
    for rtype, rdir in REPORT_DIRS.items():
        result[rtype] = []
        if os.path.isdir(rdir):
            pattern = REPORT_PATTERNS.get(rtype, r"")
            for fname in sorted(os.listdir(rdir), reverse=True):
                m = re.match(pattern, fname)
                if m:
                    result[rtype].append({
                        "date": m.group(1),
                        "filename": fname,
                        "path": os.path.join(rdir, fname),
                    })
    return jsonify({"code": 0, "data": result})

@app.route("/reports/<report_type>/<filename>")
def serve_report(report_type, filename):
    """直接提供报告 HTML 文件"""
    if report_type not in REPORT_DIRS:
        return "Not found", 404
    filepath = os.path.join(REPORT_DIRS[report_type], filename)
    if not os.path.exists(filepath):
        return "Not found", 404
    return send_file(filepath)


# ====== 全量配置 ======
@app.route("/api/config")
def api_get_all():
    return jsonify({"code": 0, "data": get_all()})


# ====== 数据库 ======
@app.route("/api/config/db")
def api_get_db():
    return jsonify({"code": 0, "data": get_db_config()})

@app.route("/api/config/db", methods=["POST"])
def api_update_db():
    return jsonify({"code": 0, "data": update_db_config(request.get_json()), "message": "已保存"})

@app.route("/api/config/tushare", methods=["POST"])
def api_update_tushare():
    cfg = get_all()
    cfg["tushare_token"] = request.get_json().get("token", "")
    update_all(cfg)
    return jsonify({"code": 0, "message": "已保存"})


# ====== 邮件 ======
@app.route("/api/config/email")
def api_get_email():
    return jsonify({"code": 0, "data": get_email_config()})

@app.route("/api/config/email", methods=["POST"])
def api_update_email():
    return jsonify({"code": 0, "data": update_email_config(request.get_json()), "message": "已保存"})


# ====== 收件人 ======
@app.route("/api/config/recipients")
def api_get_recipients():
    return jsonify({"code": 0, "data": get_recipients()})

@app.route("/api/config/recipients", methods=["POST"])
def api_add_recipient():
    d = request.get_json()
    email = d.get("email", "").strip()
    if not email:
        return jsonify({"code": 1, "message": "邮箱不能为空"}), 400
    return jsonify({"code": 0, "data": add_recipient(email, d.get("remark", "")), "message": "已添加 " + email})

@app.route("/api/config/recipients", methods=["DELETE"])
def api_remove_recipient():
    d = request.get_json()
    email = d.get("email", "").strip()
    if not email:
        return jsonify({"code": 1, "message": "邮箱不能为空"}), 400
    return jsonify({"code": 0, "data": remove_recipient(email), "message": "已删除 " + email})

@app.route("/api/config/recipients/<email>", methods=["PUT"])
def api_update_recipient(email):
    return jsonify({"code": 0, "data": update_recipient(email, request.get_json()), "message": "已更新"})

@app.route("/api/config/recipients/toggle", methods=["POST"])
def api_toggle_all():
    en = request.get_json().get("enabled", True)
    return jsonify({"code": 0, "data": toggle_all_recipients(en), "message": "全部启用" if en else "全部停用"})

@app.route("/api/config/recipients/reorder", methods=["PUT"])
def api_reorder_recipients():
    return jsonify({"code": 0, "data": reorder_recipients(request.get_json()), "message": "顺序已更新"})


# ====== LLM ======
@app.route("/api/config/llm")
def api_get_llm():
    return jsonify({"code": 0, "data": get_llm_config()})

@app.route("/api/config/llm", methods=["POST"])
def api_update_llm():
    return jsonify({"code": 0, "data": update_llm_config(request.get_json()), "message": "已保存"})


# ====== GitHub ======
@app.route("/api/config/github")
def api_get_github():
    return jsonify({"code": 0, "data": get_github_config()})

@app.route("/api/config/github", methods=["POST"])
def api_update_github():
    return jsonify({"code": 0, "data": update_github_config(request.get_json()), "message": "已保存"})


# ====== 监控池 ======
@app.route("/api/config/monitor")
def api_get_monitor():
    return jsonify({"code": 0, "data": get_monitor_pool()})

@app.route("/api/config/monitor/stock", methods=["POST"])
def api_add_stock():
    d = request.get_json()
    return jsonify({"code": 0, "data": add_stock(d.get("code", ""), d.get("name", ""), d.get("remark", "")), "message": "已添加"})

@app.route("/api/config/monitor/stock/<code>", methods=["PUT"])
def api_update_stock(code):
    return jsonify({"code": 0, "data": update_stock(code, request.get_json()), "message": "已更新"})

@app.route("/api/config/monitor/stock/<code>", methods=["DELETE"])
def api_remove_stock(code):
    return jsonify({"code": 0, "data": remove_stock(code), "message": "已删除"})

@app.route("/api/config/monitor/stocks/import", methods=["POST"])
def api_import_stocks():
    return jsonify({"code": 0, "data": import_stocks(request.get_json().get("stocks", [])), "message": "导入完成"})

@app.route("/api/config/monitor/stocks/reorder", methods=["PUT"])
def api_reorder_stocks():
    return jsonify({"code": 0, "data": reorder_stocks(request.get_json()), "message": "顺序已更新"})

@app.route("/api/config/monitor/concept", methods=["POST"])
def api_add_concept():
    d = request.get_json()
    return jsonify({"code": 0, "data": add_concept(d.get("name", "")), "message": "已添加"})

@app.route("/api/config/monitor/concept/<name>", methods=["DELETE"])
def api_remove_concept(name):
    return jsonify({"code": 0, "data": remove_concept(name), "message": "已删除"})

@app.route("/api/config/monitor/concepts/import", methods=["POST"])
def api_import_concepts():
    return jsonify({"code": 0, "data": import_concepts(request.get_json().get("concepts", [])), "message": "导入完成"})

@app.route("/api/config/monitor/concepts/reorder", methods=["PUT"])
def api_reorder_concepts():
    return jsonify({"code": 0, "data": reorder_concepts(request.get_json()), "message": "顺序已更新"})

@app.route("/api/config/monitor/export")
def api_export_monitor():
    pool = export_monitor_pool()
    return Response(json.dumps(pool, ensure_ascii=False, indent=2),
                    mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=monitor_pool_export.json"})

@app.route("/api/config/monitor/import-excel", methods=["POST"])
def api_import_monitor_excel():
    """从上传的 Excel 文件导入监控池（内存读取，避免文件锁问题）"""
    if "file" not in request.files:
        return jsonify({"code": 1, "message": "未上传文件"}), 400
    file = request.files["file"]
    if not file.filename.endswith((".xlsx", ".xls")):
        return jsonify({"code": 1, "message": "请上传 .xlsx 或 .xls 格式文件"}), 400
    import io
    try:
        excel_bytes = io.BytesIO(file.read())
        result = import_monitor_from_excel(excel_bytes)
        return jsonify({"code": 0, "data": result, "message": f"导入完成: {result['stocks']} 只股票, {result['concepts']} 个概念"})
    except Exception as e:
        return jsonify({"code": 1, "message": str(e)}), 500

@app.route("/api/config/monitor/export-excel")
def api_export_monitor_excel():
    """导出监控池为 Excel 文件（内存生成，避免临时文件问题）"""
    import io
    import pandas as pd
    pool = get_monitor_pool()
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
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
    output.seek(0)
    return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name="monitor_pool_export.xlsx")


# ====== 任务执行 ======
def _run_in_thread(mode):
    global task_status
    global task_start_time
    task_status = {"running": True, "task": mode, "logs": [], "result": None}
    task_start_time = datetime.now()

    def _run():
        global task_status
        try:
            from config import ensure_runtime_dirs
            from core.db_engine import DBEngine
            from report.renderer import ReportRenderer
            ensure_runtime_dirs()
            db = DBEngine()
            renderer = ReportRenderer()
            # 重新挂载 QueueHandler（main.py 的 force=True 会清除它）
            ensure_qh_attached()
            logger.info("[" + mode + "] 任务开始执行...")

            if mode == "update":
                from core.data_updater import StockDataUpdater
                from core.factor_calculator import FactorCalculator
                StockDataUpdater().run("update", 1)
                ld = db.get_latest_date()
                if ld:
                    FactorCalculator(db).update_daily_factors(ld)
                logger.info("[" + mode + "] 数据更新完成")

            elif mode == "review":
                from main import run_review
                ensure_qh_attached()
                run_review(db, renderer)
                logger.info("[" + mode + "] 复盘报告生成完成")

            elif mode == "select":
                from main import run_selection
                ensure_qh_attached()
                run_selection(db, renderer)
                logger.info("[" + mode + "] 选股与回测完成")

            elif mode == "monitor":
                from main import run_monitor
                ensure_qh_attached()
                run_monitor(db, renderer)
                logger.info("[" + mode + "] 监控报告生成完成")

            elif mode == "email":
                from main import run_email
                ensure_qh_attached()
                run_email(db)
                logger.info("[" + mode + "] 邮件发送完成")

            elif mode == "deploy":
                from services.deploy_hexo import deploy_reports
                td = db.get_latest_date()
                if td:
                    deploy_reports(td)
                logger.info("[" + mode + "] Hexo 部署完成")

            elif mode == "github":
                from main import run_github_deploy
                ensure_qh_attached()
                run_github_deploy(db)
                logger.info("[" + mode + "] GitHub 发布完成")

            elif mode == "full":
                steps = [("update", "数据更新"), ("review", "复盘"), ("monitor", "监控"),
                         ("email", "邮件"), ("deploy", "部署")]
                for i, (sm, sl) in enumerate(steps, 1):
                    logger.info("[全流程] 步骤 " + str(i) + "/5 - " + sl + " 开始")
                    if sm == "update":
                        from core.data_updater import StockDataUpdater
                        from core.factor_calculator import FactorCalculator
                        StockDataUpdater().run("update", 1)
                        ld = db.get_latest_date()
                        if ld:
                            FactorCalculator(db).update_daily_factors(ld)
                    elif sm == "review":
                        from main import run_review
                        _root = logging.getLogger()
                        if qh not in _root.handlers: _root.addHandler(qh)
                        run_review(db, renderer)
                    elif sm == "monitor":
                        from main import run_monitor
                        _root = logging.getLogger()
                        if qh not in _root.handlers: _root.addHandler(qh)
                        run_monitor(db, renderer)
                    elif sm == "email":
                        from main import run_email
                        _root = logging.getLogger()
                        if qh not in _root.handlers: _root.addHandler(qh)
                        run_email(db)
                    elif sm == "deploy":
                        from services.deploy_hexo import deploy_reports
                        td = db.get_latest_date()
                        if td:
                            deploy_reports(td)
                    logger.info("[全流程] 步骤 " + str(i) + "/5 - " + sl + " 完成")

            task_status["result"] = "success"
            logger.info("[" + mode + "] 全部完成！")

        except Exception as e:
            import traceback
            task_status["result"] = "error"
            logger.error("[" + mode + "] 错误: " + str(e))
            logger.error(traceback.format_exc())
        finally:
            task_status["running"] = False

    threading.Thread(target=_run, daemon=True).start()


@app.route("/api/run/<mode>", methods=["POST"])
def api_run(mode):
    valid = ["update", "factor", "review", "select", "monitor", "email", "deploy", "github", "full"]
    if mode not in valid:
        return jsonify({"code": 1, "message": "不支持的模式"}), 400
    if task_status["running"]:
        return jsonify({"code": 1, "message": "当前有任务运行中: " + task_status["task"]}), 409
    _run_in_thread(mode)
    return jsonify({"code": 0, "message": "任务 [" + mode + "] 已启动", "data": {"task": mode}})


@app.route("/api/status")
def api_status():
    global task_start_time
    drained = 0
    while not log_queue.empty():
        try:
            e = log_queue.get_nowait()
            task_status["logs"].append(e["msg"])
            drained += 1
        except queue.Empty:
            break
    if drained > 0:
        print(f"[WebLog] api_status 从队列取出 {drained} 条日志，当前共 {len(task_status['logs'])} 条", flush=True)
    elapsed = ""
    if task_start_time:
        secs = int((datetime.now() - task_start_time).total_seconds())
        m = secs // 60
        s = secs % 60
        elapsed = f"{m:02d}:{s:02d}"
    return jsonify({"code": 0, "data": {
        "running": task_status["running"],
        "task": task_status["task"],
        "result": task_status["result"],
        "logs": task_status["logs"][-200:],
        "elapsed": elapsed,
        "log_count": len(task_status["logs"])
    }})


# ====== 调试端点 ======
@app.route("/api/debug/handlers")
def api_debug_handlers():
    """查看根 logger 的所有 handlers"""
    root = logging.getLogger()
    handlers_info = []
    for h in root.handlers:
        handlers_info.append({
            "type": type(h).__name__,
            "level": logging.getLevelName(h.level) if hasattr(h, 'level') else 'N/A'
        })
    return jsonify({
        "code": 0,
        "data": {
            "root_level": logging.getLevelName(root.level),
            "handlers": handlers_info,
            "queue_size": log_queue.qsize(),
            "task_running": task_status["running"],
            "log_count": len(task_status["logs"]),
            "qh_in_root": qh in root.handlers
        }
    })

@app.route("/api/debug/test-log")
def api_debug_test_log():
    """写入一条测试日志"""
    logger.info(">>> 这是一条测试日志 - 如果你能看到说明 QueueHandler 工作正常")
    root = logging.getLogger()
    # 也尝试直接用 root 写
    logging.info(">>> Root logger 测试日志")
    return jsonify({
        "code": 0,
        "message": "测试日志已写入，请检查 /api/status",
        "qh_in_root": qh in root.handlers,
        "handlers": [type(h).__name__ for h in root.handlers]
    })


# ====== 启动 ======
def main():
    import argparse
    p = argparse.ArgumentParser(description="JA_Stock Web 控制台")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()

    os.makedirs(os.path.join(BASE_DIR, "web", "templates"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "web", "static"), exist_ok=True)

    print("\n========================================")
    print("  JA_Stock Web Console v2")
    print("  http://localhost:" + str(args.port))
    print("========================================\n")

    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()