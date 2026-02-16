# ARGUS 风险分类 - 快速参考卡

## 📋 三层级风险分类体系

```
核心类别 (3)
├─ Financial Risk (金融风险)
│  ├─ Market Risk (市场风险)
│  ├─ Credit Risk (信用风险)
│  ├─ Liquidity Risk (流动性风险)
│  └─ Operational Risk (运营风险)
├─ Supply Chain Risk (供应链风险)
│  ├─ Supply Risk (供应风险)
│  ├─ Demand Risk (需求风险)
│  ├─ Process Risk (流程风险)
│  ├─ Business Risk (商业风险)
│  └─ Disaster Risk (灾难风险)
└─ Non-Commercial Risk (非商业风险)
   ├─ Political Risk (政治风险)
   ├─ Regulatory Risk (监管风险)
   ├─ Reputation Risk (声誉风险)
   └─ Systematic Risk (系统性风险)
```

## 🎯 快速开始

### 1. 导入和初始化
```python
from risk_classifier import RiskClassifier

classifier = RiskClassifier("risk_classification.json")
```

### 2. 分类新闻
```python
result = classifier.classify_news("Federal Reserve announces interest rate increase")

# 获取结果
print(result['primary_category'])      # FINANCIAL_RISK::MARKET_RISK
print(result['risk_score'])             # 0-100
print(result['matched_keywords'])      # 匹配的关键词
```

### 3. 获取风险等级
```python
level = classifier.get_risk_level(result['risk_score'])
# Returns: "Critical", "High", "Medium", "Low", or "None"
```

## ⚡ 高优先级关键词 (需立即关注)

| 关键词 | 含义 | 风险类别 |
|--------|------|---------|
| **crisis** | 危机 | NON_COMMERCIAL_RISK::SYSTEMATIC_RISK |
| **default** | 违约 | FINANCIAL_RISK::CREDIT_RISK |
| **bankruptcy** | 破产 | FINANCIAL_RISK::CREDIT_RISK |
| **fraud** | 欺诈 | FINANCIAL_RISK::OPERATIONAL_RISK |
| **cyber attack** | 网络攻击 | SUPPLY_CHAIN_RISK::PROCESS_RISK |
| **scandal** | 丑闻 | NON_COMMERCIAL_RISK::REPUTATION_RISK |

## 📊 风险等级映射

| 等级 | 分数 | 说明 | 行动 |
|------|------|------|------|
| 🔴 Critical | 80-100 | 极高风险 | 立即通知管理层 |
| 🟠 High | 60-79 | 高风险 | 深入分析 |
| 🟡 Medium | 40-59 | 中等风险 | 定期监控 |
| 🟢 Low | 1-39 | 低风险 | 常规监控 |
| ⚪ None | 0 | 无风险 | 无特殊行动 |

## 💡 常用代码片段

### 批量分类
```python
results = [classifier.classify_news(news) for news in news_list]
```

### 按类别统计
```python
categories = classifier.get_all_risk_categories()
print(categories['core_categories'])
```

### 按优先级获取关键词
```python
high_priority = classifier.get_high_priority_keywords()
```

### 获取特定类别关键词
```python
financial_keywords = classifier.get_all_keywords_by_category("FINANCIAL_RISK")
```

### 完整分类信息
```python
result = classifier.classify_news(title)
details = result['classification']

print(f"核心类别: {details['core_category']}")
print(f"中间类别: {details['intermediate_category']}")
print(f"外部来源: {details['outer_sources']}")
```

## 🔧 配置文件

**文件**: `risk_classification.json`

**结构**:
```json
{
  "risk_categories": {
    "CORE_CATEGORY": {
      "subcategories": {
        "SUB_CATEGORY": {
          "keywords": ["keyword1", "keyword2"],
          "outer_sources": ["source1", "source2"]
        }
      }
    }
  }
}
```

## 📁 文件清单

| 文件 | 说明 |
|------|------|
| `risk_classifier.py` | 分类器主程序 |
| `risk_classification.json` | 配置文件 |
| `risk-classification.csv` | 原始数据 |
| `RISK_CLASSIFICATION_GUIDE.md` | 完整指南 |
| `RISK_CLASSIFICATION_QUICK_REFERENCE.md` | 本文件 |

## 🚀 集成到 ARGUS

### 集成到爬虫
```python
from fed_news_crawler import FedNewsCrawler
from risk_classifier import RiskClassifier

crawler = FedNewsCrawler()
classifier = RiskClassifier()

result = await crawler.crawl()
for news in result['json_data']:
    classification = classifier.classify_news(news['title'])
    news['risk_category'] = classification['primary_category']
```

### 集成到数据库
```python
from fed_news_db import FedNewsDatabase

db = FedNewsDatabase()
db.insert_news(news_items)
db.add_keyword_matches(classifier.get_high_priority_keywords())
```

### 集成到风险分析
```python
from fed_risk_analyzer import FedRiskAnalyzer

analyzer = FedRiskAnalyzer()
# 使用分类结果进行深度分析
```

## 🔍 调试技巧

### 查看所有关键词匹配
```python
result = classifier.classify_news(title)
print(result['matched_keywords'])
```

### 查看匹配的所有类别
```python
print(result['matched_categories'])
```

### 获取分类详情
```python
print(result['classification'])
# {
#   "core_category": "...",
#   "intermediate_category": "...",
#   "outer_sources": [...]
# }
```

## 📈 使用示例

### 检测高风险新闻
```python
high_risk_news = [
    news for news in news_items
    if classifier.get_risk_level(
        classifier.classify_news(news['title'])['risk_score']
    ) in ['High', 'Critical']
]
```

### 按风险类别分组
```python
by_category = {}
for news in news_items:
    result = classifier.classify_news(news['title'])
    cat = result['primary_category']
    if cat not in by_category:
        by_category[cat] = []
    by_category[cat].append(news)
```

### 实时告警
```python
if classifier.get_risk_level(score) == 'Critical':
    send_alert(f"Critical Risk: {title}")
```

## ❓ 常见问题

**Q: 如何添加新的风险类别?**
A: 编辑 `risk_classification.json` 文件，添加新的分类和关键词。

**Q: 如何自定义风险评分?**
A: 修改 `classifier.get_risk_level()` 方法中的阈值。

**Q: 如何提高分类准确性?**
A: 添加更多关键词、同义词，并根据实际反馈调整权重。

**Q: 是否支持多语言?**
A: 目前仅支持英文，可扩展支持其他语言。

## 🔗 相关文档

- [完整使用指南](RISK_CLASSIFICATION_GUIDE.md)
- [爬虫指南](FED_CRAWLER_GUIDE.md)
- [实现细节](IMPLEMENTATION_GUIDE.md)
- [快速开始](NEXT_STEPS.md)

---

**版本**: 1.0 | **更新**: 2026-02-13 | **状态**: ✅ 生产就绪
