#!/usr/bin/env python3
"""
ARGUS: Federal Reserve News Backtest Crawler
用于抓取历史News数据，支持多年份和Date范围Filter
"""

import asyncio
import json
from datetime import datetime
from typing import List, Dict, Optional

from fed_news_crawler import FedNewsCrawler


async def crawl_multiple_years(
    years: List[int],
    keywords: Optional[List[str]] = None,
    output_file: Optional[str] = None
) -> List[Dict]:
    """
    抓取多个年份的News数据

    Args:
        years: 要抓取的年份列表 (例如: [2024, 2025, 2026])
        keywords: FilterKeywords列表
        output_file: 输出FilePath

    Returns:
        所有年份的News数据列表
    """
    crawler = FedNewsCrawler(keywords=keywords)
    all_news = []

    print(f"开始抓取 {len(years)} 个年份的News数据...")
    print(f"年份: {years}")
    print(f"Keywords: {', '.join(crawler.keywords)}")
    print("="*60)

    for year in years:
        url = f"https://www.federalreserve.gov/newsevents/pressreleases/{year}-press.htm"

        print(f"\n正在抓取 {year} 年的数据...")
        result = await crawler.crawl(url=url)

        if result.get("status") == "success":
            year_news = result['json_data']
            all_news.extend(year_news)

            print(f"  ✓ {year} 年: 找到 {result.get('total_items_found')} 条News, "
                  f"Filter后 {result.get('filtered_items')} 条")

            # 添加随机延迟以避免被限制
            import random
            await asyncio.sleep(random.uniform(1, 2))
        else:
            print(f"  ✗ {year} 年: 抓取失败 - {result.get('error')}")

    # 按Date倒序排列
    all_news.sort(key=lambda x: x['date'], reverse=True)

    print("\n" + "="*60)
    print(f"✓ 抓取完成: 共获取 {len(all_news)} 条News")

    # 保存Results
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(all_news, f, ensure_ascii=False, indent=2)
        print(f"✓ 数据已保存到: {output_file}")

    return all_news


def filter_by_date_range(
    items: List[Dict],
    start_date: str,
    end_date: str
) -> List[Dict]:
    """
    按Date范围FilterNews (格式: YYYY-MM-DD)

    Args:
        items: News列表
        start_date: 开始Date (YYYY-MM-DD)
        end_date: 结束Date (YYYY-MM-DD)

    Returns:
        Filter后的News列表
    """
    from datetime import datetime

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    filtered = [
        item for item in items
        if start <= datetime.strptime(item['date'], "%Y-%m-%d").date() <= end
    ]

    return filtered


def filter_by_keywords(items: List[Dict], keywords: List[str]) -> List[Dict]:
    """
    按KeywordsFilterNews

    Args:
        items: News列表
        keywords: Keywords列表

    Returns:
        Filter后的News列表
    """
    filtered = []

    for item in items:
        title_lower = item['title'].lower()

        # Check标题是否包含任何Keywords
        for keyword in keywords:
            if keyword.lower() in title_lower:
                filtered.append(item)
                break  # 避免重复添加

    return filtered


async def main():
    """
    Example: 抓取 2024 和 2025 年的News数据

    修改说明:
    - 如果需要其他年份，修改 years 列表
    - 如果需要其他Keywords，修改 keywords 列表
    - 如果需要特定的Date范围，使用 filter_by_date_range()
    """

    # ===== Example 1: 抓取 2024 和 2025 年全年数据 =====
    print("\n" + "="*60)
    print("Example 1: 抓取 2024 和 2025 年的News数据")
    print("="*60)

    backtest_data = await crawl_multiple_years(
        years=[2024, 2025],
        output_file="data/fed_news/fed_news_backtest_2024_2025.json"
    )

    # ===== Example 2: Filter特定Date范围的数据 =====
    print("\n" + "="*60)
    print("Example 2: Filter 2024 年 Q4 (10-12 月) 的数据")
    print("="*60)

    q4_data = filter_by_date_range(
        backtest_data,
        start_date="2024-10-01",
        end_date="2024-12-31"
    )

    print(f"✓ Q4 数据: {len(q4_data)} 条记录")
    if q4_data:
        print("\n前 5 条:")
        for item in q4_data[:5]:
            print(f"  - [{item['date']}] {item['title'][:80]}...")

    # ===== Example 3: 使用自定义KeywordsFilter =====
    print("\n" + "="*60)
    print("Example 3: Filter包含特定Keywords的News")
    print("="*60)

    custom_keywords = ["FOMC", "interest", "monetary policy", "rate decision"]
    filtered_data = filter_by_keywords(backtest_data, custom_keywords)

    print(f"✓ 自定义KeywordsFilter: {len(filtered_data)} 条记录")
    if filtered_data:
        print("\n前 3 条:")
        for item in filtered_data[:3]:
            print(f"  - [{item['date']}] {item['title']}")

    # ===== Example 4: 保存Filter后的数据 =====
    output_path = "data/fed_news/fed_news_filtered_2024_2025.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(filtered_data, f, ensure_ascii=False, indent=2)
    print(f"\n✓ Filter后的数据已保存到: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
