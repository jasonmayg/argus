# 美联储新闻爬虫 - 使用指南

## 概述

ARGUS 项目的联邦储备委员会（Federal Reserve）新闻爬虫用于监控和抓取美联储的官方新闻公告，支持关键词过滤和历史数据回测。

**数据来源**: https://www.federalreserve.gov/newsevents/pressreleases.htm

## 快速开始

### 1. 获取当年新闻数据（默认）

```bash
python fed_news_crawler.py
```

这将：
- 抓取当前年份（2026）的所有新闻公告
- 按预设关键词过滤（Payment, Regulation, Bank, Interest, Rate 等）
- 输出 JSON 和 Markdown 两种格式
- 保存到 `data/fed_news/` 目录

**输出结果**:
```
✓ 爬虫统计:
  找到总记录数: 14
  过滤后记录数: 5
  抓取 URL: https://www.federalreserve.gov/newsevents/pressreleases/2026-press.htm
```

### 2. 获取历史数据（2024-2025 年回测）

```bash
python fed_news_backtest.py
```

这将：
- 抓取 2024 和 2025 两个年份的数据
- 应用关键词过滤
- 支持日期范围过滤
- 保存为合并的 JSON 文件

## 脚本详解

### `fed_news_crawler.py` - 核心爬虫

#### 核心类: `FedNewsCrawler`

```python
# 初始化 (使用默认关键词)
crawler = FedNewsCrawler()

# 或使用自定义关键词
custom_keywords = ["FOMC", "Interest Rate", "Policy"]
crawler = FedNewsCrawler(keywords=custom_keywords)

# 执行爬虫
result = await crawler.crawl(
    url="https://www.federalreserve.gov/newsevents/pressreleases/2025-press.htm"
)
```

#### 主要方法

| 方法 | 说明 |
|------|------|
| `fetch_page(url, page_num)` | 获取页面 HTML 和 Markdown |
| `extract_news_items(markdown)` | 从 Markdown 提取新闻（日期、标题、链接） |
| `filter_by_keywords(items)` | 按关键词过滤新闻 |
| `normalize_date(date_str)` | 将日期标准化为 YYYY-MM-DD |
| `to_json()` | 转换为 JSON 格式 |
| `to_markdown(markdown_content)` | 生成格式化的 Markdown 文档 |
| `crawl(url, page_num)` | 执行完整爬虫流程 |

#### 输出格式

**JSON 格式**:
```json
[
  {
    "date": "2026-02-12",
    "title": "Federal Reserve Board issues enforcement action...",
    "link": "https://www.federalreserve.gov/newsevents/..."
  }
]
```

**Markdown 格式**:
```markdown
# 美联储新闻公告

**抓取时间**: 2026-02-13 09:16:46
**数据来源**: https://www.federalreserve.gov/newsevents/pressreleases.htm
**记录数量**: 5

## 筛选条件
- **关键词**: Payment, Payments, Regulation, ...

## 新闻列表

### [标题](链接)
- **日期**: 2026-02-12
- **链接**: https://...
```

### `fed_news_backtest.py` - 历史数据抓取

#### 主要函数

##### 1. `crawl_multiple_years(years, keywords, output_file)`

抓取多个年份的新闻数据

```python
# 示例: 抓取 2020-2025 年数据
backtest_data = await crawl_multiple_years(
    years=[2020, 2021, 2022, 2023, 2024, 2025],
    output_file="data/fed_news/fed_news_2020_2025.json"
)
```

##### 2. `filter_by_date_range(items, start_date, end_date)`

按日期范围过滤（格式: YYYY-MM-DD）

```python
# 示例: 过滤 2024 年 Q4
q4_data = filter_by_date_range(
    backtest_data,
    start_date="2024-10-01",
    end_date="2024-12-31"
)
```

##### 3. `filter_by_keywords(items, keywords)`

按自定义关键词过滤

```python
# 示例: 仅保留 FOMC 相关新闻
fomc_news = filter_by_keywords(
    backtest_data,
    keywords=["FOMC", "monetary policy", "interest rate"]
)
```

## 配置说明

### 修改默认关键词

编辑 `fed_news_crawler.py` 的 `KEYWORDS_FILTER`:

```python
KEYWORDS_FILTER = [
    "Payment", "Payments",
    "Regulation", "Regulatory",
    "Bank", "Banking", "Banks",
    "Interest", "Rate", "Rates",
    "Treasury", "Monetary",
    "Financial", "Risk",
    "Stability", "Crisis"
]
```

### 修改输出目录

编辑 `fed_news_crawler.py` 的 `OUTPUT_DIR`:

```python
OUTPUT_DIR = "data/fed_news"  # 改为自己需要的路径
```

### 修改爬虫的年份

在 `fed_news_crawler.py` 的 `main()` 函数中：

```python
# 改为要抓取的年份
current_year = 2024
news_url = f"https://www.federalreserve.gov/newsevents/pressreleases/{current_year}-press.htm"
```

或在 `fed_news_backtest.py` 中修改 `years` 列表。

## 高级使用

### 自定义爬虫流程

```python
import asyncio
from fed_news_crawler import FedNewsCrawler

async def custom_crawl():
    crawler = FedNewsCrawler(
        keywords=["FOMC", "interest rate", "policy"]
    )

    result = await crawler.crawl(
        url="https://www.federalreserve.gov/newsevents/pressreleases/2025-press.htm"
    )

    if result.get("status") == "success":
        # 访问过滤后的新闻项目
        for item in result['json_data']:
            print(f"{item['date']}: {item['title']}")
            print(f"  Link: {item['link']}\n")

asyncio.run(custom_crawl())
```

### 处理 FOMC 相关新闻

```python
# 抓取 2026 年 FOMC 相关新闻
fomc_url = "https://www.federalreserve.gov/newsevents/pressreleases/2026-press-fomc.htm"
result = await crawler.crawl(url=fomc_url)
```

### 批量抓取并汇总

```python
import json
from fed_news_backtest import crawl_multiple_years, filter_by_date_range

# 1. 抓取 5 年数据
all_news = await crawl_multiple_years(
    years=[2021, 2022, 2023, 2024, 2025]
)

# 2. 按年份分组
news_by_year = {}
for item in all_news:
    year = item['date'][:4]
    if year not in news_by_year:
        news_by_year[year] = []
    news_by_year[year].append(item)

# 3. 保存各年份数据
for year, items in news_by_year.items():
    with open(f"data/fed_news_{year}.json", 'w') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
```

## 输出文件

爬虫会在 `data/fed_news/` 目录下生成：

| 文件 | 说明 |
|------|------|
| `fed_news_YYYYMMDD_HHMMSS.json` | 过滤后的新闻 JSON 数据 |
| `fed_news_YYYYMMDD_HHMMSS.md` | 格式化的 Markdown 文档 |

## 常见问题

### Q: 如何只获取最新的新闻？
A: 默认爬虫获取当年数据，JSON 按日期倒序排列，最新的在前。

### Q: 如何获取 2020 年之前的新闻？
A: 美联储官方网站提到"Press Releases prior to 2006 are available in our archive"。
可改为访问: https://www.federalreserve.gov/newsevents/pressreleases/press-release-archive.htm

### Q: 能否分页获取一年中所有新闻？
A: 美联储年份页面已经包含该年所有新闻，不需要分页。

### Q: 如何避免被限制？
A: 已在爬虫中配置了延迟。批量爬取时建议在循环中添加 `await asyncio.sleep(1-2)`。

### Q: 能否实时监控新闻更新？
A: 可以每天运行爬虫并比较新增记录，或改用 RSS feed（如有）。

## 数据使用建议

### 用于 Claude 风险评分

将 Markdown 输出直接输入给 Claude：

```python
# 1. 获取 Markdown
result = await crawler.crawl(url=...)
markdown_content = result['markdown']

# 2. 发送给 Claude API
response = await claude_client.messages.create(
    model="claude-opus",
    messages=[{
        "role": "user",
        "content": f"""Based on these Federal Reserve news, please analyze treasury risks:

{markdown_content}

Provide risk scores for:
1. Interest Rate Risk
2. Monetary Policy Risk
3. Banking Stability Risk
"""
    }]
)
```

### 用于时间序列分析

```python
import json
from datetime import datetime

# 1. 加载回测数据
with open("data/fed_news_2024_2025.json") as f:
    news_data = json.load(f)

# 2. 按日期聚合
daily_counts = {}
for item in news_data:
    date = item['date']
    daily_counts[date] = daily_counts.get(date, 0) + 1

# 3. 绘图分析趋势
import matplotlib.pyplot as plt
dates = sorted(daily_counts.keys())
counts = [daily_counts[d] for d in dates]
plt.plot(dates, counts)
plt.title("Daily Fed News Count")
plt.show()
```

## 技术细节

### Crawl4AI 配置

爬虫使用以下配置确保完整的 JavaScript 渲染：

```python
result = await crawler.arun(
    url=url,
    cache_mode=CacheMode.BYPASS,          # 禁用缓存
    wait_until='networkidle',              # 等待网络空闲
    delay_before_return_ms=2000            # 额外延迟
)
```

### Markdown 提取优势

相比 HTML 提取：
- ✓ 更简洁、更易解析
- ✓ 自动移除导航和页脚
- ✓ 链接已标准化
- ✓ 日期和标题清晰分离

## 故障排除

### 问题: "提取了 0 条新闻记录"

**原因**: 通常是由于：
1. JavaScript 没有完全渲染
2. 网页结构改变

**解决**:
1. 增加 `delay_before_return_ms`
2. 检查 HTML/Markdown 结构是否改变
3. 查看原始 HTML 文件调试

### 问题: 关键词过滤太严格

**解决**: 在 `FedNewsCrawler` 初始化时传入自定义关键词列表：
```python
custom_crawler = FedNewsCrawler(
    keywords=["bank", "interest"]  # 更少的关键词
)
```

## 更新日志

### v1.0 (2026-02-13)
- ✓ 基础爬虫实现
- ✓ 关键词过滤
- ✓ JSON/Markdown 输出
- ✓ 多年份回测支持
- ✓ 日期范围过滤

## 许可

此爬虫遵守 robots.txt 和网站 ToS。仅用于非商业研究目的。
