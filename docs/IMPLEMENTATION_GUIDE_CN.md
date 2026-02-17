# ARGUS 完整实现指南

## 目录
1. [系统架构](#系统架构)
2. [安装与配置](#安装与配置)
3. [日常监控设置](#日常监控设置)
4. [风险分析集成](#风险分析集成)
5. [数据库管理](#数据库管理)
6. [监控和维护](#监控和维护)

---

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    ARGUS 系统架构                         │
└─────────────────────────────────────────────────────────┘

┌──────────────────────────┐
│   每日监控任务 (Cron)     │
│   daily_monitor.py       │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│   新闻爬虫 (Crawler)      │
│   fed_news_crawler.py    │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│   数据库管理 (Database)   │
│   fed_news_db.py         │
│  • 存储新闻              │
│  • 关键词匹配            │
│  • 风险分析              │
└──────────┬───────────────┘
           │
           ├─────────────────────────────┐
           ▼                             ▼
┌──────────────────────┐    ┌───────────────────────┐
│   风险分析 (Claude)   │    │   数据导出 (JSON)     │
│ fed_risk_analyzer.py │    │ 用于后续分析          │
└──────────────────────┘    └───────────────────────┘
```

---

## 安装与配置

### 前提条件

```bash
# 检查 Python 版本 (需要 3.10+)
python --version

# 检查虚拟环境
source venv/bin/activate

# 检查依赖
pip list | grep -E "crawl4ai|anthropic"
```

### 环境变量设置

编辑 `.env` 文件：

```bash
# 必需
ANTHROPIC_API_KEY=sk-ant-...  # 从 Anthropic 获取

# 可选
TARGET_MARKET=USA
PROJECT_DIR=/path/to/Downloads/argus
```

### 验证安装

```bash
# 测试爬虫
python fed_news_crawler.py

# 测试数据库
python fed_news_db.py

# 测试风险分析（需要 API 密钥）
python fed_risk_analyzer.py
```

---

## 日常监控设置

### 步骤 1: 配置自动化监控

```bash
# 进入项目目录
cd /path/to/Downloads/argus

# 运行设置脚本
bash setup_cron.sh
```

脚本会提示你选择：
- **每天 9:00 AM** (推荐)
- 每天 12:00 PM
- 每天 6:00 PM
- 每 6 小时
- 自定义时间

### 步骤 2: 验证 Cron 任务

```bash
# 查看已安装的 cron 任务
crontab -l

# 应该显示类似:
# 0 9 * * * cd '/path/to/Downloads/argus' && source venv/bin/activate && ...
```

### 步骤 3: 监控日志

```bash
# 查看最新日志
tail -f data/fed_news/cron_logs/*

# 或查看特定日期的日志
ls -lt data/fed_news/cron_logs/ | head

# 查看日志内容
cat data/fed_news/cron_logs/daily_monitor_20260213.json
```

### 步骤 4: 手动测试

```bash
# 运行一次监控任务
source venv/bin/activate
python daily_monitor.py

# 检查输出结果
cat data/fed_news/logs/daily_monitor_20260213.json
```

---

## 风险分析集成

### 步骤 1: 配置 API 密钥

```bash
# 编辑 .env 文件
export ANTHROPIC_API_KEY="sk-ant-your-key-here"

# 或在脚本中设置
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
```

### 步骤 2: 运行风险分析

```bash
# 分析当年新闻
python fed_risk_analyzer.py

# 输出示例:
# ========================================
# 风险分析结果
# ========================================
#
# ## Interest Rate Risk
# 风险信号: Federal Reserve announces...
# 严重程度: 7/10
# ...
```

### 步骤 3: 集成到日常监控

编辑 `daily_monitor.py`，添加风险分析：

```python
# 在 run() 方法中添加:
from fed_risk_analyzer import FedRiskAnalyzer

# ... 数据库部分代码 ...

# 5. 风险分析
print("\n[5/5] 执行风险分析...")
analyzer = FedRiskAnalyzer()
analysis = await analyzer.analyze_news_batch(
    news_items=news_items
)
analyzer.save_analysis(analysis)

result['analysis'] = analysis['status']
```

### 步骤 4: 自定义关键词

编辑 `keywords_config.json`，定义多个关键词集：

```json
{
  "custom_profile": {
    "name": "自定义风险评估",
    "keywords": [
      "treasury operations",
      "liquidity management",
      "currency exposure"
    ]
  }
}
```

在爬虫中使用自定义关键词：

```python
import json

with open('keywords_config.json') as f:
    configs = json.load(f)

crawler = FedNewsCrawler(
    keywords=configs['custom_profile']['keywords']
)
```

---

## 数据库管理

### 初始化数据库

```bash
# 第一次运行会自动创建数据库
python fed_news_db.py

# 验证数据库
sqlite3 data/fed_news/argus_news.db ".tables"
```

### 数据库操作

```python
from fed_news_db import FedNewsDatabase

db = FedNewsDatabase()

# 1. 查询按日期范围
q4_news = db.query_by_date_range("2026-10-01", "2026-12-31")

# 2. 查询按关键词
bank_news = db.query_by_keyword("Bank")

# 3. 查询多个关键词
multi = db.query_by_keywords(["Interest", "Rate", "Policy"])

# 4. 获取统计信息
stats = db.get_statistics()

# 5. 导出数据
db.export_to_json("backup.json")

db.close()
```

### 数据库备份

```bash
# 备份数据库
cp data/fed_news/argus_news.db data/fed_news/backup_$(date +%Y%m%d).db

# 恢复备份
cp data/fed_news/backup_20260213.db data/fed_news/argus_news.db
```

### 数据库清理

```bash
# 删除旧数据 (2 年前)
sqlite3 data/fed_news/argus_news.db "DELETE FROM news WHERE date < date('now', '-2 years');"

# 优化数据库
sqlite3 data/fed_news/argus_news.db "VACUUM;"
```

---

## 监控和维护

### 日常检查清单

```bash
#!/bin/bash
# daily_check.sh - 日常检查脚本

echo "===== ARGUS 日常检查 ====="

# 1. 检查 Cron 任务状态
echo "1. Cron 任务:"
crontab -l | grep daily_monitor || echo "  ⚠️ 未安装"

# 2. 检查最新日志
echo -e "\n2. 最新日志:"
ls -lt data/fed_news/cron_logs/ | head -3

# 3. 检查数据库大小
echo -e "\n3. 数据库大小:"
du -h data/fed_news/argus_news.db

# 4. 数据库中的记录数
echo -e "\n4. 数据库记录数:"
sqlite3 data/fed_news/argus_news.db "SELECT COUNT(*) FROM news;"

# 5. 最新的新闻
echo -e "\n5. 最新新闻:"
sqlite3 data/fed_news/argus_news.db "SELECT date, title FROM news ORDER BY date DESC LIMIT 5;" ".mode column"

echo -e "\n===== 检查完成 ====="
```

### 故障排除

#### 问题: Cron 任务未运行

```bash
# 检查 cron 守护进程
ps aux | grep cron

# macOS 检查
log stream --predicate 'eventMessage contains "cron"'

# 检查 cron 日志
tail -f /var/log/system.log | grep cron
```

#### 问题: Python 找不到模块

```bash
# 确保虚拟环境已激活
which python

# 应该显示: /path/to/Downloads/argus/venv/bin/python
```

#### 问题: 数据库被锁定

```bash
# 检查是否有其他进程正在使用
lsof | grep argus_news.db

# 如果需要，强制重置
sqlite3 data/fed_news/argus_news.db "PRAGMA journal_mode=DELETE;"
```

### 性能优化

```bash
# 1. 建立索引以加快查询
sqlite3 data/fed_news/argus_news.db "CREATE INDEX idx_news_date ON news(date);"
sqlite3 data/fed_news/argus_news.db "CREATE INDEX idx_keyword ON keyword_matches(keyword);"

# 2. 定期清理
sqlite3 data/fed_news/argus_news.db "DELETE FROM news WHERE date < date('now', '-5 years');"
sqlite3 data/fed_news/argus_news.db "VACUUM;"

# 3. 监控数据库大小
du -h data/fed_news/argus_news.db

# 如果超过 500MB，考虑分区或存档
```

---

## 高级功能

### 实时告警

创建 `alert_system.py`:

```python
import asyncio
from fed_news_db import FedNewsDatabase

async def check_alerts():
    """检查是否有新的高风险新闻"""
    db = FedNewsDatabase()

    # 查询最近 24 小时的新闻
    from datetime import datetime, timedelta

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # 查询风险关键词
    risk_keywords = ["crisis", "default", "stress", "risk", "emergency"]
    risk_news = db.query_by_keywords(risk_keywords)

    # 过滤最近新闻
    recent = [n for n in risk_news if n['date'] >= yesterday]

    if recent:
        print(f"⚠️ 警告: 找到 {len(recent)} 条风险新闻")
        # 可以这里添加邮件/Slack 通知

    db.close()

# 可以添加到 daily_monitor.py 中作为额外步骤
```

### 数据导出和报告

```python
# 生成月报
from datetime import datetime
from fed_news_db import FedNewsDatabase

def monthly_report(year, month):
    db = FedNewsDatabase()

    start = f"{year}-{month:02d}-01"
    # 计算月末
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    end = f"{year}-{month:02d}-{last_day}"

    news = db.query_by_date_range(start, end)
    stats = db.get_statistics()

    # 生成报告
    with open(f"reports/monthly_{year}{month:02d}.md", 'w') as f:
        f.write(f"# {year} 年 {month} 月报告\n\n")
        f.write(f"## 统计\n")
        f.write(f"- 本月新闻: {len(news)}\n")
        f.write(f"- 数据库总数: {stats['total_news']}\n\n")
        f.write(f"## 新闻列表\n")
        for item in news:
            f.write(f"- [{item['date']}] {item['title']}\n")

    db.close()
    return f"reports/monthly_{year}{month:02d}.md"
```

---

## 总结

### 已完成

✅ 新闻爬虫配置
✅ 自动化监控设置
✅ 数据库集成
✅ Claude API 风险分析
✅ 关键词配置
✅ Cron 任务自动化

### 建议的后续步骤

1. **立即执行**:
   - [ ] 运行 `setup_cron.sh` 配置日常监控
   - [ ] 测试 `python daily_monitor.py`
   - [ ] 验证日志文件

2. **本周内**:
   - [ ] 设置 ANTHROPIC_API_KEY
   - [ ] 测试风险分析功能
   - [ ] 调整关键词配置

3. **本月内**:
   - [ ] 建立完整的历史数据（回测 2024-2025）
   - [ ] 创建月度报告系统
   - [ ] 建立告警机制

---

## 快速参考

### 常用命令

```bash
# 运行爬虫
python fed_news_crawler.py

# 运行监控
python daily_monitor.py

# 风险分析
python fed_risk_analyzer.py

# 数据库操作
python fed_news_db.py

# 查看日志
tail -f data/fed_news/cron_logs/*

# 查看 Cron 状态
crontab -l

# 编辑 Cron
crontab -e
```

### 文件结构

```
argus/
├── fed_news_crawler.py       # 爬虫主程序
├── fed_news_backtest.py      # 回测工具
├── fed_news_db.py            # 数据库管理
├── fed_risk_analyzer.py      # 风险分析
├── daily_monitor.py          # 日常监控
├── setup_cron.sh             # Cron 配置
├── keywords_config.json      # 关键词配置
├── data/
│   └── fed_news/
│       ├── *.json            # 爬虫输出
│       ├── *.md              # Markdown 输出
│       ├── argus_news.db     # SQLite 数据库
│       ├── logs/             # 监控日志
│       └── cron_logs/        # Cron 日志
└── README.md
```

---

**最后更新**: 2026-02-13
**版本**: 2.0
**状态**: ✅ 完全功能
