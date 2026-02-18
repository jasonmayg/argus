#!/usr/bin/env python3
"""
ARGUS: Risk Classification and Mapping Module
Uses a detailed risk classification system for automatic news classification
"""

import json
from typing import List, Dict, Tuple, Optional
from pathlib import Path


class RiskClassifier:
    """Risk Classification engine"""

    def __init__(self, config_file: str = "risk_classification.json"):
        """
        Initialize the classifier

        Args:
            config_file: Risk classification configuration file path
        """
        self.config_file = config_file
        self.config = self._load_config()
        self.risk_map = self._build_risk_map()

    def _load_config(self) -> Dict:
        """Load risk classification configuration"""
        if not Path(self.config_file).exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_file}")

        with open(self.config_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _build_risk_map(self) -> Dict:
        """Build mapping from keywords to risk categories"""
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
        Classify a single news item

        Args:
            title: News title
            content: News content (optional)

        Returns:
            Classification results
        """
        text = f"{title} {content or ''}".lower()
        matched_categories = {}
        matched_keywords = {}

        # Search for matching keywords
        for keyword, categories in self.risk_map.items():
            if keyword in text:
                for category in categories:
                    if category not in matched_categories:
                        matched_categories[category] = 0
                    matched_categories[category] += 1

                    if keyword not in matched_keywords:
                        matched_keywords[keyword] = []
                    matched_keywords[keyword].append(category)

        # Calculate risk score
        risk_score = min(len(matched_keywords) * 10, 100)  # Maximum 100

        # Sort classification results
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
        """Get classification details"""
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
        Get risk level based on score

        Args:
            score: Risk score (0-100)

        Returns:
            Risk level: Low, Medium, High, Critical
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
        Get keywords for specific or all classifications

        Args:
            core_category: Core category (optional)

        Returns:
            Classification keywords dictionary
        """
        result = {}

        if core_category:
            # Get keywords for specific category
            if core_category not in self.config['risk_categories']:
                return {}

            core_data = self.config['risk_categories'][core_category]
            for sub_cat, sub_data in core_data['subcategories'].items():
                result[f"{core_category}::{sub_cat}"] = sub_data.get('keywords', [])
        else:
            # Get all keywords
            for core_cat, core_data in self.config['risk_categories'].items():
                for sub_cat, sub_data in core_data['subcategories'].items():
                    result[f"{core_cat}::{sub_cat}"] = sub_data.get('keywords', [])

        return result

    def get_high_priority_keywords(self) -> List[str]:
        """Get high priority keywords"""
        return self.config['mapping_guide']['priority_keywords']['high']

    def get_all_risk_categories(self) -> Dict:
        """Get hierarchical structure of all risk categories"""
        return {
            "core_categories": [
                cat_data['core_category']
                for cat_data in self.config['risk_categories'].values()
            ],
            "total_categories": len(self.config['risk_categories']),
            "details": self.config['risk_categories']
        }


async def example_usage():
    """Usage example"""
    print("\n" + "="*70)
    print("ARGUS: Risk Classification System - Usage Example")
    print("="*70)

    # Initialize classifier
    classifier = RiskClassifier()

    # Example 1: Classify single news item
    print("\n[Example 1] Classify single news item")
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

        print(f"\n📰 Title: {news[:70]}")
        print(f"   Risk Score: {result['risk_score']} / 100 ({risk_level})")
        print(f"   Primary Category: {result['primary_category']}")
        print(f"   Matched Keywords: {', '.join(result['matched_keywords'].keys())[:60]}")

    # Example 2: Get all keywords
    print("\n\n[Example 2] Get all keywords for Financial Risk")
    print("-" * 70)

    keywords = classifier.get_all_keywords_by_category("FINANCIAL_RISK")
    for category, kw_list in keywords.items():
        sub_cat = category.split("::")[-1]
        print(f"\n{sub_cat}:")
        print(f"  Keywords: {', '.join(kw_list[:10])}")

    # Example 3: Get high priority keywords
    print("\n\n[Example 3] High Priority Keywords (require immediate attention)")
    print("-" * 70)

    high_priority = classifier.get_high_priority_keywords()
    print(f"Keywords: {', '.join(high_priority)}")

    # Example 4: Get all risk categories
    print("\n\n[Example 4] Overview of all risk categories")
    print("-" * 70)

    categories = classifier.get_all_risk_categories()
    print(f"Number of core risk categories: {categories['total_categories']}")
    print(f"Core categories: {', '.join(categories['core_categories'])}")

    # Example 5: Batch classification
    print("\n\n[Example 5] Batch classification results summary")
    print("-" * 70)

    results = [classifier.classify_news(news) for news in news_examples]
    risk_distribution = {}

    for result in results:
        level = classifier.get_risk_level(result['risk_score'])
        if level not in risk_distribution:
            risk_distribution[level] = 0
        risk_distribution[level] += 1

    print("Risk level distribution:")
    for level in ["Critical", "High", "Medium", "Low", "None"]:
        count = risk_distribution.get(level, 0)
        print(f"  • {level}: {count} items")


if __name__ == "__main__":
    import asyncio
    asyncio.run(example_usage())
