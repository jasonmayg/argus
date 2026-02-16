# ARGUS Risk Classification - Quick Reference Card (English)

## 📋 Three-Tier Risk Classification System

```
Core Categories (3)
├─ Financial Risk
│  ├─ Market Risk
│  ├─ Credit Risk
│  ├─ Liquidity Risk
│  └─ Operational Risk
├─ Supply Chain Risk
│  ├─ Supply Risk
│  ├─ Demand Risk
│  ├─ Process Risk
│  ├─ Business Risk
│  └─ Disaster Risk
└─ Non-Commercial Risk
   ├─ Political Risk
   ├─ Regulatory Risk
   ├─ Reputation Risk
   └─ Systematic Risk
```

## 🎯 Quick Start

### 1. Import and Initialize
```python
from risk_classifier import RiskClassifier

classifier = RiskClassifier("risk_classification_en.json")
```

### 2. Classify News
```python
result = classifier.classify_news("Federal Reserve announces interest rate increase")

# Get results
print(result['primary_category'])      # FINANCIAL_RISK::MARKET_RISK
print(result['risk_score'])             # 0-100
print(result['matched_keywords'])      # Matched keywords
```

### 3. Get Risk Level
```python
level = classifier.get_risk_level(result['risk_score'])
# Returns: "Critical", "High", "Medium", "Low", or "None"
```

## ⚡ High Priority Keywords (Immediate Attention Required)

| Keyword | Meaning | Risk Category |
|---------|---------|---------------|
| **crisis** | Financial or operational crisis | SYSTEMATIC_RISK |
| **default** | Default on obligations | CREDIT_RISK |
| **bankruptcy** | Bankruptcy proceedings | CREDIT_RISK |
| **fraud** | Fraudulent activities | OPERATIONAL_RISK |
| **cyber attack** | Cybersecurity breaches | PROCESS_RISK |
| **scandal** | Major scandals | REPUTATION_RISK |

## 📊 Risk Level Mapping

| Level | Score | Description | Action |
|-------|-------|-------------|--------|
| 🔴 Critical | 80-100 | Critical risk | Notify management immediately |
| 🟠 High | 60-79 | High risk | Conduct deep analysis |
| 🟡 Medium | 40-59 | Medium risk | Monitor regularly |
| 🟢 Low | 1-39 | Low risk | Routine monitoring |
| ⚪ None | 0 | No risk | No special action |

## 💡 Common Code Snippets

### Batch Classification
```python
results = [classifier.classify_news(news) for news in news_list]
```

### Statistics by Category
```python
categories = classifier.get_all_risk_categories()
print(categories['core_categories'])
```

### Get High Priority Keywords
```python
high_priority = classifier.get_high_priority_keywords()
```

### Get Keywords by Category
```python
financial_keywords = classifier.get_all_keywords_by_category("FINANCIAL_RISK")
```

### Complete Classification Info
```python
result = classifier.classify_news(title)
details = result['classification']

print(f"Core Category: {details['core_category']}")
print(f"Intermediate Category: {details['intermediate_category']}")
print(f"Outer Sources: {details['outer_sources']}")
```

## 🔧 Configuration File

**File**: `risk_classification_en.json`

**Structure**:
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

## 📁 File Checklist

| File | Description |
|------|-------------|
| `risk_classifier.py` | Main classifier program |
| `risk_classification_en.json` | English configuration |
| `risk-classification.csv` | Original data |
| `RISK_CLASSIFICATION_GUIDE_EN.md` | Complete guide |
| `RISK_CLASSIFICATION_QUICK_REFERENCE_EN.md` | This file |

## 🚀 Integration with ARGUS

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
```

### Integration with Database
```python
from fed_news_db import FedNewsDatabase

db = FedNewsDatabase()
db.insert_news(news_items)
db.add_keyword_matches(classifier.get_high_priority_keywords())
```

### Integration with Risk Analysis
```python
from fed_risk_analyzer import FedRiskAnalyzer

analyzer = FedRiskAnalyzer()
# Use classification results for deep analysis
```

## 🔍 Debugging Tips

### View All Keyword Matches
```python
result = classifier.classify_news(title)
print(result['matched_keywords'])
```

### View All Matched Categories
```python
print(result['matched_categories'])
```

### Get Classification Details
```python
print(result['classification'])
# {
#   "core_category": "...",
#   "intermediate_category": "...",
#   "outer_sources": [...]
# }
```

## 📈 Usage Examples

### Detect High-Risk News
```python
high_risk_news = [
    news for news in news_items
    if classifier.get_risk_level(
        classifier.classify_news(news['title'])['risk_score']
    ) in ['High', 'Critical']
]
```

### Group by Risk Category
```python
by_category = {}
for news in news_items:
    result = classifier.classify_news(news['title'])
    cat = result['primary_category']
    if cat not in by_category:
        by_category[cat] = []
    by_category[cat].append(news)
```

### Real-Time Alert
```python
if classifier.get_risk_level(score) == 'Critical':
    send_alert(f"Critical Risk: {title}")
```

## ❓ FAQ

**Q: How to add new risk categories?**
A: Edit `risk_classification_en.json` file and add new categories with keywords.

**Q: How to customize risk scoring?**
A: Modify the `get_risk_level()` method to adjust thresholds.

**Q: How to improve classification accuracy?**
A: Add more keywords, synonyms, and adjust weights based on feedback.

**Q: Does it support multiple languages?**
A: Currently English only, but extensible to other languages.

## 🔗 Related Documentation

- [Complete User Guide](RISK_CLASSIFICATION_GUIDE_EN.md)
- [Crawler Guide](FED_CRAWLER_GUIDE.md)
- [Implementation Guide](IMPLEMENTATION_GUIDE.md)
- [Quick Start](NEXT_STEPS.md)

---

**Version**: 1.0 | **Updated**: 2026-02-13 | **Status**: ✅ Production Ready
**Language**: English
