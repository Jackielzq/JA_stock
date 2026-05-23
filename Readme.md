# A股量化分析系统

这是一个面向 A 股日常复盘与量化分析的本地项目，覆盖数据更新、因子计算、选股回测、新闻分析、监控池跟踪，以及报告生成和静态页面发布。

当前版本已经完成一轮结构整理，配置、输入目录和输出目录都已经切换到新结构。

## 当前功能

- `init`：全量初始化历史数据
- `update`：增量更新行情、行业、概念数据
- `factor`：手动重算基础因子与总分
- `review`：生成每日复盘 HTML + PDF
- `news`：生成每日新闻分析报告
- `select`：生成选股结果和回测报告
- `monitor`：生成监控池报告
- `email`：发送复盘邮件
- `github`：上传静态复盘页到展示仓库
- `deploy`：同步复盘、新闻、监控页到 Hexo
- `fix`：修复历史数据
- `concept`：更新概念映射

## 功能概述

- 数据管理：从 Tushare 获取 A 股历史数据和增量数据
- 因子计算：计算情绪、行业、概念、总分等分析因子
- 策略选股：运行选股策略并生成回测结果
- 每日复盘：生成市场复盘 HTML 与 PDF
- 新闻分析：抓取财联社快讯并用大模型筛选重点新闻
- 监控池跟踪：基于 Excel 维护监控池并输出监控报告
- 自动发布：支持邮件发送、GitHub 静态页上传、Hexo 同步

## 当前目录结构

```text
Stock_data-V8/
├─ config/                 # 配置入口与本地配置模板
├─ core/                   # 核心业务逻辑
├─ services/               # 邮件、GitHub、Hexo、新闻等外部服务
├─ report/                 # Jinja2 渲染与 HTML 模板
├─ utils/                  # 工具代码
├─ data/
│  └─ input/
│     ├─ monitor_pool.sample.xlsx # 仓库模板文件
│     └─ monitor_pool.local.xlsx  # 本地真实文件（不上传）
├─ output/
│  ├─ daily_review/        # 每日复盘 HTML
│  ├─ daily_news/          # 每日新闻 HTML
│  ├─ monitor/             # 监控池 HTML
│  ├─ stock_selection/     # 选股结果 HTML
│  ├─ backtest/            # 回测报告 HTML
│  ├─ pdf/                 # 每日复盘 PDF
│  ├─ img/                 # 图像输出目录
│  └─ architecture_diagram.svg
├─ main.py                 # 主程序入口
├─ requirements.txt
└─ .gitignore
```

## 配置方式

当前配置入口已经切换到 `config/` 目录。

- 实际运行入口：`config/__init__.py`
- 示例模板：`config/settings.example.py`
- 本地真实配置：`config/settings.local.py`

注意：

- 根目录旧的 `config.py` 已经删除
- `config/settings.local.py` 仅供本机使用，不应提交到仓库
- 当前项目代码仍然可以继续使用 `from config import ...`

## 输入文件

监控池 Excel 现在拆成两份：

- [monitor_pool.sample.xlsx](C:/Users/Admin/Nutstore/1/我的坚果云/Stock_data/Stock_data-V8/data/input/monitor_pool.sample.xlsx)
  - 仓库模板文件，可提交
- `data/input/monitor_pool.local.xlsx`
  - 本地真实文件，不上传

代码通过配置常量 `MONITOR_POOL_FILE` 读取：

- 如果本地存在 `monitor_pool.local.xlsx`，优先读取本地真实文件
- 否则回退到 `monitor_pool.sample.xlsx`

## 输出目录说明

当前各类报告输出位置如下：

- 每日复盘 HTML：`output/daily_review/`
- 每日复盘 PDF：`output/pdf/`
- 每日新闻：`output/daily_news/`
- 监控池报告：`output/monitor/`
- 选股结果：`output/stock_selection/`
- 回测报告：`output/backtest/`

## 核心模块说明

- `core/db_engine.py`
  - 数据库连接、SQL 查询、Tushare API 初始化

- `core/data_updater.py`
  - 全量初始化、增量更新、历史修复、行业与概念数据更新

- `core/factor_calculator.py`
  - 情绪因子、行业概念排行、个股总分与概念拼接

- `core/strategies.py`
  - 选股策略执行、结果增强、历史回测

- `core/monitor_manager.py`
  - 监控池 Excel 同步、监控数据聚合

- `report/renderer.py`
  - 使用 Jinja2 模板生成 HTML 报告

- `services/news_service.py`
  - 抓取新闻并调用大模型筛选重点内容

- `services/deploy_hexo.py`
  - 将复盘、新闻、监控页同步到 Hexo

- `services/github_service.py`
  - 将复盘静态页上传到展示仓库

## 环境准备

安装依赖：

```bash
pip install -r requirements.txt
```

你需要准备的本地能力：

- MySQL
- Tushare Token
- 邮件 SMTP 配置
- 大模型 API Key
- 如需部署：GitHub Token / Hexo 本地目录

## 常用命令

全量初始化：

```bash
python main.py --mode init
```

增量更新：

```bash
python main.py --mode update
```

生成每日复盘：

```bash
python main.py --mode review
```

生成新闻报告：

```bash
python main.py --mode news
```

生成选股和回测：

```bash
python main.py --mode select
```

生成监控池报告：

```bash
python main.py --mode monitor
```

发送邮件：

```bash
python main.py --mode email
```

同步到 Hexo：

```bash
python main.py --mode deploy
```

上传复盘静态页到 GitHub 展示仓库：

```bash
python main.py --mode github
```

## 注意事项

- Tushare 有调用频率限制，建议在非交易时段执行大批量更新
- 首次初始化数据量较大，建议预留足够时间和磁盘空间
- `settings.local.py` 包含本地敏感信息，不应提交到源码仓库
- `github` 模式当前只负责静态页面发布，不负责源码同步
- 历史回测和策略结果仅供研究，不构成投资建议

## 常见问题

### 初始化时报表或数据表不存在

先确认 MySQL 数据库已经创建，例如：

```sql
CREATE DATABASE stock_data CHARACTER SET utf8mb4;
```

### Tushare Token 无效

请检查 `config/settings.local.py` 中的 `TUSHARE_TOKEN` 是否正确，并确认 token 权限可用。

### 报告生成失败

优先检查：

- 数据库是否有最新交易日数据
- `output/` 子目录是否存在
- Playwright / 邮件 / GitHub / Hexo 等外部依赖是否安装完整

### deploy 或 github 模式失败

优先检查：

- Hexo 根目录路径是否正确
- GitHub token、owner、repo、branch 是否正确
- 当天对应的 HTML 报告是否已先生成

## 说明

- `github` 模式当前用于上传静态页面，不是源码仓库同步
- 源码仓库如果后续要接 GitHub，建议单独作为开发流程管理，不混入当前业务脚本
- 本次结构调整后的变更说明，请看 [重构迁移说明.md](C:/Users/Admin/Nutstore/1/我的坚果云/Stock_data/Stock_data-V8/重构迁移说明.md)
