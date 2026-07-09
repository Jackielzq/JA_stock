// JA_Stock Web Console - Frontend Logic v2

var currentPage = "home";
var currentSubTab = "email-config";
var currentReportType = 'review';
var reportData = null;
var statusPollTimer = null;

// ==================== Page Switching ====================

function switchPage(page) {
    currentPage = page;

    // Update main nav
    document.querySelectorAll(".nav-item").forEach(function (item) {
        item.classList.toggle("active", item.dataset.page === page);
    });

    // Toggle pages
    document.querySelectorAll(".page").forEach(function (p) {
        p.classList.remove("active");
    });
    var target = document.getElementById("page-" + page);
    if (target) {
        target.classList.add("active");
    }

    // Load data
    if (page === "config") {
        loadSubData(currentSubTab);
    }
    if (page === 'reports') { loadReports(); }

    // Polling
    if (page === "home") {
        startStatusPolling();
    } else {
        stopStatusPolling();
    }
}

function switchSubTab(sub) {
    currentSubTab = sub;

    // Update config nav
    document.querySelectorAll(".cfg-nav-item").forEach(function (item) {
        item.classList.toggle("active", item.dataset.sub === sub);
    });

    // Toggle sub pages
    document.querySelectorAll(".sub-page").forEach(function (sp) {
        sp.classList.remove("active");
    });
    var el = document.getElementById("sub-" + sub);
    if (el) {
        el.classList.add("active");
    }

    loadSubData(sub);
}

function loadSubData(sub) {
    if (sub === "email-config")       loadEmailConfig();
    if (sub === "recipients-config")  loadRecipients();
    if (sub === "llm-config")         loadLLMConfig();
    if (sub === "monitor-stocks")     loadMonitorStocks();
    if (sub === "monitor-concepts")   loadMonitorConcepts();
    if (sub === "github-config")      loadGithubConfig();
    if (sub === "db-config")          loadDbConfig();
    if (sub === "tushare-config")     loadTushareConfig();
}

// ==================== Toast ====================

function showToast(message, type) {
    type = type || "success";
    var toast = document.getElementById("toast");
    toast.textContent = message;
    toast.className = "toast " + type + " show";
    setTimeout(function () {
        toast.className = "toast";
    }, 2500);
}

// ==================== Helpers ====================

function byId(id) { return document.getElementById(id); }
function escHtml(text) { var d = document.createElement("div"); d.textContent = text; return d.innerHTML; }

// ==================== Task Execution ====================

function runTask(mode) {
    var taskNames = {
        update: "数据更新", review: "每日复盘", news: "新闻筛选",
        monitor: "监控报告", select: "量化选股", email: "发送邮件",
        deploy: "Hexo部署", github: "GitHub发布", full: "全流程"
    };

    setButtonsDisabled(true);
    byId("status-text").textContent = "执行中: " + (taskNames[mode] || mode);

    fetch("/api/run/" + mode, { method: "POST" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.code === 0) {
                showToast("任务已启动: " + (taskNames[mode] || mode));
                startStatusPolling();
            } else {
                showToast(data.message || "启动失败", "error");
                setButtonsDisabled(false);
                resetStatus();
            }
        })
        .catch(function (e) {
            showToast("请求失败: " + e.message, "error");
            setButtonsDisabled(false);
            resetStatus();
        });
}

function setButtonsDisabled(disabled) {
    document.querySelectorAll(".cmd-card, .btn-full").forEach(function (btn) {
        btn.disabled = disabled;
    });
}

function resetStatus() {
    byId("status-text").textContent = "就绪";
}

// ==================== Status Polling ====================

function startStatusPolling() {
    stopStatusPolling();
    pollStatus();
    statusPollTimer = setInterval(pollStatus, 2000);
}

function stopStatusPolling() {
    if (statusPollTimer) {
        clearInterval(statusPollTimer);
        statusPollTimer = null;
    }
}

// === Enhanced pollStatus with elapsed time ===
function pollStatus() {
    fetch("/api/status")
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.code !== 0) return;
            var s = data.data;
            var dot = document.querySelector(".status-dot");
            var txt = byId("status-text");
            var tm = byId("status-time");
            var taskNames = {
                update: "数据更新", review: "每日复盘", factor: "因子计算",
                monitor: "监控报告", select: "量化选股", email: "发送邮件",
                deploy: "Hexo部署", github: "GitHub发布", full: "全流程"
            };

            if (s.running) {
                dot.className = "status-dot running";
                var taskLabel = taskNames[s.task] || s.task;
                txt.textContent = "执行中: " + taskLabel + (s.elapsed ? " (已用时 " + s.elapsed + ")" : "");
            } else if (s.result === "error") {
                dot.className = "status-dot error";
                txt.textContent = "上次任务失败" + (s.elapsed ? " (耗时 " + s.elapsed + ")" : "");
            } else if (s.result === "success") {
                dot.className = "status-dot idle";
                txt.textContent = "上一次执行成功" + (s.elapsed ? " (耗时 " + s.elapsed + ")" : "");
            } else {
                dot.className = "status-dot idle";
                txt.textContent = "就绪";
            }

            // Always render log area - show state even when empty
            if (s.logs) {
                renderLogs(s.logs, s.running, s.task, s.elapsed);
            } else if (s.running) {
                renderLogs([], s.running, s.task, s.elapsed);
            }
            if (!s.running) {
                setButtonsDisabled(false);
            }
            if (tm) {
                tm.textContent = new Date().toLocaleTimeString("zh-CN");
            }
        })
        .catch(function () {});
}





// === Enhanced renderLogs with header + timing ===
function renderLogs(logs, running, task, elapsed) {
    var el = byId("log-output");
    if (!el) return;

    var taskNames = {
        update: "数据更新", review: "每日复盘", factor: "因子计算",
        monitor: "监控报告", select: "量化选股", email: "发送邮件",
        deploy: "Hexo部署", github: "GitHub发布", full: "全流程"
    };

    var html = "";
    if (task) {
        var taskLabel = taskNames[task] || task;
        html += '<div class="log-line header">=== ' + taskLabel + (running ? " - 运行中" : " - 已完成") + (elapsed ? " [" + elapsed + "]" : "") + ' ===</div>';
    }

    if (logs.length === 0 && running) {
        html += '<div class="log-line info">等待日志输出中...</div>';
    }

    html += logs.map(function (line) {
        var cls = "log-line";
        if (line.indexOf("ERROR") !== -1 || line.indexOf("错误") !== -1 || line.indexOf("失败") !== -1) {
            cls += " error";
        } else if (line.indexOf("WARNING") !== -1 || line.indexOf("警告") !== -1) {
            cls += " warn";
        } else if (line.indexOf("INFO") !== -1 || line.indexOf("成功") !== -1 || line.indexOf("完成") !== -1 || line.indexOf("全部") !== -1) {
            cls += " success";
        }
        return '<div class="' + cls + '">' + escHtml(line) + "</div>";
    }).join("");

    el.innerHTML = html;
    el.scrollTop = el.scrollHeight;
}

// === Enhanced clearLogs ===
function clearLogs() {
    var el = byId("log-output");
    if (el) {
        el.innerHTML = '<div class="log-empty">等待任务执行...</div>';
    }
    var dot = document.querySelector(".status-dot");
    if (dot) dot.className = "status-dot idle";
    var txt = byId("status-text");
    if (txt) txt.textContent = "就绪";
    var tm = byId("status-time");
    if (tm) tm.textContent = new Date().toLocaleTimeString("zh-CN");
}

// ==================== Email Config ====================

function loadEmailConfig() {
    fetch("/api/config/email")
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (d.code !== 0) return;
            var c = d.data;
            byId("email-smtp-server").value = c.smtp_server || "";
            byId("email-smtp-port").value = c.smtp_port || 465;
            byId("email-sender").value = c.sender || "";
            byId("email-password").value = c.password || "";
            byId("email-subject-prefix").value = c.subject_prefix || "";
        });
}

// ==================== Recipients ====================

function loadRecipients() {
    fetch("/api/config/recipients")
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (d.code !== 0) return;
            renderRecipientsTable(d.data);
        });
}

function renderRecipientsTable(list) {
    var tbody = document.querySelector("#recipients-table tbody");
    if (!list || list.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-cell">暂无收件人</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(function (r, i) {
        return '<tr class="draggable" draggable="true">' +
            '<td class="drag-handle"><span class="grip">&vellip;</span></td>' +
            '<td class="col-email">' + escHtml(r.email) + '</td>' +
            '<td class="col-remark"><span class="remark-display" id="r-remark-' + i + '">' + escHtml(r.remark || "-") + '</span></td>' +
            '<td class="col-status"><label class="toggle-switch"><input type="checkbox" ' + (r.enabled ? "checked" : "") + ' onchange="toggleRecipient(\'' + escHtml(r.email) + '\',this.checked)"><span class="toggle-slider"></span></label></td>' +
            '<td class="col-actions"><div class="table-actions">' +
            '<button class="btn-table-edit" onclick="editRecipientRemark(\'' + escHtml(r.email) + '\',' + i + ')">编辑</button>' +
            '<button class="btn-table-del" onclick="removeRecipient(\'' + escHtml(r.email) + '\')">删除</button></div></td>' +
            '</tr>';
    }).join("");
    addDragListeners(tbody, "recipients");
}

function addRecipient() {
    var email = byId("new-recipient").value.trim();
    var remark = byId("new-recipient-remark").value.trim();
    if (!email) { showToast("请输入邮箱地址", "error"); return; }

    fetch("/api/config/recipients", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email, remark: remark })
    }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.code === 0) {
            byId("new-recipient").value = "";
            byId("new-recipient-remark").value = "";
            loadRecipients();
            showToast(d.message);
        } else {
            showToast(d.message, "error");
        }
    });
}

function editRecipientRemark(email, idx) {
    var span = byId("r-remark-" + idx);
    var old = span.textContent === "-" ? "" : span.textContent;
    span.innerHTML = '<input class="inline-edit" value="' + escHtml(old) + '" onblur="saveRecipientRemark(\'' + escHtml(email) + '\',' + idx + ',this.value)" onkeydown="if(event.key===\'Enter\')this.blur()" autofocus>';
    span.querySelector("input").focus();
}

function saveRecipientRemark(email, idx, val) {
    var span = byId("r-remark-" + idx);
    if (!span) return;
    fetch("/api/config/recipients/" + encodeURIComponent(email), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ remark: val })
    }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.code === 0) loadRecipients(); else showToast(d.message, "error");
    });
}

function removeRecipient(email) {
    fetch("/api/config/recipients", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email })
    }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.code === 0) { loadRecipients(); showToast(d.message); }
        else showToast(d.message, "error");
    });
}

function toggleRecipient(email, enabled) {
    fetch("/api/config/recipients/" + encodeURIComponent(email), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: enabled })
    }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.code !== 0) showToast(d.message, "error");
    });
}

function toggleAllRecipients(enabled) {
    fetch("/api/config/recipients/toggle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: enabled })
    }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.code === 0) { loadRecipients(); showToast(d.message); }
        else showToast(d.message, "error");
    });
}

// ==================== LLM Config ====================

function loadLLMConfig() {
    fetch("/api/config/llm")
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (d.code !== 0) return;
            var c = d.data;
            byId("llm-api-key").value = c.api_key || "";
            byId("llm-base-url").value = c.base_url || "";
            byId("llm-model").value = c.model || "";
            byId("llm-temperature").value = c.temperature || 1.0;
        });
}

// ==================== GitHub Config ====================

function loadGithubConfig() {
    fetch("/api/config/github")
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (d.code !== 0) return;
            var c = d.data;
            byId("github-token").value = c.token || "";
            byId("github-owner").value = c.owner || "";
            byId("github-repo").value = c.repo || "";
            byId("github-branch").value = c.branch || "main";
            byId("github-target-path").value = c.target_path || "";
        });
}

// ==================== DB Config ====================

function loadDbConfig() {
    fetch("/api/config/db")
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (d.code !== 0) return;
            var c = d.data;
            byId("db-host").value = c.host || "";
            byId("db-port").value = c.port || 3306;
            byId("db-user").value = c.user || "";
            byId("db-password").value = c.password || "";
            byId("db-database").value = c.database || "";
            byId("db-charset").value = c.charset || "utf8mb4";
        });
}

// ==================== Tushare Config ====================

function loadTushareConfig() {
    fetch("/api/config")
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (d.code === 0) {
                byId("tushare-token").value = d.data.tushare_token || "";
            }
        });
}

// ==================== Monitor Stocks ====================

function loadMonitorStocks() {
    fetch("/api/config/monitor")
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (d.code !== 0) return;
            renderStocksTable(d.data.stocks || []);
        });
}

function renderStocksTable(stocks) {
    var tbody = document.querySelector("#stocks-table tbody");
    if (!stocks || stocks.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-cell">暂无监控股票</td></tr>';
        return;
    }
    tbody.innerHTML = stocks.map(function (s, i) {
        return '<tr class="draggable" draggable="true" id="stock-row-' + i + '">' +
            '<td class="drag-handle"><span class="grip">&vellip;</span></td>' +
            '<td class="col-code">' + escHtml(s.code) + '</td>' +
            '<td class="col-name">' + escHtml(s.name) + '</td>' +
            '<td class="col-stock-remark" id="stock-remark-' + i + '"><span class="remark-display">' + escHtml(s.remark || "-") + '</span></td>' +
            '<td class="col-stock-actions"><div class="table-actions">' +
            '<button class="btn-table-edit" onclick="editStockRemark(\'' + escHtml(s.code) + '\',' + i + ')">编辑</button>' +
            '<button class="btn-table-del" onclick="removeStock(\'' + escHtml(s.code) + '\')">删除</button></div></td>' +
            '</tr>';
    }).join("");
    addDragListeners(tbody, "stocks");
}

function addMonitorStock() {
    var code = byId("new-stock-code").value.trim();
    var name = byId("new-stock-name").value.trim();
    var remark = byId("new-stock-remark").value.trim();
    if (!code) { showToast("请输入股票代码", "error"); return; }

    fetch("/api/config/monitor/stock", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code, name: name, remark: remark })
    }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.code === 0) {
            byId("new-stock-code").value = "";
            byId("new-stock-name").value = "";
            byId("new-stock-remark").value = "";
            loadMonitorStocks();
            showToast(d.message);
        } else {
            showToast(d.message, "error");
        }
    });
}

function removeStock(code) {
    fetch("/api/config/monitor/stock/" + encodeURIComponent(code), { method: "DELETE" })
        .then(function (r) { return r.json(); }).then(function (d) {
            if (d.code === 0) { loadMonitorStocks(); showToast(d.message); }
            else showToast(d.message, "error");
        });
}

function editStockRemark(code, idx) {
    var td = byId("stock-remark-" + idx);
    var span = td.querySelector(".remark-display");
    var old = span ? (span.textContent === "-" ? "" : span.textContent) : "";
    td.innerHTML = '<input class="inline-edit" value="' + escHtml(old) + '" onblur="saveStockRemark(\'' + escHtml(code) + '\',' + idx + ',this.value)" onkeydown="if(event.key===\'Enter\')this.blur()" autofocus>';
    td.querySelector("input").focus();
}

function saveStockRemark(code, idx, val) {
    fetch("/api/config/monitor/stock/" + encodeURIComponent(code), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ remark: val })
    }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.code === 0) loadMonitorStocks(); else showToast(d.message, "error");
    });
}

function importMonitorExcel() {
    byId("modal-import-excel").classList.add("show");
}

function doImportExcel() {
    var fileInput = byId("import-excel-file");
    var file = fileInput.files[0];
    if (!file) { showToast("请选择Excel文件", "error"); return; }
    if (!file.name.match(/\.xlsx?$/i)) { showToast("请上传 .xlsx 或 .xls 文件", "error"); return; }

    var formData = new FormData();
    formData.append("file", file);

    fetch("/api/config/monitor/import-excel", {
        method: "POST",
        body: formData
    }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.code === 0) {
            closeModal("modal-import-excel");
            fileInput.value = "";
            loadMonitorStocks();
            loadMonitorConcepts();
            showToast(d.message);
        } else {
            showToast(d.message, "error");
        }
    }).catch(function (e) {
        showToast("导入失败: " + e.message, "error");
    });
}

// ==================== Monitor Concepts ====================

function loadMonitorConcepts() {
    fetch("/api/config/monitor")
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (d.code !== 0) return;
            renderConceptsTable(d.data.concepts || []);
        });
}

function renderConceptsTable(concepts) {
    var tbody = document.querySelector("#concepts-table tbody");
    if (!concepts || concepts.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="empty-cell">暂无监控概念</td></tr>';
        return;
    }
    tbody.innerHTML = concepts.map(function (c) {
        return '<tr class="draggable" draggable="true">' +
            '<td class="drag-handle"><span class="grip">&vellip;</span></td>' +
            '<td class="col-concept-name">' + escHtml(c.name) + '</td>' +
            '<td class="col-concept-actions"><div class="table-actions">' +
            '<button class="btn-table-del" onclick="removeConcept(\'' + escHtml(c.name) + '\')">删除</button></div></td>' +
            '</tr>';
    }).join("");
    addDragListeners(tbody, "concepts");
}

function addMonitorConcept() {
    var name = byId("new-concept").value.trim();
    if (!name) { showToast("请输入概念名称", "error"); return; }

    fetch("/api/config/monitor/concept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name })
    }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.code === 0) {
            byId("new-concept").value = "";
            loadMonitorConcepts();
            showToast(d.message);
        } else {
            showToast(d.message, "error");
        }
    });
}

function removeConcept(name) {
    fetch("/api/config/monitor/concept/" + encodeURIComponent(name), { method: "DELETE" })
        .then(function (r) { return r.json(); }).then(function (d) {
            if (d.code === 0) { loadMonitorConcepts(); showToast(d.message); }
            else showToast(d.message, "error");
        });
}



function exportMonitorExcel() {
    fetch("/api/config/monitor/export-excel")
        .then(function (r) { return r.blob(); })
        .then(function (blob) {
            var a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = "monitor_pool_export.xlsx";
            a.click();
        })
        .catch(function () {
            window.open("/api/config/monitor/export-excel", "_blank");
        });
}

// ==================== Drag and Drop ====================

var dragSrcRow = null;
var dragType = null;

function handleDragStart(e, type) {
    dragSrcRow = this;
    dragType = type;
    this.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
}

function handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    return false;
}

function handleDragEnter() {
    this.classList.add("drag-over");
}

function handleDragLeave() {
    this.classList.remove("drag-over");
}

function handleDrop(e, type) {
    e.stopPropagation();
    this.classList.remove("drag-over");
    if (dragSrcRow !== this && dragType === type) {
        var tbody = this.parentNode;
        var rows = Array.from(tbody.querySelectorAll("tr.draggable"));
        var srcIdx = rows.indexOf(dragSrcRow);
        var dstIdx = rows.indexOf(this);
        if (srcIdx < dstIdx) {
            tbody.insertBefore(dragSrcRow, this.nextSibling);
        } else {
            tbody.insertBefore(dragSrcRow, this);
        }
        saveOrder(type);
    }
    return false;
}

function handleDragEnd() {
    this.classList.remove("dragging");
    document.querySelectorAll(".drag-over").forEach(function (r) { r.classList.remove("drag-over"); });
}

function addDragListeners(tbody, type) {
    tbody.querySelectorAll("tr.draggable").forEach(function (row) {
        row.addEventListener("dragstart", function (e) { handleDragStart.call(this, e, type); });
        row.addEventListener("dragover", handleDragOver);
        row.addEventListener("dragenter", handleDragEnter);
        row.addEventListener("dragleave", handleDragLeave);
        row.addEventListener("drop", function (e) { handleDrop.call(this, e, type); });
        row.addEventListener("dragend", handleDragEnd);
    });
}

function saveOrder(type) {
    var tbody, apiUrl;
    if (type === "recipients") {
        tbody = document.querySelector("#recipients-table tbody");
        apiUrl = "/api/config/recipients/reorder";
    } else if (type === "stocks") {
        tbody = document.querySelector("#stocks-table tbody");
        apiUrl = "/api/config/monitor/stocks/reorder";
    } else if (type === "concepts") {
        tbody = document.querySelector("#concepts-table tbody");
        apiUrl = "/api/config/monitor/concepts/reorder";
    }
    if (!tbody || !apiUrl) return;

    var items = [];
    tbody.querySelectorAll("tr.draggable").forEach(function (row) {
        if (type === "recipients") {
            var emailEl = row.querySelector(".col-email");
            var remarkEl = row.querySelector(".remark-display");
            var checkbox = row.querySelector("input[type=checkbox]");
            items.push({
                email: emailEl ? emailEl.textContent : "",
                remark: remarkEl ? remarkEl.textContent : "",
                enabled: checkbox ? checkbox.checked : true
            });
        } else if (type === "stocks") {
            var codeEl = row.querySelector(".col-code");
            var nameEl = row.querySelector(".col-name");
            var remarkEl = row.querySelector(".col-stock-remark .remark-display");
            items.push({
                code: codeEl ? codeEl.textContent : "",
                name: nameEl ? nameEl.textContent : "",
                remark: remarkEl ? remarkEl.textContent : ""
            });
        } else if (type === "concepts") {
            var nameEl = row.querySelector(".col-concept-name");
            items.push({ name: nameEl ? nameEl.textContent : "" });
        }
    });

    fetch(apiUrl, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(items)
    }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.code === 0) showToast("顺序已更新");
    });
}




// ==================== Reports ====================

function loadReports() {
    fetch("/api/reports")
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (d.code !== 0) return;
            reportData = d.data;
            switchReportType("review");
        });
}

function switchReportType(rtype) {
    currentReportType = rtype;
    document.querySelectorAll(".report-tab").forEach(function (t) {
        t.classList.toggle("active", t.dataset.rtype === rtype);
    });

    var sel = byId("report-date-select");
    sel.innerHTML = "";

    var items = (reportData && reportData[rtype]) ? reportData[rtype] : [];
    if (items.length === 0) {
        sel.innerHTML = '<option value="">暂无报告</option>';
        byId("report-frame").src = "";
        return;
    }

    items.forEach(function (item, i) {
        var opt = document.createElement("option");
        opt.value = item.filename;
        opt.textContent = item.date.substring(0, 4) + "-" + item.date.substring(4, 6) + "-" + item.date.substring(6, 8);
        if (i === 0) opt.selected = true;
        sel.appendChild(opt);
    });

    loadSelectedReport();
}

function loadSelectedReport() {
    var sel = byId("report-date-select");
    var filename = sel.value;
    if (!filename) { byId("report-frame").src = ""; return; }
    byId("report-frame").src = "/reports/" + currentReportType + "/" + filename;
}

function openReportNewTab() {
    var sel = byId("report-date-select");
    var filename = sel.value;
    if (!filename) return;
    window.open("/reports/" + currentReportType + "/" + filename, "_blank");
}
// ==================== Modal ====================

function closeModal(id) {
    byId(id).classList.remove("show");
}

// ==================== Form Submit Handlers ====================

document.addEventListener("DOMContentLoaded", function () {
    // Email form
    var emailForm = byId("email-form");
    if (emailForm) {
        emailForm.addEventListener("submit", function (e) {
            e.preventDefault();
            var data = {
                smtp_server: byId("email-smtp-server").value,
                smtp_port: parseInt(byId("email-smtp-port").value) || 465,
                sender: byId("email-sender").value,
                password: byId("email-password").value,
                subject_prefix: byId("email-subject-prefix").value
            };
            fetch("/api/config/email", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            }).then(function (r) { return r.json(); }).then(function (r) { showToast(r.message || "已保存"); });
        });
    }

    // LLM form
    var llmForm = byId("llm-form");
    if (llmForm) {
        llmForm.addEventListener("submit", function (e) {
            e.preventDefault();
            var data = {
                api_key: byId("llm-api-key").value,
                base_url: byId("llm-base-url").value,
                model: byId("llm-model").value,
                temperature: parseFloat(byId("llm-temperature").value) || 1.0
            };
            fetch("/api/config/llm", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            }).then(function (r) { return r.json(); }).then(function (r) { showToast(r.message || "已保存"); });
        });
    }

    // GitHub form
    var githubForm = byId("github-form");
    if (githubForm) {
        githubForm.addEventListener("submit", function (e) {
            e.preventDefault();
            var data = {
                token: byId("github-token").value,
                owner: byId("github-owner").value,
                repo: byId("github-repo").value,
                branch: byId("github-branch").value,
                target_path: byId("github-target-path").value
            };
            fetch("/api/config/github", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            }).then(function (r) { return r.json(); }).then(function (r) { showToast(r.message || "已保存"); });
        });
    }

    // DB form
    var dbForm = byId("db-form");
    if (dbForm) {
        dbForm.addEventListener("submit", function (e) {
            e.preventDefault();
            var data = {
                host: byId("db-host").value,
                port: parseInt(byId("db-port").value) || 3306,
                user: byId("db-user").value,
                password: byId("db-password").value,
                database: byId("db-database").value,
                charset: byId("db-charset").value
            };
            fetch("/api/config/db", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            }).then(function (r) { return r.json(); }).then(function (r) { showToast("数据库配置已保存"); });
        });
    }

    // Tushare form
    var tushareForm = byId("tushare-form");
    if (tushareForm) {
        tushareForm.addEventListener("submit", function (e) {
            e.preventDefault();
            fetch("/api/config/tushare", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ token: byId("tushare-token").value })
            }).then(function (r) { return r.json(); }).then(function (r) { showToast(r.message || "已保存"); });
        });
    }

    // Start polling on load
    startStatusPolling();
});

window.addEventListener("beforeunload", function () {
    stopStatusPolling();
});