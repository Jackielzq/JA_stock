# JA_Stock股票分析

## 项目来源

做为一个全球最大多人在线竞技平台的玩家之一，之前每天会花相当的时间进行复盘，但是当复盘到一定阶段时渐渐形成了相对固定的模式，于是想着将这个模式固定下来，然后通过代码每天自动执行以节约大量的数据整理时间，因此就有了本项目的想法，主要解决以下问题：

- **平台条件限制**，不管是同花顺的问财、还是通达信的公式或者条件选股，还是有一些局限性，当自己有一些非常灵活的条件时，就必须有一套非常靠谱的源数据来满足自己各种指标的计算
- **内容分散**，大盘、板块、个股、异动、新闻、选股策略、效果回测等，可以通过一个简单的HTML页面将内容快速呈现
- **便于分享**，可以支持将这些结果快速通过邮件、在线部署等方式分享给相关的朋友

这个项目不是通用 Web 平台，而是一个以 **Python 脚本调度 + 数据库驱动 + HTML/PDF 报告输出** 为核心的分析工作流；同时提供 **Web 控制台** 来简化日常操作和配置管理。

## 核心能力

- **数据更新**
  - 支持历史初始化和日常增量更新
  - 覆盖个股日线、行业数据、概念映射、概念日线等表

- **市场因子与情绪分析**
  - 计算市场情绪、涨跌结构、板块强弱、概念热度等复盘指标
  - 支持个股总分与概念拼接回写

- **选股与回测**
  - 支持多策略选股（什么乱七八糟的都能算，全自己定义）
  - 输出选股结果页面与历史回测页面

- **新闻智能筛选**
  - 抓取财联社快讯
  - 使用大模型做重点新闻筛选与摘要（此处用的Deepseek）

- **监控池管理**
  - 从 Excel 维护股票池和概念池
  - 根据Excel中重点监控的个股和概念生成单独的监控页

- **Web 控制台（v2 新增）**
  - 可视化操作界面，一键执行所有分析流程
  - 在线管理数据库、邮件、LLM、GitHub 等全部配置
  - 可视化编辑监控池（股票/概念增删改查、批量导入、拖拽排序）
  - 在线浏览历史报告（复盘、选股、回测、监控）
  - 实时日志输出，任务状态一目了然

- **结果发布**
  - 输出 HTML / PDF
  - 支持邮件发送
  - 支持 GitHub 静态页面发布
  - 支持同步到 Hexo（目前用的Hexo静态博客）

## 架构示意

结构图见：

- [architecture_diagram.svg](output/architecture_diagram.svg)

主流程可以概括为：

```text
Tushare / 财联社 / Excel
        ↓
   core + services
        ↓
      MySQL
        ↓
因子计算 / 选股 / 监控 / 新闻分析
        ↓
  report/templates 渲染
        ↓
  HTML / PDF / GitHub / Hexo / Email
        ↓
    Web 控制台（在线查看 & 操作）
```

## 当前目录结构

```text
├─ config/                 # 配置入口与本地配置模板
│  ├─ __init__.py          #   运行入口，根据环境加载对应配置
│  ├─ settings.example.py  #   配置示例模板
│  ├─ settings.local.py    #   本地真实配置（已 gitignore）
│  └─ web_settings.json    #   Web 控制台统一配置（已 gitignore）
├─ core/                   # 核心业务逻辑
│  ├─ db_engine.py         #   数据库连接、查询、Tushare API
│  ├─ data_updater.py      #   数据初始化与增量更新
│  ├─ factor_calculator.py #   市场因子、情绪评分、总分回写
│  ├─ strategies.py        #   选股策略、回测逻辑
│  └─ monitor_manager.py   #   监控池数据聚合
├─ services/               # 邮件、GitHub、Hexo、新闻等外部服务
│  ├─ news_service.py      #   财联社新闻抓取 + 大模型筛选
│  ├─ mail_service.py      #   报告邮件发送
│  ├─ pdf_service.py       #   HTML 转 PDF
│  ├─ github_service.py    #   静态页面发布到 GitHub
│  └─ deploy_hexo.py       #   报告同步到 Hexo
├─ report/                 # Jinja2 渲染与 HTML 模板
│  ├─ renderer.py          #   模板渲染器
│  └─ templates/           #   各类报告 HTML 模板
├─ utils/                  # 工具代码
│  └─ market_utils.py      #   市场工具函数
├─ web/                    # Web 控制台前端
│  ├─ templates/
│  │  └─ index.html        #   SPA 主页面
│  └─ static/
│     ├─ app.js            #   前端逻辑
│     └─ style.css         #   样式
├─ data/
│  ├─ input/
│  │  ├─ monitor_pool.sample.xlsx
│  │  └─ monitor_pool.local.xlsx   # 真实监控池（已 gitignore）
│  └─ user_only/                   # 个人数据（已 gitignore）
├─ output/
│  ├─ daily_review/
│  ├─ daily_news/
│  ├─ monitor/
│  ├─ stock_selection/
│  ├─ backtest/
│  ├─ pdf/
│  ├─ img/
│  └─ architecture_diagram.svg
├─ main.py                  # 命令行入口
├─ web_server.py            # Web 服务端（Flask）
├─ web_config_manager.py    # Web 配置管理器
├─ 启动Web控制台.bat         # Windows 一键启动脚本
├─ requirements.txt
└─ .gitignore
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置本地参数

当前配置入口已经切换到 `config/` 目录：

- 运行入口：`config/__init__.py`
- 示例模板：`config/settings.example.py`
- 本地真实配置：`config/settings.local.py`
- Web 配置存储：`config/web_settings.json`（首次启动 Web 控制台自动从 `settings.local.py` 迁移生成）

你需要准备的本地能力包括：

- MySQL （本地需要建一个数据库）
- Tushare Token  （免费的数据源都不太靠谱，建议花个200买个最低档的Tushare token，省很多事）
- 邮件 SMTP 配置
- 大模型 API Key  /目前我只用了deepseek，主打一个便宜，几块钱用半年
- 如需发布：GitHub Token / Hexo 本地路径

### 3. 准备监控池文件

- `data/input/monitor_pool.local.xlsx`
  - 配置你关注的股票以及概念，monitor方法会对这个清单的内容做一个针对性的分析报告

## Web 控制台（推荐）

Web 控制台提供了比命令行更方便的操作方式，适合日常使用。

### 启动方式

**Windows（一键启动）：**
双击根目录的 `启动Web控制台.bat`，会自动启动服务并打开浏览器。

**手动启动：**
```bash
python web_server.py --port 5000
```

然后访问 `http://localhost:5000`

**启动参数：**
- `--port`：服务端口，默认 5000
- `--host`：监听地址，默认 0.0.0.0（允许局域网访问）
- `--debug`：开启 Flask 调试模式

### 控制台功能

| 页面 | 功能 |
|------|------|
| **首页控制台** | 一键执行：数据更新、每日复盘、监控报告、量化选股、发送邮件、Hexo部署、GitHub发布、全流程自动化 |
| **配置页** | 可视化编辑数据库、Tushare、邮件、LLM、GitHub 等配置；管理收件人列表（增删改查、启停、排序） |
| **监控池** | 股票/概念的增删改查、批量导入、拖拽排序、导出 |
| **报告页** | 在线浏览复盘、选股、回测、监控等历史 HTML 报告 |

### 首次启动说明

首次启动时，系统会自动检测 `config/web_settings.json` 是否存在：
- 如果不存在，自动从 `config/settings.local.py` 和 `data/input/monitor_pool.local.xlsx` 迁移数据
- 之后所有配置在 Web 界面修改即可，无需手动编辑文件

> **注意**：`web_settings.json` 包含敏感信息，已加入 `.gitignore`，不会被提交到 GitHub。

## 常用运行模式

### 全量初始化

```bash
python main.py --mode init
```

适合首次建库或重建历史数据时使用。

### 增量更新

```bash
python main.py --mode update
```

更新完成后会自动执行基础因子计算和评分回写。

如果需要补最近多天：

```bash
python main.py --mode update --days 3
```

### 手动重算基础因子

```bash
python main.py --mode factor
```

适合不重新拉取数据、只想重算总分和概念拼接时使用。（正常情况忽略）

### 生成每日复盘

```bash
python main.py --mode review
```

输出每日复盘报告（包含大盘、情绪、连板情况、板块、概念、策略选股、异动监控等）：

- `output/daily_review/`
- `output/pdf/`

### 生成新闻报告

```bash
python main.py --mode news
```

输出：

- `output/daily_news/`

### 生成选股和回测

```bash
python main.py --mode select
```

做review中已经有，此处可单独用作选股策略的实验

输出：

- `output/stock_selection/`
- `output/backtest/`

### 生成监控池报告

```bash
python main.py --mode monitor
```

输出监控池中个股及概念的评估结果：

- `output/monitor/`

### 发送邮件

```bash
python main.py --mode email
```

把生成的每日复盘pdf文件发送给config中配置的收件人列表

### 同步到 Hexo

```bash
python main.py --mode deploy
```

会同步：

- 复盘页
- 新闻页
- 监控页

### 发布到 GitHub 静态页仓库

```bash
python main.py --mode github
```

注意：

- 当前 `github` 模式只负责 **静态页面发布**
- 不负责 **源码仓库同步**

### 历史修复 / 概念更新

```bash
python main.py --mode fix
python main.py --mode concept
```

## 输出目录说明

当前各类产物已经按类型拆分：

- `output/daily_review/`
  - 每日复盘 HTML

- `output/pdf/`
  - 每日复盘 PDF

- `output/daily_news/`
  - 每日新闻 HTML

- `output/monitor/`
  - 监控池 HTML

- `output/stock_selection/`
  - 选股结果 HTML

- `output/backtest/`
  - 回测报告 HTML

## 主要模块说明

### `core/`

- `db_engine.py`
  - 数据库连接、查询、Tushare API 初始化

- `data_updater.py`
  - 历史初始化、增量更新、行业和概念数据维护

- `factor_calculator.py`
  - 市场因子、情绪评分、行业概念分析、个股总分回写

- `strategies.py`
  - 选股策略执行、结果增强、回测逻辑

- `monitor_manager.py`
  - 监控池 Excel 同步、监控页数据聚合

### `services/`

- `news_service.py`
  - 财联社新闻抓取 + 大模型筛选

- `mail_service.py`
  - 报告邮件发送

- `pdf_service.py`
  - HTML 转 PDF

- `github_service.py`
  - 静态页面发布到 GitHub

- `deploy_hexo.py`
  - 报告同步到 Hexo

### `report/`

- `renderer.py`
  - Jinja2 模板渲染器

- `templates/`
  - `daily_review.html`
  - `daily_news.html`
  - `stock_selection.html`
  - `back_test.html`
  - `monitor.html`

### `web/`（v2 新增）

- `web_server.py`
  - Flask Web 服务端，提供 REST API 和前端页面
  - 支持一键执行所有分析模式（update / review / select / monitor / email / deploy / github / full）
  - 实时 SSE 日志流，任务状态追踪
  - 在线报告浏览服务

- `web_config_manager.py`
  - 统一配置管理器，以 `config/web_settings.json` 为单一数据源
  - 支持数据迁移（从旧 `settings.local.py` + Excel → JSON）
  - 线程安全的读写操作

- `web/templates/index.html`
  - 单页应用（SPA）主页面
  - 首页控制台、配置管理、监控池编辑、报告浏览四大模块

- `web/static/`
  - `app.js`：前端交互逻辑（页面切换、配置CRUD、任务调度、报告加载）
  - `style.css`：Apple 风格 UI 样式

## 适合的使用方式

这个项目更适合下面这种节奏：

**命令行方式：**

1. 收盘后执行 `update` （Tushare数据源，每日16:00以后获取）
2. 生成 `review / news / monitor`
3. 需要时运行 `select`
4. 最后根据需要执行：
   - `email`
   - `deploy`
   - `github`

**Web 控制台方式（推荐日常使用）：**

1. 启动 `python web_server.py --port 5000`
2. 在首页控制台按需点击执行按钮
3. 在报告页查看生成结果
4. 如有配置变更，直接在配置页修改保存

## 配置文件说明

| 文件 | 用途 | 是否上传 |
|------|------|----------|
| `config/settings.example.py` | 配置模板，供参考 | ✅ 上传 |
| `config/settings.local.py` | 命令行模式真实配置 | ❌ gitignore |
| `config/web_settings.json` | Web 控制台统一配置 | ❌ gitignore |
| `data/input/monitor_pool.sample.xlsx` | 监控池示例 | ✅ 上传 |
| `data/input/monitor_pool.local.xlsx` | 真实监控池 | ❌ gitignore |

## 注意事项

- Tushare 有频率限制，建议在非交易时段执行大批量更新
- 首次初始化数据量较大，建议预留足够时间和磁盘空间
- 本地配置和监控池真实文件不要提交到源码仓库
- `config/web_settings.json` 包含数据库密码、API Key 等敏感信息，请勿上传
- 历史回测结果仅供研究，不构成投资建议

## 仓库说明

当前仓库保存的是 **源码基线**，而不是运行产物仓库。

因此：

- 真实本地配置不会上传（`settings.local.py` / `web_settings.json`）
- 真实监控池 Excel 不会上传
- 日常生成的 HTML / PDF 报告不会上传

## ⚠️ 免责声明



本项目仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。作者不对使用本项目产生的任何损失负责。
