#!/usr/bin/env python3
"""
ARGUS: Risk Classification and Mapping Module
使用详细的RiskClassification体系对News进行自动Classification
"""

import json
from typing import List, Dict, Tuple, Optional
from pathlib import Path


class RiskClassifier:
    """RiskClassification器"""

    def __init__(self, config_file: str = "risk_classification.json"):
        """
        初始化Classification器

        Args:
            config_file: RiskClassificationConfigurationFilePath
        """
        self.config_file = config_file
        self.config = self._load_config()
        self.risk_map = self._build_risk_map()

    def _load_config(self) -> Dict:
        """加载RiskClassificationConfiguration"""
        if not Path(self.config_file).exists():
            raise FileNotFoundError(f"ConfigurationFile不存在: {self.config_file}")

        with open(self.config_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _build_risk_map(self) -> Dict:
        """构建Keywords到Risk类别的映射"""
        risk_map = {}

        for core_category, core_data in self.config['risk_categories'].items():
            for sub_category, sub_data in core_data['subcategories'].items():
                category_key = f"{core_category}::{sub_category}"

                for keyword in sub_data.get('keywords', []):
                    if keyword not in risk_map:
                        risk_map[keyword.lower()] = []
                    risk_map[keyword.lower()].append(category_key)

        return risk_map

    def classify_news(self, title: str, content: Optional[str] = None) -> Dict:
        """
        Classification单条News

        Args:
            title: News标题
            content: News内容（可选）

        Returns:
            ClassificationResults
        """
        text = f"{title} {content or ''}".lower()
        matched_categories = {}
        matched_keywords = {}

        # 搜索匹配的Keywords
        for keyword, categories in self.risk_map.items():
            if keyword in text:
                for category in categories:
                    if category not in matched_categories:
                        matched_categories[category] = 0
                    matched_categories[category] += 1

                    if keyword not in matched_keywords:
                        matched_keywords[keyword] = []
                    matched_keywords[keyword].append(category)

        # 计算Risk评分
        risk_score = min(len(matched_keywords) * 10, 100)  # 最高 100 分

        # 排序ClassificationResults
        sorted_categories = sorted(
            matched_categories.items(),
            key=lambda x: x[1],
            reverse=True
        )

        return {
            "title": title,
            "matched_keywords": matched_keywords,
            "matched_categories": dict(sorted_categories),
            "risk_score": risk_score,
            "primary_category": sorted_categories[0][0] if sorted_categories else None,
            "classification": self._get_classification_details(sorted_categories)
        }

    def _get_classification_details(self, sorted_categories: List[Tuple]) -> Dict:
        """获取Classification详情"""
        if not sorted_categories:
            return {
                "core_category": "Unknown",
                "intermediate_category": "Unknown",
                "outer_sources": []
            }

        primary_category = sorted_categories[0][0]
        core_cat, sub_cat = primary_category.split("::")

        core_data = self.config['risk_categories'][core_cat]
        sub_data = core_data['subcategories'][sub_cat]

        return {
            "core_category": core_data['core_category'],
            "intermediate_category": sub_data['intermediate'],
            "outer_sources": sub_data['outer_sources']
        }

    def get_risk_level(self, score: int) -> str:
        """
        根据评分获取Risk等级

        Args:
            score: Risk评分 (0-100)

        Returns:
            Risk等级: Low, Medium, High, Critical
        """
        if score >= 80:
            return "Critical"
        elif score >= 60:
            return "High"
        elif score >= 40:
            return "Medium"
        elif score > 0:
            return "Low"
        else:
            return "None"

    def get_all_keywords_by_category(self, core_category: Optional[str] = None) -> Dict:
        """
        获取特定或所有Classification的Keywords

        Args:
            core_category: 核心类别（可选）

        Returns:
            ClassificationKeywords字典
        """
        result = {}

        if core_category:
            # 获取特定类别的Keywords
            if core_category not in self.config['risk_categories']:
                return {}

            core_data = self.config['risk_categories'][core_category]
            for sub_cat, sub_data in core_data['subcategories'].items():
                result[f"{core_category}::{sub_cat}"] = sub_data.get('keywords', [])
        else:
            # 获取所有Keywords
            for core_cat, core_data in self.config['risk_categories'].items():
                for sub_cat, sub_data in core_data['subcategories'].items():
                    result[f"{core_cat}::{sub_cat}"] = sub_data.get('keywords', [])

        return result

    def get_high_priority_keywords(self) -> List[str]:
        """获取高优先级Keywords"""
        return self.config['mapping_guide']['priority_keywords']['high']

    def get_all_risk_categories(self) -> Dict:
        """获取所有Risk类别的层级结构"""
        return {
            "core_categories": [
                cat_data['core_category']
                for cat_data in self.config['risk_categories'].values()
            ],
            "total_categories": len(self.config['risk_categories']),
            "details": self.config['risk_categories']
        }


async def example_usage():
    """使用Example"""
    print("\n" + "="*70)
    print("ARGUS: RiskClassification系统 - 使用Example")
    print("="*70)

    # 初始化Classification器
    classifier = RiskClassifier()

    # Example 1: Classification单条News
    print("\n[Example 1] Classification单条News")
    print("-" * 70)

    news_examples = [
        "Federal Reserve Board announces new regulation on crypto currency",
        "Market shows signs of panic selling amid economic uncertainty",
        "Credit rating downgrade for major financial institution",
        "Cybersecurity breach affects major payment system",
        "Trade war escalates with new tariff announcement"
    ]

    for news in news_examples:
        result = classifier.classify_news(news)
        risk_level = classifier.get_risk_level(result['risk_score'])

        print(f"\n📰 标题: {news[:70]}")
        print(f"   Risk评分: {result['risk_score']} / 100 ({risk_level})")
        print(f"   主要类别: {result['primary_category']}")
        print(f"   匹配Keywords: {', '.join(result['matched_keywords'].keys())[:60]}")

    # Example 2: 获取所有Keywords
    print("\n\n[Example 2] 获取 Financial Risk 的所有Keywords")
    print("-" * 70)

    keywords = classifier.get_all_keywords_by_category("FINANCIAL_RISK")
    for category, kw_list in keywords.items():
        sub_cat = category.split("::")[-1]
        print(f"\n{sub_cat}:")
        print(f"  Keywords: {', '.join(kw_list[:10])}")

    # Example 3: 获取高优先级Keywords
    print("\n\n[Example 3] 高优先级Keywords（需要立即关注）")
    print("-" * 70)

    high_priority = classifier.get_high_priority_keywords()
    print(f"Keywords: {', '.join(high_priority)}")

    # Example 4: 获取所有Risk类别
    print("\n\n[Example 4] 所有Risk类别概览")
    print("-" * 70)

    categories = classifier.get_all_risk_categories()
    print(f"核心Risk类别数: {categories['total_categories']}")
    print(f"核心类别: {', '.join(categories['core_categories'])}")

    # Example 5: 批量Classification
    print("\n\n[Example 5] 批量ClassificationResults统计")
    print("-" * 70)

    results = [classifier.classify_news(news) for news in news_examples]
    risk_distribution = {}

    for result in results:
        level = classifier.get_risk_level(result['risk_score'])
        if level not in risk_distribution:
            risk_distribution[level] = 0
        risk_distribution[level] += 1

    print("Risk等级分布:")
    for level in ["Critical", "High", "Medium", "Low", "None"]:
        count = risk_distribution.get(level, 0)
        print(f"  • {level}: {count} 条")


if __name__ == "__main__":
    import asyncio
    asyncio.run(example_usage())
