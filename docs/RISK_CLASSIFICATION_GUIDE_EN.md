# ARGUS Risk Classification Guide - English Edition

## Overview

The ARGUS system now integrates the **Alphabet Global Treasury Risk Classification Framework**, providing a three-tier risk classification system:

1. **Core Risk Categories** (3 main categories)
2. **Intermediate Risk Categories** (11 sub-categories)
3. **Outer Risk Sources & Events** (Multiple specific sources)

---

## Risk Classification Framework

### Core Category 1: Financial Risk

#### Market Risk
- **Sources**: Equity prices, Interest rates, Foreign exchange, Commodity prices
- **Keywords**: equity, stock, interest rate, forex, currency, commodity
- **Risk Indicators**: Stock market decline, Rate increase, Currency fluctuation

#### Credit Risk
- **Sources**: Settlement issues, Rating downgrades, Bankruptcy, Default
- **Keywords**: credit, default, downgrade, bankruptcy, settlement
- **Risk Indicators**: Rating downgrade, Default events, Bankruptcy

#### Liquidity Risk
- **Sources**: Funding liquidity, Trading liquidity
- **Keywords**: liquidity, funding, cash flow, illiquid
- **Risk Indicators**: Liquidity crisis, Funding pressure, Market freeze

#### Operational Risk
- **Sources**: Fraud, Human error, Technology risks
- **Keywords**: fraud, error, technology, system failure, compliance
- **Risk Indicators**: Fraud detection, System failures, Model errors

### Core Category 2: Supply Chain Risk

#### Supply Risk
- **Sources**: Quality, Suppliers, Quantity, Delivery, Price, Sourcing, Communication
- **Keywords**: supply, supplier, sourcing, procurement, delivery, shortage
- **Risk Indicators**: Supplier failure, Supply shortage, Delivery delays

#### Demand Risk
- **Sources**: Product lifecycle, Forecasting, Consumer preference, Volatility, Price sensitivity, Regulations
- **Keywords**: demand, forecasting, consumer, preference, volatility
- **Risk Indicators**: Demand decline, Forecast errors, Lifecycle changes

#### Process Risk
- **Sources**: Innovation, Information, Cybersecurity, Coordination, Production, Logistics, ESG
- **Keywords**: cybersecurity, cyber, innovation, logistics, esg, sustainability
- **Risk Indicators**: Cyber attacks, Process failures, ESG issues

#### Business Risk
- **Sources**: Legal, Strategic, Competition, Contractual
- **Keywords**: legal, strategic, competition, contract, litigation
- **Risk Indicators**: Litigation, Increased competition, Contract disputes

#### Disaster Risk
- **Sources**: Natural and man-made catastrophes
- **Keywords**: disaster, catastrophe, earthquake, flood, pandemic, emergency
- **Risk Indicators**: Natural disasters, Pandemics, Emergency events

### Core Category 3: Non-Commercial Risk

#### Political Risk
- **Sources**: Politics, Trade wars, Tariffs
- **Keywords**: political, trade war, tariff, geopolitical, sanctions
- **Risk Indicators**: Trade wars, Tariff increases, Geopolitical tensions

#### Regulatory Risk
- **Sources**: Local, state, and global regulations
- **Keywords**: regulatory, regulation, compliance, policy, ban, restriction
- **Risk Indicators**: Regulatory changes, Compliance violations, Policy shifts

#### Reputation Risk
- **Sources**: Industry reputation, Individual reputation, Rumors
- **Keywords**: reputation, scandal, rumor, brand, social media
- **Risk Indicators**: Scandals, Reputation damage, Negative news

#### Systematic Risk
- **Sources**: Chain reactions, Panic selling
- **Keywords**: systematic, crisis, market crash, contagion, financial crisis
- **Risk Indicators**: Market crashes, Financial crises, Panic selling

---

## Quick Start

### 1. Initialize the Classifier

```python
from risk_classifier import RiskClassifier

classifier = RiskClassifier("risk_classification_en.json")
```

### 2. Classify News

```python
result = classifier.classify_news("Federal Reserve announces interest rate policy")

print(result['primary_category'])      # Primary risk category
print(result['risk_score'])             # 0-100 score
print(result['matched_keywords'])      # Matched keywords
```

### 3. Get Risk Level

```python
level = classifier.get_risk_level(result['risk_score'])
# Returns: "Critical", "High", "Medium", "Low", or "None"
```

### 4. Batch Classification

```python
results = [classifier.classify_news(news) for news in news_list]

# Filter by risk level
high_risk = [r for r in results if classifier.get_risk_level(r['risk_score']) == 'High']
```

### 5. Get High Priority Keywords

```python
critical_keywords = classifier.get_high_priority_keywords()
# Returns: ["crisis", "default", "bankruptcy", "fraud", "cyber attack", "scandal"]
```

### 6. Get Keywords by Category

```python
financial_keywords = classifier.get_all_keywords_by_category("FINANCIAL_RISK")

for category, keywords in financial_keywords.items():
    print(f"{category}: {keywords}")
```

---

## Risk Level System

| Level | Score | Description | Action |
|-------|-------|-------------|--------|
| **Critical** | 80-100 | Critical risk requiring immediate action | Notify management immediately |
| **High** | 60-79 | High risk requiring attention | Conduct deep analysis |
| **Medium** | 40-59 | Medium risk requiring monitoring | Regular monitoring |
| **Low** | 1-39 | Low risk for observation | Routine monitoring |
| **None** | 0 | No risk identified | No special action |

---

## High Priority Keywords

The following keywords indicate critical priority and require immediate attention:

- **crisis** - Financial or operational crisis
- **default** - Default on obligations
- **bankruptcy** - Bankruptcy proceedings
- **fraud** - Fraudulent activities
- **cyber attack** - Cybersecurity breaches
- **scandal** - Major scandals

Any news containing these keywords should be flagged as **Critical** risk level.

---

## Integration with ARGUS System

### Integration with Crawler

```python
from fed_news_crawler import FedNewsCrawler
from risk_classifier import RiskClassifier

crawler = FedNewsCrawler()
classifier = RiskClassifier()

result = await crawler.crawl()
for news in result['json_data']:
    classification = classifier.classify_news(news['title'])
    news['risk_category'] = classification['primary_category']
    news['risk_score'] = classification['risk_score']
```

### Integration with Database

```python
from fed_news_db import FedNewsDatabase

db = FedNewsDatabase()

for news in news_items:
    classification = classifier.classify_news(news['title'])
    db.save_analysis(
        news_id=news['id'],
        risk_category=classification['primary_category'],
        risk_score=classification['risk_score'],
        analysis_text=str(classification['classification'])
    )
```

### Integration with Risk Analysis

```python
from fed_risk_analyzer import FedRiskAnalyzer

analyzer = FedRiskAnalyzer()

# Analyze high-risk news items
high_risk_news = [
    n for n in news_items
    if classifier.get_risk_level(classifier.classify_news(n['title'])['risk_score']) in ['High', 'Critical']
]

analysis = await analyzer.analyze_news_batch(high_risk_news)
```

---

## Configuration File Structure

### risk_classification_en.json

```json
{
  "metadata": {
    "version": "1.0",
    "description": "Framework description",
    "language": "English"
  },
  "risk_categories": {
    "CORE_CATEGORY": {
      "core_category": "Display name",
      "subcategories": {
        "SUB_CATEGORY": {
          "intermediate": "Intermediate category name",
          "outer_sources": ["Source 1", "Source 2"],
          "keywords": ["keyword1", "keyword2"]
        }
      }
    }
  },
  "mapping_guide": {
    "priority_keywords": {
      "high": ["High priority keywords"],
      "medium": ["Medium priority keywords"],
      "low": ["Low priority keywords"]
    }
  }
}
```

---

## Complete Risk Analysis Workflow

```python
import asyncio
from fed_news_crawler import FedNewsCrawler
from fed_news_db import FedNewsDatabase
from risk_classifier import RiskClassifier
from fed_risk_analyzer import FedRiskAnalyzer

async def complete_risk_analysis():
    # 1. Crawl news
    crawler = FedNewsCrawler()
    result = await crawler.crawl()
    news_items = result['json_data']

    # 2. Initialize classifier and database
    classifier = RiskClassifier()
    db = FedNewsDatabase()
    db.insert_news(news_items)

    # 3. Classify each news item
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

    # 4. Conduct deep analysis on high-risk items
    high_priority_news = [n for n in classified_news if n['risk_score'] >= 60]

    if high_priority_news:
        analyzer = FedRiskAnalyzer()
        for news in high_priority_news:
            analysis = await analyzer.analyze_news_batch([news])
            print(f"\nDeep Analysis: {news['title']}")
            print(analysis['analysis'][:500])

    # 5. Generate report
    print("\n" + "="*60)
    print("Risk Report")
    print("="*60)

    by_level = {}
    for news in classified_news:
        level = news['risk_level']
        if level not in by_level:
            by_level[level] = []
        by_level[level].append(news)

    for level in ['Critical', 'High', 'Medium', 'Low']:
        if level in by_level:
            print(f"\n{level} Risk: {len(by_level[level])} items")
            for news in by_level[level][:3]:
                print(f"  • {news['title'][:80]}")

asyncio.run(complete_risk_analysis())
```

---

## Best Practices

### 1. Regular Keyword Updates
- Review and update keywords in `risk_classification_en.json` regularly
- Add newly discovered risk signal keywords
- Remove outdated keywords

### 2. Set Alert Thresholds
```python
if risk_score >= 80:
    send_alert("CRITICAL RISK DETECTED")
elif risk_score >= 60:
    send_notification("HIGH RISK ALERT")
```

### 3. Track and Learn
- Record classification errors and improvement opportunities
- Regularly evaluate classification accuracy
- Adjust weights based on actual experience

### 4. System Integration
- Integrate with treasury risk management systems
- Connect to compliance systems
- Integrate with portfolio management tools

---

## Troubleshooting

### Issue: Inaccurate Classification Results

**Cause**: Keywords don't match or weights are inappropriate

**Solution**:
1. Check if news title contains relevant keywords
2. Add synonyms or related keywords
3. Adjust risk scoring logic

### Issue: High-Priority News Classified as Low Risk

**Cause**: High-priority keywords missing from configuration

**Solution**:
1. Check `high_priority_keywords` configuration
2. Add missing keywords
3. Increase weight for these keywords

---

## File Reference

| File | Description |
|------|-------------|
| `risk_classifier.py` | Main classifier program |
| `risk_classification_en.json` | English configuration file |
| `risk_classification.json` | Original configuration file |
| `risk-classification.csv` | Original data source |
| `RISK_CLASSIFICATION_GUIDE_EN.md` | This guide |
| `RISK_CLASSIFICATION_QUICK_REFERENCE.md` | Quick reference card |

---

## Summary

The ARGUS risk classification system provides:

✅ **Three-tier classification system**: Core → Intermediate → Sources
✅ **100+ keywords**: Automatic risk signal identification
✅ **Risk scoring system**: 0-100 score with four risk levels
✅ **High-priority alerts**: Immediate notification for critical keywords
✅ **Complete integration**: Seamless integration with crawler, database, and analysis

**Status**: ✅ Production Ready

---

**Last Updated**: 2026-02-13
**Version**: 1.0 - English Edition
**Language**: English
