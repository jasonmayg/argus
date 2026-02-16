# ARGUS 风险分类指南

## 概述

ARGUS 系统现已集成了 **Alphabet 全球国库风险分类框架**，提供三层级的风险分类体系：

1. **核心风险类别** (Core Category) - 3 个
2. **中间风险类别** (Intermediate Category) - 11 个
3. **外部风险来源** (Outer Sources & Events) - 多个

---

## 风险分类体系

### 核心类别 1: 金融风险 (Financial Risk)

#### 市场风险 (Market Risk)
- **外部来源**: 股票价格、利率、外汇、商品价格
- **关键词**: equity, stock, interest rate, forex, currency, commodity
- **风险指标**: 股市下跌、利率上升、汇率波动

#### 信用风险 (Credit Risk)
- **外部来源**: 结算、评级下调、破产、违约
- **关键词**: credit, default, downgrade, bankruptcy, settlement
- **风险指标**: 评级下调、违约事件、破产

#### 流动性风险 (Liquidity Risk)
- **外部来源**: 融资流动性、交易流动性
- **关键词**: liquidity, funding, cash flow, illiquid
- **风险指标**: 流动性危机、融资压力、市场冻结

#### 运营风险 (Operational Risk)
- **外部来源**: 欺诈、人员错误、技术风险
- **关键词**: fraud, error, technology, system failure, compliance
- **风险指标**: 欺诈检测、系统故障、模型错误

### 核心类别 2: 供应链风险 (Supply Chain Risk)

#### 供应风险 (Supply Risk)
- **外部来源**: 质量、供应商、数量、交付、价格、采购、沟通
- **关键词**: supply, supplier, sourcing, procurement, delivery, shortage
- **风险指标**: 供应商失败、供应短缺、交付延迟

#### 需求风险 (Demand Risk)
- **外部来源**: 产品生命周期、预测、消费者偏好、波动、价格敏感性、法规
- **关键词**: demand, forecasting, consumer, preference, volatility
- **风险指标**: 需求下降、预测误差、生命周期变化

#### 流程风险 (Process Risk)
- **外部来源**: 创新、信息、网络安全、协调、生产、物流、环保社会
- **关键词**: cybersecurity, cyber, innovation, logistics, esg, sustainability
- **风险指标**: 网络攻击、流程故障、ESG 问题

#### 商业风险 (Business Risk)
- **外部来源**: 法律、战略、竞争、合同
- **关键词**: legal, strategic, competition, contract, litigation
- **风险指标**: 诉讼、竞争加剧、合同纠纷

#### 灾难风险 (Disaster Risk)
- **外部来源**: 自然和人为灾难
- **关键词**: disaster, catastrophe, earthquake, flood, pandemic, emergency
- **风险指标**: 自然灾害、疫情、紧急事件

### 核心类别 3: 非商业风险 (Non-Commercial Risk)

#### 政治风险 (Political Risk)
- **外部来源**: 政治、贸易战、关税
- **关键词**: political, trade war, tariff, geopolitical, sanctions
- **风险指标**: 贸易战、关税提升、地缘政治紧张

#### 监管风险 (Regulatory Risk)
- **外部来源**: 地方、州、全球监管
- **关键词**: regulatory, regulation, compliance, policy, ban, restriction
- **风险指标**: 监管变化、合规违反、政策转变

#### 声誉风险 (Reputation Risk)
- **外部来源**: 行业声誉、个人声誉、谣言
- **关键词**: reputation, scandal, rumor, public, brand, social media
- **风险指标**: 丑闻、声誉受损、负面新闻

#### 系统性风险 (Systematic Risk)
- **外部来源**: 连锁反应、恐慌性抛售
- **关键词**: systematic, systemic, crisis, market crash, contagion
- **风险指标**: 市场崩溃、金融危机、恐慌性抛售

---

## 使用方法

### 1. 初始化分类器

```python
from risk_classifier import RiskClassifier

# 创建分类器实例
classifier = RiskClassifier()
```

### 2. 分类单条新闻

```python
# 分类新闻
result = classifier.classify_news(
    title="Federal Reserve announces new interest rate policy"
)

# 获取结果
print(f"主要类别: {result['primary_category']}")
print(f"风险评分: {result['risk_score']}")
print(f"匹配关键词: {result['matched_keywords']}")

# 获取风险等级
risk_level = classifier.get_risk_level(result['risk_score'])
print(f"风险等级: {risk_level}")  # Low, Medium, High, Critical
```

### 3. 获取分类详情

```python
# 获取详细分类信息
details = result['classification']
print(f"核心类别: {details['core_category']}")
print(f"中间类别: {details['intermediate_category']}")
print(f"外部来源: {details['outer_sources']}")
```

### 4. 批量分类

```python
news_list = [
    "Federal Reserve reports on financial stability",
    "Market crash triggers trading halt",
    "Cybersecurity breach discovered in payment system"
]

results = [classifier.classify_news(news) for news in news_list]

# 统计风险分布
risk_distribution = {}
for result in results:
    level = classifier.get_risk_level(result['risk_score'])
    risk_distribution[level] = risk_distribution.get(level, 0) + 1

print(risk_distribution)
```

### 5. 获取高优先级关键词

```python
# 获取需要立即关注的关键词
high_priority = classifier.get_high_priority_keywords()
print(high_priority)
# ['crisis', 'default', 'bankruptcy', 'fraud', 'cyber attack', 'scandal']
```

### 6. 获取特定类别的关键词

```python
# 获取 Financial Risk 的所有关键词
keywords = classifier.get_all_keywords_by_category("FINANCIAL_RISK")

for category, kw_list in keywords.items():
    print(f"{category}: {kw_list}")
```

### 7. 获取风险类别层级

```python
# 获取所有风险类别的完整结构
categories = classifier.get_all_risk_categories()
print(categories['core_categories'])
# ['Financial Risk', 'Supply Chain Risk', 'Non-Commercial Risk']
```

---

## 与 ARGUS 系统的集成

### 集成到爬虫

```python
from fed_news_crawler import FedNewsCrawler
from risk_classifier import RiskClassifier

crawler = FedNewsCrawler()
classifier = RiskClassifier()

# 爬取并分类
result = await crawler.crawl(url="...")
for news in result['json_data']:
    classification = classifier.classify_news(news['title'])
    news['risk_category'] = classification['primary_category']
    news['risk_score'] = classification['risk_score']
    news['risk_level'] = classifier.get_risk_level(classification['risk_score'])
```

### 集成到数据库

```python
from fed_news_db import FedNewsDatabase
from risk_classifier import RiskClassifier

db = FedNewsDatabase()
classifier = RiskClassifier()

# 在 risk_analysis 表中存储分类结果
for news_id, news_title in news_items:
    result = classifier.classify_news(news_title)

    db.save_analysis(
        news_id=news_id,
        risk_category=result['primary_category'],
        risk_score=result['risk_score'],
        analysis_text=str(result['classification'])
    )
```

### 集成到风险分析

```python
from fed_risk_analyzer import FedRiskAnalyzer
from risk_classifier import RiskClassifier

analyzer = FedRiskAnalyzer()
classifier = RiskClassifier()

# 在 Claude 分析中使用分类结果
classifications = {
    news['title']: classifier.classify_news(news['title'])
    for news in news_items
}

# 按风险类别分组进行分析
for risk_category in classifier.get_all_risk_categories()['core_categories']:
    relevant_news = [
        n for n in news_items
        if risk_category in str(classifications[n['title']]['primary_category'])
    ]
    # 针对每个类别进行专门分析
```

---

## 风险等级说明

| 等级 | 分数范围 | 说明 | 行动 |
|------|---------|------|------|
| **Critical** | 80-100 | 极高风险，需要立即采取行动 | 立即通知管理层，启动应急预案 |
| **High** | 60-79 | 高风险，需要重点关注 | 深入分析，准备应对方案 |
| **Medium** | 40-59 | 中等风险，需要监控 | 定期监控，更新预警 |
| **Low** | 1-39 | 低风险，定期观察 | 常规监控，定期审查 |
| **None** | 0 | 无风险识别 | 不需要特殊行动 |

---

## 高优先级关键词

以下关键词表示极高优先级，必须立即关注和处理：

- **crisis** - 危机
- **default** - 违约
- **bankruptcy** - 破产
- **fraud** - 欺诈
- **cyber attack** - 网络攻击
- **scandal** - 丑闻

检测到这些关键词的新闻应自动提升为 **Critical** 风险等级。

---

## 配置文件详解

### risk_classification.json 结构

```json
{
  "metadata": {
    "version": "1.0",
    "description": "风险分类框架",
    "total_categories": 14
  },
  "risk_categories": {
    "CORE_CATEGORY": {
      "core_category": "显示名称",
      "subcategories": {
        "SUB_CATEGORY": {
          "intermediate": "中间类别名",
          "outer_sources": ["来源1", "来源2"],
          "keywords": ["关键词1", "关键词2"]
        }
      }
    }
  },
  "mapping_guide": {
    "priority_keywords": {
      "high": ["优先级高的关键词"],
      "medium": ["优先级中的关键词"],
      "low": ["优先级低的关键词"]
    }
  }
}
```

---

## 示例：完整的风险分析工作流

```python
import asyncio
from fed_news_crawler import FedNewsCrawler
from fed_news_db import FedNewsDatabase
from risk_classifier import RiskClassifier
from fed_risk_analyzer import FedRiskAnalyzer

async def complete_risk_analysis():
    """完整的风险分析工作流"""

    # 1. 爬取新闻
    crawler = FedNewsCrawler()
    result = await crawler.crawl()
    news_items = result['json_data']

    # 2. 初始化分类器和数据库
    classifier = RiskClassifier()
    db = FedNewsDatabase()
    db.insert_news(news_items)

    # 3. 对每条新闻进行风险分类
    classified_news = []
    for news in news_items:
        classification = classifier.classify_news(news['title'])
        risk_level = classifier.get_risk_level(classification['risk_score'])

        classified_news.append({
            **news,
            'risk_category': classification['primary_category'],
            'risk_score': classification['risk_score'],
            'risk_level': risk_level,
            'classification': classification['classification']
        })

        print(f"[{risk_level}] {news['title'][:60]}")

    # 4. 使用 Claude 进行高优先级新闻的深度分析
    high_priority_news = [n for n in classified_news if n['risk_score'] >= 60]

    if high_priority_news:
        analyzer = FedRiskAnalyzer()
        for news in high_priority_news:
            analysis = await analyzer.analyze_news_batch([news])
            print(f"\n深度分析: {news['title']}")
            print(analysis['analysis'][:500])

    # 5. 生成报告
    print("\n" + "="*60)
    print("风险报告")
    print("="*60)

    # 按风险等级分组
    by_level = {}
    for news in classified_news:
        level = news['risk_level']
        if level not in by_level:
            by_level[level] = []
        by_level[level].append(news)

    for level in ['Critical', 'High', 'Medium', 'Low']:
        if level in by_level:
            print(f"\n{level} Risk: {len(by_level[level])} 条")
            for news in by_level[level][:3]:
                print(f"  • {news['title'][:80]}")

asyncio.run(complete_risk_analysis())
```

---

## 最佳实践

### 1. 定期更新关键词
- 定期审查和更新 `risk_classification.json` 中的关键词
- 添加新发现的风险信号关键词
- 移除已过时的关键词

### 2. 设置告警阈值
```python
if risk_score >= 80:
    # 发送紧急告警
    send_alert("Critical Risk Detected")
elif risk_score >= 60:
    # 发送高优先级通知
    send_notification("High Risk Alert")
```

### 3. 跟踪和学习
- 记录分类错误和改进机会
- 定期评估分类准确性
- 根据实际经验调整权重和评分

### 4. 与内部系统集成
- 集成到风险管理系统
- 连接到合规性系统
- 整合到投资组合管理工具

---

## 故障排除

### 问题: 分类结果不准确

**原因**: 关键词不匹配或权重不合适

**解决**:
1. 检查新闻标题中是否包含关键词
2. 添加同义词或相关关键词
3. 调整风险评分计算逻辑

### 问题: 高优先级新闻被分类为低风险

**原因**: 高优先级关键词不在配置中

**解决**:
1. 查看 `high_priority_keywords` 配置
2. 添加缺失的关键词
3. 增加这些关键词的权重

---

## 参考资源

- **配置文件**: `risk_classification.json`
- **分类器代码**: `risk_classifier.py`
- **原始数据**: `risk-classification.csv`

---

**最后更新**: 2026-02-13
**版本**: 1.0
