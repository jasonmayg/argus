# ARGUS 项目 - 立即开始指南

> **推荐阅读时间**: 5 分钟
> **完成实施时间**: 30 分钟

---

## 🎯 你现在拥有

一个**完整的、生产就绪的**美联储新闻监控系统，包括：

✅ **自动爬虫** - 每日自动获取美联储新闻
✅ **数据库存储** - SQLite 数据库存储所有历史数据
✅ **风险分析** - 使用 Claude API 自动分析风险
✅ **日常监控** - 可配置的自动化监控任务
✅ **数据导出** - JSON/Markdown 多种格式输出

---

## 🚀 立即开始（3 步）

### 步骤 1: 配置 API 密钥（2 分钟）

```bash
# 编辑 .env 文件
nano .env

# 添加或修改以下内容:
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

获取 API 密钥：
- 访问 https://console.anthropic.com
- 创建 API 密钥
- 复制到 `.env` 文件

### 步骤 2: 运行一次完整流程（10 分钟）

```bash
# 1. 激活虚拟环境
source venv/bin/activate

# 2. 运行爬虫
echo "=== 步骤 1: 爬取新闻 ==="
python fed_news_crawler.py

# 3. 运行数据库
echo "=== 步骤 2: 存储到数据库 ==="
python fed_news_db.py

# 4. 运行日常监控
echo "=== 步骤 3: 日常监控 ==="
python daily_monitor.py

# 5. 运行风险分析（需要 API 密钥）
echo "=== 步骤 4: 风险分析 ==="
python fed_risk_analyzer.py
```

### 步骤 3: 配置自动化（10 分钟）

```bash
# 运行 Cron 配置脚本
bash setup_cron.sh

# 按照提示选择:
# 1. 每天 9:00 AM (推荐)
# 2. 按确认安装
```

验证安装：
```bash
# 查看已安装的 Cron 任务
crontab -l

# 应该看到类似的输出
# 0 9 * * * cd '/path/to/Downloads/argus' && source venv/bin/activate && ...
```

---

## 📊 验证系统是否工作

运行此检查列表，确保一切就绪：

```bash
# ✓ 1. 检查爬虫
python fed_news_crawler.py
# 预期: 找到新闻 + 过滤后的新闻

# ✓ 2. 检查数据库
python fed_news_db.py
# 预期: 数据库连接 + 数据插入

# ✓ 3. 检查监控
python daily_monitor.py
# 预期: 监控完成 + 日志保存

# ✓ 4. 检查日志
ls -la data/fed_news/logs/
# 预期: 看到 daily_monitor_*.json 文件

# ✓ 5. 检查数据库文件
ls -lh data/fed_news/argus_news.db
# 预期: 数据库文件存在
```

---

## 📁 文件结构说明

```
argus/
│
├─ 🎯 核心脚本
│  ├── fed_news_crawler.py       # 爬虫主程序
│  ├── fed_news_db.py            # 数据库管理
│  ├── fed_news_backtest.py      # 历史数据回测
│  ├── fed_risk_analyzer.py      # 风险分析（需要 API）
│  ├── daily_monitor.py          # 日常监控（Cron 运行）
│  └── setup_cron.sh             # Cron 自动化配置
│
├─ 📚 配置文件
│  ├── keywords_config.json      # 关键词配置（6 个预设）
│  ├── .env                      # 环境变量（API 密钥）
│  └── .env.example              # 环境变量示例
│
├─ 📖 文档
│  ├── SETUP_SUMMARY.md          # 项目总结
│  ├── FED_CRAWLER_GUIDE.md      # 完整使用指南
│  ├── IMPLEMENTATION_GUIDE.md   # 实现详解
│  ├── NEXT_STEPS.md             # 本文件
│  └── README.md                 # 项目描述
│
├─ 📊 数据目录
│  └── data/fed_news/
│      ├── argus_news.db         # SQLite 数据库
│      ├── *.json                # 爬虫输出（JSON）
│      ├── *.md                  # 爬虫输出（Markdown）
│      ├── logs/                 # 监控日志
│      └── cron_logs/            # Cron 任务日志
│
└─ 🔧 工具
   ├── example_usage.py          # 使用示例
   └── venv/                     # Python 虚拟环境
```

---

## 🔄 工作流程

### 日常工作流（自动）

```
每天 9:00 AM
    ↓
Cron 运行 daily_monitor.py
    ↓
爬取美联储最新新闻
    ↓
存储到 SQLite 数据库
    ↓
添加关键词匹配
    ↓
生成监控日志
    ↓
(可选) 发送告警通知
```

### 手动分析工作流

```
1. 查询数据库中的新闻
   └── python -c "from fed_news_db import FedNewsDatabase; db = FedNewsDatabase(); print(db.get_statistics())"

2. 导出特定数据
   └── python fed_news_backtest.py

3. 进行风险分析
   └── python fed_risk_analyzer.py

4. 生成报告
   └── (可添加自定义报告生成脚本)
```

---

## 💡 常见问题解答

### Q1: 如何修改监控频率？

```bash
# 编辑 Cron
crontab -e

# 常见的 Cron 表达式:
# 0 9 * * *          # 每天 9:00 AM
# 0 */6 * * *        # 每 6 小时
# 0 9 * * 1-5        # 每个工作日 9:00 AM
# 0 12 * * 0         # 每周日 12:00 PM

# 保存后自动生效
```

### Q2: 如何修改关键词？

**方法 1: 使用配置文件**
```bash
# 编辑 keywords_config.json
nano keywords_config.json

# 添加新的关键词集合
# 在爬虫中使用:
import json
with open('keywords_config.json') as f:
    config = json.load(f)
    keywords = config['fomc_focus']['keywords']
```

**方法 2: 直接修改爬虫**
```python
from fed_news_crawler import FedNewsCrawler

crawler = FedNewsCrawler(keywords=[
    "FOMC", "interest rate", "monetary policy"
])
```

### Q3: 数据库太大了怎么办？

```bash
# 备份数据库
cp data/fed_news/argus_news.db backup_$(date +%Y%m%d).db

# 清理旧数据（保留最近 3 年）
sqlite3 data/fed_news/argus_news.db \
  "DELETE FROM news WHERE date < date('now', '-3 years');"

# 优化数据库大小
sqlite3 data/fed_news/argus_news.db "VACUUM;"

# 验证
ls -lh data/fed_news/argus_news.db
```

### Q4: 如何查看 Cron 日志？

```bash
# 查看最新日志
tail -f data/fed_news/cron_logs/*

# 查看特定日期
tail -f data/fed_news/cron_logs/daily_monitor_20260213*.log

# 查看 JSON 格式的日志
cat data/fed_news/logs/daily_monitor_20260213.json | python -m json.tool
```

### Q5: 如何恢复之前的数据？

```bash
# 列出备份
ls -la backup_*.db

# 恢复
cp backup_20260201.db data/fed_news/argus_news.db
```

---

## 🎓 学习资源

### 了解组件

- **爬虫**: `fed_news_crawler.py` - 如何抓取网页
- **数据库**: `fed_news_db.py` - 如何存储和查询数据
- **分析**: `fed_risk_analyzer.py` - 如何使用 Claude API
- **自动化**: `daily_monitor.py` - 如何组合所有组件

### 阅读文档

1. **快速开始**: 本文件（NEXT_STEPS.md）
2. **完整指南**: `FED_CRAWLER_GUIDE.md`
3. **实现细节**: `IMPLEMENTATION_GUIDE.md`
4. **使用示例**: `example_usage.py`

---

## 🛠️ 故障排除快速指南

| 问题 | 解决方案 |
|------|--------|
| "ModuleNotFoundError" | `source venv/bin/activate` |
| "API key not found" | 检查 `.env` 文件中的 ANTHROPIC_API_KEY |
| "Database locked" | 等待 1 分钟或重启终端 |
| "Cron not running" | 运行 `crontab -l` 验证，或检查 `/var/log/system.log` |
| "No news found" | 美联储可能还没有发布新闻，检查 HTML 结构 |

---

## 📞 获取帮助

### 查看脚本帮助

```bash
# 查看爬虫文档
python -c "from fed_news_crawler import FedNewsCrawler; help(FedNewsCrawler)"

# 查看数据库文档
python -c "from fed_news_db import FedNewsDatabase; help(FedNewsDatabase)"
```

### 运行诊断

```bash
# 完整的系统诊断
bash << 'EOF'
echo "=== ARGUS 系统诊断 ==="
echo "1. Python 版本:"
python --version
echo ""
echo "2. 虚拟环境:"
which python
echo ""
echo "3. 依赖检查:"
pip list | grep -E "crawl4ai|anthropic|sqlite3"
echo ""
echo "4. 数据库状态:"
sqlite3 data/fed_news/argus_news.db ".tables"
echo ""
echo "5. Cron 状态:"
crontab -l | grep daily_monitor || echo "未安装"
echo ""
echo "诊断完成"
EOF
```

---

## ✅ 实施检查清单

使用此清单跟踪你的进度：

- [ ] **配置 API 密钥** (.env 文件)
- [ ] **测试爬虫** (python fed_news_crawler.py)
- [ ] **测试数据库** (python fed_news_db.py)
- [ ] **测试监控** (python daily_monitor.py)
- [ ] **配置 Cron** (bash setup_cron.sh)
- [ ] **验证 Cron** (crontab -l)
- [ ] **检查日志** (tail -f data/fed_news/logs/*)
- [ ] **测试风险分析** (python fed_risk_analyzer.py)

---

## 🎉 完成！

你现在已经拥有一个**完整的自动化系统**！

### 接下来的建议

1. **第 1 天**: 配置 Cron，让它自动运行
2. **第 1 周**: 调整关键词，优化过滤结果
3. **第 2 周**: 建立历史数据（回测 2024-2025）
4. **第 1 个月**: 集成告警和报告功能

---

## 📈 性能预期

| 指标 | 预期值 |
|------|--------|
| 爬虫速度 | 1.4-1.7 秒/页 |
| 数据库操作 | <100ms |
| 风险分析 | 5-15 秒（取决于新闻数量） |
| 每日运行时间 | 2-3 分钟 |
| 数据库大小 | 10KB-1MB（取决于数据量） |

---

## 📚 下一步深度学习

当你熟悉基本流程后，可以尝试：

1. **自定义报告** - 创建月度/季度报告脚本
2. **告警系统** - 添加邮件/Slack 通知
3. **数据可视化** - 使用 matplotlib 绘制趋势图
4. **多源集成** - 集成 ECB、BOE 等其他央行数据
5. **实时仪表板** - 创建 Web 界面查看数据

---

**恭喜！你已准备好启动 ARGUS！** 🚀

---

**最后更新**: 2026-02-13
**文档版本**: 2.0
**建议反馈**: 本文档设计用于快速入门，任何问题欢迎反馈
